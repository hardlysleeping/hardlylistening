/* The handshake's second half: once the connect screen has a server, this is what turns the
   window from a gate into the library.

   The app's own `index.html` is the library, and its inline script asks for `/api/library`,
   `/api/playlists`, `/api/track/…/audio` — the Python host's routes. In the player there is no
   host, so this file does three things in this order:

   1. **Waits for the session.** `PlayerConnect.current()` becomes non-null only after
      `/rest/ping.view` proved the server answers. Nothing here runs before that, and nothing
      here reads the password from anywhere else.
   2. **Installs the bridge on `window.fetch`** (api-bridge.js), so the very first request the
      library page makes is already answered from `/rest/*`.
   3. **Writes the assembled `index.html` into this same window.** It is fetched as text and
      parsed with `document.open()/write()/close()` rather than navigated to, because a
      navigation would discard the JavaScript realm and with it the session — which this build
      deliberately keeps in memory only. Same realm, same `PlayerConnect`, same bridge; the URL
      stays `connect.html` on purpose, so a reload lands back on the gate instead of on a
      library page with no bridge.

   The page that arrives is the app's page, unmodified, so this file does not re-implement any
   of it. It only removes controls that cannot act here (the acquisition search, the paste-a-link
   box, the host's import/export) and gives a failing server a line of its own at the top,
   because the app paints an empty library when `/api/library` fails and an empty library is
   indistinguishable from a library that is actually empty.

   Loaded as a classic script, no build step. `PlayerLibraryBoot` is the API and nothing starts
   until the markup is there, so the same file can be exercised under node with a stub document. */
