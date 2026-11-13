/* The connect screen: the shell's first promise, and the only thing between an empty window
   and a music library.

   It has one job. A person types the address of a Subsonic / OpenSubsonic server they already
   have, the username and password that server gave them, and this asks `/rest/ping.view`. A
   Subsonic answer is the proof: the envelope carries `status` and, when it is refused, an
   `error` with a code that names the actual fault (40 is wrong credentials, 30 is a version
   the server will not speak). Anything else — an HTML page, a proxy error, a JSON document
   that is not an envelope — is refused with the reason, because "something went wrong" hides
   the one thing a person needs to fix.

   The server and the username are a plain record; the password is handed to `accounts.js`, which
   puts it in the system keychain when there is one and keeps it in memory when there is not. No
   secret reaches localStorage, IndexedDB, a file or a cookie, and a machine without a keychain
   asks again after a restart rather than leaving a password on disk.

   Loaded as a classic script with no build step, like the rest of the app: `PlayerConnect` is
   the API and the wiring at the bottom only runs when the markup is there, so the same file
   can be exercised with a stub `fetch` outside a browser. */
(function (root) {
  "use strict";

  const PROTOCOL_VERSION = "1.16.1";
  const CLIENT_NAME = "hardlylistening-player";

  /* The codes a connect attempt can actually meet. The message the server sends is appended
     when it adds something the code does not already say. */
  const SUBSONIC_ERRORS = {
    0: "The server did not say what went wrong",
    10: "The server refused a required parameter",
    20: "This client speaks a protocol version the server will not use",
    30: "The server speaks a Subsonic version this player does not understand",
    40: "Wrong username or password",
    41: "This server does not accept token authentication",
    50: "That account is not allowed to use the API",
    60: "The server cannot accept that request",
    70: "The server could not find what the request asked for",
  };

  /* Held for this page's lifetime only. Nothing here is written anywhere. */
  let session = null;

  /* The secret store, when the shell provides one. In a browser tab there is no `invoke`, so the
   * vault reports "memory" and nothing is written anywhere — the same behaviour as before, said
   * out loud instead of assumed. */
  const invoke = root.__TAURI__ && root.__TAURI__.core && root.__TAURI__.core.invoke;
  const accounts = root.PlayerAccounts
    ? root.PlayerAccounts.open({ vault: root.PlayerAccounts.keychainVault(invoke) })
    : null;

  function text(value) {
    return value == null ? "" : String(value);
  }

  /* A server address is a scheme, a host and an optional path — the part before `/rest`.
     Trailing slashes are dropped so `…/` and `…` ask the same URL, but the address is never
     guessed at: a missing scheme is refused instead of being turned into one, because
     `http` and `https` are the person's choice and not this file's. */
  function normaliseServerUrl(raw) {
    const value = text(raw).trim();
    if (!value) {
      return { error: "Enter the server address — for example https://music.example.com" };
    }
    let parsed;
    try {
      parsed = new root.URL(value);
    } catch (error) {
      return { error: "That address is missing its scheme. Try https://music.example.com" };
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return { error: "A server address starts with http:// or https://" };
    }
    if (!parsed.hostname) {
      return { error: "That address has no host name" };
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

  function pingUrl(server, username, password) {
    const query = new root.URLSearchParams({
      u: username,
      p: password,
      v: PROTOCOL_VERSION,
      c: CLIENT_NAME,
      f: "json",
    });
    return server + "/rest/ping.view?" + query.toString();
  }

  function reasonOf(error) {
    const said = text(error && error.message).trim();
    return said || "the request failed before an answer arrived";
  }

  /* One round trip. Resolves to `{ok: true, server}` or `{ok: false, error, code?}` and never
     rejects: a caller that has to catch is a caller that will forget to. */
  async function connect(options) {
    const input = options || {};
    const target = normaliseServerUrl(input.server);
    if (target.error) return { ok: false, error: target.error };

    const username = text(input.username).trim();
    if (!username) return { ok: false, error: "Enter the username the server gave you" };
    const password = text(input.password);
    if (!password) return { ok: false, error: "Enter the password for that username" };

    const send = input.fetch || (typeof root.fetch === "function" ? root.fetch : null);
    if (!send) return { ok: false, error: "This build has no way to reach a server" };

    let response;
    try {
      response = await send(pingUrl(target.url, username, password), {
        method: "GET",
        headers: { Accept: "application/json" },
        redirect: "follow",
      });
    } catch (error) {
      return { ok: false, error: "Could not reach " + target.url + " — " + reasonOf(error) };
    }

    if (response.status === 401 || response.status === 403) {
      return { ok: false, code: 40, error: "Wrong username or password" };
    }
    if (!response.ok) {
      return { ok: false, error: target.url + " answered HTTP " + response.status };
    }

    let body;
    try {
      body = await response.json();
    } catch (error) {
      return {
        ok: false,
        error: target.url + " answered, but not with JSON — that address is probably a web "
          + "page rather than a Subsonic server",
      };
    }

    const envelope = body && typeof body === "object" ? body["subsonic-response"] : null;
    if (!envelope || typeof envelope !== "object") {
      return {
        ok: false,
        error: target.url + " answered, but not as a Subsonic server (there is no "
          + "subsonic-response in the reply)",
      };
    }

    if (envelope.error && typeof envelope.error === "object") {
      const code = Number(envelope.error.code);
      const known = SUBSONIC_ERRORS[code];
      const said = text(envelope.error.message).trim();
      return {
        ok: false,
        code: Number.isFinite(code) ? code : null,
        error: known && said && said !== known ? known + " — " + said
          : known || said || "The server refused the request",
      };
    }

    if (text(envelope.status) !== "ok") {
      return {
        ok: false,
        error: "The server answered with status “" + text(envelope.status)
          + "” instead of ok",
      };
    }

    const server = {
      url: target.url,
      type: text(envelope.type),
      version: text(envelope.version),
      serverVersion: text(envelope.serverVersion),
      openSubsonic: envelope.openSubsonic === true,
    };
    session = { server: server, credentials: { username: username, password: password } };
    /* The page keeps no copy beyond this line: `accounts` splits the record from the secret, and
     * the secret's home is the handoff below, not this object. */
    if (accounts) {
      const moved = await accounts.migrate(session);
      if (!moved.ok) { root.console.warn(accounts.log(moved.error)); }
    }
    return { ok: true, server: server };
  }

  function current() {
    return session;
  }

  function forget() {
    session = null;
  }

  /* The page: no framework, and no wiring at all unless its markup is present. */
  function wire() {
    const form = root.document.getElementById("connect-form");
    if (!form) return;
    const button = root.document.getElementById("connect-go");
    const message = root.document.getElementById("connect-msg");
    const say = (line, kind) => {
      message.textContent = line;
      message.className = kind ? "msg " + kind : "msg";
    };

    form.addEventListener("submit", async event => {
      event.preventDefault();
      say("", "");
      if (button) {
        button.disabled = true;
        button.textContent = "Connecting…";
      }
      const result = await connect({
        server: root.document.getElementById("server").value,
        username: root.document.getElementById("username").value,
        password: root.document.getElementById("password").value,
      });
      if (button) {
        button.disabled = false;
        button.textContent = "Connect";
      }
      if (!result.ok) {
        say(result.error, "err");
        return;
      }
      const info = result.server;
      say("Connected to " + (info.type || "a Subsonic server")
        + (info.serverVersion ? " " + info.serverVersion : "")
        + (info.openSubsonic ? " (OpenSubsonic)" : "")
        + ". This build stops at the handshake: the library screen is PLAYER-003.", "ok");
    });
  }

  root.PlayerConnect = {
    connect: connect,
    normaliseServerUrl: normaliseServerUrl,
    pingUrl: pingUrl,
    current: current,
    forget: forget,
    PROTOCOL_VERSION: PROTOCOL_VERSION,
    CLIENT_NAME: CLIENT_NAME,
  };

  if (root.document && typeof root.document.getElementById === "function") wire();
})(typeof window === "undefined" ? globalThis : window);