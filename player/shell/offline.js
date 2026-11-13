/* Offline copies: the tracks this device keeps, so a play starts from disk instead of from the
   network.

   Two halves, and this is the one that decides. It owns the budget, the download, the resume,
   the integrity check, the eviction order, and the way a kept file is handed to the audio
   element. It never touches the filesystem — it cannot, the window has none — so every byte
   crosses `invoke` into the commands in `src-tauri/src/offline.rs`, which sanitises every name,
   writes through a temporary file and renames it, and keeps the per-account manifest.

   The connection is one-way in the interesting direction: `local()` answers from the manifest
   and the file on disk and never asks the server whether the track is still there. Only
   `download()` opens a socket. That is the whole point of an offline copy: a bus, a basement,
   a hotel wifi that answers a captive portal with a login page.

   The budget speaks the app's own vocabulary (`web/index.html`, the Offline audio dialog):
   bytes, 0 meaning keep nothing, the oldest *played* copy goes first, and the copy that is
   playing is never taken. A track larger than the whole budget is not kept at all — evicting
   the library to hold one file is not a budget, it is a surprise.

   No credentials live here. The caller composes the stream URL (`configure({streamUrl})`), so
   the password stays in the connect screen's session and this file never reads it. The only
   thing written to storage is the budget itself, which is a preference, not a secret.

   Loaded as a classic script with no build step, like `connect.js`: `PlayerOffline` is the API,
   `invoke`/`fetch`/`createUrl` are injectable, and there is no wiring at the bottom because this
   file has no markup of its own. */
