/* Social, when the server actually has it.

   The activity feed, comments, votes, likes and boosts are a HardlyListening server's own
   surface. The protocol this player speaks -- Subsonic / OpenSubsonic -- has none of them: a
   Subsonic server has a catalogue, a play queue and stars, and no idea what a member is. So the
   question here is not "how do we draw a feed" but "is there a feed here at all", and the
   answer belongs to the server.

   It is asked the way the app's own SDK asks it: `GET /api/v1/capabilities` is public (the
   server answers it before it authenticates anything), and a domain counts as available only
   when the document names it with at least one flag. Nothing is inferred from the app's own
   source, and a server that stays quiet is not credited with a feature -- a client that guesses
   "surely it has that" is the failure this file exists to prevent.

   Three states, and each one is said out loud rather than drawn as an empty list:

   1. **activity** -- the document names `social.activity` and the server does not require
      members, so `GET /api/v1/social/activity` answers without a session. The app's own
      `GET /api/activity` is answered from it, in the host's own `{"items": [...]}` shape.
   2. **members** (or **no-activity**) -- the server has the domain but this player cannot read
      it. Comments, votes, likes, boosts and profiles are a member's, and this player never holds
      the `__Host-hl_session` cookie a member session is: the connect screen has the Subsonic
      credentials and nothing else. A 501 naming that is the answer, never an empty feed.
   3. **absent** -- a plain Subsonic server, or a capability document that names no social
      domain. This file then steps out of the way entirely: the bridge's existing refusal is
      what the person reads, word for word.

   What this file never does: it never sends a credential. The capability document is public and
   the activity read is the server's own public read, so no `u`, `p`, `t` or `s` appears in any
   URL it builds, and it holds no password to put there. `log()` records an endpoint and a
   status, never a query string -- the same rule the bridge follows for the same reason.

   Loaded as a classic script with no build step: `PlayerSocial` is the API, `attach()` wraps
   `window.fetch` once and `detach()` puts it back, and nothing runs at load. The transport is
   injectable, so the same file can be exercised under node with `fetch` stubbed. */
