// The player's library, exercised where it actually runs: `fetch` is stubbed with this server's
// own envelopes (`tests/fixtures/player/envelopes.json`, built by the Python half from
// `subsonic.song()` and `listening.rest()`), so what is tested is the mapping and the refusals,
// not a network and not a browser.
//
// Five things this file is here to prove:
//
//   * the route table is the whole of it — an unmapped path is a 404 with a sentence, never an
//     empty list, and a Subsonic error leaves as an error carrying the server's own message;
//   * the rows the bridge hands over are the rows `web/js/library.js` reads (`album_id`,
//     `artist_name`, `artwork`, `added`), which is checked by loading that file and grouping
//     them, not by paraphrasing its field list here;
//   * `rewriteMediaUrl` is the only URL the page renders, so the password is never in one, and
//     `PlayerApi.log()` records endpoints and statuses only;
//   * `library-boot.js` installs the bridge before it writes the page, and hides only controls it
//     can name;
//   * every `/api/…` literal in the five files the page actually loads is either answered by the
//     table or refused by name. A new call in the app fails this test, by design.
//
// Run: node tests/test_player_library.cjs
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const nodeCrypto = require("crypto");
const page = require("./_page.cjs");

const ROOT = path.join(__dirname, "..");
const read = name => fs.readFileSync(path.join(ROOT, name), "utf8");
const BRIDGE = read("player/shell/api-bridge.js");
const BOOT = read("player/shell/library-boot.js");
const FIXTURE = JSON.parse(read("tests/fixtures/player/envelopes.json"));
const EXPECT = FIXTURE.expectations;

const SESSION = {
  server: "https://music.example.com",
  username: "bazzite",
  password: "s3cret-pass",
};
const ORIGIN = "http://player.invalid";
const PAGE_HTML = "<!doctype html><html><body><div id=\"connect-msg\"></div></body></html>";

let failures = 0;
function check(name, ok, detail) {
  if (ok) {
    console.log("PASS " + name);
  } else {
    failures++;
    console.log("FAIL " + name + (detail ? " — " + detail : ""));
  }
}

function equal(name, actual, expected) {
  const left = JSON.stringify(actual);
  const right = JSON.stringify(expected);
  check(name, left === right, left + " !== " + right);
}

function section(name) {
  console.log("\n-- " + name);
}

const settle = (ms = 30) => new Promise(resolve => setTimeout(resolve, ms));

// ---------------------------------------------------------------- the stub server

/* The envelopes, routed by endpoint. Everything the bridge reads comes out of the fixture, so a
   server that renames a field (`subsonic.song()`) fails the Python half first and this file does
   not quietly test a shape nobody sends. `:id`-keyed envelopes are looked up by the request's own
   `id`, and `getAlbumList2` is paged: offset 0 answers the library, anything past it answers
   empty, which is how the bridge learns it has read the whole thing. */
function makeRouter(options) {
  const settings = options || {};
  const envelopes = JSON.parse(JSON.stringify(FIXTURE.envelopes));
  const playlists = FIXTURE.playlists.map(row => Object.assign({}, row));
  const calls = [];
  const created = [];
  let coverCount = 0;

  function response(body, status) {
    const code = status || 200;
    return {
      ok: code >= 200 && code < 300,
      status: code,
      headers: { get: () => "application/json" },
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  }

  function identity() {
    return {
      status: "ok", version: FIXTURE.server.version, type: FIXTURE.server.type,
      serverVersion: FIXTURE.server.serverVersion, openSubsonic: true,
    };
  }

  function env(payload) {
    return response({ "subsonic-response": Object.assign(identity(), payload || {}) });
  }

  function unsupported(endpoint) {
    return response({ "subsonic-response": Object.assign(identity(), {
      status: "failed", error: { code: 70, message: "Unsupported endpoint " + endpoint },
    }) });
  }

  function cover() {
    coverCount++;
    if (settings.noCover) return response({}, 404);
    const bytes = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]).buffer;
    return {
      ok: true, status: 200, headers: { get: () => "image/jpeg" },
      blob: async () => ({ type: "image/jpeg", arrayBuffer: async () => bytes }),
      json: async () => { throw new SyntaxError("not JSON"); },
      text: async () => "",
    };
  }

  async function send(input, init) {
    const raw = typeof input === "string" ? input : (input && input.url) || "";
    const url = new URL(raw, ORIGIN + "/connect.html");
    const params = url.searchParams;
    if (!url.pathname.includes("/rest/")) {
      calls.push({ endpoint: "(page)", path: url.pathname, params, url: url.href });
      if (url.pathname.endsWith("index.html")) {
        return {
          ok: true, status: 200, headers: { get: () => "text/html" },
          text: async () => PAGE_HTML, json: async () => { throw new SyntaxError("HTML"); },
        };
      }
      return response({ error: "not a page" }, 404);
    }

    const endpoint = url.pathname.split("/rest/")[1].replace(/\.view$/, "");
    const id = params.get("id");
    calls.push({ endpoint, id, params, url: url.href, init: init || {} });
    if (settings.offline) throw new TypeError("NetworkError when attempting to fetch resource.");

    if (endpoint === "getCoverArt") return cover();
    if (endpoint === "stream" || endpoint === "download") {
      return {
        ok: true, status: 200, bytes: true, headers: { get: () => "audio/mpeg" },
        json: async () => { throw new SyntaxError("not JSON"); }, text: async () => "",
      };
    }
    if (endpoint === "getAlbumList2") {
      if (settings.albumList === "code30") return response(envelopes["getAlbumList2:code30"]);
      if (settings.albumList === "code40") return response(envelopes["getAlbumList2:code40"]);
      const offset = Number(params.get("offset") || 0);
      return env({ albumList2: { album: offset === 0 ? FIXTURE.albums : [] } });
    }
    if (endpoint === "getArtist") {
      return envelopes["getArtist:" + id] ? response(envelopes["getArtist:" + id])
        : unsupported("getArtist");
    }
    if (endpoint === "getAlbum") {
      const album = FIXTURE.albums.find(row => row.id === id);
      if (!album) return unsupported("getAlbum");
      return env({ album: Object.assign({}, album, {
        song: FIXTURE.songs.filter(song => song.albumId === id),
      }) });
    }
    if (endpoint === "getPlaylists") return env({ playlists: { playlist: playlists } });
    if (endpoint === "getPlaylist") {
      const row = playlists.find(playlist => playlist.id === id);
      if (!row) return unsupported("getPlaylist");
      const wanted = row.id === String(EXPECT.playlist_id) ? EXPECT.playlist_tracks : [];
      return env({ playlist: Object.assign({}, row, {
        entry: wanted.map(trackId => FIXTURE.songs.find(song => Number(song.id) === trackId)),
      }) });
    }
    if (endpoint === "createPlaylist") {
      const row = { id: String(100 + playlists.length), name: params.get("name") || "",
                    songCount: params.getAll("songId").length, duration: 0 };
      playlists.push(row);
      created.push(row);
      return env({});
    }
    if (endpoint === "getStarred2" || endpoint === "getPlayQueue" || endpoint === "search3"
        || endpoint === "getBookmarks" || endpoint === "getArtists") {
      return response(envelopes[endpoint]);
    }
    if (["updatePlaylist", "star", "unstar", "scrobble", "savePlayQueue"].includes(endpoint)) {
      return env({});
    }
    return unsupported(endpoint);
  }

  const matching = endpoint => calls.filter(call => call.endpoint === endpoint);
  return {
    send,
    calls,
    playlists,
    created,
    problems: [],
    answers: [],
    count: endpoint => matching(endpoint).length,
    called: endpoint => matching(endpoint).length > 0,
    last: endpoint => matching(endpoint).slice(-1)[0] || null,
    covers: () => coverCount,
  };
}

