// The playback layer of the player shell: what the OS is told about the song that is playing,
// what it may do about it, and the single report the server is owed for the play.
//
// Everything here is fake on purpose — a fake `navigator.mediaSession`, a fake audio element, a
// fake queue and a recorder in place of the server — so what is pinned is this module's own
// behaviour, not a browser and certainly not a network. No audio file is opened and no request
// leaves the process.
//
// The one thing a test cannot settle is what a real head unit draws; `docs/player/playback.md`
// says so.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "web", "js", "playback.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}
function equal(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  check(name, ok, ok ? "" : "got " + JSON.stringify(got) + " want " + JSON.stringify(want));
}

// ---- fakes ---------------------------------------------------------------------------------
function fakeAudio(overrides) {
  const listeners = {};
  const audio = Object.assign({
    paused: true, currentTime: 0, duration: NaN, playbackRate: 1, ended: false,
    plays: 0, pauses: 0, src: "",
    addEventListener(name, handler) {
      (listeners[name] = listeners[name] || []).push(handler);
    },
    play() { audio.paused = false; audio.plays += 1; return Promise.resolve(); },
    pause() { audio.paused = true; audio.pauses += 1; },
    emit(name) { for (const handler of (listeners[name] || []).slice()) handler(); },
    listening: listeners,
  }, overrides || {});
  return audio;
}

function fakeSession() {
  const session = {
    metadata: null, playbackState: "none", tags: [], positions: [], handlers: new Map(),
    setActionHandler(action, handler) { session.handlers.set(action, handler); },
    setPositionState(point) {
      session.positions.push({ duration: point.duration, rate: point.playbackRate, position: point.position });
    },
  };
  return session;
}

function fakeDocument() {
  const doc = { visibilityState: "visible", listeners: {}, addEventListener(name, handler) {
    (doc.listeners[name] = doc.listeners[name] || []).push(handler);
  }, emit(name) { for (const handler of (doc.listeners[name] || []).slice()) handler(); } };
  return doc;
}

// The app's queue, as small as the adapter contract: current row, order, step, reorder.
function fakeQueue(rows, current) {
  const log = { steps: [], reasons: [], orders: [] };
  const queue = {
    order: rows.map(row => row.id),
    current: current == null ? null : current,
    log,
    row() { return rows.find(row => row.id === queue.current) || null; },
    ids() { return queue.order.slice(); },
    index() { return queue.order.indexOf(queue.current); },
    setOrder(ids) { log.orders.push(ids.slice()); queue.order = ids.slice(); },
    play(id) { queue.current = id; },
    step(delta, reason) {
      log.steps.push(delta);
      log.reasons.push(reason);
      if (!queue.order.length) return;
      // The app's own `stepTrack`: modulo around the queue, and a track that is not one of the
      // queued ones (-1) steps forward onto the queue's first song.
      const at = queue.order.indexOf(queue.current);
      if (at < 0 && delta < 0) return;
      const from = at < 0 ? -1 : at;
      queue.current = queue.order[(from + delta + queue.order.length) % queue.order.length];
    },
  };
  return queue;
}

function sandbox() {
  const ctx = {
    URL, URLSearchParams, Promise, console, Math, Map, Number, Error, Array, String, JSON,
    location: { href: "https://music.example.com/index.html" },
    performance: { now: () => 0 },
  };
  vm.createContext(ctx);
  vm.runInContext(SOURCE, ctx, { filename: "playback.js" });
  return { ctx, api: vm.runInContext("this.PlayerPlayback", ctx) };
}

