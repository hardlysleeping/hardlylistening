/* Accounts: many servers, one active, and a secret the list never sees.

   PLAYER-001 kept one session in the page's memory and asked again after a restart, because
   the alternative it could reach was a plaintext file. This is the other side of that: an
   account is a *public record* — server address, username, a label, and the form of
   authentication that server actually accepted — and the secret that goes with it lives in a
   vault the page never writes itself. On the desktop that vault is the OS keychain, reached
   through two Rust commands; a machine with no keyring gets an in-memory vault that says so
   rather than one that pretends. The page can be pointed at either, and at a stub in a test,
   because a secret store is a dependency and dependencies are passed in.

   The rule the rest of the file serves: **a secret is not in the list, not in a log line and
   not in an address.** `list()` and `serialise()` return public records only, and a saved
   record is read back through an allow-list so a secret written into a config file by an older
   build (or by a person) is dropped by name and never carried into the model. `redact()` is
   the single door text goes through before it is logged, and `origin()` refuses an address
   that carries credentials in it at all.

   The form is recorded, never chosen. A server accepts a password, or a token and a salt, or a
   credential it issued once as a device invite; `add()` demands the form that was accepted and
   refuses an account that claims any other, because a stored credential in the wrong shape is
   a login that fails later in a place that explains nothing.

   Loaded as a classic script like `connect.js`: `PlayerAccounts` is the API, there is no build
   step, and nothing here touches the DOM or the network, so the whole model runs under node
   with the vault stubbed. */