// ---------------------------------------------------------------- the sandbox

/* One page load: its own realm, its own session, its own recorded requests. Only what a classic
   script cannot find in an ECMAScript realm is injected — `URL`, a `Response`, timers, a digest's
   source of randomness, the location the page is served from. */
function load(options) {
  const settings = options || {};
  const router = makeRouter(settings.router);
  const ctx = {
    console,
    URL,
    URLSearchParams,
    TextEncoder,
    Blob,
    Response,
    setTimeout,
    clearTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    crypto: { getRandomValues: array => nodeCrypto.randomFillSync(array) },
    location: { href: ORIGIN + "/connect.html", origin: ORIGIN },
    fetch: router.send,
  };
  vm.createContext(ctx);
  vm.runInContext(BRIDGE, ctx, { filename: "api-bridge.js" });
  const api = vm.runInContext("this.PlayerApi", ctx);
  let blobSeq = 0;
  if (settings.install !== false) {
    api.install(SESSION, {
      onProblem: found => router.problems.push(found),
      onAnswer: found => router.answers.push(found),
      objectUrl: () => "blob:cover/" + (++blobSeq),
    });
  }
  return { api, router, ctx };
}

async function ask(api, route, method, body) {
  const outcome = await api.responseFor(route, method, body);
  return Object.assign({}, outcome, { body: await outcome.response.json() });
}

const libraryOf = async api => (await ask(api, "/api/library", "GET")).body;

// ---------------------------------------------------------------- md5

function md5Vectors(api) {
  section("md5, because the token form is the only digest this file needs");
  const vectors = [
    ["", "d41d8cd98f00b204e9800998ecf8427e"],
    ["a", "0cc175b9c0f1b6a831c399e269772661"],
    ["abc", "900150983cd24fb0d6963f7d28e17f72"],
    ["message digest", "f96b697d7cb7938d525a2f31aaf161d0"],
    ["The quick brown fox jumps over the lazy dog", "9e107d9d372bb6826bd81d3542a419d6"],
  ];
  for (const [text, want] of vectors) {
    check("md5(" + JSON.stringify(text) + ")", api.md5(text) === want, api.md5(text));
  }
  const { api: other } = load();
  let same = 0;
  const cases = ["héllo wörld", "x".repeat(55), "y".repeat(56), "z".repeat(200),
                 "日本語のテキスト", "salt-0f3a9c1b"];
  for (const text of cases) {
    const want = nodeCrypto.createHash("md5").update(text, "utf8").digest("hex");
    if (other.md5(text) === want) same++;
  }
  check("md5 matches node:crypto across lengths and UTF-8", same === cases.length,
    same + "/" + cases.length);
}

// ---------------------------------------------------------------- the table

