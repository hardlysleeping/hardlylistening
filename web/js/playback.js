/* The playback layer: what the operating system is told about the song that is playing, what
   it is allowed to do about it, and the one report the server is owed for a play.

   It is not a second player. The audio element, the queue, the shuffle order and the repeat
   mode already exist in `web/`; this file drives them through a small adapter
   (`PlayerPlayback.appQueue()`) and owns only the four things the assembled app cannot state
   for itself:

     1. the media session — artwork, title, artist, album — so the lock screen, the notification,
        the car and a Bluetooth head unit have a tag. It is re-published on every load milestone
        as well as on a track change, because WebKit drops the tag when the element's resource is
        detached and a head unit that latched the block never asks again.
     2. the transport commands they send back: play, pause, next, previous, and a seek from the
        lock-screen timeline. `seekto` is claimed; `seekbackward`/`seekforward` are explicitly
        cleared, because registering either replaces the skip buttons with a ±10 s pair.
     3. the position, pushed as it changes rather than once per play, so a head unit's elapsed
        clock is the real one. The timeline never moves backwards without a real seek.
     4. the scrobble, at the point the Subsonic protocol names: `time` is the position, and
        `submission=true` goes out once per track when it has played for half its duration or
        four minutes (whichever is first) or has played to the end.

   Backgrounding, plainly: the *audio element* is what keeps playing when the window is hidden or
   the app is in the background — not JavaScript. A mobile WebView may freeze or throttle timers
   for a backgrounded page, so **nothing here is scheduled on a timer**: no setInterval, no
   setTimeout, no animation frame. Every decision is taken from an event the media element emits
   (`timeupdate`, `ended`, `seeked`) or from the element's own state when the page becomes visible
   again. That is what makes a suspended page resume in the right place instead of replaying a
   queue of stale decisions: there is no queue of decisions to replay. What this cannot fix is the
   process itself — if the platform freezes the WebView, audio stops with it; keeping audio alive
   in the background is the wrapper's job (an audio session or a foreground service, declared
   outside the web assets), not a script's. See `docs/player/playback.md`.

   Loaded as a classic script with no build step, like the rest of the app: `PlayerPlayback` is
   the API, and nothing runs until `attach()` is called with the app's own element and queue, so
   the same file can be exercised with fakes outside a browser. */