function harness(options) {
  const config = options || {};
  const { ctx, api } = sandbox();
  const audio = fakeAudio();
  const session = fakeSession();
  const doc = fakeDocument();
  const rows = config.rows || [
    { id: 1, title: "One", artist_name: "Artist A", album: "Album A", artwork: "/rest/getCoverArt.view?id=1&size=512" },
    { id: 2, title: "Two", artist_name: "Artist B", album: "", artwork: "",
      playlists: [{ title: "Set B" }] },
    { id: 3, title: "Three", artist_name: "Artist C", album: "", artwork: "" },
  ];
  const queue = fakeQueue(rows, config.current === undefined ? 1 : config.current);
  const reports = [];
  const reloads = { count: 0 };
  const clock = { now: 0 };
  const Metadata = class {
    constructor(tag) { session.tags.push(tag); Object.assign(this, tag); }
  };
  const controller = api.attach(Object.assign({
    audio, queue, mediaSession: session, document: doc, metadata: Metadata,
    report: entry => reports.push(entry),
    clock: () => clock.now,
    random: config.random || (() => 0),
    // The app's own reload path: one fresh request for the same track.
    reloadSource: () => {
      reloads.count += 1;
      audio.src = "/api/track/1/audio?retry=1";
      audio.paused = true;
      return true;
    },
  }, config.attach || {}));
  return { api, audio, session, doc, queue, reports, clock, controller, ctx, reloads,
    tags: () => session.tags.length,
    last: () => session.tags[session.tags.length - 1] };
}