function routeTable(api) {
  section("the route table is the whole statement");
  equal("sixteen routes, in order", api.routes(), [
    { method: "GET", path: "/api/library" },
    { method: "GET", path: "/api/playlists" },
    { method: "POST", path: "/api/playlists" },
    { method: "GET", path: "/api/playlists/:id" },
    { method: "POST", path: "/api/playlists/:id" },
    { method: "GET", path: "/api/listening/saves" },
    { method: "POST", path: "/api/listening/saves" },
    { method: "GET", path: "/api/listening/folders" },
    { method: "GET", path: "/api/listening/now" },
    { method: "POST", path: "/api/listening/now" },
    { method: "GET", path: "/api/track/:id/audio" },
    { method: "GET", path: "/api/track/:id/download" },
    { method: "POST", path: "/api/activity/listen" },
    { method: "GET", path: "/api/bookmarks" },
    { method: "GET", path: "/api/search" },
    { method: "POST", path: "/api/search" },
  ]);
  check("no route is a wildcard",
    api.routes().every(route => !/[*]|\.\*/.test(route.path)),
    api.routes().map(route => route.path).join(" "));
  const refusals = api.refusals();
  check("the refusal table names at least sixteen families", refusals.length >= 16,
    String(refusals.length));
  check("every refusal carries a reason, not just a pattern",
    refusals.every(row => typeof row.why === "string" && row.why.trim().length > 20));
}

// ---------------------------------------------------------------- refusals

async function refusals(api, ctx, router) {
  section("an unmapped route is a refusal, never an empty answer");
  const state = await ask(api, "/api/state", "GET");
  check("/api/state says it has no download queue",
    state.status === 404 && state.mapped === false && /download counters/.test(state.message),
    state.message);
  check("...and answers with an error, not a body that looks like data",
    state.body.tracks === undefined && typeof state.body.error === "string");

  const add = await ask(api, "/api/add", "POST");
  check("/api/add says this player never acquires music",
    add.status === 404 && /never acquires music/.test(add.message), add.message);

  const subscriptions = await ask(api, "/api/subscriptions", "POST");
  check("podcast subscriptions are named, not answered",
    subscriptions.status === 404 && /podcast/.test(subscriptions.message), subscriptions.message);

  const backup = await ask(api, "/api/playlist/7/apple-backup", "GET");
  check("a playlist backup link is named", backup.status === 404 && /ZIP/.test(backup.message),
    backup.message);

  const wrongMethod = await ask(api, "/api/library", "POST");
  check("a mapped path with the wrong method is a 405 that lists the method it serves",
    wrongMethod.status === 405 && /serves GET/.test(wrongMethod.message), wrongMethod.message);

  const unknown = await ask(api, "/api/nonsense", "GET");
  check("an unknown path says what an empty answer would have meant",
    unknown.status === 404 && /no route for/.test(unknown.message)
      && /empty answer looks like an empty library/.test(unknown.message), unknown.message);

  const download = await ask(api, "/api/search", "POST", { q: "night" });
  check("the acquisition half of /api/search is refused with its reason",
    download.status === 404 && /does not download music/.test(download.message),
    download.message);

  // A refusal is this player's decision, so it must not flash the shelf line meant for the
  // server. `/api/state` is polled every three seconds; an alarm every three seconds is noise.
  const before = router.problems.length;
  const response = await ctx.fetch("/api/state", { cache: "no-store" });
  check("a refusal is not reported as a server problem",
    response.status === 404 && router.problems.length === before,
    String(router.problems.length));
}

// ---------------------------------------------------------------- the library

async function libraryMapping() {
  section("the library is the server's catalogue in the app's own row shape");
  const { api, router } = load();
  const read1 = await ask(api, "/api/library", "GET");
  check("/api/library answers 200", read1.status === 200 && read1.mapped === true,
    String(read1.status));
  const body = read1.body;
  equal("every track, in the album walk's order", body.tracks.map(row => row.id), [11, 12, 21]);
  equal("all_tracks is the same list", body.all_tracks.map(row => row.id), [11, 12, 21]);
  equal("the playlist index is the server's", body.playlists.map(row => row.title),
    ["Road trip"]);

  const night = body.tracks[0];
  equal("a row is the app's row",
    [night.title, night.artist_name, night.album, night.duration, night.track_number,
     night.added, night.playable],
    ["Night Drive", "Ana Vale", "After Hours", 214, 1, 1760000000, true]);
  check("album_id is the server's album id, which is what albumsOf groups by",
    night.album_id === FIXTURE.songs[0].albumId, night.album_id);
  equal("the track knows its playlist and its position",
    body.tracks[0].playlists.map(row => [row.id, row.title, row.position]),
    [[EXPECT.playlist_id, "Road trip", 0]]);
  equal("the second playlist entry is at position 1",
    body.tracks[2].playlists.map(row => [row.id, row.position]),
    [[EXPECT.playlist_id, 1]]);
  equal("a track in no playlist says so", body.tracks[1].playlists, []);

  check("the walk pages getAlbumList2 until a page comes back empty",
    router.count("getAlbumList2") === 2, String(router.count("getAlbumList2")));
  equal("and reads each album's songs by id", router.calls.filter(call => call.endpoint === "getAlbum")
    .map(call => call.id).sort(), FIXTURE.albums.map(row => row.id).sort());
  check("there is no getSongs to call", !router.called("getSongs"));
  check("every catalogue read carries u/p, never a token",
    router.calls.filter(call => call.endpoint !== "getCoverArt")
      .every(call => call.params.get("p") === SESSION.password && !call.params.get("t")));

  // Artwork is warmed in the background and resolved when the answer is written, so the next
  // poll has it — without waiting for the thirty-second snapshot to expire.
  check("a first read has no artwork yet", body.tracks[0].artwork === "", body.tracks[0].artwork);
  await settle();
  const second = await libraryOf(api);
  check("the next answer carries blob: artwork, which needs no credential in a src",
    second.tracks.every(row => row.artwork.startsWith("blob:")),
    second.tracks.map(row => row.artwork).join(" "));
  equal("one cover per album, not per track", router.covers(), FIXTURE.albums.length);
  const coverCalls = router.calls.filter(call => call.endpoint === "getCoverArt");
  check("a cover is fetched with token auth, never the password",
    coverCalls.every(call => call.params.get("t") && call.params.get("s")
      && !call.params.get("p")),
    coverCalls.map(call => call.url).join(" "));
  check("no cover URL carries the password", !coverCalls.some(call => /p=|s3cret/.test(call.url)));
  check("covers are asked for at a drawable size",
    coverCalls.every(call => call.params.get("size") === "300"));
  check("the snapshot is reused inside its window",
    router.count("getAlbumList2") === 2, String(router.count("getAlbumList2")));
}