(function (root) {
  "use strict";

  /* Where the budget is remembered. Distinct from the web app's `hl.offlineBudget` on purpose:
     the two run in different origins and a stray copy of one must not set the other's ceiling. */
  const BUDGET_KEY = "hl.player.offlineBudget";
  /* The same default the app's dialog offers, so a person who never opens the setting is not
     surprised by an empty cache either. */
  const DEFAULT_BUDGET = 2 * 1024 ** 3;
  /* One IPC message carries one chunk. 256 KiB is a compromise: large enough that a 5-minute
     track is ~50 round trips, small enough that a cancelled download has not thrown away much. */
  const CHUNK = 256 * 1024;
  /* Reads for playback are larger: they are not re-sent, they are reassembled. */
  const READ_CHUNK = 4 * 1024 * 1024;
  /* Attempts at one download's *interruptions*, per fresh connection. A server that drops the
     socket every 100 bytes would otherwise loop forever. */
  const MAX_ATTEMPTS = 3;

  /* A file name this player will accept, mirroring `safe_component` in offline.rs: ASCII
     alphanumerics, dot, dash, underscore; no leading dot (so no `.`, `..` or `.hidden`), and
     short. The mirror exists so a hostile track id is refused before it reaches a command, and
     the Rust side refuses it again — a name that only one side checks is a name that gets
     through where the other side runs. */
  const COMPONENT_MAX = 64;
  const COMPONENT_CHARS = /^[A-Za-z0-9._-]+$/;
  const SUFFIX_MAX = 8;
  const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

  let state = {
    account: null,
    invoke: null,
    fetch: null,
    streamUrl: null,
    createUrl: null,
    storage: null,
    readChunk: READ_CHUNK,
    now: function () { return Date.now(); },
  };

  function text(value) {
    return value == null ? "" : String(value);
  }

  function numberOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function reasonOf(error) {
    const said = text(error && error.message ? error.message : error).trim();
    return said || "the request failed before an answer arrived";
  }

  /* ---- names ------------------------------------------------------------ */

  /* The Rust rule, twice: once here so a bad name never leaves this file, once in offline.rs so
     a command cannot be talked into one. Both are pinned by their own tests against the same
     table. */
  function safeComponent(raw) {
    const value = text(raw).trim();
    if (!value || value.length > COMPONENT_MAX) return null;
    if (value.charAt(0) === ".") return null;
    if (!COMPONENT_CHARS.test(value)) return null;
    return value;
  }

  /* An extension is a hint, never a path: everything but ASCII alphanumerics is dropped, so
     `../../etc` becomes `etc`, and a name that is left empty becomes `bin`. */
  function safeSuffix(raw) {
    const clean = text(raw).trim().replace(/^\.+/, "").toLowerCase().replace(/[^a-z0-9]/g, "");
    return clean && clean.length <= SUFFIX_MAX ? clean : "bin";
  }

  function utf8Bytes(value) {
    if (typeof root.TextEncoder === "function") return new root.TextEncoder().encode(text(value));
    throw new Error("this build cannot encode a name");
  }

  /* FNV-1a over the UTF-8 bytes, the same 32-bit function offline.rs folds. It is not a
     security hash; it is what keeps two accounts whose sanitised prefixes collide — one server
     at `https://a/b`, another at `https://a_b` — out of each other's directory. */
  function fnv1a(bytes) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < bytes.length; i += 1) {
      hash ^= bytes[i];
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash >>> 0;
  }

  function hex16(value) {
    return ("0000000000000000" + value.toString(16)).slice(-16);
  }

  /* A readable prefix plus a tag of the whole key. The prefix is what makes `ls` legible; the
     tag is what makes it correct. Returns "" when the platform cannot encode the key, and the
     caller then refuses rather than inventing a directory. */
  function accountName(raw) {
    const value = text(raw);
    let prefix = "";
    for (const character of value) {
      if (prefix.length >= 40) break;
      const code = character.codePointAt(0);
      const plain = (code >= 48 && code <= 57) || (code >= 65 && code <= 90)
        || (code >= 97 && code <= 122) || character === "-" || character === "_";
      prefix += plain ? character.toLowerCase() : "_";
    }
    prefix = prefix.replace(/_+$/, "");
    if (!prefix) prefix = "account";
    try {
      return prefix + "-" + hex16(fnv1a(utf8Bytes(value)));
    } catch (error) {
      return "";
    }
  }

  /* The two things a track contributes to a file name, both checked before anything is written.
     The id is the key the manifest uses; the suffix is a hint the store turns into an
     extension. */
  function keyOf(track) {
    const row = track && typeof track === "object" ? track : {};
    const key = safeComponent(row.id);
    if (!key) {
      return { error: "this player cannot use that track id as a file name: "
        + (text(row.id).trim() ? "“" + text(row.id).slice(0, 40) + "” is not one" : "there is none") };
    }
    const suffix = safeSuffix(row.suffix || row.transcodedSuffix || "");
    return { key: key, suffix: suffix };
  }

  /* ---- base64 ----------------------------------------------------------- */

  /* The IPC payload is JSON, so bytes travel base64. Both directions are here rather than
     delegated to `btoa`/`Buffer`: the same functions work in the window and under node, and a
     round trip is one line of test. */
  function encodeBase64(bytes) {
    let out = "";
    for (let i = 0; i < bytes.length; i += 3) {
      const a = bytes[i];
      const b = i + 1 < bytes.length ? bytes[i + 1] : 0;
      const c = i + 2 < bytes.length ? bytes[i + 2] : 0;
      out += B64.charAt(a >> 2)
        + B64.charAt(((a & 3) << 4) | (b >> 4))
        + (i + 1 < bytes.length ? B64.charAt(((b & 15) << 2) | (c >> 6)) : "=")
        + (i + 2 < bytes.length ? B64.charAt(c & 63) : "=");
    }
    return out;
  }

  function decodeBase64(value) {
    const source = text(value).replace(/[\s]+/g, "");
    const out = new Uint8Array(Math.floor((source.length * 3) / 4));
    let acc = 0;
    let bits = 0;
    let at = 0;
    for (const character of source) {
      if (character === "=") break;
      const index = B64.indexOf(character);
      if (index < 0) return null;
      acc = (acc << 6) | index;
      bits += 6;
      if (bits >= 8) {
        bits -= 8;
        out[at] = (acc >> bits) & 0xff;
        at += 1;
      }
    }
    return at === out.length ? out : out.subarray(0, at);
  }

  /* ---- the store on the other side of invoke ---------------------------- */

  function defaultInvoke() {
    const internals = root.__TAURI_INTERNALS__;
    if (internals && typeof internals.invoke === "function") {
      return function (name, args) { return internals.invoke(name, args); };
    }
    const api = root.__TAURI__;
    if (api && api.core && typeof api.core.invoke === "function") {
      return function (name, args) { return api.core.invoke(name, args); };
    }
    return null;
  }

  function defaultCreateUrl() {
    if (typeof root.Blob !== "function" || !root.URL
        || typeof root.URL.createObjectURL !== "function") {
      return null;
    }
    return function (bytes, type) {
      return root.URL.createObjectURL(new root.Blob([bytes], { type: type }));
    };
  }

  function defaultStorage() {
    try {
      const storage = root.localStorage;
      if (storage && typeof storage.getItem === "function") return storage;
    } catch (error) {
      /* A window whose storage is denied still plays what it downloaded. */
    }
    return null;
  }

  function configure(options) {
    const input = options || {};
    state.account = null;
    const server = text(input.account && input.account.server).trim();
    const username = text(input.account && input.account.username).trim();
    if (server && username) {
      const key = server + "|" + username;
      const dir = accountName(key);
      if (dir) state.account = { key: key, dir: dir, server: server, username: username };
    }
    state.invoke = typeof input.invoke === "function" ? input.invoke : defaultInvoke();
    state.fetch = typeof input.fetch === "function" ? input.fetch
      : (typeof root.fetch === "function" ? root.fetch.bind(root) : null);
    state.streamUrl = typeof input.streamUrl === "function" ? input.streamUrl : null;
    state.createUrl = typeof input.createUrl === "function" ? input.createUrl : defaultCreateUrl();
    state.storage = input.storage || defaultStorage();
    state.readChunk = Math.max(1, numberOrNull(input.readChunk) || READ_CHUNK);
    state.now = typeof input.now === "function" ? input.now : function () { return Date.now(); };
    return { account: state.account ? state.account.key : null, dir: state.account ? state.account.dir : null };
  }

  function ready() {
    if (state.account && state.invoke) return null;
    if (!state.account) return "no account is configured, so there is nowhere to keep a copy";
    return "this build has no local store";
  }

  /* Every command answers `{ok, value, reason}`: a rejected promise is a refusal with a
     sentence in it, and a caller that has to catch is a caller that will forget to. */
  async function call(name, args) {
    try {
      return { ok: true, value: await state.invoke(name, args) };
    } catch (error) {
      return { ok: false, reason: reasonOf(error) };
    }
  }

  async function manifest() {
    if (ready()) return { entries: {} };
    const result = await call("offline_manifest", { account: state.account.key });
    const value = result.ok ? result.value : null;
    const entries = value && value.entries && typeof value.entries === "object" ? value.entries : {};
    return { entries: entries };
  }

  /* ---- the budget ------------------------------------------------------- */

  function storedBudget() {
    if (!state.storage) return null;
    try {
      const raw = state.storage.getItem(BUDGET_KEY);
      return raw === null || raw === undefined ? null : Number(raw);
    } catch (error) {
      return null;
    }
  }

  /* Bytes. 0 is a real answer — keep nothing — so it is never confused with "unset". */
  function budget() {
    const stored = storedBudget();
    if (stored === null) return DEFAULT_BUDGET;
    return Number.isFinite(stored) && stored > 0 ? stored : 0;
  }

  function rememberBudget(bytes) {
    if (!state.storage) return;
    try {
      state.storage.setItem(BUDGET_KEY, String(bytes));
    } catch (error) {
      /* A denied storage costs the setting, not the copies already on disk. */
    }
  }

  /* ---- what is kept ----------------------------------------------------- */

  function entrySize(entry) {
    return numberOrNull(entry && entry.size) || 0;
  }

  async function stats() {
    const entries = (await manifest()).entries;
    let bytes = 0;
    let count = 0;
    let partial = 0;
    for (const entry of Object.keys(entries)) {
      const row = entries[entry];
      /* A pinned partial is a download in flight, not a copy this device can play: counting it
         as kept would make the dialog claim something it cannot offer. */
      if (!row || row.complete === false) { partial += 1; continue; }
      bytes += entrySize(row);
      count += 1;
    }
    return { bytes: bytes, count: count, partial: partial, budget: budget(), free: await freeSpace() };
  }

  /* Reported by the system, or a stated reason why not. A build that cannot answer must not
     pretend the disk is empty or infinite. */
  async function freeSpace() {
    if (ready()) return { available: null, total: null, reason: ready() };
    const result = await call("offline_free", { account: state.account.key });
    if (!result.ok) return { available: null, total: null, reason: result.reason };
    const value = result.value || {};
    return {
      available: numberOrNull(value.available),
      total: numberOrNull(value.total),
      reason: text(value.reason) || null,
    };
  }

  /* A kept copy is only a copy if the file is still the length the manifest recorded. A file
     that is shorter than that is truncated — a half-written file, a filesystem that lost its
     tail — and it is dropped rather than played, because the audio element would happily stop
     in the middle and call it the end of the song. */
  async function verify(track) {
    const target = keyOf(track);
    if (target.error) return { ok: false, reason: target.error, key: null };
    const blocked = ready();
    if (blocked) return { ok: false, reason: blocked, key: target.key };

    const entry = (await manifest()).entries[target.key];
    if (!entry || entry.complete === false) {
      return { ok: false, reason: "no copy of this track is kept", key: target.key };
    }
    const size = entrySize(entry);
    const stat = await call("offline_stat", { account: state.account.key, key: target.key });
    const held = stat.ok ? numberOrNull(stat.value && stat.value.bytes) : null;
    const present = !!(stat.ok && stat.value && stat.value.exists);
    if (!present) {
      await remove(target.key);
      return { ok: false, reason: "the copy this device recorded is gone", key: target.key, removed: true };
    }
    if (!size) {
      await remove(target.key);
      return { ok: false, reason: "the manifest holds no length for this copy", key: target.key, removed: true };
    }
    if (held !== size) {
      await remove(target.key);
      return {
        ok: false,
        key: target.key,
        removed: true,
        reason: "the copy is truncated: " + (held === null ? "an unknown number of" : held)
          + " of " + size + " bytes are on disk",
      };
    }
    return { ok: true, key: target.key, entry: entry, size: size };
  }

  async function touch(key) {
    if (ready()) return false;
    const result = await call("offline_touch", {
      account: state.account.key, key: key, at: Math.floor(state.now()),
    });
    return result.ok;
  }

  async function remove(key) {
    const name = safeComponent(key);
    if (!name || ready()) return false;
    const result = await call("offline_delete", { account: state.account.key, key: name });
    return !!(result.ok && (!result.value || result.value.ok !== false));
  }

  async function removeAll() {
    const entries = (await manifest()).entries;
    let removed = 0;
    for (const key of Object.keys(entries)) {
      if (await remove(key)) removed += 1;
    }
    return removed;
  }

  /* Oldest played first, and never a track that is playing. The rule is stated in that order on
     purpose: it is what a person can predict from the dialog, and it is what makes "why did it
     delete that one" answerable. `playing` is one key or a list of them — the copy the player is
     using right now, and the one that was just kept. If the budget can only be met by evicting
     those, the budget is exceeded until they stop, and `over` says by how much. */
  function protectedKeys(playing) {
    if (playing === null || playing === undefined) return [];
    const list = Array.isArray(playing) ? playing : [playing];
    return list.filter(function (key) { return key !== null && key !== undefined; })
      .map(function (key) { return String(key); });
  }

  async function evict(playing) {
    const ceiling = budget();
    const keep = protectedKeys(playing);
    const entries = (await manifest()).entries;
    const rows = Object.keys(entries)
      .map(function (key) { return Object.assign({ key: key }, entries[key]); })
      .filter(function (row) { return row.complete !== false && entrySize(row) > 0; });
    let total = rows.reduce(function (sum, row) { return sum + entrySize(row); }, 0);
    if (!ceiling || total <= ceiling) return { evicted: [], over: 0, total: total };

    rows.sort(function (left, right) {
      const played = (numberOrNull(left.lastPlayed) || 0) - (numberOrNull(right.lastPlayed) || 0);
      if (played) return played;
      const added = (numberOrNull(left.addedAt) || 0) - (numberOrNull(right.addedAt) || 0);
      if (added) return added;
      return String(left.key).localeCompare(String(right.key));
    });

    const evicted = [];
    for (const row of rows) {
      if (total <= ceiling) break;
      if (keep.indexOf(String(row.key)) >= 0) continue;
      if (await remove(row.key)) {
        evicted.push(row.key);
        total -= entrySize(row);
      }
    }
    return { evicted: evicted, over: Math.max(0, total - ceiling), total: total };
  }

  async function setBudget(bytes) {
    const value = Math.max(0, Math.floor(numberOrNull(bytes) || 0));
    rememberBudget(value);
    /* "Off" means no copies, not "keep them but stop adding" — the app's own dialog says so and
       a setting that leaves gigabytes behind while claiming to be off is a lie. */
    if (!value) return { budget: 0, evicted: await removeAll() };
    const result = await evict(null);
    return { budget: value, evicted: result.evicted.length, over: result.over };
  }

  /* ---- the download ----------------------------------------------------- */

  function contentRange(value) {
    const match = /^bytes\s+(\d+)-(\d+)\/(\d+|\*)$/i.exec(text(value).trim());
    if (!match) return null;
    const total = match[3] === "*" ? null : Number(match[3]);
    return { start: Number(match[1]), end: Number(match[2]), total: total };
  }

  function cancelled(at) {
    return { ok: false, cancelled: true, at: at, reason: "the download was cancelled; it resumes from byte " + at };
  }

  /* One read that a cancel can interrupt. Waiting on a socket that has stopped delivering is
     exactly the state a person cancels from, so the wait is raced against the signal rather than
     left to the transport to abort. Resolves `{aborted: true}` when the cancel won. */
  function readStep(reader, signal) {
    if (!signal || typeof signal.addEventListener !== "function") return reader.read();
    if (signal.aborted) return Promise.resolve({ aborted: true });
    return new Promise(function (resolve, reject) {
      let settled = false;
      const finish = function (fn, value) {
        if (settled) return;
        settled = true;
        try { signal.removeEventListener("abort", onAbort); } catch (error) { /* nothing to undo */ }
        fn(value);
      };
      const onAbort = function () { finish(resolve, { aborted: true }); };
      signal.addEventListener("abort", onAbort, { once: true });
      reader.read().then(function (step) { finish(resolve, step); },
        function (error) { finish(reject, error); });
    });
  }

  /* One download, from where the store says it stopped. Every request carries `Range` from that
     byte, and the reply has to be a 206 whose `Content-Range` starts there — a 200 means the
     server ignored the header and sent the file from zero, so the partial is thrown away rather
     than stitched onto the wrong offset. An ETag that changed under a resume is the same
     situation: the file is not the one the partial came from.

     Nothing is committed until the byte count matches what the server declared. A body that
     ends early is resumed; a body that keeps ending early is left as a `.part` the next attempt
     picks up. */
  async function download(track, options) {
    const input = options || {};
    const target = keyOf(track);
    if (target.error) return { ok: false, reason: target.error };
    const blocked = ready();
    if (blocked) return { ok: false, reason: blocked };
    if (!state.fetch) return { ok: false, reason: "this build has no way to reach a server" };
    if (!state.streamUrl) return { ok: false, reason: "no stream URL was configured" };
    const ceiling = budget();
    if (!ceiling) return { ok: false, reason: "offline copies are switched off in the settings" };

    /* Already kept: playing it again is a `touch`, not a download. */
    const kept = await verify(track);
    if (kept.ok) {
      await touch(target.key);
      return { ok: true, bytes: kept.size, kept: true, resumedFrom: 0 };
    }

    let url;
    try {
      url = text(state.streamUrl(track));
    } catch (error) {
      return { ok: false, reason: reasonOf(error) };
    }
    if (!url) return { ok: false, reason: "the stream URL for this track is empty" };

    let begun = await call("offline_begin", { account: state.account.key, key: target.key, suffix: target.suffix });
    if (!begun.ok) return { ok: false, reason: begun.reason };
    let offset = numberOrNull(begun.value && begun.value.partial) || 0;
    const resumedFrom = offset;

    /* What the earlier attempt recorded: the ETag of the file it started, and the length the
       server declared then. Both are what a resume has to agree with. */
    const previous = (await manifest()).entries[target.key];
    let etag = offset && previous ? text(previous.etag) : "";
    let total = previous ? numberOrNull(previous.expected) : null;
    let pinned = false;
    let attempts = 0;

    /* Throw the partial away and start again from zero: the server sent whole-file bytes, or a
       different file, or the offset it answered does not line up. */
    const restart = async () => {
      const dropped = await call("offline_delete", { account: state.account.key, key: target.key });
      const again = dropped.ok
        ? await call("offline_begin", { account: state.account.key, key: target.key, suffix: target.suffix })
        : dropped;
      if (!again.ok) return false;
      offset = numberOrNull(again.value && again.value.partial) || 0;
      etag = "";
      pinned = false;
      attempts += 1;
      return true;
    };

    while (true) {
      if (input.signal && input.signal.aborted) return cancelled(offset);
      const headers = {};
      if (offset) headers.Range = "bytes=" + offset + "-";
      let response;
      try {
        response = await state.fetch(url, { method: "GET", headers: headers, signal: input.signal });
      } catch (error) {
        if (input.signal && input.signal.aborted) return cancelled(offset);
        /* A request that never answered is worth another try, from the same byte: a network
           that dropped the connection before the headers is the ordinary way a download on a
           phone fails. The attempt counter is what keeps this from becoming a loop. */
        attempts += 1;
        if (attempts > MAX_ATTEMPTS) {
          return {
            ok: false,
            at: offset,
            reason: "the download stopped at byte " + offset + ": " + reasonOf(error),
          };
        }
        continue;
      }
      if (!response || typeof response.status !== "number") {
        return { ok: false, at: offset, reason: "the stream request produced no answer" };
      }
      if (input.signal && input.signal.aborted) return cancelled(offset);

      const range = contentRange(response.headers && response.headers.get
        ? response.headers.get("Content-Range") : null);
      if (offset && response.status === 200) {
        if (!(await restart())) return { ok: false, at: offset, reason: "the partial copy could not be reset" };
        if (attempts > MAX_ATTEMPTS) {
          return { ok: false, at: offset, reason: "the server keeps ignoring the Range request" };
        }
        continue;
      }
      if (offset && response.status === 206 && range && range.start !== offset) {
        if (!(await restart())) return { ok: false, at: offset, reason: "the partial copy could not be reset" };
        if (attempts > MAX_ATTEMPTS) {
          return { ok: false, at: offset, reason: "the server answered from byte " + range.start
            + " instead of " + offset };
        }
        continue;
      }
      if (response.status !== 200 && response.status !== 206) {
        return {
          ok: false,
          at: offset,
          reason: "the server answered HTTP " + response.status + " for this track",
        };
      }

      if (range && range.total !== null) total = range.total;
      const declared = numberOrNull(response.headers && response.headers.get
        ? response.headers.get("Content-Length") : null);
      if (total === null && declared !== null) total = offset + declared;

      const tag = text(response.headers && response.headers.get ? response.headers.get("ETag") : "").trim();
      if (offset && tag && etag && tag !== etag) {
        if (!(await restart())) return { ok: false, at: offset, reason: "the partial copy could not be reset" };
        continue;
      }
      if (tag) etag = tag;
      /* Record the file this attempt belongs to before its bytes are kept, so a resume after a
         crash can tell a changed file from the same one. */
      if (!pinned && (etag || total !== null)) {
        pinned = true;
        await call("offline_pin", {
          account: state.account.key,
          key: target.key,
          suffix: target.suffix,
          etag: etag || null,
          expected: total,
        });
      }

      const body = response.body;
      let interrupted = null;
      let progressed = false;
      try {
        if (body && typeof body.getReader === "function") {
          const reader = body.getReader();
          while (true) {
            const step = await readStep(reader, input.signal);
            if (step.aborted || (input.signal && input.signal.aborted)) {
              if (typeof reader.cancel === "function") {
                try { await reader.cancel(); } catch (error) { /* the socket is going away anyway */ }
              }
              return cancelled(offset);
            }
            if (step.done) break;
            const chunk = step.value instanceof Uint8Array ? step.value : new Uint8Array(step.value);
            if (!chunk.length) continue;
            const written = await call("offline_write", {
              account: state.account.key,
              key: target.key,
              suffix: target.suffix,
              offset: offset,
              data: encodeBase64(chunk),
            });
            if (!written.ok) return { ok: false, at: offset, reason: written.reason };
            offset += chunk.length;
            progressed = true;
            if (typeof input.onProgress === "function") {
              input.onProgress({ key: target.key, received: offset, total: total, resumedFrom: resumedFrom });
            }
            if (total !== null && offset >= total) break;
          }
        } else {
          const whole = new Uint8Array(await response.arrayBuffer());
          for (let at = 0; at < whole.length; at += CHUNK) {
            const slice = whole.subarray(at, Math.min(at + CHUNK, whole.length));
            const written = await call("offline_write", {
              account: state.account.key,
              key: target.key,
              suffix: target.suffix,
              offset: offset,
              data: encodeBase64(slice),
            });
            if (!written.ok) return { ok: false, at: offset, reason: written.reason };
            offset += slice.length;
            progressed = true;
            if (typeof input.onProgress === "function") {
              input.onProgress({ key: target.key, received: offset, total: total, resumedFrom: resumedFrom });
            }
          }
        }
      } catch (error) {
        interrupted = error;
      }
      if (input.signal && input.signal.aborted) return cancelled(offset);
      if (total !== null && offset >= total) break;
      if (!interrupted && !progressed) break;
      attempts += 1;
      if (attempts > MAX_ATTEMPTS) {
        return {
          ok: false,
          at: offset,
          truncated: total !== null,
          reason: "the download stopped at byte " + offset
            + (total !== null ? " of " + total : "") + " and did not resume",
        };
      }
    }

    if (total !== null && offset !== total) {
      return {
        ok: false,
        at: offset,
        truncated: true,
        reason: "the download stopped at byte " + offset + " of " + total
          + "; what is on disk is not the whole file",
      };
    }
    if (!offset) return { ok: false, reason: "the server sent no bytes for this track" };

    const entry = {
      key: target.key,
      suffix: target.suffix,
      size: offset,
      etag: etag || null,
      contentType: text(track && (track.contentType || track.mimeType)) || null,
      title: text(track && (track.title || track.name)).slice(0, 200) || null,
      sourceSize: numberOrNull(track && track.size),
      addedAt: Math.floor(state.now()),
      lastPlayed: Math.floor(state.now()),
      complete: true,
    };
    const committed = await call("offline_commit", {
      account: state.account.key, key: target.key, suffix: target.suffix, entry: entry,
    });
    if (!committed.ok) return { ok: false, at: offset, reason: committed.reason };

    /* A track bigger than the whole budget is not a track this device can keep: it would evict
       everything and then still be over. Delete it and say so plainly. */
    if (offset > ceiling) {
      await remove(target.key);
      return {
        ok: false,
        bytes: offset,
        reason: "this track is " + offset + " bytes, larger than the whole budget of " + ceiling
          + ", so it was not kept",
      };
    }
    const trimmed = await evict([target.key, input.playing]);
    return { ok: true, bytes: offset, resumedFrom: resumedFrom, evicted: trimmed.evicted, over: trimmed.over };
  }

  /* A download that can be stopped. `cancel()` does not delete the partial: the next attempt
     starts at the byte this one reached, which is the entire point of keeping it. */
  function start(track, options) {
    const input = Object.assign({}, options || {});
    const controller = typeof root.AbortController === "function" ? new root.AbortController() : null;
    if (controller) {
      const outer = input.signal;
      if (outer) {
        if (outer.aborted) controller.abort();
        else if (typeof outer.addEventListener === "function") {
          outer.addEventListener("abort", function () { controller.abort(); });
        }
      }
      input.signal = controller.signal;
    }
    const promise = download(track, input);
    return {
      promise: promise,
      cancel: function () {
        if (controller) controller.abort();
      },
    };
  }

  /* ---- playing from the copy ------------------------------------------- */

  /* The whole file, reassembled from the store, as a URL the audio element can take. Not one
     request leaves this device: the manifest and the file are the only things consulted, which
     is what makes this different from "the player might have a cached response somewhere". */
  async function local(track) {
    const checked = await verify(track);
    if (!checked.ok) return { ok: false, reason: checked.reason, removed: !!checked.removed };
    const size = checked.size;
    const chunks = [];
    let offset = 0;
    while (offset < size) {
      const part = await call("offline_read", {
        account: state.account.key,
        key: checked.key,
        offset: offset,
        length: Math.min(state.readChunk, size - offset),
      });
      if (!part.ok) return { ok: false, reason: part.reason };
      const bytes = decodeBase64(part.value && part.value.data);
      if (!bytes || !bytes.length) break;
      chunks.push(bytes);
      offset += bytes.length;
      if (part.value && part.value.eof) break;
    }
    const total = chunks.reduce(function (sum, chunk) { return sum + chunk.length; }, 0);
    if (total !== size) {
      await remove(checked.key);
      return { ok: false, reason: "the copy read back as " + total + " of " + size + " bytes" };
    }
    const all = new Uint8Array(total);
    let at = 0;
    for (const chunk of chunks) {
      all.set(chunk, at);
      at += chunk.length;
    }
    if (!state.createUrl) return { ok: false, reason: "this build cannot hand a file to the audio element" };
    const type = text((checked.entry && checked.entry.contentType) || "") || "audio/mpeg";
    const url = state.createUrl(all, type);
    await touch(checked.key);
    return { ok: true, key: checked.key, size: total, url: url, type: type };
  }

  function release(url) {
    try {
      if (url && root.URL && typeof root.URL.revokeObjectURL === "function") {
        root.URL.revokeObjectURL(url);
      }
    } catch (error) {
      /* A URL that is already gone is not a problem worth a message. */
    }
  }

  root.PlayerOffline = {
    configure: configure,
    download: download,
    start: start,
    local: local,
    verify: verify,
    stats: stats,
    budget: budget,
    setBudget: setBudget,
    evict: evict,
    remove: remove,
    removeAll: removeAll,
    freeSpace: freeSpace,
    release: release,
    keyOf: keyOf,
    accountName: accountName,
    safeComponent: safeComponent,
    safeSuffix: safeSuffix,
    encodeBase64: encodeBase64,
    decodeBase64: decodeBase64,
    BUDGET_KEY: BUDGET_KEY,
    DEFAULT_BUDGET: DEFAULT_BUDGET,
    CHUNK: CHUNK,
  };
})(typeof window === "undefined" ? globalThis : window);