async function main() {
  // ---- the pure parts ----------------------------------------------------------------------
  {
    const { api } = sandbox();
    equal("half a short song is its own threshold", api.thresholdSeconds(100), 50);
    equal("a long song is capped at four minutes", api.thresholdSeconds(900), 240);
    equal("an unknown duration falls back to four minutes", api.thresholdSeconds(NaN), 240);
    equal("a position is never negative", api.clampPosition(-5, 100), 0);
    equal("a position never passes the end", api.clampPosition(200, 100), 100);
    equal("a position without a duration is only floored", api.clampPosition(5, NaN), 5);
    equal("a png cover is declared a png", api.artworkType("https://h/c.png?size=512"), "image/png");
    equal("a webp cover is declared a webp", api.artworkType("/c.webp"), "image/webp");
    equal("a Subsonic cover with no suffix is a jpeg",
      api.artworkType("https://h/rest/getCoverArt.view?id=7"), "image/jpeg");
    equal("no session means no scrobble URL, rather than a guessed one",
      api.scrobbleUrl(null, { id: 1 }), "");
    equal("the scrobble URL is the protocol's own endpoint and query",
      api.scrobbleUrl({ server: { url: "https://music.example.com/" },
        credentials: { username: "u", password: "p" } },
        { id: 7, submission: true, timeMs: 1234 }),
      "https://music.example.com/rest/scrobble.view?u=u&p=p&v=1.16.1&c=hardlylistening-player"
        + "&f=json&id=7&submission=true&time=1234");
    equal("a start notification says submission=false",
      api.scrobbleUrl({ server: { url: "https://h" }, credentials: { username: "u", password: "p" } },
        { id: 7, submission: false, timeMs: 0 }),
      "https://h/rest/scrobble.view?u=u&p=p&v=1.16.1&c=hardlylistening-player"
        + "&f=json&id=7&submission=false&time=0");
  }
  {
    const { api } = sandbox();
    let threw = false;
    try { api.attach({}); } catch (error) { threw = /audio element/.test(error.message); }
    check("attaching without the app's element is refused, not silently ignored", threw);
  }

  // ---- the tag -----------------------------------------------------------------------------
  {
    const h = harness();
    equal("the tag is published for the track that is loaded", h.last().title, "One");
    equal("the artist comes with it", h.last().artist, "Artist A");
    equal("the album is the release the server named", h.last().album, "Album A");
    equal("artwork is absolute, because a car fetches it itself",
      h.last().artwork[0].src, "https://music.example.com/rest/getCoverArt.view?id=1&size=512");
    equal("artwork declares its type, or WebKit draws an empty square",
      h.last().artwork[0].type, "image/jpeg");
    equal("artwork declares a size", h.last().artwork[0].sizes, "512x512");

    const before = h.tags();
    h.session.handlers.get("nexttrack")();
    check("a skip publishes a new tag", h.tags() > before, String(h.tags()));
    equal("and it is the new song", h.last().title, "Two");
    equal("a song with no release publishes the set it came from", h.last().album, "Set B");
    equal("a row with neither a release nor a set publishes no album",
      h.api.albumFor({ id: 9, title: "Nine" }), "");
    equal("a song with no artwork publishes no artwork entries", h.last().artwork.length, 0);

    const afterSkip = h.tags();
    h.audio.emit("loadedmetadata");
    check("the tag is re-published when the new resource is attached", h.tags() > afterSkip,
      String(h.tags()));
    equal("and still names the new song", h.last().title, "Two");
    const afterLoaded = h.tags();
    h.audio.emit("playing");
    check("the tag is re-published when playback starts", h.tags() > afterLoaded, String(h.tags()));
    const afterPlaying = h.tags();
    h.audio.emit("emptied");
    check("the tag is re-published when WebKit drops it on `emptied`", h.tags() > afterPlaying,
      String(h.tags()));
  }

  // ---- the transport -----------------------------------------------------------------------
  {
    const h = harness({ rows: [
      { id: 1, title: "One" }, { id: 2, title: "Two" }, { id: 3, title: "Three" },
    ], current: 2 });
    const handlers = h.session.handlers;
    equal("play, pause, next, previous and the timeline seek are all claimed",
      ["play", "pause", "nexttrack", "previoustrack", "seekto"]
        .filter(name => typeof handlers.get(name) === "function").length, 5);
    check("the ±10 s pair is cleared, because it costs the skip buttons",
      handlers.has("seekbackward") && handlers.get("seekbackward") === null
        && handlers.has("seekforward") && handlers.get("seekforward") === null,
      JSON.stringify([...handlers.keys()]));

    handlers.get("play")();
    equal("the lock screen's play starts the app's own element", h.audio.plays, 1);
    equal("and says so", h.session.playbackState, "playing");
    handlers.get("pause")();
    equal("the lock screen's pause stops it", h.audio.pauses, 1);
    equal("and says so", h.session.playbackState, "paused");

    handlers.get("nexttrack")();
    equal("next is one step forward in the app's queue", h.queue.log.steps, [1]);
    equal("a skip asked for by hand is a manual step", h.queue.log.reasons, ["manual"]);
    equal("and the queue followed it", h.queue.current, 3);
    equal("and the tag followed it", h.last().title, "Three");

    // Past the restart point, previous restarts: the queue must not move.
    h.audio.currentTime = 30;
    handlers.get("previoustrack")();
    equal("previous past three seconds restarts the song", h.audio.currentTime, 0);
    equal("and leaves the queue exactly where it was", h.queue.log.steps, [1]);
    equal("and leaves the row where it was", h.queue.current, 3);

    h.audio.currentTime = 1;
    handlers.get("previoustrack")();
    equal("previous before three seconds steps back", h.queue.log.steps, [1, -1]);
    equal("and the row went back with it", h.queue.current, 2);

    // A seek is the timeline's, not the queue's.
    h.audio.duration = 100;
    const orderBefore = h.queue.ids();
    handlers.get("seekto")({ seekTime: 42 });
    equal("a timeline seek moves the element", h.audio.currentTime, 42);
    equal("a seek is not a queue operation", h.queue.log.steps, [1, -1]);
    equal("a seek does not reorder the queue", h.queue.ids(), orderBefore);
    equal("a seek does not change the row", h.queue.current, 2);
    handlers.get("seekto")({ seekTime: 500 });
    equal("a seek past the end is clamped, never past the duration", h.audio.currentTime, 100);
    handlers.get("seekto")({ seekTime: -10 });
    equal("a seek before the start is clamped to zero", h.audio.currentTime, 0);
    handlers.get("seekto")({});
    equal("a seek with no time is ignored", h.audio.currentTime, 0);
  }

  // ---- the position ------------------------------------------------------------------------
  {
    const h = harness();
    h.audio.duration = 100;
    h.audio.currentTime = 10;
    h.audio.emit("timeupdate");
    equal("a position is published as it changes, not only on play",
      h.session.positions.map(point => point.position), [10]);
    equal("the duration goes with it", h.session.positions[0].duration, 100);
    equal("and the playback rate", h.session.positions[0].rate, 1);

    h.clock.now = 500;
    h.audio.currentTime = 9;
    h.audio.emit("timeupdate");
    equal("pushes are throttled to about 1 Hz", h.session.positions.length, 1);

    h.clock.now = 1500;
    h.audio.currentTime = 9;
    h.audio.emit("timeupdate");
    equal("a backward blip with no seek keeps the high-water mark",
      h.session.positions.map(point => point.position), [10, 10]);

    h.clock.now = 3000;
    h.audio.currentTime = -4;
    h.audio.emit("timeupdate");
    equal("a negative currentTime is never published",
      h.session.positions[h.session.positions.length - 1].position, 10);

    h.clock.now = 4500;
    h.audio.currentTime = 12;
    h.audio.playbackRate = 2;
    h.audio.emit("timeupdate");
    const values = h.session.positions.map(point => point.position);
    equal("the timeline moved on", values, [10, 10, 10, 12]);
    check("no published position is ever negative", values.every(value => value >= 0),
      JSON.stringify(values));
    check("no published position ever goes backwards",
      values.every((value, index) => index === 0 || value >= values[index - 1]),
      JSON.stringify(values));
    equal("the rate is published with the position",
      h.session.positions[h.session.positions.length - 1].rate, 2);

    // A real seek may go backwards, and only a real seek may.
    h.clock.now = 6000;
    h.controller.seek(4);
    equal("a seek publishes the new position at once",
      h.session.positions[h.session.positions.length - 1].position, 4);
  }
  {
    const h = harness();
    h.audio.currentTime = 30;
    h.audio.emit("timeupdate");
    h.controller.publish();
    equal("with no duration there is nothing to publish", h.session.positions.length, 0);
  }

  // ---- shuffle -----------------------------------------------------------------------------
  {
    const rows = [1, 2, 3, 4, 5].map(id => ({ id, title: "Song " + id }));
    const h = harness({ rows, current: 3 });
    const original = h.queue.ids();
    h.controller.setShuffle(true);
    const shuffled = h.queue.ids();
    equal("shuffle keeps the song that is playing exactly where it is", shuffled[2], 3);
    equal("shuffle keeps the current index", h.queue.index(), 2);
    equal("shuffle is a permutation, not a new queue",
      shuffled.slice().sort((a, b) => a - b), original.slice().sort((a, b) => a - b));
    check("shuffle actually changed the order", shuffled.join() !== original.join(),
      JSON.stringify(shuffled));
    equal("the reorder goes through the app's own commit, once", h.queue.log.orders.length, 1);
    equal("shuffle reports its state", h.controller.shuffle(), true);

    h.controller.setShuffle(false);
    equal("shuffle off restores the order it started from", h.queue.ids(), original);
    equal("and reports that too", h.controller.shuffle(), false);

    }
  {
    // Turning it on twice must change something, or the button is a lie. The random source
    // varies here the way a real one does: with a constant source there are only as many
    // permutations as the tail has, and no implementation could promise a different one.
    let seed = 7;
    const varying = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
    const rows = [1, 2, 3, 4, 5, 6].map(id => ({ id, title: "Song " + id }));
    const h = harness({ rows, current: 3, random: varying });
    h.controller.setShuffle(true);
    const first = h.queue.ids().join();
    equal("shuffle keeps the current song current with a real random source too",
      h.queue.ids()[2], 3);
    h.controller.setShuffle(false);
    h.controller.setShuffle(true);
    check("shuffle off and on again is a different order", h.queue.ids().join() !== first,
      h.queue.ids().join() + " vs " + first);
    equal("and still keeps the current song current", h.queue.ids()[2], 3);
  }

  // ---- repeat ------------------------------------------------------------------------------
  {
    const rows = [{ id: 1, title: "One" }, { id: 2, title: "Two" }];
    const h = harness({ rows, current: 1 });
    h.audio.duration = 100;
    h.audio.currentTime = 30;
    equal("repeat starts off", h.controller.repeat(), "off");
    h.controller.setRepeat("one");
    equal("repeat one is a state the controller reports", h.controller.repeat(), "one");

    h.audio.emit("ended");
    h.audio.emit("ended");
    h.audio.emit("ended");
    equal("repeat one plays the same song again, every time", h.audio.plays, 3);
    equal("repeat one never steps the queue", h.queue.log.steps, []);
    equal("and the row is still the same song", h.queue.current, 1);
    equal("a restart is not a second play to report", h.reports.length, 1);
    check("and the one report is a submission", h.reports[0].submission === true,
      JSON.stringify(h.reports[0]));

    // Repeat all: at the end, the app's own step wraps.
    h.queue.current = 2;
    h.controller.setRepeat("all");
    h.audio.emit("ended");
    equal("repeat all wraps at the end through the same one step", h.queue.log.steps, [1]);
    equal("and lands back on the first song", h.queue.current, 1);

    // Repeat off: the end of the queue stops.
    h.queue.current = 2;
    h.controller.setRepeat("off");
    const played = h.audio.plays;
    h.audio.emit("ended");
    equal("repeat off does not step past the end", h.queue.log.steps, [1]);
    equal("repeat off does not start anything", h.audio.plays, played);

    equal("cycling goes off → all → one → off", [
      h.controller.cycleRepeat(), h.controller.cycleRepeat(), h.controller.cycleRepeat(),
    ], ["all", "one", "off"]);
    equal("an unknown repeat mode is off, not a crash", h.controller.setRepeat("sideways"), "off");
  }
  {
    // A song played from the library that is not one of the queued ones: the app steps forward
    // onto the queue's first song, and the player has to do the same rather than stop.
    const rows = [{ id: 1, title: "One" }, { id: 2, title: "Two" }];
    const h = harness({ rows, current: null });
    h.controller.setRepeat("off");
    h.audio.duration = 100;
    h.audio.currentTime = 100;
    h.audio.emit("ended");
    equal("a song outside the queue hands over to the queue's first song", h.queue.current, 1);
    equal("and the tag follows it", h.last().title, "One");
  }
  {
    // An empty queue stops, and does not ask the app to step into nothing.
    const h = harness({ rows: [], current: null });
    h.controller.setRepeat("all");
    h.audio.duration = 100;
    h.audio.emit("ended");
    equal("an empty queue does not step", h.queue.log.steps, []);
  }

  // ---- the scrobble ------------------------------------------------------------------------
  {
    const rows = [{ id: 1, title: "One" }, { id: 2, title: "Two" }];
    const h = harness({ rows, current: 1 });
    h.audio.duration = 100;               // threshold: half of 100 = 50 s
    h.audio.currentTime = 10;
    h.audio.emit("timeupdate");
    h.audio.currentTime = 49;
    h.audio.emit("timeupdate");
    equal("nothing is reported before the threshold", h.reports.length, 0);

    h.audio.currentTime = 50;
    h.audio.emit("timeupdate");
    equal("the play is reported once at half the song", h.reports.length, 1);
    equal("and it is a submission", h.reports[0], { id: 1, submission: true, timeMs: 50000 });

    h.audio.emit("timeupdate");
    h.audio.emit("timeupdate");
    equal("and not again while it keeps playing", h.reports.length, 1);

    // The scrub that used to report the same play twice.
    h.controller.seek(5);
    h.audio.emit("seeked");
    h.audio.emit("timeupdate");
    h.audio.currentTime = 70;
    h.audio.emit("timeupdate");
    equal("seeking back and playing on does not report a second play", h.reports.length, 1);
    h.audio.emit("ended");
    equal("and reaching the end does not report a third", h.reports.length, 1);

    // The next track is a new play.
    h.audio.currentTime = 60;
    h.audio.emit("timeupdate");
    equal("a new track is reported as its own play", h.reports.length, 2);
    equal("and names the new track", h.reports[1].id, 2);
  }
  {
    const h = harness({ rows: [{ id: 1, title: "Long" }], current: 1 });
    h.audio.duration = 900;
    h.audio.currentTime = 239;
    h.audio.emit("timeupdate");
    equal("a fifteen minute song is not reported at 239 s", h.reports.length, 0);
    h.audio.currentTime = 241;
    h.audio.emit("timeupdate");
    equal("it is reported at four minutes, not at half of fifteen", h.reports.length, 1);
    equal("with the position that was reached", h.reports[0].timeMs, 241000);
  }
  {
    // The now-playing notification is a second report of the same play, so it is off unless a
    // server that honours `submission` asks for it.
    const h = harness({ rows: [{ id: 1, title: "One" }], current: 1 });
    h.audio.duration = 100;
    h.audio.emit("play");
    equal("no start notification by default", h.reports.length, 0);

    const asked = harness({ rows: [{ id: 1, title: "One" }], current: 1,
      attach: { announce: true } });
    asked.audio.duration = 100;
    asked.audio.emit("play");
    asked.audio.emit("play");
    equal("a server that asks for it gets one start notification", asked.reports.length, 1);
    equal("and it is marked as a notification, not a submission",
      asked.reports[0], { id: 1, submission: false, timeMs: 0 });
  }

  // ---- backgrounded, then back -------------------------------------------------------------
  {
    const rows = [{ id: 1, title: "One" }, { id: 2, title: "Two" }];
    const h = harness({ rows, current: 1 });
    h.audio.duration = 200;               // threshold: half of 200 = 100 s
    h.audio.paused = false;               // it was playing when the page went away
    h.audio.currentTime = 10;
    h.audio.emit("timeupdate");
    equal("nothing reported yet", h.reports.length, 0);

    const orderBefore = h.queue.ids();
    const rowBefore = h.queue.current;
    h.doc.visibilityState = "hidden";
    h.doc.emit("visibilitychange");
    equal("going hidden reports nothing by itself", h.reports.length, 0);

    // The suspension: no timeupdate, no timers to miss — the position simply moved on.
    h.audio.currentTime = 150;
    const positionsBeforeResume = h.session.positions.length;
    h.doc.visibilityState = "visible";
    h.doc.emit("visibilitychange");
    equal("a resume reports the play that passed the threshold while hidden",
      h.reports.length, 1);
    equal("with the position the element is actually at", h.reports[0].timeMs, 150000);
    equal("the queue was not touched by the suspension", h.queue.ids(), orderBefore);
    equal("the row did not change", h.queue.current, rowBefore);
    equal("the queue did not step", h.queue.log.steps, []);
    check("the position is re-published from the element, not from a counter",
      h.session.positions.length > positionsBeforeResume,
      h.session.positions.length + " vs " + positionsBeforeResume);
    equal("the timeline published on resume is the element's own",
      h.session.positions[h.session.positions.length - 1].position, 150);
    check("the transport is re-claimed on resume",
      ["play", "pause", "nexttrack", "previoustrack", "seekto"]
        .every(name => typeof h.session.handlers.get(name) === "function"));
    equal("the state still says it is playing", h.session.playbackState, "playing");

    h.doc.emit("visibilitychange");
    equal("a second resume cannot report the same play twice", h.reports.length, 1);
    h.controller.refresh();
    equal("and neither can an explicit refresh", h.reports.length, 1);

    // A track that ended while the page was away is reported, not advanced by guesswork.
    h.audio.ended = true;
    h.doc.emit("visibilitychange");
    equal("a missed end is reported as a play, not as an advance", h.reports.length, 1);
    equal("and nothing was stepped", h.queue.log.steps, []);
  }

  // ---- interrupted, then recovered ---------------------------------------------------------
  // A car head unit that leaves the now-playing screen deactivates the session. WebKit then
  // refuses a `play()` or pauses the element on its own. Neither is the listener's decision, and
  // treating either as one leaves a head unit showing a paused song until someone picks up the
  // phone. The tests below pin the difference — and pin that a real pause is never fought.
  {
    const rows = [{ id: 1, title: "One" }, { id: 2, title: "Two" }];
    const h = harness({ rows, current: 1 });
    h.audio.src = "/api/track/1/audio";
    h.audio.duration = 100;
    equal("nothing is wanted before anyone asks", h.controller.state().wanted, "paused");

    h.audio.play = () => Promise.reject(new DOMException("NotAllowed", "NotAllowedError"));
    h.controller.play();
    await Promise.resolve();
    await Promise.resolve();
    equal("a refusal still counts as wanting to play", h.controller.state().wanted, "playing");
    equal("and is recorded as an interruption", h.controller.state().interrupted, true);

    // The next signal is the retry: a refusal is often the session still deactivating.
    h.audio.play = () => { h.audio.paused = false; h.audio.plays += 1; return Promise.resolve(); };
    h.doc.visibilityState = "visible";
    h.doc.emit("visibilitychange");
    equal("coming back resumes the song that was refused", h.audio.plays, 1);
    equal("the session says it is playing again", h.session.playbackState, "playing");
    equal("and the interruption is over", h.controller.state().interrupted, false);

    // A pause that no call of this module asked for: the platform's own.
    h.audio.paused = true;
    h.audio.emit("pause");
    equal("a pause nobody asked for is an interruption", h.controller.state().interrupted, true);
    equal("and does not become the listener's intent", h.controller.state().wanted, "playing");
    const plays = h.audio.plays;
    h.doc.emit("visibilitychange");
    equal("the next signal resumes it", h.audio.plays, plays + 1);

    // A pause the listener asked for stays paused, forever.
    h.session.handlers.get("pause")();
    h.audio.emit("pause");
    equal("a pause the listener asked for is not an interruption",
      h.controller.state().interrupted, false);
    equal("and the intent is theirs", h.controller.state().wanted, "paused");
    const afterPause = h.audio.plays;
    h.doc.emit("visibilitychange");
    h.controller.refresh();
    equal("and is never undone by a resume", h.audio.plays, afterPause);

    // A refused play is asked a second time at once. No timer: this module schedules nothing on
    // one, because a suspended page runs no timers at all.
    const again = harness({ rows, current: 1 });
    again.audio.src = "/api/track/1/audio";
    let attempts = 0;
    again.audio.play = () => { attempts += 1; return Promise.reject(new Error("NotAllowed")); };
    again.controller.play();
    await Promise.resolve();
    await Promise.resolve();
    equal("a refusal is asked again, immediately", attempts, 2);
    again.controller.play();
    await Promise.resolve();
    await Promise.resolve();
    equal("and never a third time, so a refusal cannot become a loop", attempts, 3);
  }

  // ---- a route that dies mid-track ---------------------------------------------------------
  {
    const rows = [{ id: 1, title: "One" }];
    const h = harness({ rows, current: 1 });
    h.audio.src = "/api/track/1/audio";
    h.controller.play();
    const plays = h.audio.plays;
    h.audio.error = { code: 2 };
    h.audio.emit("error");
    equal("a dropped connection asks the app for one reload", h.reloads.count, 1);
    equal("and that reload is played", h.audio.plays, plays + 1);
    h.audio.emit("error");
    equal("a second error is not a loop", h.reloads.count, 1);
  }
  {
    // A source the platform cannot use fails the same way twice: a reload there is a loop.
    const h = harness({ rows: [{ id: 1, title: "One" }], current: 1 });
    h.audio.src = "/api/track/1/audio";
    h.controller.play();
    h.audio.error = { code: 4 };
    h.audio.emit("error");
    equal("an unsupported source is not reloaded", h.reloads.count, 0);
  }
  {
    // The module never picks a source. An app with nothing to offer is not played into the void.
    const h = harness({ rows: [{ id: 1, title: "One" }], current: 1,
      attach: { reloadSource: () => false } });
    h.audio.src = "/api/track/1/audio";
    h.controller.play();
    h.audio.error = { code: 2 };
    const plays = h.audio.plays;
    h.audio.emit("error");
    equal("an app with nothing to reload is left alone", h.audio.plays, plays);
    equal("and the failure is still written down", h.controller.state().interrupted, true);
  }

  // ---- what one drive leaves behind --------------------------------------------------------
  {
    const events = [];
    const rows = [{ id: 1, title: "One" }, { id: 2, title: "Two" }];
    const h = harness({ rows, current: 1, attach: { log: event => events.push(event),
      sourceKind: () => "stream" } });
    h.audio.src = "/api/track/1/audio";
    h.audio.play = () => Promise.reject(new Error("NotAllowed"));
    h.controller.play();
    await Promise.resolve();
    await Promise.resolve();
    h.controller.play();
    await Promise.resolve();
    await Promise.resolve();
    const refused = events.filter(event => event.event === "refused");
    equal("a refusal is written down once per track, not once per attempt", refused.length, 1);
    check("the line names the track", refused[0] && refused[0].trackId === 1,
      JSON.stringify(refused[0]));
    check("and says whether the bytes were ours or the server's",
      refused[0] && refused[0].srcKind === "stream", JSON.stringify(refused[0]));
    check("and carries no URL at all",
      refused[0] && !Object.keys(refused[0]).some(key => /src$|url|cookie/i.test(key)),
      JSON.stringify(refused[0]));
    check("and reports what the element itself thought",
      refused[0] && "readyState" in refused[0] && "networkState" in refused[0],
      JSON.stringify(refused[0]));

    h.audio.paused = true;
    h.audio.emit("pause");
    check("a platform pause is written down too",
      events.some(event => event.event === "interrupted"), JSON.stringify(events));
    equal("but a listener's own pause is not",
      (h.controller.pause(), events.filter(event => event.event === "interrupted").length), 1);
  }

  // ---- a manual skip throws the item away ---------------------------------------------------
  // A skip destroys the element, so the platform is told the item is gone rather than paused:
  // a head unit told "paused" keeps the old item latched, which is the frozen cover and title a
  // skip used to leave. The pause that follows is the module's own and is never an interruption.
  {
    const rows = [{ id: 1, title: "One" }, { id: 2, title: "Two" }];
    const h = harness({ rows, current: 1 });
    h.audio.src = "/api/track/1/audio";
    h.controller.play();
    await Promise.resolve();
    h.controller.release();
    equal("a skip declares the item gone", h.session.playbackState, "none");
    equal("and pauses the element", h.audio.paused, true);
    h.audio.emit("pause");
    equal("the module's own teardown is not an interruption", h.controller.state().interrupted, false);
    equal("and the intent to play is untouched", h.controller.state().wanted, "playing");
    const plays = h.audio.plays;
    h.doc.emit("visibilitychange");
    equal("so coming back does not fight the skip", h.audio.plays, plays);
    // After the teardown the next resource plays as usual.
    h.controller.play();
    await Promise.resolve();
    equal("and the next play is the listener's again", h.controller.state().wanted, "playing");
  }
  {
    // Event order is not guaranteed: iOS queues the teardown's pause, and the next track can
    // start before it is delivered. A pause for an element that is now playing is stale.
    const h = harness({ rows: [{ id: 1, title: "One" }, { id: 2, title: "Two" }], current: 1 });
    h.audio.src = "/api/track/1/audio";
    h.controller.play();
    await Promise.resolve();
    h.controller.release();
    h.audio.play();          // the next track, before the queued pause lands
    h.audio.emit("pause");
    equal("a pause that arrives after the next play started is ignored",
      h.controller.state().interrupted, false);
    equal("and is not published over the new state", h.session.playbackState, "none");
  }

  // ---- the adapter over the app's queue ----------------------------------------------------
  {
    const { ctx, api } = sandbox();
    const log = { steps: [], reasons: [], commits: 0 };
    ctx.libQueue = [{ id: 7, title: "Seven" }, { id: 8, title: "Eight" }];
    ctx.libAll = [{ id: 9, title: "Nine" }];
    ctx.playingId = 8;
    ctx.playFromLibrary = id => { ctx.playingId = id; };
    ctx.stepTrack = (delta, reason) => { log.steps.push(delta); log.reasons.push(reason); };
    ctx.commitPlaybackQueue = () => { log.commits += 1; };

    const queue = api.appQueue();
    equal("the adapter reads the row that is playing", queue.row().title, "Eight");
    equal("and the order the app actually has", queue.ids(), [7, 8]);
    equal("and its index", queue.index(), 1);
    queue.step(-1, "manual");
    equal("a step is the app's own step", log.steps, [-1]);
    equal("and carries why it happened", log.reasons, ["manual"]);
    queue.play(7);
    equal("a start is the app's own start path", ctx.playingId, 7);
    queue.setOrder([8, 7]);
    equal("a reorder is written back into the app's queue", ctx.libQueue.map(row => row.id), [8, 7]);
    equal("and committed through the app's own commit", log.commits, 1);
    queue.setOrder([8, 999]);
    equal("an order naming a song the app does not have is refused, never partially applied",
      ctx.libQueue.map(row => row.id), [8, 7]);
    equal("and nothing was committed for it", log.commits, 1);

    const audio = fakeAudio();
    const session = fakeSession();
    const controller = api.attach({
      audio, queue: api.appQueue(), mediaSession: session,
      metadata: class { constructor(tag) { session.tags.push(tag); } },
      report: () => {},
    });
    equal("the adapter feeds the tag from the app's own row",
      session.tags[session.tags.length - 1].title, "Seven");
    const currentAt = ctx.libQueue.findIndex(row => row.id === ctx.playingId);
    controller.setShuffle(true);
    equal("shuffling through the adapter keeps the playing song current",
      ctx.libQueue.findIndex(row => row.id === ctx.playingId), currentAt);
    equal("and the app's commit ran for it", log.commits, 2);
  }

  if (failures) {
    console.log(failures + " failed");
    process.exit(1);
  }
  console.log("all playback checks passed");
}

main().catch(error => {
  console.log("FAIL the harness threw — " + (error && error.stack || error));
  process.exit(1);
});