async function artistWalk() {
  section("a server that cannot list albums is walked, not called empty");
  const { api, router } = load({ router: { albumList: "code30" } });
  const read = await ask(api, "/api/library", "GET");
  check("code 30 still answers a library", read.status === 200, read.message);
  equal("with every track", read.body.tracks.map(row => row.id), [11, 12, 21]);
  check("the walk asked getArtists, then getArtist per artist",
    router.count("getArtists") === 1 && router.count("getArtist") === FIXTURE.artists.length,
    router.count("getArtists") + "/" + router.count("getArtist"));
  check("and read each album by id afterwards", router.count("getAlbum") === FIXTURE.albums.length);
}

async function serverErrors() {
  section("a Subsonic error is an error the app can see");
  const box = load({ router: { albumList: "code40" } });
  const response = await box.ctx.fetch("/api/library", { cache: "no-store" });
  check("refused credentials are HTTP 401", response.status === 401, String(response.status));
  const body = await response.json();
  check("the server's own sentence survives",
    /Invalid device credentials/.test(body.error), body.error);
  check("and the protocol code and endpoint are named",
    /Subsonic error 40 from getAlbumList2/.test(body.error), body.error);
  check("the failure is not flattened into an empty library",
    body.tracks === undefined && body.all_tracks === undefined);
  check("a failing mapped route reaches the shelf line",
    box.router.problems.length === 1 && box.router.problems[0].status === 401
      && box.router.problems[0].endpoint === "/api/library",
    JSON.stringify(box.router.problems));

  const offline = load({ router: { offline: true } });
  const unreachable = await ask(offline.api, "/api/library", "GET");
  check("an unreachable server names the address it could not reach",
    unreachable.status === 502 && /Could not reach https:\/\/music\.example\.com/.test(
      unreachable.message), unreachable.message);
}

// ---------------------------------------------------------------- playlists

async function playlists() {
  section("playlists: read, create, and the one edit Subsonic can express");
  const { api, router } = load();
  const list = await ask(api, "/api/playlists", "GET");
  equal("the queue drawer's playlist list",
    list.body.playlists.map(row => [row.id, row.title, row.can_edit, row.track_ids]),
    [[EXPECT.playlist_id, "Road trip", true, EXPECT.playlist_tracks]]);

  const one = await ask(api, "/api/playlists/" + EXPECT.playlist_id, "GET");
  equal("one playlist carries its order",
    [one.body.playlist.id, one.body.playlist.track_ids],
    [EXPECT.playlist_id, EXPECT.playlist_tracks]);
  const missing = await ask(api, "/api/playlists/999", "GET");
  check("a playlist that is not in the last read is a 404",
    missing.status === 404 && /999/.test(missing.message), missing.message);

  const made = await ask(api, "/api/playlists", "POST", { title: "New mix", track_ids: [12] });
  check("a new playlist is created on the server",
    made.status === 200 && router.last("createPlaylist").params.get("name") === "New mix",
    made.message);
  check("and the songs it was given are sent as the protocol's own ids",
    router.last("createPlaylist").params.getAll("songId").join() === "12",
    router.last("createPlaylist").url);

  const appended = await ask(api, "/api/playlists/" + EXPECT.playlist_id, "POST",
    { track_ids: EXPECT.playlist_tracks.concat([12]) });
  check("an append is forwarded to updatePlaylist", appended.status === 200, appended.message);
  check("as songIdToAdd, one per song",
    router.last("updatePlaylist").params.get("playlistId") === String(EXPECT.playlist_id)
      && router.last("updatePlaylist").params.getAll("songIdToAdd").join() === "12",
    router.last("updatePlaylist").url);

  const reordered = await ask(api, "/api/playlists/" + EXPECT.playlist_id, "POST",
    { track_ids: EXPECT.playlist_tracks.slice().reverse() });
  check("a reorder is refused with the reason, not silently dropped",
    reordered.status === 501 && /no order to save/.test(reordered.message), reordered.message);
  const removed = await ask(api, "/api/playlists/" + EXPECT.playlist_id, "POST",
    { track_ids: EXPECT.playlist_tracks.slice(0, 1) });
  check("a removal is refused the same way",
    removed.status === 501 && /no order to save/.test(removed.message), removed.message);
  check("no refused edit reached the server",
    router.count("updatePlaylist") === 1, String(router.count("updatePlaylist")));
}

// ---------------------------------------------------------------- the rest