(function (root) {
  "use strict";

  /* Where the server says what it has, and where the feed is read. Both are the app's versioned
     API, not the Subsonic protocol: `/rest/*` has no opinion about members. */
  const CAPABILITIES_PATH = "/api/v1/capabilities";
  const ACTIVITY_PATH = "/api/v1/social/activity";
  /* The app's own page size (`web/js/social-feed.js` asks for 80) and the server's clamp. */
  const LIMIT = 80;
  const LIMIT_MIN = 1;
  const LIMIT_MAX = 200;

  /* The route table: the one thing on the app's own front end this file answers. `from` is what
     it is answered from, so a reader can follow it without reading the dispatcher. */
  const ROUTES = [
    { method: "GET", path: "/api/activity", from: "GET " + ACTIVITY_PATH },
  ];

  /* The rest of the app's social surface, and why this player cannot act on any of it even when
     the server does have social. Each answer is a 501 with the reason, never a silent no-op. */
  const REFUSALS = [
    [/^\/api\/boost$/, "a boost is written as a member: the server records it against "
      + "`__Host-hl_session`, and this player holds no member session"],
    [/^\/api\/social\/.+$/, "comments, votes and likes are a member's: the server reads and "
      + "writes them against `__Host-hl_session`, which this player does not hold"],
    [/^\/api\/member\/[^/]+$/, "a member profile is the server's member record; this player has "
      + "no member session to read it with"],
  ];

  let state = null;

  function text(value) {
    return value == null ? "" : String(value);
  }

  function mapping(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function reasonOf(error) {
    const said = text(error && error.message).trim();
    return said || "the request failed before an answer arrived";
  }

  /* ------------------------------------------------------------------ responses */

  /* The same shape the bridge builds, so the app's `response.ok` / `response.json()` checks work
     whether the answer came from here or from there. */
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

  function refused(method, path, why) {
    return respond(501, { error: "The player does not serve " + method + " " + path + ": " + why });
  }

  function failed(status, message) {
    return respond(status, { error: message });
  }

  /* ------------------------------------------------------------------ the decision */

  function absent(reason) {
    return {
      state: "absent", available: false, needed: "social", reason: reason,
      flags: { activity: false, comments: false, votes: false, likes: false },
    };
  }

  /* One capability document, read the way the SDK reads it: a domain is available only when the
     document names it (a non-empty object), and a flag is what the document put there. `repo` is
     the cautionary case in the server's own SDK -- knowing the app has a Repository is not the
     server saying it does -- so nothing is assumed here beyond the document. */
  function decide(document, server) {
    const doc = mapping(document);
    const social = mapping(mapping(doc.features).social);
    const flags = {
      activity: !!social.activity,
      comments: !!social.comments,
      votes: !!social.votes,
      likes: !!social.likes,
    };
    if (!Object.keys(social).length) {
      return absent("the server at " + server + " names no social domain in its capability "
        + "document (" + CAPABILITIES_PATH + ")");
    }
    if (!flags.activity) {
      return {
        state: "no-activity", available: false, needed: "social.activity", flags: flags,
        reason: "the server at " + server + " has a social domain, but it does not name the "
          + "activity feed (`social.activity`)",
      };
    }
    /* Members are the whole difference between "the server has a feed" and "this player can
       read it": the read is public only when the deployment has no accounts. With accounts on,
       the feed answers 401 and the cookie it wants is one this player cannot hold. */
    if (mapping(doc.server).requires_members === true) {
      return {
        state: "members", available: false, needed: "members", flags: flags,
        reason: "the server at " + server + " requires a member sign-in; its activity feed is a "
          + "member's read and this player holds no member session",
      };
    }
    return {
      state: "activity", available: true, needed: "", flags: flags, reason: "",
      server: { name: text(mapping(doc.server).name), version: text(mapping(doc.server).version),
                api: text(mapping(doc.server).api) },
    };
  }

  /* The probe: one public GET, no credential, and no exception on any answer. A server that is
     not there, is not a HardlyListening server, or answers something unusable degrades to
     `absent` with the reason -- the same rule the SDK's own probe follows, because a client
     that cannot start because a capability read failed has made a failed read fatal. */
  async function probeDecision() {
    const url = state.server + CAPABILITIES_PATH;
    let response;
    try {
      response = await state.send(url, { method: "GET", headers: { Accept: "application/json" },
                                        cache: "no-store", redirect: "follow" });
    } catch (error) {
      return absent("could not read the capability document at " + state.server
        + CAPABILITIES_PATH + " - " + reasonOf(error));
    }
    note(CAPABILITIES_PATH, response.status);
    if (response.status === 401 || response.status === 403) {
      return absent("the capability document at " + state.server + CAPABILITIES_PATH
        + " was refused (HTTP " + response.status + ")");
    }
    if (!response.ok) {
      return absent(state.server + " has no capability document (HTTP " + response.status + " "
        + "for " + CAPABILITIES_PATH + "), so it may be a Subsonic server, which has no social "
        + "surface at all");
    }
    let document;
    try {
      document = await response.json();
    } catch (error) {
      return absent(state.server + CAPABILITIES_PATH + " answered, but not with JSON - that "
        + "address is probably not the app's own API");
    }
    if (!Object.keys(mapping(document)).length) {
      return absent("the capability document at " + state.server + CAPABILITIES_PATH
        + " was empty");
    }
    return decide(document, state.server);
  }

  function settle() {
    if (!state) return Promise.resolve(absent("no server is attached"));
    if (state.decision) return Promise.resolve(state.decision);
    if (!state.pending) {
      state.pending = probeDecision().then(decision => {
        state.decision = decision;
        state.pending = null;
        if (state.say) state.say(decision);
        return decision;
      });
    }
    return state.pending;
  }

  /* ------------------------------------------------------------------ the read */

  function limitOf(path) {
    const at = path.indexOf("?");
    const query = new root.URLSearchParams(at >= 0 ? path.slice(at + 1) : "");
    const asked = Number(query.get("limit"));
    if (!Number.isFinite(asked) || asked <= 0) return LIMIT;
    return Math.min(LIMIT_MAX, Math.max(LIMIT_MIN, Math.round(asked)));
  }

  /* The feed, in the shape the app already reads: the host answers `/api/activity` with
     `{"items": [...]}` from the same service, so this is the same rows in the same envelope and
     not a second rendering of them. Day cards and boosts are passed through exactly as the
     server wrote them. */
  async function activityAnswer(decision, path) {
    if (decision.state !== "activity") {
      return refused("GET", "/api/activity", decision.reason);
    }
    const limit = limitOf(path);
    const url = state.server + ACTIVITY_PATH + "?limit=" + limit;
    let response;
    try {
      response = await state.send(url, { method: "GET", headers: { Accept: "application/json" },
                                        cache: "no-store", redirect: "follow" });
    } catch (error) {
      return failed(502, "Could not reach " + state.server + " for the activity feed - "
        + reasonOf(error));
    }
    note(ACTIVITY_PATH, response.status);
    if (!response.ok) {
      return failed(response.status === 401 || response.status === 403 ? 501 : 502,
        state.server + " answered " + ACTIVITY_PATH + " with HTTP " + response.status
        + (response.status === 401 || response.status === 403
          ? " - that read needs a member session, which this player does not hold" : ""));
    }
    let document;
    try {
      document = await response.json();
    } catch (error) {
      return failed(502, state.server + " answered " + ACTIVITY_PATH + ", but not with JSON");
    }
    if (text(document && document.api) !== "v1" || !Array.isArray(document.data)) {
      return failed(502, state.server + " answered " + ACTIVITY_PATH + ", but not as a v1 "
        + "envelope with a `data` list");
    }
    return respond(200, { items: document.data });
  }

  /* ------------------------------------------------------------------ dispatch */

  function pagePath(input) {
    const raw = text(typeof input === "object" && input ? input.url : input);
    if (!raw) return null;
    let path;
    try {
      path = new root.URL(raw, (root.location && root.location.href) || "http://player.invalid/");
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

  /* A Response, or null for "not this file's question" -- and null is what keeps the bridge's
     own refusals exactly as they are for a server with no social. */
  async function answerFor(path, method) {
    const bare = path.split("?")[0];
    const decision = await settle();
    if (decision.state === "absent") return null;
    if (bare === "/api/activity" && method === "GET") return activityAnswer(decision, path);
    for (const [pattern, why] of REFUSALS) {
      if (pattern.test(bare)) return refused(method, bare, why);
    }
    return null;
  }

  /* The transport this file replaced, so a call that lands on a stale wrapper after `detach()`
     still reaches the server instead of throwing. */
  let fallback = null;

  function wrapped(input, init) {
    if (!state) {
      return fallback ? fallback(input, init)
        : Promise.reject(new Error("social was detached and has no transport"));
    }
    const path = pagePath(input);
    if (path === null) return state.inner(input, init);
    const method = methodOf(input, init);
    return answerFor(path, method).then(found => found || state.inner(input, init));
  }

  /* ------------------------------------------------------------------ log */

  function note(endpoint, status) {
    state.trace.push({ endpoint: endpoint, status: status, at: Date.now() });
    if (state.trace.length > 40) state.trace.shift();
  }

  function log() {
    return state ? state.trace.slice() : [];
  }

  /* ------------------------------------------------------------------ api */

  /* `session` is the connect screen's own value and only its address is read: the module has no
     use for the credentials and must not hold them. `send` is the transport to the server (the
     default is the current `window.fetch`, which -- with the bridge installed -- passes an
     absolute URL on another origin straight through), and `say` is told the decision once, in a
     sentence meant for the shelf line. */
  function attach(options) {
    if (state) detach();
    const settings = options || {};
    const session = settings.session
      || (root.PlayerConnect && typeof root.PlayerConnect.current === "function"
        ? root.PlayerConnect.current() : null);
    const server = text(session && session.server && session.server.url).trim()
      .replace(/\/+$/, "");
    if (!/^https?:\/\/[^/]+/i.test(server)) {
      throw new Error("a connected server is required to attach social");
    }
    const inner = settings.send
      || (typeof root.fetch === "function" ? root.fetch : null);
    if (typeof inner !== "function") throw new Error("this build has no way to reach a server");
    state = {
      server: server,
      inner: inner,
      send: typeof settings.send === "function" ? settings.send : inner,
      say: typeof settings.say === "function" ? settings.say : null,
      decision: null, pending: null, trace: [],
    };
    fallback = inner;
    root.fetch = wrapped;
    return PlayerSocial;
  }

  function detach() {
    if (!state) return false;
    if (root.fetch === wrapped) root.fetch = state.inner;
    state = null;
    return true;
  }

  const PlayerSocial = {
    attach: attach,
    detach: detach,
    installed: () => !!state,
    /* The decision, and a promise of it for a caller that has to wait for one round trip. */
    ready: settle,
    decision: () => (state && state.decision ? Object.assign({}, state.decision) : null),
    routes: () => ROUTES.map(row => ({ method: row.method, path: row.path, from: row.from })),
    refusals: () => REFUSALS.map(([pattern, why]) => ({ path: pattern.source, why: why })),
    log: log,
    CAPABILITIES_PATH: CAPABILITIES_PATH,
    ACTIVITY_PATH: ACTIVITY_PATH,
    LIMIT: LIMIT,
    STATES: ["activity", "members", "no-activity", "absent"],
  };

  root.PlayerSocial = PlayerSocial;
})(typeof window === "undefined" ? globalThis : window);