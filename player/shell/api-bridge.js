/* The bridge between the app's own front end and a Subsonic server.

   The player serves the app's `web/` exactly as the Python host serves it, so the library and
   the queue drawer call `/api/library`, `/api/playlists`, `/api/track/…/audio` and so on —
   routes that only exist inside the server's Python. A player has no Python. This file is the
   answer: it wraps `window.fetch` once, and the handful of `/api/*` routes the library and the
   queue actually call are answered from `/rest/*`, translated into the shapes `js/library.js`
   and `js/playback-queue.js` already read.

   Three rules, because the alternative is a screen that looks like an empty library:

   1. **An explicit route table.** Only the routes listed in `ROUTES` are answered. Everything
      else under `/api/` is refused with a 404 and a sentence naming why, never a catch-all that
      returns `{}` or `[]` — an empty list is indistinguishable from an empty library, and the
      person looking at it would believe it.
   2. **A Subsonic error is an error.** An envelope with `status: "failed"` (or an `error`
      object) leaves here as an HTTP failure carrying the server's own message and code; it is
      never flattened into an empty result.
   3. **The password never lands in a URL the page renders or this file logs.** Ordinary calls
      authenticate with `u`/`p` the way the connect screen already does — they are fetches, not
      URLs anyone sees. Streams and downloads are opened by the media element itself, so they
      carry Subsonic token auth (`u`, `t = md5(password + salt)`, `s`), which is the protocol's
      own mechanism; cover art is fetched here and handed over as a `blob:` URL, so a rendered
      `<img src>` carries no credential at all. `log()` records endpoints and statuses, never a
      query string.

   Loaded as a classic script, no build step: `PlayerApi` is the API and nothing runs until
   `install()` is called, so the same file can be exercised under node with `fetch` stubbed. */