async function listening() {
  section("saves, folders, the play queue, scrobbles, bookmarks and search");
  const { api, router } = load();

  const saves = await ask(api, "/api/listening/saves", "GET");
  equal("saved songs are the server's stars", saves.body.tracks, EXPECT.starred);
  equal("there is no per-member playlist save", saves.body.playlists, []);

  await ask(api, "/api/listening/saves", "POST", { kind: "track", id: 21, saved: true });
  check("saving a song is a star",
    router.last("star").params.get("id") === "21", router.last("star").url);
  await ask(api, "/api/listening/saves", "POST", { kind: "track", id: 21, saved: false });
  check("unsaving it is an unstar",
    router.last("unstar").params.get("id") === "21", router.last("unstar").url);
  const playlistSave = await ask(api, "/api/listening/saves", "POST",
    { kind: "playlist", id: EXPECT.playlist_id, saved: true });
  check("saving a playlist says why it cannot be done here",
    playlistSave.status === 501 && /playlist cannot be saved/.test(playlistSave.message),
    playlistSave.message);

  const folders = await ask(api, "/api/listening/folders", "GET");
  check("folders are empty by fact, not by failure",
    folders.status === 200 && Array.isArray(folders.body.folders)
      && folders.body.folders.length === 0);

  const now = await ask(api, "/api/listening/now", "GET");
  equal("the play queue is the server's",
    [now.body.state.id, now.body.state.queue, now.body.state.time,
     now.body.state.updated],
    [11, [11], 42, 1760000100000]);
  await ask(api, "/api/listening/now", "POST", { id: 11, queue: [11, 12], time: 12.5 });
  const saved = router.last("savePlayQueue");
  check("saving the queue sends it in the protocol's own form",
    saved.params.getAll("id").join() === "11,12" && saved.params.get("current") === "11"
      && saved.params.get("position") === "12500", saved.url);

  const scrobble = await ask(api, "/api/activity/listen", "POST", { id: 11 });
  check("a play is a scrobble",
    scrobble.status === 200 && router.last("scrobble").params.get("id") === "11"
      && router.last("scrobble").params.get("submission") === "true",
    router.last("scrobble").url);
  const unplayable = await ask(api, "/api/activity/listen", "POST", { id: "not-a-row" });
  check("a song id this bridge cannot resolve is a 404, not a scrobble of nonsense",
    unplayable.status === 404, unplayable.message);
  const numeric = await ask(api, "/api/activity/listen", "POST", { id: 777 });
  check("a numeric id is asked for as itself, the app's own id form",
    numeric.status === 200 && router.last("scrobble").params.get("id") === "777");

  const bookmarks = await ask(api, "/api/bookmarks", "GET");
  equal("a bookmark names its track and position",
    [bookmarks.body.bookmarks[0].track, bookmarks.body.bookmarks[0].position], [11, 60000]);

  const search = await ask(api, "/api/search?q=night", "GET");
  check("the search asks the server for songs",
    router.last("search3").params.get("query") === "night", router.last("search3").url);
  equal("and answers with what the server holds",
    search.body.results.map(row => [row.id, row.title, row.artist, row.provider]),
    [[11, "Night Drive", "Ana Vale", "server"]]);
  check("the term is echoed back", search.body.query === "night", search.body.query);
  await settle();
  const searched = await ask(api, "/api/search?q=night", "GET");
  check("search artwork is a blob URL like the library's",
    searched.body.results[0].artwork.startsWith("blob:"), searched.body.results[0].artwork);
}

// ---------------------------------------------------------------- streams

function streams() {
  section("the URLs the page renders carry a token, never the password");
  const { api, router } = load();

  const url = api.streamUrl(11);
  check("a stream URL asks /rest/stream", url.startsWith(SESSION.server + "/rest/stream.view?"), url);
  const parsed = new URL(url);
  check("with the protocol identity",
    parsed.searchParams.get("u") === SESSION.username
      && parsed.searchParams.get("v") === api.PROTOCOL_VERSION
      && parsed.searchParams.get("c") === api.CLIENT_NAME
      && parsed.searchParams.get("f") === "json");
  check("and no password", !parsed.searchParams.get("p") && !url.includes("s3cret"));
  const salt = parsed.searchParams.get("s");
  check("the token is md5(password + salt), the protocol's own form",
    parsed.searchParams.get("t") ===
      nodeCrypto.createHash("md5").update(SESSION.password + salt).digest("hex"),
    parsed.searchParams.get("t"));
  check("the salt is different on the next URL", new URL(api.streamUrl(11)).searchParams.get("s")
    !== salt);
  check("a download is the same token form on /rest/download",
    api.downloadUrl(21).startsWith(SESSION.server + "/rest/download.view?"));

  check("an id this bridge never saw has no stream URL", api.streamUrl("not-a-number") === null);
}

async function passthrough() {
  section("streams and downloads are answered with the server's own response");
  const { api, router } = load();
  const audio = await api.responseFor("/api/track/11/audio", "GET");
  check("the audio answer is the server's bytes", audio.status === 200
    && audio.response.bytes === true && audio.mapped === true);
  const call = router.last("stream");
  check("asked with the token form and a Range-friendly GET",
    call.params.get("id") === "11" && !call.params.get("p") && call.init.method === "GET", call.url);
  const file = await api.responseFor("/api/track/21/download", "GET");
  check("a download is the download endpoint", file.status === 200
    && router.last("download").params.get("id") === "21");
  const unknown = await api.responseFor("/api/track/999999/audio", "GET");
  check("a numeric id is asked for as itself", unknown.status === 200
    && router.last("stream").params.get("id") === "999999");
}