(function (root) {
  "use strict";

  /* What a saved list may say its shape is. A file from a later build is refused rather than
     half-read: silently dropping fields is how a wrong account becomes a wrong server. */
  const SCHEMA = 1;

  /* The three forms a Subsonic / OpenSubsonic server can accept, and the parameters each one
     puts on the request. The device invite is its own form rather than a password with a note,
     because it is issued once by `POST /api/listening/devices` (`{username, password}`), it can
     be revoked by the server, and it is the only credential a person cannot type from memory. */
  const AUTH_FORMS = {
    password: {
      params: ["u", "p"],
      label: "password",
      why: "the server checks the password itself, sent as Subsonic's p= parameter",
    },
    token: {
      params: ["u", "t", "s"],
      label: "token + salt",
      why: "the server keeps the secret and checks md5(secret + salt), sent as t= and s=",
    },
    "device-invite": {
      params: ["u", "p"],
      label: "device invite",
      why: "the server issued this credential once and can revoke it",
    },
  };
  const FORM_IDS = Object.keys(AUTH_FORMS);

  /* A public account record, and the only fields a saved one may carry. `form` and `added` are
     facts about the handshake; a secret is not one of these fields and never will be. */
  const RECORD_FIELDS = ["id", "server", "username", "label", "form", "added"];
  const DOCUMENT_FIELDS = ["version", "activeId", "accounts"];

  function text(value) {
    return value == null ? "" : String(value);
  }

  /* ---- the secret keeps its shape ---------------------------------------- */

  /* An account's server is a scheme, a host and an optional path — the part before `/rest`.
     Credentials in the address are refused rather than stripped: `https://user:pass@host` is
     exactly the URL this card exists to keep out of the tree, the log and the config file, and
     dropping the userinfo silently would hide the fact that somebody pasted it. */
  function origin(raw) {
    const value = text(raw).trim();
    if (!value) return { error: "Enter the server address — for example https://music.example.com" };
    let parsed;
    try {
      parsed = new root.URL(value);
    } catch (error) {
      return { error: "That address is missing its scheme. Try https://music.example.com" };
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return { error: "A server address starts with http:// or https://" };
    }
    if (!parsed.hostname) return { error: "That address has no host name" };
    if (parsed.username || parsed.password) {
      return { error: "Drop the username and password from the address — they belong to the account, never to the URL" };
    }
    if (parsed.search || parsed.hash) {
      return { error: "Use the server's address on its own, without a query string or a #fragment" };
    }
    const path = parsed.pathname.replace(/\/+$/, "");
    if (/\/rest(\/ping\.view)?$/.test(path)) {
      return { error: "Drop the /rest part — the address is the server root, the player adds /rest itself" };
    }
    return { url: parsed.protocol + "//" + parsed.host + path };
  }

  /* The account's own parameters, for the form the server accepted. `u` comes from the record;
     `p` is the secret as typed, and `t`/`s` is Subsonic's md5(secret + salt) and the salt that
     was used to make it. Nothing here touches the record: the result is a throwaway object the
     caller puts on one request and drops. */
  function authParams(account, secret, salt) {
    const record = account || {};
    const form = text(record.form);
    const value = text(secret);
    if (!AUTH_FORMS[form]) return { error: "“" + form + "” is not a form a server can accept" };
    if (!value) return { error: "There is no secret for " + text(record.server) };
    const params = { u: text(record.username) };
    if (form === "token") {
      const nonce = text(salt);
      if (!nonce) return { error: "Token authentication needs a salt from the server" };
      params.s = nonce;
      params.t = md5Hex(value + nonce);
    } else {
      params.p = value;
    }
    return { params: params, form: form };
  }

  /* ---- the id an account is keyed by ------------------------------------- */

  /* Stable, not secret: the same server and username always produce the same key, so signing in
     again updates that account instead of leaving a second copy behind, and the keychain entry
     is the same one. Two FNV-1a passes over the pair give 64 bits, which is plenty for an
     account list this size; a real collision is refused by `add()` rather than papered over. */
  function fnv(value, seed) {
    let hash = seed >>> 0;
    for (let i = 0; i < value.length; i++) {
      hash ^= value.charCodeAt(i) & 0xff;
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash >>> 0;
  }

  function hex8(value) {
    return (value >>> 0).toString(16).padStart(8, "0");
  }

  function accountId(server, username) {
    return "acct_"
      + hex8(fnv("a\u0000" + server + "\u0000" + username, 0x811c9dc5))
      + hex8(fnv("b\u0000" + username + "\u0000" + server, 0x01000193));
  }

  /* ---- md5, because token authentication is md5 ---------------------------------- */

  /* Subsonic's token form is `t = md5(password + salt)`. The OS WebView offers SHA-1 and SHA-2
     through `crypto.subtle` and no md5 at all, so a player that records "this server wanted a
     token" has to be able to make one. This is RFC 1321 in one function, over the UTF-8 bytes
     of the string, which is what every server compares against. It is checked against Python's
     hashlib in the tests rather than against itself. */
  const MD5_S = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
  ];
  const MD5_K = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
  ];

  function utf8Bytes(value) {
    const bytes = [];
    for (let i = 0; i < value.length; i++) {
      let code = value.charCodeAt(i);
      if (code >= 0xd800 && code <= 0xdbff && i + 1 < value.length) {
        const next = value.charCodeAt(i + 1);
        if (next >= 0xdc00 && next <= 0xdfff) {
          code = 0x10000 + ((code - 0xd800) << 10) + (next - 0xdc00);
          i++;
        }
      }
      if (code < 0x80) {
        bytes.push(code);
      } else if (code < 0x800) {
        bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
      } else if (code < 0x10000) {
        bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
      } else {
        bytes.push(0xf0 | (code >> 18), 0x80 | ((code >> 12) & 0x3f),
          0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
      }
    }
    return bytes;
  }

  function md5Hex(value) {
    const bytes = utf8Bytes(text(value));
    const bitLength = bytes.length * 8;
    const padded = bytes.slice();
    padded.push(0x80);
    while (padded.length % 64 !== 56) padded.push(0);
    const low = bitLength >>> 0;
    const high = Math.floor(bitLength / 0x100000000) >>> 0;
    for (let i = 0; i < 4; i++) padded.push((low >>> (8 * i)) & 0xff);
    for (let i = 0; i < 4; i++) padded.push((high >>> (8 * i)) & 0xff);

    let a0 = 0x67452301;
    let b0 = 0xefcdab89;
    let c0 = 0x98badcfe;
    let d0 = 0x10325476;
    const words = new Array(16);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i++) {
        const at = offset + i * 4;
        words[i] = padded[at] | (padded[at + 1] << 8) | (padded[at + 2] << 16) | (padded[at + 3] << 24);
      }
      let a = a0;
      let b = b0;
      let c = c0;
      let d = d0;
      for (let i = 0; i < 64; i++) {
        let f;
        let g;
        if (i < 16) {
          f = (b & c) | (~b & d);
          g = i;
        } else if (i < 32) {
          f = (d & b) | (~d & c);
          g = (5 * i + 1) % 16;
        } else if (i < 48) {
          f = b ^ c ^ d;
          g = (3 * i + 5) % 16;
        } else {
          f = c ^ (b | ~d);
          g = (7 * i) % 16;
        }
        const sum = (a + f + MD5_K[i] + words[g]) | 0;
        const rotated = (sum << MD5_S[i]) | (sum >>> (32 - MD5_S[i]));
        a = d;
        d = c;
        c = b;
        b = (b + rotated) | 0;
      }
      a0 = (a0 + a) | 0;
      b0 = (b0 + b) | 0;
      c0 = (c0 + c) | 0;
      d0 = (d0 + d) | 0;
    }
    return [a0, b0, c0, d0].map(word => {
      let out = "";
      for (let i = 0; i < 4; i++) out += ((word >>> (8 * i)) & 0xff).toString(16).padStart(2, "0");
      return out;
    }).join("");
  }

  /* ---- text on its way out ----------------------------------------------- */

  /* The parameter names Subsonic puts a secret in: `p` (or `enc:` hex, same parameter), the
     token `t` and the salt `s`, plus the spellings an API key or a password takes elsewhere. */
  const SECRET_PARAM = /((?:^|[?&\s])(?:p|t|s|enc|pw|password|passwd|token|secret|api_?key)=)[^&#\s"']*/gi;

  /* Scrub a string for a log line or a message: the values in `secrets` (longest first, so a
     short secret that is a prefix of a longer one cannot leave a tail behind) and then the
     protocol's own parameter names. Exported on its own for callers that hold a secret and no
     store; the store's version adds the secrets this page has handled. */
  function redact(value, secrets) {
    let out = text(value);
    const held = (secrets || []).map(text).filter(Boolean)
      .sort((left, right) => right.length - left.length);
    for (const secret of held) out = out.split(secret).join("***");
    return out.replace(SECRET_PARAM, "$1***");
  }

  /* ---- the vaults -------------------------------------------------------- */

  /* The honest fallback: the page's own memory, for this run only. `persists` is false and
     `mode()` says "memory" so a caller can tell a person that closing the window forgets the
     password, instead of letting them find out at the next launch. */
  function memoryVault() {
    const secrets = new Map();
    return {
      mode: function () { return "memory"; },
      persists: false,
      put: async function (id, secret) {
        secrets.set(text(id), text(secret));
        return { mode: "memory" };
      },
      get: async function (id) {
        return secrets.has(text(id)) ? secrets.get(text(id)) : null;
      },
      del: async function (id) {
        secrets.delete(text(id));
      },
      has: async function (id) {
        return secrets.has(text(id));
      },
    };
  }

  /* The OS keychain, through the two commands `src-tauri/src/accounts.rs` exposes. Nothing is
     cached here: every read crosses the bridge, so a secret cannot outlive the request that
     needed it in this page. `mode()` reports what the Rust side says it managed to open —
     "keychain" or "memory" — and a probe failure degrades to the in-memory vault rather than
     throwing, because a player that will not start without a keyring is a player that does
     not run on a headless Linux box. */
  function keychainVault(invoke) {
    if (typeof invoke !== "function") return memoryVault();
    const store = {
      /* Nothing is claimed until the Rust side answers: "pending" is not a keychain and is not
         pretending to be one. `probe()` asks once at startup; the first `put()` also settles
         it, because the Rust store reports the backend it actually used. */
      _mode: "pending",
      persists: false,
      mode: function () { return store._mode; },
      put: async function (id, secret) {
        const mode = await invoke("accounts_put", { account: text(id), secret: text(secret) });
        store._mode = text(mode) || "keychain";
        store.persists = store._mode === "keychain";
        return { mode: store._mode };
      },
      get: async function (id) {
        const secret = await invoke("accounts_secret", { account: text(id) });
        return secret == null ? null : text(secret);
      },
      del: async function (id) {
        await invoke("accounts_forget", { account: text(id) });
      },
      has: async function (id) {
        return (await store.get(id)) !== null;
      },
      probe: async function () {
        try {
          store._mode = text(await invoke("accounts_mode"));
        } catch (error) {
          return memoryVault();
        }
        store.persists = store._mode === "keychain";
        return store;
      },
    };
    return store;
  }

  /* ---- the store --------------------------------------------------------- */

  /* One page, one store. The vault is the only thing that knows a secret; the store knows which
     account is active and what the list may say about it. */
  function open(options) {
    const config = options || {};
    const vault = config.vault || memoryVault();
    const accounts = [];
    let activeId = null;
    /* Secrets this page has handled, for `redact()` only — a log line cannot be scrubbed of a
       value the page never held. Cleared by `lock()`. */
    const revealed = [];

    function find(id) {
      const wanted = text(id);
      return accounts.find(account => account.id === wanted) || null;
    }

    /* What the UI renders, and what a config file may hold. A copy: a caller that mutates the
       answer cannot reach the model. */
    function publicView(record) {
      return {
        id: record.id,
        server: record.server,
        username: record.username,
        label: record.label,
        form: record.form,
        added: record.added,
        active: record.id === activeId,
      };
    }

    function list() {
      return accounts.map(publicView);
    }

    function active() {
      const record = find(activeId);
      return record ? publicView(record) : null;
    }

    function revealedSecrets() {
      return revealed.slice().sort((left, right) => right.length - left.length);
    }

    /* The single door text goes through before it is logged or shown beside an account: the
       values this page has handled, and then the parameter names the protocol puts a secret in,
       so a URL from somewhere else is scrubbed too. */
    function redactAll(value) {
      return redact(value, revealedSecrets());
    }

    function logLine(message) {
      return redactAll(message);
    }

    /* The text a list renders: one line per account, the active one marked. Deliberately built
       from the public record only, so there is no expression here that could reach a secret. */
    function render() {
      if (!accounts.length) return "";
      return accounts.map(record => {
        const view = publicView(record);
        return (view.active ? "* " : "  ") + view.label + " — " + view.server + " — "
          + view.username + " (" + AUTH_FORMS[view.form].label + ")";
      }).join("\n");
    }

    async function add(input) {
      const given = input || {};
      const target = origin(given.server);
      if (target.error) return { ok: false, error: target.error };
      const username = text(given.username).trim();
      if (!username) return { ok: false, error: "Enter the username the server gave you" };
      const form = text(given.accepted).trim();
      if (!AUTH_FORMS[form]) {
        return {
          ok: false,
          error: "An account records the form its server accepted ("
            + FORM_IDS.join(", ") + "); “" + form + "” is not one",
        };
      }
      const secret = text(given.secret);
      if (!secret) return { ok: false, error: "There is no secret to keep for " + target.url };

      const id = accountId(target.url, username);
      const again = find(id);
      if (again && (again.server !== target.url || again.username !== username)) {
        return { ok: false, error: "Two different accounts keyed to the same id — refusing to overwrite either" };
      }
      const label = (text(given.label).trim().slice(0, 80)
        || target.url.replace(/^https?:\/\//, ""));
      const record = {
        id: id,
        server: target.url,
        username: username,
        label: label,
        form: form,
        added: again ? again.added : new Date().toISOString(),
      };

      /* The account is added only if the secret was stored. A list entry whose secret went
         nowhere is a login that fails at the next launch with nothing to explain it. */
      let stored;
      try {
        stored = await vault.put(id, secret);
      } catch (error) {
        return { ok: false, error: "Could not keep the secret: " + text(error && error.message || error) };
      }
      if (stored && stored.error) return { ok: false, error: "Could not keep the secret: " + stored.error };

      revealed.push(secret);
      if (again) accounts[accounts.indexOf(again)] = record;
      else accounts.push(record);
      if (!activeId) activeId = id;
      return { ok: true, account: publicView(record), mode: vault.mode() };
    }

    /* Switching the active server reads the list and writes one field. It does not touch the
       vault, which is what "the others are not lost" means in practice: their secrets are still
       where they were, keyed by an id this call never names. */
    function use(id) {
      const record = find(id);
      if (!record) return { ok: false, error: "There is no account " + text(id) };
      activeId = record.id;
      return { ok: true, active: publicView(record) };
    }

    function rename(id, label) {
      const record = find(id);
      if (!record) return { ok: false, error: "There is no account " + text(id) };
      const wanted = text(label).trim().slice(0, 80);
      if (!wanted) return { ok: false, error: "A label cannot be empty" };
      record.label = wanted;
      return { ok: true, account: publicView(record) };
    }

    async function secret(id) {
      const record = find(id);
      if (!record) return null;
      let value = null;
      try {
        value = await vault.get(record.id);
      } catch (error) {
        return null;
      }
      if (value == null) return null;
      revealed.push(text(value));
      return text(value);
    }

    async function hasSecret(id) {
      const record = find(id);
      if (!record) return false;
      try {
        return await vault.has(record.id) === true;
      } catch (error) {
        return false;
      }
    }

    /* Removing an account removes its secret first. If the vault refuses, the entry stays, so
       the list never claims to have forgotten something a keychain still holds. */
    async function remove(id) {
      const record = find(id);
      if (!record) return { ok: false, error: "There is no account " + text(id) };
      try {
        await vault.del(record.id);
      } catch (error) {
        return { ok: false, error: "Could not remove the secret: " + text(error && error.message || error) };
      }
      accounts.splice(accounts.indexOf(record), 1);
      if (activeId === record.id) activeId = accounts.length ? accounts[0].id : null;
      return { ok: true, activeId: activeId };
    }

    /* Drop the copy of the secrets this page kept for redaction. The vault is untouched. */
    function lock() {
      revealed.length = 0;
      return { ok: true, activeId: activeId };
    }

    /* PLAYER-001's shape: `{server: {url, ...}, credentials: {username, password}}`, held in
       the page's memory and gone at the next launch. It becomes an ordinary password account,
       which is what the server accepted, so nothing about it is special afterwards. */
    async function migrate(session) {
      const entered = session || {};
      const server = entered.server && typeof entered.server === "object"
        ? entered.server.url : entered.server;
      const credentials = entered.credentials || {};
      if (!server || !text(credentials.password)) {
        return { ok: false, error: "There is no in-memory session to remember" };
      }
      const result = await add({
        server: server,
        username: credentials.username,
        accepted: "password",
        secret: credentials.password,
        label: entered.label || "",
      });
      if (result.ok) result.migratedFrom = "PLAYER-001";
      return result;
    }

    function serialise() {
      return {
        version: SCHEMA,
        activeId: activeId,
        accounts: accounts.map(record => ({
          id: record.id,
          server: record.server,
          username: record.username,
          label: record.label,
          form: record.form,
          added: record.added,
        })),
      };
    }

    /* Read a saved list through the allow-list. A field the model does not have is dropped and
       *named* — never echoed — so a config file that picked up a `password` is refused rather
       than carried. `activeId` that names nothing falls back to the first account. */
    function restore(saved) {
      const ignored = new Set();
      let blob = saved;
      if (typeof blob === "string") {
        try {
          blob = JSON.parse(blob);
        } catch (error) {
          return { ok: false, error: "That saved account list is not JSON" };
        }
      }
      if (!blob || typeof blob !== "object") {
        return { ok: false, error: "That saved account list is not an object" };
      }
      for (const key of Object.keys(blob)) {
        if (!DOCUMENT_FIELDS.includes(key)) ignored.add(key);
      }
      if (Number(blob.version) > SCHEMA) {
        return {
          ok: false,
          error: "That account list was written by a newer player (version "
            + text(blob.version) + "); this one reads version " + SCHEMA,
        };
      }
      const rows = Array.isArray(blob.accounts) ? blob.accounts : [];
      const accepted = [];
      const refused = [];
      for (const row of rows) {
        if (!row || typeof row !== "object") {
          refused.push("not a record");
          continue;
        }
        for (const key of Object.keys(row)) {
          if (!RECORD_FIELDS.includes(key)) ignored.add(key);
        }
        const target = origin(row.server);
        if (target.error) {
          refused.push(target.error);
          continue;
        }
        const username = text(row.username).trim();
        if (!username) {
          refused.push("a record without a username");
          continue;
        }
        if (!AUTH_FORMS[text(row.form)]) {
          refused.push("a record whose form is “" + text(row.form) + "”");
          continue;
        }
        accepted.push({
          id: accountId(target.url, username),
          server: target.url,
          username: username,
          label: text(row.label).trim().slice(0, 80) || target.url.replace(/^https?:\/\//, ""),
          form: text(row.form),
          added: text(row.added),
        });
      }
      accounts.length = 0;
      accounts.push(...accepted);
      const wanted = text(blob.activeId);
      activeId = accounts.some(record => record.id === wanted)
        ? wanted : (accounts.length ? accounts[0].id : null);
      return {
        ok: true,
        count: accounts.length,
        ignored: Array.from(ignored).sort(),
        refused: refused,
      };
    }

    function status() {
      return {
        mode: vault.mode(),
        persists: vault.persists !== false,
        accounts: accounts.length,
        activeId: activeId,
      };
    }

    return {
      list: list,
      active: active,
      add: add,
      use: use,
      rename: rename,
      remove: remove,
      secret: secret,
      hasSecret: hasSecret,
      migrate: migrate,
      lock: lock,
      restore: restore,
      serialise: serialise,
      status: status,
      render: render,
      redact: redactAll,
      log: logLine,
      vault: vault,
    };
  }

  root.PlayerAccounts = {
    open: open,
    memoryVault: memoryVault,
    keychainVault: keychainVault,
    origin: origin,
    accountId: accountId,
    authParams: authParams,
    md5Hex: md5Hex,
    redact: redact,
    AUTH_FORMS: AUTH_FORMS,
    FORM_IDS: FORM_IDS,
    SCHEMA: SCHEMA,
  };
})(typeof window === "undefined" ? globalThis : window);