(function (root) {
  "use strict";

  const PROTOCOL_VERSION = "1.16.1";
  const CLIENT_NAME = "hardlylistening-player";
  //: One composed library is reused for this long. A Subsonic server has no change feed, so the
  //: only honest freshness is "read again after a while", and the app polls every three seconds.
  const SNAPSHOT_TTL_MS = 30000;
  //: `getAlbumList2`'s page size, and how many pages are read before the read is called broken.
  const ALBUM_PAGE = 500;
  const ALBUM_PAGES = 200;
  //: Requests in flight at once while walking albums. A personal library is a few dozen.
  const POOL = 4;
  //: Cover art is asked for at this edge length; the app draws 46 px rows and 200 px tiles.
  const COVER_SIZE = 300;
  const COVER_LIMIT = 400;
  //: App-facing ids are integers (the app writes them into `onclick` and `data-id`). A server
  //: id that is already a number passes through; anything else gets one of these, above any
  //: plausible row id, and is remembered for the life of the session.
  const SYNTHETIC_SONG = 2000000000;
  const SYNTHETIC_PLAYLIST = 1000000000;
  //: Marks a handler's answer as the server's own Response, handed over byte for byte.
  const PASS_THROUGH = Symbol("pass-through");

  /* The routes the app's library and queue surfaces call, and nothing else. `pattern` is the
     whole statement: a path not in this list is refused. */
  const ROUTES = [
    route("GET", "/api/library", library),
    route("GET", "/api/playlists", playlistList),
    route("POST", "/api/playlists", playlistCreate),
    route("GET", "/api/playlists/:id", playlistGet),
    route("POST", "/api/playlists/:id", playlistSave),
    route("GET", "/api/listening/saves", savesGet),
    route("POST", "/api/listening/saves", savesSet),
    route("GET", "/api/listening/folders", foldersGet),
    route("GET", "/api/listening/now", nowGet),
    route("POST", "/api/listening/now", nowSet),
    route("GET", "/api/track/:id/audio", audioGet),
    route("GET", "/api/track/:id/download", downloadGet),
    route("POST", "/api/activity/listen", listenPost),
    route("GET", "/api/bookmarks", bookmarksGet),
    route("GET", "/api/search", searchGet),
    route("POST", "/api/search", searchPost),
  ];

  /* Host routes the app still asks for and the player deliberately does not answer. Each one is
     refused with the reason it cannot exist here, so a control that hits it says something true
     instead of quietly doing nothing. This is the list a reader should check before believing a
     missing feature was an oversight. */
  const REFUSALS = [
    [/^\/api\/state$/, "the queue, the log and the download counters are the host app's own "
      + "snapshot; this player has no download queue"],
    [/^\/api\/health$/, "the host's health probe watches the host's own build for an update; "
      + "this player is a client and has nothing to reload"],
    [/^\/api\/library\/download$/, "a ZIP of the library is assembled by the host; this player "
      + "streams from your server and saves nothing"],
    [/^\/api\/playlist\/[^/]+\/(download|requeue|verify|apple-backup)$/, "a ZIP, a re-download "
      + "or a source check of a playlist is the host's own work on the host's own files"],
    [/^\/api\/add$/, "the player never acquires music: it asks a server you already have"],
    [/^\/api\/import(\/.*)?$/, "importing files is a host feature; the player has nothing to "
      + "import them into"],
    [/^\/api\/search$/, "the player never acquires music: every song it plays comes from the "
      + "server you already have, so an acquisition search has nothing to queue"],
    [/^\/api\/library\/(grab|move|verify|export|tempo|titles)$/, "that changes the host's own "
      + "library; this player only reads from your server"],
    [/^\/api\/track\/[^/]+\/(source|skip|retry|move|delete|unpick|restore|candidates)$/,
      "that edits the host's download queue; this player only reads from your server"],
    [/^\/api\/art\/[^/]+$/, "a cover here is the music server's own picture, carried as bytes; "
      + "this player has no host artwork store to address"],
    [/^\/api\/track\/[^/]+\/artwork$/, "a cover is the music server's own picture, carried as "
      + "bytes; the host extracts and caches its own"],
    [/^\/api\/playback\/events$/, "playback telemetry goes to the host's own log; this player "
      + "reports a play to the music server through its own scrobble route"],
    [/^\/api\/release\/[^/]+\/remove$/, "deleting a release is a host feature"],
    [/^\/api\/playlist\/[^/]+\/remove$/, "deleting a playlist is a host feature"],
    [/^\/api\/artist(\/.*)?$/, "artist pictures and artist profiles are the host's own store"],
    [/^\/api\/boost$/, "boosting a song into the feed is the host's activity feed"],
    [/^\/api\/(social|activity|feed)(\/.*)?$/, "the activity feed arrives with its own card; "
      + "this one does not answer for it"],
    [/^\/api\/(settings|preferences|profile|members|member|account|admin|auth|signup-invite)\b.*$/,
      "account and server settings belong to the host's own backend"],
    [/^\/api\/(queue|pause|cooldown|decisions|deletions|deleted|trash)\b.*$/,
      "the download queue and its history are the host's"],
    [/^\/api\/subscriptions(\/.*)?$/, "podcast subscriptions are the host's own feed store; "
      + "this player reads one music server and has none to subscribe to"],
    [/^\/api\/listening\/(devices|ui|theme|preferences|pin-order|export|eq|playback|cues)\b.*$/,
      "that is the host's own member state — the equaliser, the playback session and a track's "
      + "cue points live in the host's database, and Subsonic carries none of them"],
    [/^\/api\/stats(\/.*)?$/,
      "listening statistics are counted by the host's own database; the server this player talks "
      + "to keeps no such history to count"],
  ];

  /* ------------------------------------------------------------------ state */

  let bridge = null;

  function route(method, label, handler) {
    const parts = label.split("/").map(part => part.startsWith(":")
      ? "([^/]+)" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return { method: method, label: label, pattern: new RegExp("^" + parts.join("/") + "$"),
             handler: handler };
  }

  function empty() {
    return {
      server: "", username: "", password: "",
      send: null, onProblem: null, onAnswer: null, objectUrl: null,
      songs: new Map(), songIds: new Map(), songSeq: 0,
      playlists: new Map(), playlistIds: new Map(), playlistSeq: 0,
      covers: new Map(), coverQueue: [], coverJobs: 0,
      snapshot: null, pending: null, trace: [],
    };
  }

  /* ------------------------------------------------------------------ values */

  function text(value) {
    return value == null ? "" : String(value);
  }

  function integer(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function rows(container, key) {
    const value = container && container[key];
    if (Array.isArray(value)) return value;
    // A server that emits one row as an object rather than a one-item list is answering the
    // same question; XML-to-JSON converters do this all the time.
    if (value && typeof value === "object") return [value];
    return [];
  }

  function epoch(value) {
    const parsed = Date.parse(value == null ? "" : String(value));
    return Number.isFinite(parsed) ? Math.round(parsed / 1000) : 0;
  }

  function reasonOf(error) {
    const said = text(error && error.message).trim();
    return said || "the request failed before an answer arrived";
  }

  /* ------------------------------------------------------------------ errors */

  function BridgeError(status, message, detail) {
    this.status = status;
    this.message = message;
    this.detail = detail || null;
  }
  BridgeError.prototype = Object.create(Error.prototype);
  BridgeError.prototype.constructor = BridgeError;

  function failure(status, message) {
    return new BridgeError(status, message);
  }

  /* The protocol's codes, turned into HTTP so the app's own `response.ok` checks work. The
     server's sentence is kept: code 30 alone does not tell a person which control will not
     work. */
  const ERROR_STATUS = { 0: 502, 10: 400, 20: 502, 30: 501, 40: 401, 50: 403, 60: 502, 70: 404 };

  function refusedBy(endpoint, error) {
    const code = Number(error && error.code);
    const status = ERROR_STATUS[Number.isFinite(code) ? code : 0] || 502;
    const said = text(error && error.message).trim();
    const label = "Subsonic error " + (Number.isFinite(code) ? code : "?") + " from " + endpoint;
    const message = code === 30
      ? (said || "This server does not implement " + endpoint) + " — " + label
      + " (this server does not implement " + endpoint + ")"
      : (said || "The server refused the request") + " — " + label;
    return new BridgeError(status, message, code);
  }

  /* ------------------------------------------------------------------ md5 */

  /* Subsonic's token form is `t = md5(password + salt)`, over the UTF-8 bytes. Thirty lines of
     MD5 cheaper than a dependency, and the only thing this file needs a digest for. */
  const MD5_SHIFT = [7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
                     5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
                     4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
                     6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21];
  const MD5_TABLE = (function () {
    const table = [];
    for (let i = 0; i < 64; i++) table[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296);
    return table;
  })();

  function utf8(value) {
    const said = text(value);
    if (typeof TextEncoder === "function") return new TextEncoder().encode(said);
    const out = [];
    for (let i = 0; i < said.length; i++) {
      let code = said.charCodeAt(i);
      if (code < 0x80) out.push(code);
      else if (code < 0x800) out.push(0xc0 | (code >> 6), 0x80 | (code & 63));
      else if (code < 0xd800 || code >= 0xe000) {
        out.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 63), 0x80 | (code & 63));
      } else {
        code = 0x10000 + (((code & 0x3ff) << 10) | (said.charCodeAt(++i) & 0x3ff));
        out.push(0xf0 | (code >> 18), 0x80 | ((code >> 12) & 63),
                 0x80 | ((code >> 6) & 63), 0x80 | (code & 63));
      }
    }
    return out;
  }

  function md5(value) {
    const bytes = utf8(value);
    const length = bytes.length;
    const words = ((length + 8) >> 6) * 16 + 16;
    const buffer = new Uint32Array(words);
    for (let i = 0; i < length; i++) buffer[i >> 2] |= bytes[i] << ((i % 4) * 8);
    buffer[length >> 2] |= 0x80 << ((length % 4) * 8);
    buffer[words - 2] = (length * 8) >>> 0;
    buffer[words - 1] = Math.floor(length / 536870912);

    let a = 0x67452301, b = 0xefcdab89, c = 0x98badcfe, d = 0x10325476;
    const rotate = (value2, count) => (value2 << count) | (value2 >>> (32 - count));
    for (let block = 0; block < words; block += 16) {
      const aa = a, bb = b, cc = c, dd = d;
      for (let i = 0; i < 64; i++) {
        let f, at;
        if (i < 16) { f = (b & c) | (~b & d); at = i; }
        else if (i < 32) { f = (d & b) | (~d & c); at = (5 * i + 1) % 16; }
        else if (i < 48) { f = b ^ c ^ d; at = (3 * i + 5) % 16; }
        else { f = c ^ (b | ~d); at = (7 * i) % 16; }
        const sum = (a + f + MD5_TABLE[i] + buffer[block + at]) | 0;
        a = d; d = c; c = b;
        b = (b + rotate(sum, MD5_SHIFT[i])) | 0;
      }
      a = (a + aa) | 0; b = (b + bb) | 0; c = (c + cc) | 0; d = (d + dd) | 0;
    }
    const out = new Uint8Array(16);
    [a, b, c, d].forEach((word, index) => {
      for (let i = 0; i < 4; i++) out[index * 4 + i] = (word >>> (i * 8)) & 0xff;
    });
    let hex = "";
    for (let i = 0; i < out.length; i++) hex += (out[i] < 16 ? "0" : "") + out[i].toString(16);
    return hex;
  }

  /* ------------------------------------------------------------------ urls */

  function salt() {
    const bytes = new Uint8Array(8);
    if (root.crypto && typeof root.crypto.getRandomValues === "function") {
      root.crypto.getRandomValues(bytes);
    } else {
      for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    }
    let hex = "";
    for (let i = 0; i < bytes.length; i++) hex += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    return hex;
  }

  /* The query for one call. `auth` is `plain` for fetches (the connect screen's own `u`/`p`) and
     `token` for a URL a media element or a rendered link will open, where the password has no
     business being. */
  function query(state, auth, params) {
    const search = new URLSearchParams();
    search.append("u", state.username);
    if (auth === "token") {
      const value = salt();
      search.append("t", md5(state.password + value));
      search.append("s", value);
    } else {
      search.append("p", state.password);
    }
    search.append("v", PROTOCOL_VERSION);
    search.append("c", CLIENT_NAME);
    search.append("f", "json");
    for (const key of Object.keys(params || {})) {
      const value = params[key];
      if (value === undefined || value === null) continue;
      if (Array.isArray(value)) {
        for (const item of value) search.append(key, text(item));
      } else {
        search.append(key, text(value));
      }
    }
    return search.toString();
  }

  function restUrl(state, auth, endpoint, params) {
    return state.server + "/rest/" + endpoint + ".view?" + query(state, auth, params);
  }

  /* ------------------------------------------------------------------ wire */

  async function request(endpoint, params) {
    const state = bridge;
    const url = restUrl(state, "plain", endpoint, params);
    let response;
    try {
      response = await state.send(url, { method: "GET", headers: { Accept: "application/json" },
                                         redirect: "follow" });
    } catch (error) {
      throw failure(502, "Could not reach " + state.server + " — " + reasonOf(error));
    }
    if (response.status === 401 || response.status === 403) {
      throw failure(401, "The server refused those credentials (" + endpoint + ") — the device "
        + "may have been revoked, or this server may not accept password authentication");
    }
    if (!response.ok) {
      throw failure(502, state.server + " answered HTTP " + response.status + " for " + endpoint);
    }
    let body;
    try {
      body = await response.json();
    } catch (error) {
      throw failure(502, state.server + " answered " + endpoint + ", but not with JSON — that "
        + "address is probably a web page rather than a Subsonic server");
    }
    const envelope = body && typeof body === "object" ? body["subsonic-response"] : null;
    if (!envelope || typeof envelope !== "object") {
      throw failure(502, state.server + " answered " + endpoint + ", but not as a Subsonic "
        + "server (there is no subsonic-response in the reply)");
    }
    if (envelope.error && typeof envelope.error === "object") throw refusedBy(endpoint, envelope.error);
    if (text(envelope.status) !== "ok") {
      throw failure(502, "The server answered " + endpoint + " with status “"
        + text(envelope.status) + "” instead of ok");
    }
    return envelope;
  }

  /* The same concurrency for every walk over albums, so a big library is a queue of four
     requests rather than forty at once. */
  async function pool(items, work) {
    const results = new Array(items.length);
    let next = 0;
    const lanes = Math.min(POOL, items.length);
    const run = async () => {
      while (next < items.length) {
        const index = next++;
        results[index] = await work(items[index], index);
      }
    };
    await Promise.all(Array.from({ length: lanes }, run));
    return results;
  }

  /* ------------------------------------------------------------------ ids */

  function songId(raw) {
    const state = bridge;
    const value = text(raw);
    if (Object.prototype.hasOwnProperty.call(state.songIds, value)) return state.songIds[value];
    let mapped;
    if (/^\d{1,15}$/.test(value)) {
      mapped = Number(value);
    } else {
      mapped = SYNTHETIC_SONG + (++state.songSeq);
      state.songs.set(mapped, value);
    }
    state.songIds[value] = mapped;
    if (!state.songs.has(mapped)) state.songs.set(mapped, value);
    return mapped;
  }

  function serverSongId(appId) {
    const state = bridge;
    const value = Number(appId);
    if (!Number.isFinite(value)) return null;
    if (state.songs.has(value)) return state.songs.get(value);
    return /^\d{1,15}$/.test(text(appId)) ? text(appId) : null;
  }

  function playlistId(raw) {
    const state = bridge;
    const value = text(raw);
    if (Object.prototype.hasOwnProperty.call(state.playlistIds, value)) return state.playlistIds[value];
    let mapped;
    if (/^\d{1,15}$/.test(value)) {
      mapped = Number(value);
      state.playlists.set(mapped, value);
    } else {
      mapped = SYNTHETIC_PLAYLIST + (++state.playlistSeq);
      state.playlists.set(mapped, value);
    }
    state.playlistIds[value] = mapped;
    return mapped;
  }

  function serverPlaylistId(appId) {
    const state = bridge;
    const value = Number(appId);
    if (!Number.isFinite(value)) return null;
    if (state.playlists.has(value)) return state.playlists.get(value);
    return /^\d{1,15}$/.test(text(appId)) ? text(appId) : null;
  }

  /* ------------------------------------------------------------------ covers */

  /* A cover is fetched here, with credentials, and handed to the page as a `blob:` URL: an
     `<img src>` cannot send a header, and a Subsonic cover URL carries `u`/`p`. The set is
     warmed in the background so a snapshot is never held up by artwork, and the app repaints
     with the covers on its own next poll. */
  function coverUrl(coverId) {
    const state = bridge;
    const key = text(coverId);
    if (!key) return "";
    return state.covers.get(key) || "";
  }

  function objectUrl(bytes, type) {
    const state = bridge;
    if (state.objectUrl) return state.objectUrl(bytes, type);
    if (typeof root.Blob === "function" && typeof root.URL === "function"
        && typeof root.URL.createObjectURL === "function") {
      return root.URL.createObjectURL(new root.Blob([bytes], { type: type || "image/jpeg" }));
    }
    return "";
  }

  function warmCovers(ids) {
    const state = bridge;
    for (const id of ids) {
      const key = text(id);
      if (!key || state.covers.has(key) || state.coverQueue.includes(key)) continue;
      state.coverQueue.push(key);
    }
    while (state.coverJobs < POOL && state.coverQueue.length) {
      const key = state.coverQueue.shift();
      state.coverJobs += 1;
      fetchCover(key).catch(() => {}).then(() => {
        state.coverJobs -= 1;
        warmCovers([]);
      });
    }
  }

  async function fetchCover(coverId) {
    const state = bridge;
    const url = restUrl(state, "token", "getCoverArt", { id: coverId, size: COVER_SIZE });
    let response;
    try {
      response = await state.send(url, { method: "GET", redirect: "follow" });
    } catch (error) {
      return;                       // artwork is decoration; a miss must not fail the library
    }
    if (!response.ok || typeof response.blob !== "function") return;
    let blob;
    try {
      blob = await response.blob();
    } catch (error) {
      return;
    }
    const type = (blob && blob.type) || "image/jpeg";
    const bytes = typeof blob.arrayBuffer === "function" ? new Uint8Array(await blob.arrayBuffer()) : null;
    const url2 = objectUrl(bytes, type);
    if (!url2) return;
    state.covers.set(coverId, url2);
    while (state.covers.size > COVER_LIMIT) {
      const oldest = state.covers.keys().next().value;
      const stale = state.covers.get(oldest);
      state.covers.delete(oldest);
      if (typeof root.URL === "function" && typeof root.URL.revokeObjectURL === "function"
          && text(stale).startsWith("blob:")) {
        try { root.URL.revokeObjectURL(stale); } catch (error) { /* a stale URL is not a fault */ }
      }
    }
  }

  /* ------------------------------------------------------------------ songs */

  function songRow(song) {
    // The cover id is the server's; `artwork` is resolved when the answer is written, so
    // artwork that lands after the snapshot is still there on the next poll.
    const cover = text(song.coverArt || song.albumId || "");
    return {
      id: songId(song.id),
      title: text(song.title),
      artist_name: text(song.artist),
      album: text(song.album),
      // `albumsOf()` groups by this; the server's album id is opaque, and so is the host's.
      album_id: text(song.albumId || ""),
      duration: integer(song.duration),
      track_number: integer(song.track),
      added: epoch(song.created),
      artwork: "",
      cover: cover,
      playable: true,
      provider: "",
      status: "completed",
      source_status: "",
      source_checked: 0,
      playlists: [],
    };
  }

  /* ------------------------------------------------------------------ albums */

  /* Every album, by id. `getAlbumList2` is the protocol's own listing; a server that answers
     "not implemented" gets the artist walk instead, which asks the same catalogue a different
     way rather than pretending the library is empty. */
  async function albumList() {
    try {
      return await albumPages();
    } catch (error) {
      if (!(error instanceof BridgeError) || error.detail !== 30) throw error;
    }
    // `getArtists` groups its artists under `index[]` (one per initial); a flat list is accepted
    // too, because a server that omits the index is answering the same question.
    const payload = (await request("getArtists")).artists;
    const artists = rows(payload, "artist").slice();
    for (const index of rows(payload, "index")) artists.push(...rows(index, "artist"));
    const walked = await pool(artists, async artist => {
      try {
        const payload = (await request("getArtist", { id: artist.id })).artist;
        return rows(payload, "album");
      } catch (error) {
        if (error instanceof BridgeError && error.status === 404) return [];
        throw error;
      }
    });
    const seen = new Map();
    for (const album of walked.flat()) if (!seen.has(text(album.id))) seen.set(text(album.id), album);
    return [...seen.values()];
  }

  async function albumPages() {
    const albums = [];
    const seen = new Set();
    let offset = 0;
    for (let page = 0; page < ALBUM_PAGES; page++) {
      const envelope = await request("getAlbumList2", { type: "alphabeticalByArtist",
                                                       size: ALBUM_PAGE, offset: offset });
      const list = rows(envelope.albumList2, "album");
      if (!list.length) return albums;
      for (const album of list) {
        const key = text(album.id);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        albums.push(album);
      }
      // Advance by what the server actually returned, so a server with a smaller cap than the
      // one asked for is paged through rather than stopping after its first page.
      offset += list.length;
    }
    throw failure(502, "The server listed more than " + (ALBUM_PAGE * ALBUM_PAGES)
      + " albums; that is not a library this player can read in one pass");
  }

  /* One album's songs, fetched by id. An album that vanished between the listing and the read is
     skipped — it is gone, not an error; anything else fails the whole read. */
  async function albumSongs(album) {
    try {
      const payload = (await request("getAlbum", { id: album.id })).album;
      return rows(payload, "song");
    } catch (error) {
      if (error instanceof BridgeError && error.status === 404) return [];
      throw error;
    }
  }

  /* ------------------------------------------------------------------ playlists */

  async function playlistIndex() {
    const listed = rows((await request("getPlaylists")).playlists, "playlist");
    const detailed = await pool(listed, async row => {
      try {
        return (await request("getPlaylist", { id: row.id })).playlist;
      } catch (error) {
        if (error instanceof BridgeError && error.status === 404) return null;
        throw error;
      }
    });
    const list = [];
    const members = new Map();
    detailed.forEach((payload, index) => {
      const row = listed[index];
      const source = payload || {};
      const id = playlistId(source.id || row.id);
      const entries = rows(source, "entry").map(song => songId(song.id));
      members.set(id, entries);
      list.push({ id: id, title: text(source.name || row.name),
                  provider: "", source_url: "", added_by: "" });
    });
    return { list: list, members: members, rows: listed };
  }

  /* ------------------------------------------------------------------ library */

  async function compose() {
    const state = bridge;
    const albums = await albumList();
    const songs = await pool(albums, albumSongs);
    const index = await playlistIndex();

    const byId = new Map();
    const tracks = [];
    const covers = new Set();
    for (const group of songs) {
      for (const song of group) {
        const row = songRow(song);
        if (byId.has(row.id)) continue;
        byId.set(row.id, row);
        tracks.push(row);
        if (song.coverArt || song.albumId) covers.add(song.coverArt || song.albumId);
      }
    }
    for (const playlist of index.list) {
      const entries = index.members.get(playlist.id) || [];
      entries.forEach((trackId, position) => {
        const row = byId.get(trackId);
        if (!row) return;
        row.playlists.push({ id: playlist.id, title: playlist.title, provider: "",
                             kind: "", position: position });
      });
    }
    warmCovers(covers);
    return { tracks: tracks, all_tracks: tracks, artist_art: {}, playlists: index.list,
             members: index.members, albums: albums };
  }

  async function snapshot() {
    const state = bridge;
    const now = Date.now();
    if (state.snapshot && now - state.snapshot.at < SNAPSHOT_TTL_MS) return state.snapshot.value;
    if (state.pending) return state.pending;
    state.pending = compose().then(value => {
      state.snapshot = { at: Date.now(), value: value };
      return value;
    }).finally(() => { state.pending = null; });
    return state.pending;
  }

  async function library() {
    const value = await snapshot();
    const picture = row => Object.assign({}, row, { artwork: coverUrl(row.cover) });
    return { tracks: value.tracks.map(picture), all_tracks: value.all_tracks.map(picture),
             artist_art: value.artist_art, playlists: value.playlists };
  }

  /* ------------------------------------------------------------------ handlers */

  async function playlistList() {
    const value = await snapshot();
    return { playlists: value.playlists.map(playlist => ({
      id: playlist.id, title: playlist.title, can_edit: true,
      track_ids: (value.members.get(playlist.id) || []).slice(),
    })) };
  }

  async function playlistGet(match) {
    const id = Number(match[1]);
    const value = await snapshot();
    const playlist = value.playlists.find(row => row.id === id);
    if (!playlist) throw failure(404, "No playlist with id " + match[1] + " in the last library read");
    return { playlist: { id: playlist.id, title: playlist.title, can_edit: true,
                         track_ids: (value.members.get(id) || []).slice() } };
  }

  /* A new playlist. `createPlaylist` answers with nothing, so the list is re-read and the name
     is what identifies the row it made; a server that made no such row is a failure, not a
     silent no-op. */
  async function playlistCreate(match, input) {
    const body = bodyOf(input);
    const title = text(body.title).trim();
    if (!title) throw failure(400, "A playlist needs a title");
    const songs = Array.isArray(body.track_ids) ? body.track_ids.map(serverSongId).filter(Boolean) : [];
    await request("createPlaylist", { name: title, songId: songs });
    bridge.snapshot = null;
    const value = await snapshot();
    // The last row with that name: `getPlaylists` is in creation order, so a second playlist
    // with an old name is the one that was just made.
    const made = value.playlists.filter(row => row.title === title).pop();
    if (!made) throw failure(502, "The server did not name the playlist it made");
    return { playlist: { id: made.id, title: made.title, can_edit: true,
                         track_ids: (value.members.get(made.id) || []).slice() } };
  }

  /* The editor saves an ordered `track_ids`. Subsonic can add and remove songs from a playlist;
     it cannot express an order. An edit that only appends is therefore forwarded, and anything
     else is refused with the reason — a reorder that silently did nothing would be worse. */
  async function playlistSave(match, input) {
    const appId = Number(match[1]);
    const serverId = serverPlaylistId(appId);
    if (!serverId) throw failure(404, "No playlist with id " + match[1] + " in the last library read");
    const body = bodyOf(input);
    const wanted = Array.isArray(body.track_ids) ? body.track_ids.map(Number) : null;
    if (!wanted) throw failure(400, "track_ids must be a list");
    const value = await snapshot();
    const current = (value.members.get(appId) || []).slice();
    const appends = wanted.length >= current.length
      && current.every((id, index) => wanted[index] === id);
    if (!appends) {
      throw failure(501, "Subsonic playlists have no order to save: this server can add a song to "
        + "the end of a playlist, but it cannot move one or take one out. The change was not "
        + "made.");
    }
    const adding = wanted.slice(current.length).map(serverSongId).filter(Boolean);
    await request("updatePlaylist", { playlistId: serverId, songIdToAdd: adding });
    bridge.snapshot = null;
    return { ok: true };
  }

  /* Saved songs are Subsonic stars, and the app's `listeningSaves` is what its save controls and
     its Library scope read. Folders are a host feature with no Subsonic counterpart, so the
     folder list is genuinely empty rather than a failed request — an empty *list of folders* is
     the truth here, unlike an empty library. */
  async function savesGet() {
    const payload = (await request("getStarred2")).getStarred2;
    return { tracks: rows(payload, "song").map(song => songId(song.id)), playlists: [],
             playlist_tracks: [], folder_tracks: [] };
  }

  async function savesSet(match, input) {
    const body = bodyOf(input);
    const kind = text(body.kind);
    if (typeof body.saved !== "boolean") throw failure(400, "kind, id and a boolean saved are required");
    if (kind !== "track") {
      throw failure(501, "Subsonic can mark a song or an album as saved; it has no "
        + "per-member playlist store, so a playlist cannot be saved this way");
    }
    const serverId = serverSongId(Number(body.id));
    if (!serverId) throw failure(404, "That song is not in the last library read");
    await request(body.saved ? "star" : "unstar", { id: serverId });
    return { ok: true, saved: body.saved, kind: "track" };
  }

  /* The host's private folders. Subsonic has no folders (its tree is artist/album), and this
     player has none to offer; the app asks on every saves read, so the honest answer is a list
     that is empty by fact. */
  async function foldersGet() {
    return { folders: [] };
  }

  async function nowGet() {
    const payload = (await request("getPlayQueue")).playQueue;
    const state = payload || {};
    const queue = rows(state, "entry").map(song => songId(song.id));
    return { state: { id: state.current ? songId(state.current) : null, queue: queue,
                      time: integer(state.position) / 1000,
                      updated: epoch(state.changed) * 1000 } };
  }

  async function nowSet(match, input) {
    const body = bodyOf(input);
    const queue = Array.isArray(body.queue) ? body.queue.map(serverSongId).filter(Boolean) : [];
    const current = body.id == null ? null : serverSongId(Number(body.id));
    if (!queue.length && !current) return { ok: true };
    await request("savePlayQueue", {
      id: queue.length ? queue : (current ? [current] : []),
      current: current || (queue[0] || null),
      position: Math.max(0, Math.round(integer(body.time) * 1000)),
    });
    return { ok: true };
  }

  /* A play is a scrobble: the protocol's own record of "this device started this song". */
  async function listenPost(match, input) {
    const body = bodyOf(input);
    const serverId = serverSongId(Number(body.id));
    if (!serverId) throw failure(404, "That song is not in the last library read");
    await request("scrobble", { id: serverId, submission: "true" });
    return { ok: true };
  }

  async function bookmarksGet() {
    const payload = (await request("getBookmarks")).bookmarks;
    return { bookmarks: rows(payload, "bookmark").map(row => ({
      track: songId(row.entry && row.entry.id),
      position: integer(row.position), comment: text(row.comment),
      created: text(row.created), changed: text(row.changed),
    })) };
  }

  /* The app's own `/api/search` is the acquisition panel's ("Search SoundCloud and YouTube"), so
     the download half is refused outright below; the read half answers with what the server
     holds, which is the only search a player can honestly run. */
  async function searchGet(match, input, path) {
    // The query is in the route's own URL; `init` carries only the body, and there is none.
    const term = text(queryOf(path).get("q")).trim();
    const payload = (await request("search3", { query: term, songCount: 30, songOffset: 0,
                                                albumCount: 0, artistCount: 0 }));
    const result = payload.searchResult3 || payload.searchResult2 || payload.searchResult || {};
    const covers = new Set();
    const hits = rows(result, "song").map(song => {
      if (song.coverArt) covers.add(song.coverArt);
      return { id: songId(song.id), title: text(song.title), artist: text(song.artist),
               duration: integer(song.duration), artwork: coverUrl(song.coverArt || ""),
               provider: "server" };
    });
    warmCovers(covers);
    return { results: hits, query: term };
  }

  async function searchPost() {
    throw failure(404, "This player does not download music. The search answers with what your "
      + "server already holds; queueing a download from SoundCloud or YouTube is the host's job.");
  }

  /* Streams and downloads are opened by the media element and by a link, so they carry token
     auth and never the password. The bytes themselves are the server's, unmodified. */
  function mediaUrl(endpoint, appId) {
    const state = bridge;
    const serverId = serverSongId(Number(appId));
    if (!serverId) return null;
    return restUrl(state, "token", endpoint, { id: serverId });
  }

  async function audioGet(match) {
    const url = mediaUrl("stream", match[1]);
    if (!url) throw failure(404, "That song is not in the last library read");
    return passthrough(url);
  }

  async function downloadGet(match) {
    const url = mediaUrl("download", match[1]);
    if (!url) throw failure(404, "That song is not in the last library read");
    return passthrough(url);
  }

  /* The bytes are the server's, so the answer is the server's own Response, marked so the
     dispatcher hands it over untouched instead of JSON-encoding it. */
  async function passthrough(url) {
    let response;
    try {
      response = await bridge.send(url, { method: "GET", headers: { Accept: "*/*" }, redirect: "follow" });
    } catch (error) {
      throw failure(502, "Could not reach " + bridge.server + " — " + reasonOf(error));
    }
    return { [PASS_THROUGH]: response };
  }

  /* ------------------------------------------------------------------ responses */

  function respond(status, body) {
    const payload = JSON.stringify(body);
    if (typeof root.Response === "function") {
      return new root.Response(payload, {
        status: status, headers: { "Content-Type": "application/json; charset=utf-8" },
      });
    }
    return {
      ok: status >= 200 && status < 300,
      status: status,
      headers: { get: () => "application/json" },
      json: async () => JSON.parse(payload),
      text: async () => payload,
    };
  }

  /* Every answer says whether it came from a route this file serves. A refusal is the
     player's own decision, so its 404 is not news; a mapped route failing is the server, and
     that is what the window's shelf line is for. */
  function answer(status, body) {
    if (body instanceof BridgeError) {
      return { response: respond(body.status, { error: body.message }), status: body.status,
               mapped: true, message: body.message };
    }
    return { response: respond(status, body), status: status, mapped: true, message: "" };
  }

  function refusal(status, message) {
    return { response: respond(status, { error: message }), status: status, mapped: false,
             message: message };
  }

  function bodyOf(input) {
    if (!input) return {};
    if (typeof input === "string") {
      try {
        return JSON.parse(input);
      } catch (error) {
        return {};
      }
    }
    return input;
  }

  function queryOf(input) {
    const raw = text(input && typeof input === "object" ? input.search : input);
    const at = raw.indexOf("?");
    return new URLSearchParams(at >= 0 ? raw.slice(at + 1) : "");
  }

  /* ------------------------------------------------------------------ dispatch */

  function apiPath(input) {
    const raw = text(typeof input === "object" && input ? input.url : input);
    if (!raw) return null;
    let path;
    try {
      path = new URL(raw, (root.location && root.location.href) || "http://player.invalid/");
    } catch (error) {
      return null;
    }
    const origin = root.location && root.location.origin ? root.location.origin : "";
    if (origin && path.origin !== origin && !raw.startsWith("/")) return null;
    if (!path.pathname.startsWith("/api/")) return null;
    return path.pathname + path.search;
  }

  function methodOf(input, init) {
    if (init && init.method) return text(init.method).toUpperCase();
    if (input && typeof input === "object" && input.method) return text(input.method).toUpperCase();
    return "GET";
  }

  function refusalFor(path) {
    const bare = path.split("?")[0];
    for (const [pattern, why] of REFUSALS) if (pattern.test(bare)) return why;
    return null;
  }

  async function dispatch(path, method, init) {
    const bare = path.split("?")[0];
    const seen = [];
    for (const entry of ROUTES) {
      const match = entry.pattern.exec(bare);
      if (!match) continue;
      seen.push(entry);
      if (entry.method !== method) continue;
      const value = await entry.handler(match, init && init.body, path);
      if (value && value[PASS_THROUGH]) {
        const response = value[PASS_THROUGH];
        return { response: response, status: response.status, mapped: true, message: "" };
      }
      return answer(200, value);
    }
    if (seen.length) {
      const allowed = [...new Set(seen.map(entry => entry.method))].join(", ");
      const why = refusalFor(path);
      return refusal(405, why || ("The player serves " + allowed + " for " + bare
        + ", not " + method));
    }
    const reason = refusalFor(path);
    return refusal(404, reason
      ? "The player does not serve " + method + " " + bare + ": " + reason
      : "The player has no route for " + method + " " + bare + ". It is either a host feature "
        + "this player does not carry, or a route PLAYER-003 has not mapped — it is not an empty "
        + "answer, because an empty answer looks like an empty library.");
  }

  function note(endpoint, status) {
    const state = bridge;
    state.trace.push({ endpoint: endpoint, status: status, at: Date.now() });
    if (state.trace.length > 60) state.trace.shift();
  }

  /* `dispatch` rejects only for an error a handler raised; the fetch path turns that into a
     Response. This is that same conversion, for a caller that holds a path rather than a
     Request — `responseFor` and the installed `fetch` share it, so neither can drift. */
  async function attempt(path, method, body) {
    let outcome;
    try {
      outcome = await dispatch(path, method, { body: body });
    } catch (error) {
      outcome = answer(error instanceof BridgeError ? error.status : 502,
        error instanceof BridgeError ? error
          : new BridgeError(502, "The player's bridge failed on " + path.split("?")[0]
            + " — " + reasonOf(error)));
    }
    // The log belongs where the answer is decided, so a call through `fetch` and a call through
    // `responseFor` are recorded exactly once each.
    note(path.split("?")[0], outcome.status);
    return outcome;
  }

  async function bridgeFetch(input, init) {
    const path = apiPath(input);
    if (path === null || !bridge) return realFetch(input, init);
    const method = methodOf(input, init);
    const entry = path.split("?")[0];
    const outcome = await attempt(path, method, init && init.body);
    /* Only a route this file serves can report on the server. A refusal of `/api/state` is
       this player's own decision and repeating it every three seconds would be noise. */
    if (outcome.mapped) {
      if (outcome.status >= 400 && typeof bridge.onProblem === "function") {
        bridge.onProblem({ status: outcome.status, endpoint: entry, message: outcome.message || "" });
      } else if (outcome.status < 400 && typeof bridge.onAnswer === "function") {
        bridge.onAnswer({ status: outcome.status, endpoint: entry });
      }
    }
    return outcome.response;
  }

  /* ------------------------------------------------------------------ api */

  let realFetch = null;

  function install(session, options) {
    const input = session || {};
    const server = text(input.server).trim().replace(/\/+$/, "");
    const username = text(input.username).trim();
    const password = text(input.password);
    if (!server || !username || !password) {
      throw new Error("a server, a username and a password are required to install the bridge");
    }
    if (realFetch) uninstall();
    const settings = options || {};
    bridge = empty();
    bridge.server = server;
    bridge.username = username;
    bridge.password = password;
    bridge.send = settings.fetch || (typeof root.fetch === "function" ? root.fetch : null);
    bridge.onProblem = typeof settings.onProblem === "function" ? settings.onProblem : null;
    bridge.onAnswer = typeof settings.onAnswer === "function" ? settings.onAnswer : null;
    bridge.objectUrl = typeof settings.objectUrl === "function" ? settings.objectUrl : null;
    if (!bridge.send) throw new Error("this build has no way to reach a server");
    realFetch = settings.fetch || root.fetch;
    root.fetch = bridgeFetch;
    return PlayerApi;
  }

  function uninstall() {
    if (realFetch) root.fetch = realFetch;
    realFetch = null;
    bridge = null;
  }

  /* The media element and a download link open a `/api/…` URL themselves; neither goes through
     `fetch`. This is the one rewrite they need, and it is a URL substitution rather than a
     second request: the bytes still come from the server, with Range support intact. */
  function rewriteMediaUrl(value) {
    if (!bridge) return value;
    const raw = text(value);
    const pattern = /^(?:[a-z][a-z0-9+.-]*:\/\/[^/]*)?\/api\/track\/(\d+)\/(audio|download)(\?.*)?$/i;
    const match = pattern.exec(raw);
    if (!match) return value;
    const endpoint = match[2].toLowerCase() === "download" ? "download" : "stream";
    return mediaUrl(endpoint, match[1]) || value;
  }

  function log() {
    return bridge ? bridge.trace.slice() : [];
  }

  const PlayerApi = {
    install: install,
    uninstall: uninstall,
    installed: () => !!bridge,
    responseFor: (path, method, body) => attempt(path, method || "GET", body),
    rewriteMediaUrl: rewriteMediaUrl,
    streamUrl: appId => mediaUrl("stream", appId),
    downloadUrl: appId => mediaUrl("download", appId),
    log: log,
    routes: () => ROUTES.map(entry => ({ method: entry.method, path: entry.label })),
    refusals: () => REFUSALS.map(([pattern, why]) => ({ path: pattern.source, why: why })),
    md5: md5,
    PROTOCOL_VERSION: PROTOCOL_VERSION,
    CLIENT_NAME: CLIENT_NAME,
  };

  root.PlayerApi = PlayerApi;
})(typeof window === "undefined" ? globalThis : window);