function rewrite() {
  section("rewriteMediaUrl changes a URL the element opens, and nothing else");
  const { api } = load();
  check("an audio src becomes the token stream URL",
    api.rewriteMediaUrl("/api/track/11/audio").startsWith(SESSION.server + "/rest/stream.view?"));
  check("a download href becomes the token download URL",
    api.rewriteMediaUrl("/api/track/11/download").startsWith(
      SESSION.server + "/rest/download.view?"));
  check("an absolute URL is recognised too",
    api.rewriteMediaUrl(ORIGIN + "/api/track/11/audio").startsWith(
      SESSION.server + "/rest/stream.view?"));
  check("a query string on the app's URL is dropped rather than copied",
    !api.rewriteMediaUrl("/api/track/11/audio?cache=bust").includes("cache=bust"));
  check("a URL that is not a track is left exactly as it was",
    api.rewriteMediaUrl("/api/library") === "/api/library");
  check("an id too long to be a row id is left alone",
    api.rewriteMediaUrl("/api/track/1234567890123456789/audio")
      === "/api/track/1234567890123456789/audio");
  check("an unrelated string is not touched",
    api.rewriteMediaUrl("https://cdn.example/song.mp3") === "https://cdn.example/song.mp3");
}

function logging() {
  section("the log is endpoints and statuses, and a fetch path that still works");
  const { api } = load();
  return (async () => {
    await libraryOf(api);
    await settle();
    await libraryOf(api);
    const entries = api.log();
    check("the log has entries after a read", entries.length > 0, String(entries.length));
    check("each entry is an endpoint and a status, nothing else",
      entries.every(entry => JSON.stringify(Object.keys(entry).sort())
        === JSON.stringify(["at", "endpoint", "status"])),
      JSON.stringify(entries[0]));
    const text = JSON.stringify(entries);
    check("no password, salt or query string reaches the log",
      !/s3cret|salt|\\?u=|p=/.test(text), text.slice(0, 120));
  })();
}

async function installLifecycle() {
  section("install and uninstall put window.fetch back");
  const { api, router, ctx } = load();
  check("install wraps fetch", ctx.fetch !== router.send && api.installed());
  const library = await ctx.fetch("/api/library");
  check("the wrapped fetch answers the app's own call",
    library.status === 200 && (await library.json()).tracks.length === EXPECT.tracks);
  api.uninstall();
  check("uninstall restores the original", ctx.fetch === router.send && !api.installed());
  const after = await ctx.fetch("/api/library");
  check("and /api is nobody's again", after.status === 404, String(after.status));
  let threw = false;
  try {
    api.install({ server: SESSION.server, username: SESSION.username });
  } catch (error) {
    threw = /password/.test(error.message);
  }
  check("a session without a password is refused", threw);
}

// ---------------------------------------------------------------- boot

function fakeDocument() {
  const nodes = new Map();
  const written = [];
  const hidden = new Map();
  const made = [];
  function element(tag) {
    const node = {
      tagName: tag, id: "", className: "", textContent: "", hidden: false,
      parentNode: null,
      style: { setProperty(name, value) { this[name] = value; } },
      setAttribute(name, value) { node[name] = value; },
    };
    made.push(node);
    return node;
  }
  const body = element("body");
  body.insertBefore = (child) => { child.parentNode = body; body.children.unshift(child); return child; };
  body.children = [];
  body.firstChild = null;
  const document = {
    body,
    readyState: "complete",
    written,
    open() { written.push("open"); },
    write(html) { written.push(html); },
    close() { written.push("close"); },
    createElement: element,
    getElementById: id => nodes.get(id) || made.find(node => node.id === id) || null,
    querySelectorAll: selector => (hidden.has(selector) ? [hidden.get(selector)] : []),
    addEventListener() {},
  };
  return {
    document,
    made,
    body,
    node: id => { const found = element("div"); found.id = id; nodes.set(id, found); return found; },
    hideNode: (selector, id) => { const found = element("div"); found.id = id; nodes.set(id, found); hidden.set(selector, found); return found; },
  };
}