(function (root) {
  "use strict";

  /* Controls the player does not carry. Each one is either acquisition (the host's download
     search and paste-a-link box), the host's own library files (import/move/check), or a page
     that assemble.mjs does not ship (party, mixer, swipe). They are hidden rather than left to
     fail: a control that can only answer 404 is a lie told by the layout. */
  const HIDDEN = [
    "#qtab-search",                                  // "Search" tab: song acquisition
    "#qsearch",                                      // its panel
    "#url",                                          // paste-a-link box
    "#btn-add",                                      // its Add button
    "#addstatus",                                    // its progress line
    "#exportbtn",                                    // host export (a ZIP of the host's files)
    "#view-library .libtools",                       // import / move / check sources
    '#navpanel a[href="/party"]',                    // pages the player does not assemble
    '#navpanel a[href="/dj"]',
    '#navpanel a[href="/swipe"]',
  ];

  /* The app remembers the last page in localStorage and replays its tab click on load. Left
     unset, it lands on the queue — a download queue this player has none of. A choice the
     person already made is kept; these are only the defaults for a first launch. */
  const DEFAULTS = [["hl.view", "library"], ["hl.scope", "shared"]];

  //: How often the gate is checked when no MutationObserver is available.
  const WATCH_MS = 400;

  let watching = false;
  let grafting = false;
  let grafted = false;
  let observer = null;
  let timer = null;
  let attempted = null;
  let problem = null;

  function text(value) {
    return value == null ? "" : String(value);
  }

  function reasonOf(error) {
    const said = text(error && error.message).trim();
    return said || "the request failed before an answer arrived";
  }

  function doc() {
    return root.document || null;
  }

  /* ------------------------------------------------------------------ defaults */

  function defaults(storage) {
    const written = [];
    if (!storage) return written;
    for (const [key, value] of DEFAULTS) {
      let current = null;
      try {
        current = storage.getItem(key);
      } catch (error) {
        continue;
      }
      if (current) continue;
      try {
        storage.setItem(key, value);
        written.push(key);
      } catch (error) {
        // A WebView with storage disabled still has a library; it just forgets the tab.
      }
    }
    return written;
  }

  /* ------------------------------------------------------------------ the page */

  function hide(document) {
    const hidden = [];
    if (!document || typeof document.querySelectorAll !== "function") return hidden;
    for (const selector of HIDDEN) {
      for (const node of document.querySelectorAll(selector)) {
        node.hidden = true;
        if (node.style && typeof node.style.setProperty === "function") {
          node.style.setProperty("display", "none", "important");
        }
        hidden.push(selector);
      }
    }
    return hidden;
  }

  /* The app's own shelf bar, reused: `#shelfmode` is the offline line the app already draws,
     and it is the right shape for "the server could not answer" too. */
  function line(document) {
    if (!document || !document.body) return null;
    const existing = document.getElementById("player-shelf");
    if (existing) return existing;
    const node = document.createElement("div");
    node.id = "player-shelf";
    node.className = "shelfbar";
    node.setAttribute("role", "status");
    node.setAttribute("aria-live", "polite");
    node.setAttribute("data-player", "shelf");
    node.hidden = true;
    const first = document.getElementById("shelfmode");
    if (first && first.parentNode === document.body) document.body.insertBefore(node, first);
    else document.body.insertBefore(node, document.body.firstChild);
    return node;
  }

  function say(message, document) {
    const node = line(document);
    if (!node) return;
    node.textContent = text(message);
    node.hidden = !text(message);
  }

  /* ------------------------------------------------------------------ media */

  /* The transport and the cover rows do not go through `fetch`: the media element and a
     download link open the URL themselves, so they need the token form of the URL rather than
     a password in a rendered attribute. Patching the two accessors is the whole rewrite, and
     every other URL is left exactly as the app wrote it. */
  function patch() {
    const api = root.PlayerApi;
    if (!api || root.__playerMediaPatched) return false;
    const guard = (prototype, name) => {
      if (!prototype) return false;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, name);
      if (!descriptor || typeof descriptor.set !== "function") return false;
      Object.defineProperty(prototype, name, {
        configurable: true,
        enumerable: descriptor.enumerable,
        get: descriptor.get,
        set: function (value) { descriptor.set.call(this, api.rewriteMediaUrl(value)); },
      });
      return true;
    };
    const media = guard(root.HTMLMediaElement && root.HTMLMediaElement.prototype, "src");
    const anchor = guard(root.HTMLAnchorElement && root.HTMLAnchorElement.prototype, "href");
    root.__playerMediaPatched = media || anchor;
    return media || anchor;
  }

  /* ------------------------------------------------------------------ the graft */

  async function graft(session, send) {
    const document = doc();
    const api = root.PlayerApi;
    const credentials = session.credentials || {};
    api.install({
      server: session.server && session.server.url,
      username: credentials.username,
      password: credentials.password,
    }, {
      onProblem: found => {
        problem = found;
        say(text(found && found.message) || ("The server answered HTTP " + text(found && found.status)
          + " for " + text(found && found.endpoint)), doc());
      },
      onAnswer: () => {
        problem = null;
        say("", doc());
      },
    });

    /* PLAYER-006: the social surface is decided after the bridge is installed, because the
     * decision is one request to the server's own capabilities document. The module refuses with
     * the reason when the server has none, and the bridge's refusal then stands as it was. */
    if (root.PlayerSocial && typeof root.PlayerSocial.attach === "function") {
      root.PlayerSocial.attach({ session: session, say: message => say(message, document) });
    }

    defaults(root.localStorage);

    let page;
    try {
      page = await send("./index.html", { cache: "no-store" });
    } catch (error) {
      throw new Error("Could not read the library page: " + reasonOf(error));
    }
    if (!page || !page.ok) {
      throw new Error("The library page is missing from this build (index.html answered "
        + "HTTP " + text(page && page.status) + ")");
    }
    const html = await page.text();
    if (!/<html[\s>]/i.test(html) || !/<body[\s>]/i.test(html)) {
      throw new Error("index.html in this build is not a page");
    }
    document.open();
    document.write(html);
    document.close();
    // Deferred scripts are still arriving when the parser is done with `document.write`, so the
    // pass runs again when the document has finished loading. `after()` is idempotent.
    if (typeof root.addEventListener === "function") root.addEventListener("load", after, { once: true });
    after();
  }

  /* Runs once the new document is parsed, and again on `load`, because a deferred script may
     still be on its way when the parser is done with `document.write`. Everything here is
     idempotent: hiding a hidden node and patching a patched accessor are both no-ops. */
  function after() {
    const document = doc();
    if (!document) return;
    patch();
    hide(document);
    line(document);
    if (problem) say(text(problem.message), document);
  }

  /* ------------------------------------------------------------------ the gate */

  function stopWatching() {
    watching = false;
    if (observer && typeof observer.disconnect === "function") observer.disconnect();
    observer = null;
    if (timer !== null) root.clearInterval(timer);
    timer = null;
  }

  function fail(message) {
    const document = doc();
    const message$ = document && document.getElementById("connect-msg");
    if (message$) {
      message$.textContent = message;
      message$.className = "msg err";
    }
    // The gate is still the page, so a second Connect can try again. The session that just
    // failed is remembered, so the watcher does not repeat it on a timer; a new successful
    // connect hands over a new session object and that one is tried.
    if (!watching) start();
  }

  async function begin(session) {
    grafting = true;
    attempted = session;
    stopWatching();
    // Captured before install so the graft itself cannot be routed through the bridge.
    const send = root.fetch;
    try {
      await graft(session, send);
      grafted = true;
      grafting = false;
    } catch (error) {
      if (root.PlayerApi && root.PlayerApi.installed()) root.PlayerApi.uninstall();
      grafting = false;
      grafted = false;
      fail("Connected, but the library could not open: " + reasonOf(error));
    }
  }

  function check() {
    if (grafted || grafting) return;
    const connect = root.PlayerConnect;
    if (!connect || typeof connect.current !== "function") return;
    const session = connect.current();
    if (!session || !session.credentials || session === attempted) return;
    begin(session);
  }

  function start() {
    if (watching || grafted || grafting) return;
    const document = doc();
    if (!document) return;
    watching = true;
    if (!root.PlayerApi) {
      fail("The library bridge is missing from this build (api-bridge.js was not loaded)");
      return;
    }
    const message = document.getElementById("connect-msg");
    if (message && typeof root.MutationObserver === "function") {
      observer = new root.MutationObserver(check);
      observer.observe(message, { childList: true, characterData: true, subtree: true });
    }
    timer = root.setInterval(check, WATCH_MS);
    check();
  }

  root.PlayerLibraryBoot = {
    start: start,
    stop: stopWatching,
    check: check,
    after: after,
    hide: hide,
    defaults: defaults,
    patch: patch,
    // The lists themselves, so a test can read what was decided rather than a paraphrase.
    HIDDEN: HIDDEN,
    DEFAULTS: DEFAULTS,
    grafted: () => grafted,
    watching: () => watching,
    problem: () => problem,
  };

  if (root.document && typeof root.document.getElementById === "function") {
    if (root.document.readyState === "loading" && typeof root.addEventListener === "function") {
      root.addEventListener("DOMContentLoaded", start);
    } else {
      start();
    }
  }
})(typeof window === "undefined" ? globalThis : window);