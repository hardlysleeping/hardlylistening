// The two choices in the window, driven without a window.
//
// `desktop/ui/mode.js` renders `desktop/host/mode.py`: the same four combinations, the same ids,
// the same remote-address rule. The crate cannot be compiled on this machine, so this file and
// `tests/test_host_mode.py` are the executable proof of HOST-004.
//
// What it pins, and why:
//   * all four combinations — a missing or fifth state is a model that drifted;
//   * both non-couplings — turning the local server off must not rewrite the target, and
//     switching the target to remote must not stop (or start) a server. That is the card's
//     actual subject, so it is asserted from both directions and through the controls as well
//     as the mutators;
//   * the remote-address rule — scheme required, https preferred, one trailing slash, an
//     address that is this computer refused, and no silent fall back to local;
//   * that the module needs no DOM and no bridge, so wiring it into `index.html` cannot break
//     the page it is loaded from.
//
// The harness is the same shape as `tests/test_desktop_state.cjs`: read the shipped file, run it
// in a `vm` context, drive it with plain objects.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const UI = path.join(__dirname, "..", "desktop", "ui");
const source = fs.readFileSync(path.join(UI, "mode.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

function load() {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  if (!ctx.Mode) throw new Error("mode.js did not publish Mode");
  return ctx.Mode;
}
const Mode = load();

const REMOTE = "https://music.example.org:8443/";
function origin(raw, options) {
  return Mode.parseOrigin(raw, options);
}
function fresh() {
  return Mode.defaults();
}
function remoteMode() {
  const parsed = origin(REMOTE);
  if (!parsed.ok) throw new Error("the fixture origin is invalid: " + parsed.error);
  return Mode.setTarget(fresh(), "remote", parsed.origin);
}
function offMode() {
  return Mode.setLocalServer(fresh(), false);
}

// ---- the four combinations ----------------------------------------------
{
  const matrix = [
    [true, "local", "local", true, true, false],
    [true, "remote", "remote", true, true, false],
    [false, "local", "local", false, false, true],
    [false, "remote", "remote", false, false, false]
  ];
  for (const [on, target, talkTo, start, stop, attention] of matrix) {
    const found = Mode.combine({ runLocalServer: on, target: target, remoteOrigin: null });
    const label = (on ? "on" : "off") + "/" + target;
    check("the combination " + label + " has an id", typeof found.id === "string" && found.id.length > 0);
    check("the window talks to " + talkTo + " (" + label + ")", found.talkTo === talkTo, found.talkTo);
    check("Start is " + (start ? "live" : "not live") + " (" + label + ")", found.start.enabled === start);
    check("Stop is " + (stop ? "live" : "not live") + " (" + label + ")", found.stop.enabled === stop);
    check("needs attention (" + label + ")", found.needsAttention === attention);
    check("no combination is degraded (" + label + ")", found.degraded === false);
  }
  check("the ids are the ones mode.py uses",
    Mode.combine({ runLocalServer: true, target: "local" }).id === "local" &&
    Mode.combine({ runLocalServer: true, target: "remote" }).id === "local-behind-remote" &&
    Mode.combine({ runLocalServer: false, target: "local" }).id === "local-off-pointed-local" &&
    Mode.combine({ runLocalServer: false, target: "remote" }).id === "remote-only");
}

// ---- which address the window uses --------------------------------------
{
  const local = fresh();
  check("a local target uses the resolved local address",
    Mode.endpoint(local, "http://127.0.0.1:8470") === "http://127.0.0.1:8470",
    Mode.endpoint(local, "http://127.0.0.1:8470"));

  const remote = remoteMode();
  check("a remote target uses the remote origin",
    Mode.endpoint(remote, "http://127.0.0.1:8470") === REMOTE,
    Mode.endpoint(remote, "http://127.0.0.1:8470"));

  const noAddress = Mode.setTarget(fresh(), "remote");
  check("a remote target with no address talks to nothing rather than falling back",
    Mode.endpoint(noAddress, "http://127.0.0.1:8470") === null,
    String(Mode.endpoint(noAddress, "http://127.0.0.1:8470")));
}

// ---- the two non-couplings ---------------------------------------------
{
  const remote = remoteMode();
  const turnedOff = Mode.setLocalServer(remote, false);
  check("turning the local server off keeps the target remote", turnedOff.target === "remote", turnedOff.target);
  check("turning the local server off keeps the remote address", turnedOff.remoteOrigin.url === REMOTE);
  check("turning the local server off reaches remote-only", Mode.combine(turnedOff).id === "remote-only");

  const backOn = Mode.setLocalServer(turnedOff, true);
  check("turning it back on does not invent a target", backOn.target === "remote" && backOn.remoteOrigin.url === REMOTE);

  const offLocal = Mode.setLocalServer(fresh(), false);
  check("a local target is not rewritten to remote when the server goes off",
    offLocal.target === "local", offLocal.target);
  check("the dead end is named, not hidden",
    Mode.combine(offLocal).id === "local-off-pointed-local" && Mode.combine(offLocal).needsAttention);

  const pointed = Mode.setTarget(fresh(), "remote", remoteMode().remoteOrigin);
  check("switching the target to remote leaves the local server running", pointed.runLocalServer === true);
  check("switching the target to remote reaches local-behind-remote",
    Mode.combine(pointed).id === "local-behind-remote");
  check("switching back to local leaves the local server choice alone",
    Mode.setTarget(pointed, "local").runLocalServer === true);

  const pointedOff = Mode.setTarget(offMode(), "remote", remoteMode().remoteOrigin);
  check("switching the target with the server off keeps it off", pointedOff.runLocalServer === false);
  check("that is remote-only, the supported state", Mode.combine(pointedOff).id === "remote-only");

  // The controls are two independent widgets, so they are checked independently too.
  const both = Mode.controls(remoteMode());
  check("the local control follows only the local axis", both.local.on === true, String(both.local.on));
  check("the target control follows only the target axis", both.target.value === "remote", both.target.value);
  const offControls = Mode.controls(offMode());
  check("turning the server off does not move the target widget",
    offControls.target.value === "local" && offControls.local.on === false);
  check("the target widget offers both choices", offControls.target.options.length === 2 &&
    offControls.target.options.map(o => o.value).join(",") === "local,remote",
    offControls.target.options.map(o => o.value).join(","));
  check("a local-server-off control says the window still works",
    /still works|remote server/i.test(offControls.local.hint), offControls.local.hint);
}

// ---- what the window says when the remote is down -----------------------
{
  const remoteOnly = Mode.setTarget(offMode(), "remote", remoteMode().remoteOrigin);
  const text = Mode.unreachableText(remoteOnly, "http://127.0.0.1:8470");
  check("the remote-only message names the address", text.indexOf(REMOTE) !== -1, text);
  check("the remote-only message says no local server is running", /runs no local server/.test(text), text);
  check("the remote-only message says that is not a problem to fix", /does not need one/.test(text), text);
  check("the remote-only message does not claim degradation", !/degraded|fallback/i.test(text), text);

  const deadEnd = Mode.unreachableText(offMode(), "http://127.0.0.1:8470");
  check("the dead end names the local address", deadEnd.indexOf("http://127.0.0.1:8470") !== -1, deadEnd);
  check("the dead end says the window is not running a server", /not running a local server/.test(deadEnd), deadEnd);

  const behind = Mode.unreachableText(remoteMode(), "http://127.0.0.1:8470");
  check("a local server behind an unreachable remote is not stopped by the message",
    /keeps running/.test(behind), behind);
}

// ---- the remote-address rule -------------------------------------------
{
  const noScheme = origin("music.example.org");
  check("a bare host is refused", noScheme.ok === false);
  check("the refusal writes out the fix", /needs a scheme/.test(noScheme.error) &&
    noScheme.error.indexOf("https://music.example.org") !== -1, noScheme.error);

  const https = origin("HTTPS://Music.Example.ORG");
  check("https is canonical", https.ok && https.origin.url === "https://music.example.org/", https.origin && https.origin.url);
  check("https is secure and carries no warning", https.origin.secure === true && https.origin.warning === "");

  const http = origin("http://music.example.org:8470");
  check("http is accepted", http.ok === true, http.error);
  check("http is flagged as not encrypted", http.origin.secure === false &&
    /not encrypted/.test(http.origin.warning) && /https is preferred/i.test(http.origin.warning),
    http.origin.warning);

  const trailing = ["https://music.example.org", "https://music.example.org/", "https://music.example.org///"];
  check("one trailing slash is the canonical form",
    trailing.every(raw => origin(raw).origin.url === "https://music.example.org/"),
    trailing.map(raw => origin(raw).origin.url).join(" | "));

  for (const raw of ["https://music.example.org/library", "https://music.example.org/?x=1",
                     "https://music.example.org/#top", "https://someone:secret@music.example.org/"]) {
    check("an origin is not a path or a query: " + raw, origin(raw).ok === false, origin(raw).error);
  }
  for (const raw of ["ftp://music.example.org", "//music.example.org"]) {
    check("only http and https are spoken: " + raw, origin(raw).ok === false);
  }
  check("a port is kept", origin("https://music.example.org:8443/").origin.url === REMOTE);
  for (const raw of ["https://music.example.org:port/", "https://music.example.org:0/",
                     "https://music.example.org:70000/"]) {
    check("a bad port is refused: " + raw, origin(raw).ok === false, origin(raw).error);
  }
  check("IPv6 needs brackets", origin("https://[2001:db8::10]:8443/").ok === true &&
    origin("https://2001:db8::10/").ok === false);

  for (const raw of ["https://127.0.0.1:8470/", "https://localhost:8470/", "https://[::1]/",
                     "https://0.0.0.0:8470/", "https://127.9.9.9/"]) {
    const parsed = origin(raw);
    check("this computer is not a remote server: " + raw, parsed.ok === false &&
      /is this computer/.test(parsed.error), parsed.error);
  }
  check("the resolved local host is not a remote server",
    origin("http://192.168.1.50:8470/", { localHost: "192.168.1.50" }).ok === false);
  check("a LAN address is a remote server when it is another machine",
    origin("http://192.168.1.50:8470/").ok === true);
  check("this machine's own name is not a remote server",
    origin("https://media-box.example/", { machineNames: ["media-box", "media-box.example"] }).ok === false);
  check("an empty address is not an address",
    origin("").ok === false && origin("   ").ok === false && origin(null).ok === false);
}

// ---- the stored payload -------------------------------------------------
{
  const remote = remoteMode();
  const payload = Mode.toPayload(remote);
  check("the payload is the documented format",
    JSON.stringify(payload) === JSON.stringify({
      version: 1,
      run_local_server: true,
      target: "remote",
      remote_origin: REMOTE
    }), JSON.stringify(payload));

  const restored = Mode.fromPayload(payload);
  check("a round trip keeps every answer", restored.problem === "" &&
    restored.mode.target === "remote" && restored.mode.remoteOrigin.url === REMOTE &&
    restored.mode.runLocalServer === true, restored.problem);

  const off = Mode.fromPayload({ version: 1, run_local_server: false, target: "remote",
    remote_origin: REMOTE });
  check("remote-only survives a round trip", off.mode.runLocalServer === false &&
    Mode.combine(off.mode).id === "remote-only");

  const broken = Mode.fromPayload({ target: "remote" });
  check("a remote target without an address stays remote", broken.mode.target === "remote");
  check("and says why", /no usable address/.test(broken.problem), broken.problem);
  check("and does not fall back to the local server",
    Mode.endpoint(broken.mode, "http://127.0.0.1:8470") === null);

  const unusable = Mode.fromPayload({ target: "remote", remote_origin: "music.example.org" });
  check("an unusable stored address is reported", /unusable/.test(unusable.problem), unusable.problem);
  check("and the target is kept", unusable.mode.target === "remote");
  check("and there is no address to use", unusable.mode.remoteOrigin === null);

  const nonsense = Mode.fromPayload({ target: "elsewhere", run_local_server: "false", version: 9 });
  check("a nonsense file keeps the defaults", nonsense.mode.target === "local" &&
    nonsense.mode.runLocalServer === true, JSON.stringify(nonsense.mode));
  check("and reports every problem it found", nonsense.problem.length > 40, nonsense.problem);

  const deadEnd = Mode.fromPayload({ run_local_server: false, target: "local" });
  check("the dead end is stored, not corrected", deadEnd.mode.target === "local" &&
    deadEnd.mode.runLocalServer === false && deadEnd.problem === "", deadEnd.problem);
}

// ---- the module is safe to wire into the page ---------------------------
{
  const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  check("mode.js needs no DOM", !/\bdocument\.\s*[A-Za-z_$]|\bwindow\s*\.\s*[A-Za-z_$]|querySelector|getElementById/.test(code));
  check("mode.js needs no bridge or network", !/__TAURI__|invoke\(|fetch\(|XMLHttpRequest/.test(code));
  check("mode.js decides nothing about tracks, queues or members",
    !/\btracks?\b|\bsongs?\b|\bplaylists?\b|\bqueue\b|\bmembers?\b/i.test(code));
  check("the module publishes itself for both a page and a test",
    /root\.Mode\s*=\s*Mode/.test(source) && /module\.exports\s*=\s*Mode/.test(source));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nall checks passed");