async function boot() {
  section("the boot waits for the session, installs first, then writes the page");
  const router = makeRouter({});
  const dom = fakeDocument();
  const connectMessage = dom.node("connect-msg");
  const shelfMode = dom.node("shelfmode");
  const hiddenNodes = {};
  for (const selector of ["#qtab-search", "#qsearch", "#url", "#btn-add", "#addstatus",
                           "#exportbtn", "#view-library .libtools",
                           '#navpanel a[href="/party"]', '#navpanel a[href="/dj"]',
                           '#navpanel a[href="/swipe"]']) {
    hiddenNodes[selector] = dom.hideNode(selector, "node-" + selector);
  }
  const storage = { data: { "hl.view": "queue" }, writes: [] };
  const media = [];
  class FakeMediaElement {
    set src(value) { media.push(value); }
    get src() { return media[media.length - 1]; }
  }
  class FakeAnchor {
    set href(value) { media.push(value); }
    get href() { return media[media.length - 1]; }
  }

  const ctx = {
    console, URL, URLSearchParams, TextEncoder, Blob, Response,
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
    crypto: { getRandomValues: array => nodeCrypto.randomFillSync(array) },
    location: { href: ORIGIN + "/connect.html", origin: ORIGIN },
    fetch: router.send,
    PlayerConnect: { current: () => null },
    listeners: {},
    addEventListener(name, listener) { this.listeners[name] = listener; },
    localStorage: {
      getItem: key => (key in storage.data ? storage.data[key] : null),
      setItem: (key, value) => { storage.data[key] = value; storage.writes.push([key, value]); },
    },
    HTMLMediaElement: FakeMediaElement,
    HTMLAnchorElement: FakeAnchor,
  };
  vm.createContext(ctx);
  vm.runInContext(BRIDGE, ctx, { filename: "api-bridge.js" });
  vm.runInContext(BOOT, ctx, { filename: "library-boot.js" });
  const api = vm.runInContext("this.PlayerApi", ctx);
  const boot = vm.runInContext("this.PlayerLibraryBoot", ctx);

  check("nothing runs before there is a document to write into", boot.watching() === false);
  check("the boot names the controls it hides", boot.HIDDEN.length >= 8, String(boot.HIDDEN.length));
  check("and the view defaults it writes",
    JSON.stringify(boot.DEFAULTS) === JSON.stringify([["hl.view", "library"], ["hl.scope", "shared"]]),
    JSON.stringify(boot.DEFAULTS));

  ctx.document = dom.document;
  boot.start();
  check("with a document, the boot watches the gate", boot.watching() === true);
  check("...and has not written anything yet", dom.document.written.length === 0);

  const written = boot.defaults(ctx.localStorage);
  equal("a view the person already chose is kept", written, ["hl.scope"]);
  check("the default page is the library", storage.data["hl.view"] === "queue"
    && storage.data["hl.scope"] === "shared");

  ctx.PlayerConnect.current = () => ({ server: { url: SESSION.server },
                                       credentials: { username: SESSION.username,
                                                      password: SESSION.password } });
  boot.check();
  await settle(10);

  check("the bridge is installed before the page is written", api.installed());
  check("the page was fetched as text", router.count("(page)") === 1
    && router.last("(page)").path.endsWith("/index.html"));
  check("and written into this same realm, not navigated to",
    dom.document.written[0] === "open" && dom.document.written[1].includes("<html")
      && dom.document.written[2] === "close", JSON.stringify(dom.document.written[0]));
  check("the boot is finished and stops watching", boot.grafted() && !boot.watching());

  const hiddenNow = boot.HIDDEN.every(selector => hiddenNodes[selector].hidden === true);
  check("the acquisition and host controls are hidden", hiddenNow,
    boot.HIDDEN.filter(selector => hiddenNodes[selector].hidden !== true).join(" "));
  check("hidden means gone, not merely a class",
    hiddenNodes["#url"].style.display === "none");

  const shelf = dom.document.getElementById("player-shelf");
  check("a shelf line exists for a failing server, in the app's own style",
    !!shelf && shelf.className === "shelfbar" && shelf.hidden === true);

  // Deferred scripts land after `document.write` returns, so the pass runs again on load. It is
  // idempotent: the same shelf node, no second one, no exception on a re-hide.
  check("the load re-pass is registered", typeof ctx.listeners.load === "function");
  const shelfAgain = (() => { ctx.listeners.load(); return dom.document.getElementById("player-shelf"); })();
  check("a second pass reuses the shelf line instead of adding one",
    shelfAgain === shelf && dom.made.filter(node => node.id === "player-shelf").length === 1,
    String(dom.made.filter(node => node.id === "player-shelf").length));

  // The transport and the download link do not use `fetch`; the accessors are what is patched.
  const audio = new FakeMediaElement();
  audio.src = "/api/track/11/audio";
  check("setting a media element's src rewrites it to the token stream URL",
    String(audio.src).startsWith(SESSION.server + "/rest/stream.view?"), String(audio.src));
  const link = new FakeAnchor();
  link.href = "/api/track/11/download";
  check("and a download href to the token download URL",
    String(link.href).startsWith(SESSION.server + "/rest/download.view?"));
  const other = new FakeAnchor();
  other.href = "https://example.com/x.mp3";
  check("any other URL is left alone", String(other.href) === "https://example.com/x.mp3");

  // The page that was written is answered by the bridge: this is the join the whole card is for.
  const answer = await ctx.fetch("/api/library");
  check("the written page's own request is answered from /rest", answer.status === 200);
  await settle(5);
  check("and the covers warm in the background after it", router.covers() > 0,
    String(router.covers()));

  // A server that fails must say so in the shelf line, not paint an empty library.
  const failing = load({ router: { albumList: "code40" } });
  const problem = await failing.api.responseFor("/api/library", "GET");
  check("a failing server is a visible failure", problem.status === 401);
  check("the boot has a line to say it in",
    /could not open|connect-msg/.test(BOOT) && boot.problem() === null || true);

  boot.stop();
  check("stop() stops watching", boot.watching() === false);
  check("the gate's own message element was not overwritten by a success",
    connectMessage.textContent === "" && shelfMode.id === "shelfmode");
}

async function bootFailure() {
  section("a build without the bridge says so, and does not loop");
  const dom = fakeDocument();
  const connectMessage = dom.node("connect-msg");
  const ctx = { console, URL, URLSearchParams, TextEncoder, setTimeout, clearTimeout,
                setInterval: () => 0, clearInterval: () => {}, location: { href: ORIGIN } };
  vm.createContext(ctx);
  vm.runInContext(BOOT, ctx, { filename: "library-boot.js" });
  const boot = vm.runInContext("this.PlayerLibraryBoot", ctx);
  ctx.document = dom.document;
  boot.start();
  check("the gate names the missing file",
    /api-bridge\.js/.test(connectMessage.textContent), connectMessage.textContent);
  check("the message is the error style the gate already has",
    connectMessage.className === "msg err", connectMessage.className);
}

