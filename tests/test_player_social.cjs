// The player's social gate, exercised where it actually runs: `fetch` is a stub server built
// from this repository's own answers (`tests/fixtures/player/social.json`, written by
// `tests/test_player_social.py` from `api.v1.envelope` and documents shaped like `server.py`),
// so what is tested is the decision and the refusals, not a network and not a browser.
//
// Five things this file is here to prove:
//
//   * the decision belongs to the server: one public `GET /api/v1/capabilities`, and Activity is
//     offered only when the document names `social.activity` and the deployment does not require
//     members. A server that answers nothing, refuses, or answers HTML is "absent", never an
//     empty feed;
//   * with social present, `GET /api/activity` is the versioned read in the host's own
//     `{"items": [...]}` envelope, and the rows are the ones the fixture recorded;
//   * with social absent, the module gets out of the way and the bridge's own sentence is what a
//     person reads -- compared byte for byte against a page that never loaded this file;
//   * comments, votes, likes, boosts and profiles are refused with the reason (a member session
//     this player cannot hold), and those refusals never reach the server;
//   * the transport is wrapped at `attach()` and put back by `detach()`, no URL carries a
//     credential, and the trace records endpoints and statuses only.
//
// Run: node tests/test_player_social.cjs
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const read = name => fs.readFileSync(path.join(ROOT, name), "utf8");
const BRIDGE = read("player/shell/api-bridge.js");
const SOCIAL = read("player/shell/social.js");
const FIXTURE = JSON.parse(read("tests/fixtures/player/social.json"));
const EXPECT = FIXTURE.expectations;
const CAPABILITIES = FIXTURE.paths.capabilities;
const ACTIVITY = FIXTURE.paths.activity;

const SESSION = { server: "https://music.example.com", username: "bazzite", password: "s3cret-pass" };
const CONNECTED = { server: { url: SESSION.server }, credentials: { username: SESSION.username,
                                                                  password: SESSION.password } };
const ORIGIN = "http://player.invalid";