(function (root) {
  "use strict";

  const PROTOCOL_VERSION = "1.16.1";
  const CLIENT_NAME = "hardlylistening-player";

  /* The protocol's "played" point: four minutes, or half the song when that comes first. */
  const SCROBBLE_AFTER = 240;
  /* Previous restarts the track before stepping back — the same rule the app's own button uses. */
  const RESTART_AFTER = 3;
  /* Position pushes are throttled to about 1 Hz: `timeupdate` fires around 4 Hz, and a car only
     draws seconds. */
  const POSITION_INTERVAL = 1000;
  /* A timeline that reports 9.8 s after 10 s is jitter, not a seek. Inside this window the
     published value keeps the high-water mark. */
  const POSITION_JITTER = 0.5;
  const ARTWORK_SIZES = "512x512";

  /* What the lock screen is offered. Order matters only for readability. */
  const TRANSPORT = ["play", "pause", "previoustrack", "nexttrack", "seekto"];
  /* Offered nowhere. Registering either of these makes the platform draw ±10 s instead of the
     skip pair, which is the one thing that loses next/previous. */
  const UNCLAIMED = ["seekbackward", "seekforward"];
  const REPEATS = ["off", "all", "one"];

  function text(value) {
    return value == null ? "" : String(value);
  }

  /* Seconds a track must play before it counts. An unknown duration falls back to the four
     minutes the protocol allows, because half of nothing is not a threshold. */
  function thresholdSeconds(duration) {
    const seconds = Number(duration);
    if (!Number.isFinite(seconds) || seconds <= 0) return SCROBBLE_AFTER;
    return Math.min(SCROBBLE_AFTER, seconds / 2);
  }

  /* A position is never negative and never past the end of the track. */
  function clampPosition(at, duration) {
    const value = Number(at);
    const safe = Number.isFinite(value) && value > 0 ? value : 0;
    const length = Number(duration);
    return Number.isFinite(length) && length > 0 ? Math.min(safe, length) : safe;
  }

  /* WebKit draws no artwork entry without a declared type, so the type is read from the URL the
     cover route actually served; anything unrecognised is the JPEG a Subsonic cover route
     returns. */
  function artworkType(url) {
    const clean = text(url).split("#")[0].split("?")[0];
    const found = clean.match(/\.([a-z0-9]{2,4})$/i);
    const extension = found ? found[1].toLowerCase() : "";
    if (extension === "png") return "image/png";
    if (extension === "webp") return "image/webp";
    return "image/jpeg";
  }

  function absolute(url) {
    const value = text(url);
    if (!value) return "";
    if (!root.URL) return value;
    try {
      const base = root.location && root.location.href ? root.location.href : undefined;
      return new root.URL(value, base).href;
    } catch (error) {
      return "";
    }
  }

  /* The album, as the app's own second line defines it: the release when the server named one,
     otherwise the set the song was pulled from. 410 of 937 rows in a real library carry no
     release at all, and the app has always named their set on the lock screen and in the car;
     publishing nothing for those rows here would quietly delete a line the app used to show. */
  function albumFor(row) {
    const release = text(row && row.album);
    if (release) return release;
    const sets = row && row.playlists;
    if (!Array.isArray(sets)) return "";
    return sets.map(item => text(item && item.title)).find(Boolean) || "";
  }

  /* The tag, as `MediaMetadata` wants it. */
  function metadataFor(row, options) {
    const config = options || {};
    const cover = typeof config.coverUrl === "function"
      ? config.coverUrl
      : item => (item && item.artwork) || "";
    const src = row ? absolute(cover(row)) : "";
    return {
      title: text(row && row.title) || "Untitled",
      artist: text(row && row.artist_name),
      album: albumFor(row),
      artwork: src ? [{ src: src, sizes: ARTWORK_SIZES, type: artworkType(src) }] : [],
    };
  }

  /* `<server>/rest/scrobble.view`, with the session the connect screen is holding. It answers
     the plain envelope, so `f=json` costs nothing and a client never has to guess. */
  function scrobbleUrl(session, entry) {
    const point = entry || {};
    if (!session || !session.server || !session.credentials) return "";
    const base = text(session.server.url).replace(/\/+$/, "");
    if (!base) return "";
    const query = new root.URLSearchParams({
      u: text(session.credentials.username),
      p: text(session.credentials.password),
      v: PROTOCOL_VERSION,
      c: CLIENT_NAME,
      f: "json",
      id: text(point.id),
      submission: point.submission === false ? "false" : "true",
      time: String(Math.max(0, Math.round(Number(point.timeMs) || 0))),
    });
    return base + "/rest/scrobble.view?" + query.toString();
  }

  /* The default reporter: the session the connect screen kept in memory, or nothing. The
     `keepalive` flag matters on a phone — a page that is being put away still finishes a
     request it has already started. */
  function subsonicReporter(entry) {
    const connect = root.PlayerConnect;
    const session = connect && typeof connect.current === "function" ? connect.current() : null;
    const url = scrobbleUrl(session, entry);
    if (!url || typeof root.fetch !== "function") return;
    root.fetch(url, { method: "GET", cache: "no-store", keepalive: true }).catch(() => {});
  }

  /* The adapter over the queue the app already has. Bare identifiers on purpose: `libQueue`,
     `playingId` and friends are declared with `let` in the app's own classic scripts, so they
     live in the global lexical scope and are *not* properties of `window`. Reading them by name
     is what reaches them; `window.libQueue` would be `undefined` and a second queue would grow
     silently. */
  function appQueue() {
    const rows = () => (typeof libQueue === "undefined" || !libQueue ? [] : libQueue);
    const playing = () => (typeof playingId === "undefined" ? null : playingId);
    const rowOf = id => {
      if (id == null) return null;
      const queued = rows().find(item => item && item.id === id);
      if (queued) return queued;
      const known = typeof libAll === "undefined" || !libAll ? [] : libAll;
      const found = known.find(item => item && item.id === id);
      if (found) return found;
      /* A search result or a release page can hold a row that is in neither the queue nor the
         library's full list, and the app plays it by passing that row's id. Reaching it here is
         what lets the lock-screen tag name the song the listener actually tapped. */
      const shown = typeof currentRows === "undefined" || !currentRows ? [] : currentRows;
      return shown.find(item => item && item.id === id) || null;
    };
    return {
      row: () => rowOf(playing()),
      ids: () => rows().map(item => item && item.id),
      index: () => rows().findIndex(item => item && item.id === playing()),
      setOrder: ids => {
        const byId = new Map(rows().map(item => [item && item.id, item]));
        const order = ids.map(id => byId.get(id)).filter(Boolean);
        // A partial order would drop songs, so it is refused rather than applied.
        if (order.length !== ids.length) return;
        libQueue = order;
        if (typeof commitPlaybackQueue === "function") commitPlaybackQueue();
        if (typeof paintPlaybackQueue === "function") paintPlaybackQueue();
      },
      play: id => {
        if (typeof playFromLibrary === "function") playFromLibrary(id);
      },
      step: delta => {
        if (typeof stepTrack === "function") stepTrack(delta);
      },
    };
  }

  function attach(options) {
    const config = options || {};
    const audio = config.audio;
    if (!audio || typeof audio.addEventListener !== "function") {
      throw new Error("PlayerPlayback.attach needs the app's audio element");
    }
    const queue = config.queue || appQueue();
    const session = config.mediaSession
      || (root.navigator && root.navigator.mediaSession) || null;
    const Metadata = config.metadata || root.MediaMetadata || null;
    const report = typeof config.report === "function" ? config.report : subsonicReporter;
    const preload = typeof config.preload === "function" ? config.preload : null;
    const clock = typeof config.clock === "function" ? config.clock
      : () => (root.performance && typeof root.performance.now === "function"
        ? root.performance.now() : Date.now());
    const random = typeof config.random === "function" ? config.random : Math.random;
    const doc = config.document || root.document || null;
    /* A "now playing" notification is a second report of the same play. Off by default: this
       server's `scrobble` endpoint counts every call it receives whatever `submission` says, so
       announcing a start would count each song twice. Turn it on for a server that honours the
       flag. */
    const announceStarted = config.announce === true;

    const state = {
      shuffle: false,
      repeat: "off",
      base: null,
      trackId: null,
      generation: 0,
      submitted: false,
      announced: false,
      published: { at: -POSITION_INTERVAL, value: 0, epoch: 0 },
      seekEpoch: 0,
    };

    /* The app persists shuffle and repeat in its own storage and hands them in, so a reload
       opens with the buttons it left pressed. This is state only: the queue the app restored is
       already in the order it was saved in, and re-shuffling it at boot would change a queue the
       listener never touched. */
    if (config.shuffle === true) state.shuffle = true;
    if (config.repeat != null) {
      const wanted = text(config.repeat);
      state.repeat = REPEATS.indexOf(wanted) >= 0 ? wanted : "off";
    }

    const handlers = {
      play: play,
      pause: pause,
      previoustrack: previous,
      nexttrack: next,
      seekto: details => {
        if (details && Number.isFinite(details.seekTime)) seek(details.seekTime);
      },
    };

    function rowNow() {
      return typeof queue.row === "function" ? queue.row() : null;
    }
    function orderNow() {
      return typeof queue.ids === "function" ? queue.ids().slice() : [];
    }
    function indexNow() {
      const at = typeof queue.index === "function" ? Number(queue.index()) : -1;
      return Number.isFinite(at) ? at : -1;
    }
    function seconds() {
      const value = Number(audio.currentTime);
      return Number.isFinite(value) && value > 0 ? value : 0;
    }

    /* Claim the transport actions. Re-claimed after every deactivation, because WebKit drops the
       handlers when the session goes inactive — a pause, a background, a PWA resume — and the
       lock screen then falls back to the platform's own ±10 s layout. */
    function claim() {
      if (!session || typeof session.setActionHandler !== "function") return;
      for (const action of UNCLAIMED) {
        try { session.setActionHandler(action, null); } catch (error) { /* not offered */ }
      }
      for (const action of TRANSPORT) {
        try { session.setActionHandler(action, handlers[action]); } catch (error) { /* not offered */ }
      }
    }

    function retag(row) {
      claim();
      if (!row || !session) return;
      const tag = metadataFor(row, config);
      try {
        session.metadata = Metadata ? new Metadata(tag) : tag;
      } catch (error) {
        /* A platform with no MediaMetadata keeps the previous tag rather than showing nothing. */
      }
      if (preload && tag.artwork.length) {
        try { preload(tag.artwork[0].src); } catch (error) { /* warm-up is a nicety */ }
      }
    }

    /* A track change is the one thing that resets the play report and the timeline. Seeks,
       pauses and resumes never do, which is what stops a seek-back from reporting a second play
       and a suspension from going backwards. */
    function sync() {
      const row = rowNow();
      const id = row ? row.id : null;
      if (id !== state.trackId) {
        state.trackId = id;
        state.generation += 1;
        state.submitted = false;
        state.announced = false;
        state.published = { at: -POSITION_INTERVAL, value: 0, epoch: state.seekEpoch };
        retag(row);
      }
      return row;
    }

    function publishState() {
      if (!session) return;
      session.playbackState = audio.paused ? "paused" : "playing";
    }

    function publishPosition(force) {
      sync();
      if (!session || typeof session.setPositionState !== "function") return;
      const duration = Number(audio.duration);
      if (!Number.isFinite(duration) || duration <= 0) return;
      const now = clock();
      if (!force && now - state.published.at < POSITION_INTERVAL) return;
      const seeked = state.published.epoch !== state.seekEpoch;
      let value = clampPosition(audio.currentTime, duration);
      /* No seek happened, so the timeline cannot have moved backwards: keep the high-water mark
         rather than make a head unit's clock jump back. */
      if (!seeked && value < state.published.value - POSITION_JITTER) value = state.published.value;
      value = clampPosition(value, duration);
      const rate = Number(audio.playbackRate);
      try {
        session.setPositionState({
          duration: duration,
          playbackRate: Number.isFinite(rate) && rate > 0 ? rate : 1,
          position: value,
        });
      } catch (error) {
        return;
      }
      state.published = { at: now, value: value, epoch: state.seekEpoch };
    }

    function send(entry) {
      try {
        report(entry);
      } catch (error) {
        /* A missed report is not worth breaking playback. */
      }
    }

    function announce() {
      if (!announceStarted) return;
      const row = sync();
      if (!row || state.announced) return;
      state.announced = true;
      send({ id: row.id, submission: false, timeMs: Math.round(seconds() * 1000) });
    }

    /* The play is reported once. `ended` counts whatever the position, because a song that
       reached its end has been played; every other call has to have passed the threshold. */
    function maybeScrobble(ended) {
      const row = sync();
      if (!row || state.submitted) return;
      const played = seconds();
      if (!ended && played < thresholdSeconds(audio.duration)) return;
      state.submitted = true;
      send({ id: row.id, submission: true, timeMs: Math.round(played * 1000) });
    }

    function advance(delta) {
      sync();
      if (typeof queue.step === "function") queue.step(delta);
      /* The app's step is synchronous, so the new row is already the current one. Tagging it
         now means the car never shows the song that just stopped. */
      sync();
      const row = rowNow();
      if (row) retag(row);
      announce();
      return row;
    }

    function play() {
      sync();
      const started = audio.play();
      if (started && typeof started.catch === "function") started.catch(() => {});
      announce();
      publishState();
      publishPosition(true);
    }

    function pause() {
      audio.pause();
      publishState();
      publishPosition(true);
    }

    function seek(at) {
      sync();
      const wanted = Number(at);
      if (!Number.isFinite(wanted)) return;
      audio.currentTime = clampPosition(wanted, audio.duration);
      /* A seek is not a queue operation: the row, the order and the play count stay exactly as
         they were, so scrubbing past the threshold and back cannot report the same play twice. */
      state.seekEpoch += 1;
      publishPosition(true);
    }

    function next() {
      advance(1);
      publishState();
    }

    function previous() {
      sync();
      /* Before the restart point this is a restart, which is a seek: the queue never moves. */
      if (seconds() > RESTART_AFTER) {
        seek(0);
        return;
      }
      advance(-1);
      publishState();
    }
    /* Neither of those pushes a position: until the new resource reports its own duration the
       element still answers with the previous track's, and a timeline that is briefly the wrong
       length is worse than one that waits for `loadedmetadata`. */

    function ended() {
      sync();
      maybeScrobble(true);
      if (state.repeat === "one") {
        /* One song, again. A restart is a seek, so nothing in the queue is touched and the
           report is not reset — this is still the same play. */
        seek(0);
        play();
        return;
      }
      const order = orderNow();
      const at = indexNow();
      /* A song played from the library that is not one of the queued ones is at -1, which the
         app treats as "before the queue": the queue's first song comes next. Running out of a
         queue you are actually in is the case that stops. */
      const last = !order.length || (at >= 0 && at >= order.length - 1);
      if (!last || (state.repeat === "all" && order.length)) {
        /* The app's own step wraps at the end, so "repeat all" is one call, not a second path. */
        advance(1);
        return;
      }
      publishState();
    }

    /* The page came back. Nothing is replayed: everything is read off the element, which is the
       authority on where the song actually is. A missed `ended` is deliberately *not* guessed at
       — the element stopped too when the page was frozen, and inventing an advance here would
       skip a song or double-play one. */
    function resume() {
      const row = sync();
      claim();
      if (row) retag(row);
      publishState();
      publishPosition(true);
      maybeScrobble(audio.ended === true);
    }

    function shuffleOrder(list) {
      for (let i = list.length - 1; i > 0; i--) {
        const j = Math.floor(random() * (i + 1));
        const swap = list[i];
        list[i] = list[j];
        list[j] = swap;
      }
      return list;
    }

    function setOrder(order) {
      if (typeof queue.setOrder === "function") queue.setOrder(order.slice());
    }

    function restoreOrder(base, order, at) {
      if (!base) return order.slice();
      const rank = new Map(base.map((id, index) => [id, index]));
      const start = at >= 0 ? at + 1 : 0;
      const tail = order.slice(start).sort((left, right) => {
        const a = rank.has(left) ? rank.get(left) : Number.MAX_SAFE_INTEGER;
        const b = rank.has(right) ? rank.get(right) : Number.MAX_SAFE_INTEGER;
        return a - b;
      });
      return order.slice(0, start).concat(tail);
    }

    function mode() {
      if (typeof config.onmode !== "function") return;
      try {
        config.onmode({ shuffle: state.shuffle, repeat: state.repeat });
      } catch (error) { /* a repaint failing is not a playback fault */ }
    }

    /* Shuffle reorders what is still to come and never moves the song that is playing. The
       order it found is kept as the base, so turning it off puts the waiting songs back.
       `anchor` is the index of the song the listener chose: a queue the app has just built is
       shuffled after that song, not after whatever was playing before it. */
    function shuffleQueue(anchor) {
      sync();
      const order = orderNow();
      const wanted = Number(anchor);
      const at = Number.isFinite(wanted) ? wanted : indexNow();
      state.base = order.slice();
      const head = at >= 0 ? order.slice(0, at + 1) : [];
      let tail = at >= 0 ? order.slice(at + 1) : order.slice();
      if (tail.length > 1) {
        const before = tail.join();
        tail = shuffleOrder(tail);
        /* Turning it off and on again has to change something, or the button is a lie. */
        for (let attempt = 0; attempt < 6 && tail.length > 2 && tail.join() === before; attempt++) {
          tail = shuffleOrder(tail);
        }
      }
      state.shuffle = true;
      setOrder(head.concat(tail));
      mode();
      return state.shuffle;
    }

    function setShuffle(on) {
      sync();
      const wanted = !!on;
      if (wanted === state.shuffle) return state.shuffle;
      if (wanted) return shuffleQueue(indexNow());
      const order = orderNow();
      const at = indexNow();
      state.shuffle = false;
      setOrder(restoreOrder(state.base, order, at));
      state.base = null;
      mode();
      return state.shuffle;
    }

    function setRepeat(value) {
      const wanted = text(value);
      state.repeat = REPEATS.indexOf(wanted) >= 0 ? wanted : "off";
      mode();
      return state.repeat;
    }

    function cycleRepeat() {
      return setRepeat(state.repeat === "off" ? "all" : state.repeat === "all" ? "one" : "off");
    }

    function publish() {
      publishPosition(true);
      return state.published.value;
    }

    function detach() {
      if (!session || typeof session.setActionHandler !== "function") return;
      for (const action of TRANSPORT.concat(UNCLAIMED)) {
        try { session.setActionHandler(action, null); } catch (error) { /* already gone */ }
      }
    }

    function listen(name, handler) {
      audio.addEventListener(name, handler);
    }

    /* The element's own milestones. These fire while the window is in the background as long as
       audio is playing and the platform has not frozen the process — which is exactly why the
       tag and the position are re-published here rather than from a timer that would be
       suspended. */
    listen("play", () => { sync(); announce(); publishState(); publishPosition(true); });
    listen("playing", () => { const row = sync(); if (row) retag(row); publishState(); });
    listen("pause", () => { sync(); publishState(); publishPosition(true); });
    listen("loadedmetadata", () => {
      const row = sync();
      if (row) retag(row);
      publishPosition(true);
    });
    listen("durationchange", () => { sync(); publishPosition(true); });
    listen("canplay", () => { const row = sync(); if (row) retag(row); });
    /* A skip detaches the old resource, and WebKit answers that with `emptied` — the same event
       it uses to drop the now-playing tag. Re-tagging here puts it back. */
    listen("emptied", () => { const row = sync(); if (row) retag(row); });
    listen("seeked", () => { sync(); publishPosition(true); });
    listen("ratechange", () => publishPosition(true));
    listen("timeupdate", () => { publishPosition(false); maybeScrobble(false); });
    listen("ended", () => { ended(); publishState(); });

    if (doc && typeof doc.addEventListener === "function") {
      doc.addEventListener("visibilitychange", () => {
        if (doc.visibilityState === "visible") resume();
      });
      if (typeof root.addEventListener === "function") root.addEventListener("pageshow", resume);
    }

    claim();
    sync();
    publishState();
    publishPosition(true);

    return {
      play: play,
      pause: pause,
      next: next,
      previous: previous,
      seek: seek,
      seekTo: seek,
      setShuffle: setShuffle,
      shuffleQueue: shuffleQueue,
      toggleShuffle: () => setShuffle(!state.shuffle),
      setRepeat: setRepeat,
      cycleRepeat: cycleRepeat,
      shuffle: () => state.shuffle,
      repeat: () => state.repeat,
      refresh: resume,
      publish: publish,
      detach: detach,
      /* What the tests read, and what a debug screen would show. */
      state: () => ({
        trackId: state.trackId,
        generation: state.generation,
        submitted: state.submitted,
        announced: state.announced,
        shuffle: state.shuffle,
        repeat: state.repeat,
        position: state.published.value,
      }),
    };
  }

  root.PlayerPlayback = {
    attach: attach,
    appQueue: appQueue,
    metadataFor: metadataFor,
    albumFor: albumFor,
    artworkType: artworkType,
    absolute: absolute,
    thresholdSeconds: thresholdSeconds,
    clampPosition: clampPosition,
    scrobbleUrl: scrobbleUrl,
    PROTOCOL_VERSION: PROTOCOL_VERSION,
    CLIENT_NAME: CLIENT_NAME,
    TRANSPORT: TRANSPORT,
    UNCLAIMED: UNCLAIMED,
    SCROBBLE_AFTER: SCROBBLE_AFTER,
  };
})(typeof window === "undefined" ? globalThis : window);