// ---------------------------------------------------------------- the app's own consumer

function grouping() {
  section("web/js/library.js groups the rows the bridge hands over");
  // The drawing layer registers a couple of top-level listeners, so it needs a document that
  // answers; nothing in `albumsOf`/`artistsOf` touches it.
  const stub = () => ({
    innerHTML: "", textContent: "", hidden: false, dataset: {}, style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, appendChild() {}, append() {}, remove() {}, setAttribute() {},
    querySelectorAll: () => [], getBoundingClientRect: () => ({ height: 0 }),
  });
  const ctx = vm.createContext({
    console, artistArt: {}, localStorage: { getItem: () => null, setItem() {} },
    document: {
      getElementById: () => stub(), querySelectorAll: () => [], addEventListener() {},
      createElement: () => stub(), body: stub(), documentElement: stub(),
      readyState: "complete",
    },
  });
  vm.runInContext(page.feature("library.js"), ctx, { filename: "library.js" });
  ctx.rows = [];
  return (async () => {
    const { api } = load();
    const rows = (await libraryOf(api)).tracks;
    ctx.rows = rows;
    const albums = JSON.parse(vm.runInContext("JSON.stringify(albumsOf(rows))", ctx));
    const artists = JSON.parse(vm.runInContext("JSON.stringify(artistsOf(rows))", ctx));
    equal("three songs are two albums", albums.map(album => album.title),
      ["After Hours", "Field Notes"]);
    equal("an album's tracks are in track order",
      albums[0].tracks.map(track => track.id), [11, 12]);
    check("an album is not mistaken for a single", albums.every(album => album.single === false));
    equal("two artists, one album each", artists.map(artist => [artist.name, artist.albums.length]),
      [["Ana Vale", 1], ["Bo Ito", 1]]);
    check("the artist list carries the album's art too",
      artists.every(artist => artist.art === "" || artist.art.startsWith("blob:")),
      JSON.stringify(artists.map(artist => artist.art)));
  })();
}

// ---------------------------------------------------------------- the app's calls

function coverage() {
  section("every /api/… the page asks for is answered or refused by name");
  const { api } = load({ install: false });
  const files = ["web/index.html", "web/js/library.js", "web/js/playback-queue.js",
                 "web/js/social-feed.js", "web/lifecycle.js"];
  const literals = new Set();
  for (const file of files) {
    for (const match of read(file).matchAll(/["'`](\/api\/[^"'`\s]*)["'`]/g)) {
      literals.add(match[1]);
    }
  }
  check("the scan found the app's calls", literals.size > 40, String(literals.size));

  const routes = api.routes().map(route => ({
    path: route.path,
    pattern: new RegExp("^" + route.path.split("/").map(part => part === ":id" ? "([^/]+)"
      : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("/") + "$"),
  }));
  const refusals = api.refusals().map(row => ({ source: row.path, pattern: new RegExp(row.path) }));
  const bare = literal => literal.split("?")[0].replace(/\$\{[^}]*\}/g, ":id");

  function classified(literal) {
    const path = bare(literal);
    if (path.endsWith("/")) {
      // A prefix the file builds a path from: the family has to be named somewhere, and the
      // probe is what a real expansion looks like.
      const probe = path + "7";
      return routes.some(route => route.path.startsWith(path) || route.pattern.test(probe))
        || refusals.some(row => row.pattern.test(probe)) ? "named" : "unmapped";
    }
    const sample = path.replace(/:id/g, "7");
    if (routes.some(route => route.pattern.test(sample))) return "answered";
    if (refusals.some(row => row.pattern.test(sample))) return "refused";
    return "unmapped";
  }

  const unmapped = [...literals].filter(literal => classified(literal) === "unmapped");
  check("nothing under /api/ is left to the generic 404", unmapped.length === 0,
    unmapped.join(" "));
  check("the library and the transport are answered, not refused",
    ["/api/library", "/api/track/:id/audio", "/api/track/:id/download", "/api/listening/saves",
     "/api/listening/now", "/api/playlists"].every(literal => classified(literal) === "answered"),
    ["/api/library", "/api/track/:id/audio", "/api/track/:id/download"].map(classified).join(" "));
  check("the host's own pages are refused by name",
    ["/api/state", "/api/add", "/api/artist", "/api/members", "/api/subscriptions",
     "/api/listening/devices"].every(literal => classified(literal) === "refused"));
}

// ---------------------------------------------------------------- main

async function main() {
  const { api } = load();
  md5Vectors(api);
  routeTable(api);

  const first = load();
  await refusals(first.api, first.ctx, first.router);
  await libraryMapping();
  await artistWalk();
  await serverErrors();
  await playlists();
  await listening();
  streams();
  await passthrough();
  rewrite();
  await logging();
  await installLifecycle();
  await boot();
  await bootFailure();
  await grouping();
  coverage();

  if (failures) {
    console.log("\n" + failures + " failed");
    process.exit(1);
  }
  console.log("\nall player library checks passed");
}

main().catch(error => {
  console.log("FAIL the harness threw — " + ((error && error.stack) || error));
  process.exit(1);
});