let failures = 0;
function check(name, ok, detail) {
  if (ok) {
    console.log("PASS " + name);
  } else {
    failures++;
    console.log("FAIL " + name + (detail ? " - " + detail : ""));
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

// ---------------------------------------------------------------- the stub server

/* Everything the module reads comes out of the fixture: the five capability documents the Python
   half built from this repository's own field lists, and the activity answer `api.v1.envelope`
   wrote. `capabilitiesStatus`, `capabilitiesNotJson`, `activityStatus`, `activityNotJson`,
   `activityBody` and `throwOn` are the ways a real server can fail, and each one has to end in a
   sentence rather than an exception. */
function makeServer(options) {
  const settings = options || {};
  const calls = [];
  const document = "document" in settings ? settings.document : FIXTURE.documents.local;
  const activity = "activityBody" in settings ? settings.activityBody : FIXTURE.activity;
  const identity = { status: "ok", version: "1.16.1", type: "hardlylistening",
                     serverVersion: "1.0.0", openSubsonic: true };

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

  function notJson() {
    return {
      ok: true, status: 200, headers: { get: () => "text/html" },
      json: async () => { throw new SyntaxError("Unexpected token < in JSON"); },
      text: async () => "<!doctype html><html></html>",
    };
  }

  /* The library is not this card's question, but the page asks for it, so the stub answers the
     two Subsonic reads a player makes on the way in: an empty catalogue and an empty playlist
     index, which the bridge maps to an empty library rather than a refusal. */
  const answered = { getAlbumList2: { albumList2: { album: [] } },
                     getPlaylists: { playlists: { playlist: [] } } };
  function subsonic(url) {
    const endpoint = url.pathname.split("/rest/")[1].replace(/\.view$/, "");
    const payload = answered[endpoint]
      || { status: "failed", error: { code: 70, message: "Unsupported endpoint " + endpoint } };
    return response({ "subsonic-response": Object.assign({}, identity, payload) });
  }

  async function send(input, init) {
    const raw = typeof input === "string" ? input : (input && input.url) || "";
    const url = new URL(raw, ORIGIN + "/connect.html");
    calls.push({ path: url.pathname, search: url.search, href: url.href,
                 method: (init && init.method) || "GET" });
    if (url.pathname === CAPABILITIES) {
      if (settings.throwOn === "capabilities") {
        throw new TypeError("NetworkError when attempting to fetch resource.");
      }
      if (settings.capabilitiesStatus) return response({ error: "no" }, settings.capabilitiesStatus);
      if (settings.capabilitiesNotJson) return notJson();
      return response(document);
    }
    if (url.pathname === ACTIVITY) {
      if (settings.throwOn === "activity") {
        throw new TypeError("NetworkError when attempting to fetch resource.");
      }
      if (settings.activityStatus) return response({ error: "no" }, settings.activityStatus);
      if (settings.activityNotJson) return notJson();
      return response(activity);
    }
    if (url.pathname.startsWith("/rest/")) return subsonic(url);
    return response({ error: "not this player's server" }, 404);
  }

  const matching = endpoint => calls.filter(call => call.path === endpoint);
  return {
    send,
    calls,
    count: endpoint => matching(endpoint).length,
    called: endpoint => matching(endpoint).length > 0,
    last: endpoint => matching(endpoint).slice(-1)[0] || null,
  };
}

// ---------------------------------------------------------------- the sandbox

/* One page load: bridge installed, social attached on top of it, its own recorded requests. The
   module is loaded *after* the bridge and wraps the bridge's `fetch`, which is the order
   `connect.html` would use -- so the composition under test is the shipped one. */
function load(options) {
  const settings = options || {};
  const server = makeServer(settings.server);
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
    location: { href: ORIGIN + "/connect.html", origin: ORIGIN },
    fetch: server.send,
  };
  vm.createContext(ctx);
  vm.runInContext(BRIDGE, ctx, { filename: "api-bridge.js" });
  const api = vm.runInContext("this.PlayerApi", ctx);
  api.install(SESSION, {});
  const bridgeFetch = ctx.fetch;
  if (settings.withSocial === false) return { api, ctx, server, bridgeFetch, social: null, said: [] };

  vm.runInContext(SOCIAL, ctx, { filename: "social.js" });
  const social = vm.runInContext("this.PlayerSocial", ctx);
  const said = [];
  if (settings.attach !== false) {
    social.attach({ session: CONNECTED, say: decision => said.push(decision) });
  }
  return { api, ctx, server, bridgeFetch, social, said };
}

const messageOf = async response => (await response.json()).error;

// ---------------------------------------------------------------- the decision

async function decisions() {
  section("the decision is the server's, from one public read");
  for (const name of Object.keys(EXPECT)) {
    const expectation = EXPECT[name];
    const box = load({ server: { document: FIXTURE.documents[name] } });
    const decision = await box.social.ready();
    equal(name + ": state", decision.state, expectation.state);
    check(name + ": available follows the server",
      decision.available === expectation.available, JSON.stringify(decision.available));
    check(name + ": it names what is missing",
      decision.available || decision.needed === expectation.needed, decision.needed);
    check(name + ": the probe is one request, to the capability document",
      box.server.count(CAPABILITIES) === 1 && box.server.last(CAPABILITIES).method === "GET",
      String(box.server.count(CAPABILITIES)));
    check(name + ": the probe carries no query and no credential",
      box.server.last(CAPABILITIES).search === ""
        && !/s3cret/.test(box.server.last(CAPABILITIES).href),
      box.server.last(CAPABILITIES).href);
    check(name + ": the decision is told once",
      box.said.length === 1 && box.said[0].state === expectation.state,
      JSON.stringify(box.said.map(row => row.state)));
    check(name + ": and it is kept rather than re-asked",
      box.social.decision().state === expectation.state
        && box.server.count(CAPABILITIES) === 1);
  }
  const box = load({ server: { document: FIXTURE.documents.local } });
  await box.social.ready();
  await box.social.ready();
  check("asking twice is one round trip", box.server.count(CAPABILITIES) === 1,
    String(box.server.count(CAPABILITIES)));
}

async function degrading() {
  section("a capability read that fails is not a feature");
  const cases = [
    ["unreachable", { throwOn: "capabilities" }, /could not read the capability document/],
    ["401", { capabilitiesStatus: 401 }, /was refused \(HTTP 401\)/],
    ["403", { capabilitiesStatus: 403 }, /was refused \(HTTP 403\)/],
    ["404", { capabilitiesStatus: 404 }, /no capability document \(HTTP 404/],
    ["html", { capabilitiesNotJson: true }, /not with JSON/],
    ["empty", { document: {} }, /was empty/],
  ];
  for (const [name, server, reason] of cases) {
    const box = load({ server: server });
    const decision = await box.social.ready();
    check(name + ": degrades to absent", decision.state === "absent" && !decision.available,
      decision.state);
    check(name + ": with the reason, not a shrug", reason.test(decision.reason), decision.reason);
    const answer = await box.ctx.fetch("/api/activity");
    check(name + ": and the app gets the bridge's refusal, not an empty feed",
      answer.status === 404 && (await answer.json()).items === undefined, String(answer.status));
  }
}

// ---------------------------------------------------------------- the read

async function feed() {
  section("the activity feed is the server's rows in the host's envelope");
  const box = load({ server: { document: FIXTURE.documents.local } });
  const response = await box.ctx.fetch("/api/activity?limit=80");
  check("/api/activity answers 200", response.status === 200, String(response.status));
  const body = await response.json();
  equal("items are the v1 envelope's own data", body.items, FIXTURE.rows);
  equal("a boost and a day card, in the server's order",
    body.items.map(row => row.kind), ["boost", "day"]);
  const call = box.server.last(ACTIVITY);
  check("it is the versioned read, not the host's unversioned route",
    call.path === ACTIVITY, call.path);
  equal("with the app's own page size", call.search, "?limit=80");

  await box.ctx.fetch("/api/activity?limit=500");
  equal("a page size past the server's clamp is clamped", box.server.last(ACTIVITY).search,
    "?limit=200");
  await box.ctx.fetch("/api/activity?limit=12");
  equal("a smaller page size is passed through", box.server.last(ACTIVITY).search, "?limit=12");
  await box.ctx.fetch("/api/activity");
  equal("no page size is the app's own 80", box.server.last(ACTIVITY).search, "?limit=80");
  await box.ctx.fetch("/api/activity?limit=0");
  equal("a page size of zero is not asked for", box.server.last(ACTIVITY).search, "?limit=80");
  check("no read carried a credential",
    box.server.calls.every(call => !/[?&](u|p|t|s)=/.test(call.search) && !/s3cret/.test(call.href)),
    box.server.calls.map(call => call.href).join(" "));
}

async function feedFailures() {
  section("a feed that fails is a sentence, not an empty list");
  const cases = [
    ["a member read", { activityStatus: 401 }, 501, /needs a member session/],
    ["a forbidden read", { activityStatus: 403 }, 501, /needs a member session/],
    ["a server error", { activityStatus: 500 }, 502, /HTTP 500/],
    ["html", { activityNotJson: true }, 502, /not with JSON/],
    ["the host's unversioned shape", { activityBody: { items: [] } }, 502, /v1 envelope/],
    ["a v1 envelope with no list", { activityBody: { api: "v1", data: null } }, 502,
     /v1 envelope/],
    ["unreachable", { throwOn: "activity" }, 502, /Could not reach/],
  ];
  for (const [name, server, status, reason] of cases) {
    const box = load({ server: Object.assign({ document: FIXTURE.documents.local }, server) });
    await box.social.ready();
    const response = await box.ctx.fetch("/api/activity");
    const body = await response.json();
    check(name + " is a " + status, response.status === status, String(response.status));
    check(name + " says why", reason.test(body.error), body.error);
    check(name + " is not an empty feed", body.items === undefined, JSON.stringify(body));
  }
}

// ---------------------------------------------------------------- refusals

async function refusals() {
  section("the rest of the app's social surface is refused by name");
  const box = load({ server: { document: FIXTURE.documents.local } });
  await box.social.ready();
  const before = box.server.calls.length;
  const asked = [
    ["/api/boost", "POST", /member session/],
    ["/api/social/summary?kind=track&target=11", "GET", /member/],
    ["/api/social/track/11/vote", "POST", /member/],
    ["/api/member/m1", "GET", /member/],
  ];
  for (const [path, method, reason] of asked) {
    const response = await box.ctx.fetch(path, { method: method });
    const body = await response.json();
    check(path + " is refused as a 501", response.status === 501, String(response.status));
    check(path + " names the session it needs", reason.test(body.error), body.error);
    check(path + " is not answered with data", body.items === undefined && body.data === undefined,
      JSON.stringify(body));
  }
  check("no refusal reached the server", box.server.calls.length === before,
    String(box.server.calls.length - before));
  check("and none of them was traced as a read",
    box.social.log().every(entry => entry.endpoint !== "/api/boost"),
    JSON.stringify(box.social.log()));

  const table = box.social.refusals();
  check("the refusal table is three families", table.length === 3, String(table.length));
  check("each one carries a reason a person can read",
    table.every(row => typeof row.why === "string" && row.why.trim().length > 20),
    JSON.stringify(table.map(row => row.why.length)));
  const samples = ["/api/boost", "/api/social/summary", "/api/member/m1"];
  check("and each sample URL is matched by one of them",
    samples.every(sample => table.some(row => new RegExp(row.path).test(sample))),
    samples.filter(sample => !table.some(row => new RegExp(row.path).test(sample))).join(" "));
}

async function members() {
  section("a server that requires members has a feed this player cannot read");
  const box = load({ server: { document: FIXTURE.documents.hosted } });
  await box.social.ready();
  const response = await box.ctx.fetch("/api/activity");
  const body = await response.json();
  check("the read is a 501 before it is a request", response.status === 501,
    String(response.status));
  check("the reason is the member sign-in", /member sign-in/.test(body.error), body.error);
  check("nothing was asked of the feed", !box.server.called(ACTIVITY));
  check("the rest of the surface is refused too",
    (await box.ctx.fetch("/api/boost", { method: "POST" })).status === 501);
}

async function absent() {
  section("a server without social gets the bridge's own sentence, unchanged");
  const bare = load({ withSocial: false });
  const bridgeOnly = { activity: await bare.ctx.fetch("/api/activity?limit=80"),
                       boost: await bare.ctx.fetch("/api/boost", { method: "POST" }) };
  const bridgeText = { activity: await messageOf(bridgeOnly.activity),
                       boost: await messageOf(bridgeOnly.boost) };

  for (const name of ["no_social", "empty"]) {
    const box = load({ server: { document: FIXTURE.documents[name] } });
    const response = await box.ctx.fetch("/api/activity?limit=80");
    equal(name + ": the same status as the bridge alone", response.status,
      bridgeOnly.activity.status);
    equal(name + ": the same sentence, word for word", await messageOf(response),
      bridgeText.activity);
    const boost = await box.ctx.fetch("/api/boost", { method: "POST" });
    equal(name + ": and the boost refusal is the bridge's", await messageOf(boost),
      bridgeText.boost);
    equal(name + ": no social read was attempted", box.server.called(ACTIVITY), false);
  }

  const loaded = load({ attach: false });
  check("loading the file does not touch fetch", loaded.ctx.fetch === loaded.bridgeFetch);
  const answer = await loaded.ctx.fetch("/api/activity");
  equal("and an unattached module changes no answer", await messageOf(answer),
    bridgeText.activity);
}

// ---------------------------------------------------------------- dispatch and lifecycle

async function passingThrough() {
  section("everything else is the bridge's, unchanged");
  const box = load({ server: { document: FIXTURE.documents.local } });
  const library = await box.ctx.fetch("/api/library");
  const body = await library.json();
  check("/api/library is still answered", library.status === 200 && Array.isArray(body.tracks),
    String(library.status));
  check("as an empty library, because the catalogue is empty by fact",
    body.tracks.length === 0, String(body.tracks.length));
  check("and the bridge went to the server's own /rest for it",
    box.server.calls.some(call => call.path.startsWith("/rest/getAlbumList2")),
    box.server.calls.map(call => call.path).join(" "));

  const state = await box.ctx.fetch("/api/state");
  check("a host route the bridge refuses is still its refusal",
    state.status === 404 && /download queue/.test(await messageOf(state)));

  const wrong = await box.ctx.fetch("/api/activity", { method: "POST" });
  check("a method this route does not serve is not answered as a read", wrong.status !== 200,
    String(wrong.status));
}

async function lifecycle() {
  section("attach wraps the transport, detach puts it back");
  const box = load({ server: { document: FIXTURE.documents.local } });
  check("installed while attached",
    box.social.installed() && box.ctx.fetch !== box.bridgeFetch);
  await box.social.ready();
  const stale = box.ctx.fetch;
  check("detach reports that it did something", box.social.detach() === true);
  check("the bridge's transport is back",
    box.ctx.fetch === box.bridgeFetch && !box.social.installed());
  equal("detaching twice changes nothing", box.social.detach(), false);
  const again = await box.ctx.fetch("/api/activity");
  check("and the app's call is the bridge's again", again.status === 404, String(again.status));
  const throughStale = await stale("/api/activity");
  check("a call already holding the old wrapper still reaches the server",
    throughStale.status === 404, String(throughStale.status));

  const fresh = load({ attach: false });
  let threw = "";
  try {
    fresh.social.attach({ session: { server: { url: "" } } });
  } catch (error) {
    threw = error.message;
  }
  check("attach without a connected server says so", /connected server/.test(threw), threw);
  check("and it left the transport alone", fresh.ctx.fetch === fresh.bridgeFetch);
}

function table() {
  section("the route table is the whole of what this file answers");
  const box = load({ attach: false });
  equal("one route, and where it is answered from", box.social.routes(), [
    { method: "GET", path: "/api/activity", from: "GET " + ACTIVITY },
  ]);
  equal("the paths are the server's own", [box.social.CAPABILITIES_PATH, box.social.ACTIVITY_PATH],
    [CAPABILITIES, ACTIVITY]);
  check("the page size is the app's own", box.social.LIMIT === 80, String(box.social.LIMIT));
  equal("the four states, named", box.social.STATES,
    ["activity", "members", "no-activity", "absent"]);
  check("no route is a wildcard",
    box.social.routes().every(row => !/[*]|\.\*/.test(row.path)), box.social.routes()[0].path);
}

async function logging() {
  section("the trace is endpoints and statuses, nothing else");
  const box = load({ server: { document: FIXTURE.documents.local } });
  await box.ctx.fetch("/api/activity?limit=12");
  const entries = box.social.log();
  equal("the probe and the read, in order", entries.map(entry => entry.endpoint),
    [CAPABILITIES, ACTIVITY]);
  equal("with the status the server gave", entries.map(entry => entry.status), [200, 200]);
  check("each entry is an endpoint, a status and a time",
    entries.every(entry => JSON.stringify(Object.keys(entry).sort())
      === JSON.stringify(["at", "endpoint", "status"])),
    JSON.stringify(entries[0]));
  check("no query string, password or salt reaches the trace",
    !/limit=|\?|s3cret|salt/.test(JSON.stringify(entries)), JSON.stringify(entries));

  const failing = load({ server: { document: FIXTURE.documents.local, activityStatus: 500 } });
  await failing.ctx.fetch("/api/activity");
  equal("a failing read is traced with the status that failed",
    failing.social.log().map(entry => entry.status), [200, 500]);
}

// ---------------------------------------------------------------- main

async function main() {
  table();
  await decisions();
  await degrading();
  await feed();
  await feedFailures();
  await refusals();
  await members();
  await absent();
  await passingThrough();
  await lifecycle();
  await logging();

  if (failures) {
    console.log("\n" + failures + " failed");
    process.exit(1);
  }
  console.log("\nall player social checks passed");
}

main().catch(error => {
  console.log("FAIL the harness threw - " + ((error && error.stack) || error));
  process.exit(1);
});