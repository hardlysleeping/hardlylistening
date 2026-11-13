// The desktop window's decisions, driven without a window.
//
// `desktop/ui/state.js` holds every rule the window applies: which buttons are live, what the
// status line says, and — the one worth a test of its own — whether a server may be stopped.
// The crate cannot be compiled on this machine, so this file is the executable proof that the
// ownership rule works: a server this window did not spawn is never stopped, and the refusal is
// explained rather than silent.
//
// The harness is the same shape as `tests/test_mixer_rate.cjs`: read the shipped file, load it
// in a `vm` context, drive it with plain objects, and assert on what a person would see.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const UI = path.join(__dirname, "..", "desktop", "ui");
const source = fs.readFileSync(path.join(UI, "state.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

function load() {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  if (!ctx.Host) throw new Error("state.js did not publish Host");
  return ctx.Host;
}
const Host = load();

// A convenient starting state: the address every packaged install uses by default.
function fresh() {
  return Host.initial(Host.formatAddress({ host: "127.0.0.1", port: 8470 }));
}
function run(state, ...events) {
  return events.reduce((current, event) => Host.reduce(current, event), state);
}
const HEALTHY = { ok: true, version: "0.16.6" };

// ---- the untouched window asks, and offers to start ----------------------
{
  const view = Host.view(fresh());
  check("a window that has not polled says so", view.status.text === "Checking…", view.status.text);
  check("nothing is claimed about the server yet", view.status.kind === "pending");
  check("Start is live before the first answer", view.buttons.start.enabled);
  check("Stop is not live: this window holds no process", !view.buttons.stop.enabled);
  check("Open is not live until something answers", !view.buttons.open.enabled);
  check("every disabled button explains itself",
        view.buttons.stop.hint.length > 20 && view.buttons.open.hint.length > 10,
        view.buttons.stop.hint);
}

// ---- unknown → up → down -------------------------------------------------
{
  const up = run(fresh(), { type: "probe", owned: false, health: HEALTHY });
  check("a health answer moves the window to up", up.health === "up", up.health);
  check("the version shown is the server's own", up.version === "0.16.6", String(up.version));
  check("a server we did not start is marked external", up.ownership === "external", up.ownership);

  const down = run(up, { type: "probe", owned: false, health: null, healthError: "ECONNREFUSED" });
  check("an answer that stops moves the window to down", down.health === "down", down.health);
  check("nobody owns a dead address", down.ownership === "none", down.ownership);
  check("the reason a poll failed is kept for the window",
        down.healthError === "ECONNREFUSED", down.healthError);
  check("Start is live again once the address is dead", Host.view(down).buttons.start.enabled);
  check("a dead address is not 'running'", Host.view(down).status.text === "Stopped",
        Host.view(down).status.text);
}

// ---- an external server cannot be stopped, and says so --------------------
{
  const external = run(fresh(), { type: "probe", owned: false, health: HEALTHY });
  const view = Host.view(external);
  check("the status line distinguishes an external server",
        view.status.text === "Running (started elsewhere)", view.status.text);
  check("Stop is refused before the click, not after it", !view.buttons.stop.enabled);
  check("Start does not offer a second server on the same port",
        !view.buttons.start.enabled && /already running/.test(view.buttons.start.hint),
        view.buttons.start.hint);

  const refused = run(external, { type: "stop", result: { stopped: false, reason: "not_started_here" } });
  check("a refused stop changes nothing", refused.health === "up" && refused.ownership === "external",
        refused.health + "/" + refused.ownership);
  check("a refused stop says the server was left alone",
        /did not start it/.test(refused.notice) && /left alone/.test(refused.notice), refused.notice);
  check("the refusal is a warning, not a silent success", refused.noticeKind === "warn",
        refused.noticeKind);
}

// ---- start → up → stop is a closed loop ---------------------------------
{
  let state = run(fresh(), { type: "start", result: { started: true, pid: 4242 } });
  check("starting claims ownership of the address", state.ownership === "ours", state.ownership);
  check("starting shows progress rather than 'stopped'", state.health === "starting" &&
        Host.view(state).status.text === "Starting…", Host.view(state).status.text);
  check("Stop is live while our own server is starting",
        Host.view(state).buttons.stop.enabled);

  state = run(state, { type: "probe", owned: true, health: HEALTHY });
  check("our own server reads as ours",
        Host.view(state).status.text === "Running (started by this window)",
        Host.view(state).status.text);

  const stopped = run(state, { type: "stop", result: { stopped: true, pid: 4242 } });
  check("stopping our own server returns to stopped", stopped.health === "down" &&
        stopped.ownership === "none", stopped.health + "/" + stopped.ownership);
  check("stopping says so", stopped.notice === "Local server stopped.", stopped.notice);
  check("a stopped server cannot be stopped twice", !Host.view(stopped).buttons.stop.enabled);
}

// ---- our server dying is noticed, and only our server --------------------
{
  const ours = run(fresh(), { type: "start", result: { started: true } },
    { type: "probe", owned: true, health: HEALTHY });
  const died = run(ours, { type: "probe", owned: false, health: null });
  check("a server this window started exits into a warning",
        /exited/.test(died.notice), died.notice);
  check("its exit releases ownership", died.ownership === "none", died.ownership);

  const external = run(fresh(), { type: "probe", owned: false, health: HEALTHY });
  const gone = run(external, { type: "probe", owned: false, health: null });
  check("a server this window never owned exits without claiming an exit",
        !/exited/.test(gone.notice), gone.notice);
}

// ---- the two refusals the server reports are told apart ------------------
{
  const alreadyRunning = run(fresh(), { type: "start", result: { started: false, reason: "already_running" } });
  check("starting over an external server is refused and named",
        alreadyRunning.ownership === "external" && /already running/.test(alreadyRunning.notice),
        alreadyRunning.notice);

  const missing = run(fresh(), {
    type: "start",
    result: { started: false, reason: "no_launcher", detail: "no sidecar was found" }
  });
  check("a missing launcher is explained, with the fix",
        /not found/.test(missing.notice) && /HARDLYLISTENING_SERVER/.test(missing.notice),
        missing.notice);
  check("a missing launcher does not pretend the server is running", missing.health === "down",
        missing.health);
}

// ---- a server that never answers is not 'starting' forever --------------
{
  const state = run(fresh(), { type: "start", result: { started: true } },
    { type: "startTimeout" });
  check("a start that never answers becomes down", state.health === "down", state.health);
  check("it is still ours to stop", state.ownership === "ours" &&
        Host.view(state).buttons.stop.enabled);
  check("the timeout is explained in seconds", /20 s/.test(state.healthError), state.healthError);
}

// ---- address formatting --------------------------------------------------
{
  const loopback = Host.formatAddress({ host: "127.0.0.1", port: 8470 });
  check("loopback is the default and says 'this computer'",
        loopback.url === "http://127.0.0.1:8470" && loopback.label === "This computer",
        loopback.url + " / " + loopback.label);
  check("the health URL is built from the same address",
        Host.api(loopback, "/api/health") === "http://127.0.0.1:8470/api/health",
        Host.api(loopback, "/api/health"));

  const lan = Host.formatAddress({ host: "192.168.1.50", port: 8470 });
  check("a LAN literal address is used as given",
        lan.url === "http://192.168.1.50:8470" && lan.scope === "lan", lan.url);
  check("a LAN address is not labelled 'this computer'", lan.label === "Network address", lan.label);

  const any = Host.formatAddress({ host: "0.0.0.0", port: 8470 });
  check("a listen-anywhere bind is polled on loopback",
        any.url === "http://127.0.0.1:8470", any.url);
  check("but the status line still shows what it listens on",
        any.listen === "0.0.0.0:8470" && any.scope === "any", any.listen);
  check("the label warns that it is every interface", any.label === "All interfaces", any.label);

  const v6 = Host.formatAddress({ host: "::1", port: 8471 });
  check("IPv6 is bracketed in the URL", v6.url === "http://[::1]:8471", v6.url);
  check("IPv6 listens as written", v6.listen === "[::1]:8471", v6.listen);

  const missingPort = Host.formatAddress({});
  check("an address with no fields falls back to the documented default",
        missingPort.url === "http://127.0.0.1:8470", missingPort.url);
}

// ---- the reducer does not mutate its input -----------------------------
{
  const state = fresh();
  const before = JSON.stringify(state);
  Host.reduce(state, { type: "probe", owned: true, health: HEALTHY });
  check("reducing returns a new state, it does not edit the old one",
        JSON.stringify(state) === before);
}

// ---- no domain rules: the window asks, it never decides ----------------
{
  const app = fs.readFileSync(path.join(UI, "app.js"), "utf8");
  const apiCalls = (source + app).match(/\/api\/[A-Za-z0-9_/{}.:-]+/g) || [];
  const allowed = new Set(["/api/health", "/api/v1/capabilities"]);
  const stray = [...new Set(apiCalls)].filter(url => !allowed.has(url));
  check("the window reads only the two documented endpoints", stray.length === 0, stray.join(", "));
  check("the window holds no library vocabulary",
        !/\btracks?\b|\bsongs?\b|\bplaylists?\b|\bmembers?\s*=|queue/i.test(source.replace(/\/\*[\s\S]*?\*\//g, "")));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }