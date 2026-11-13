// The desktop window's lifecycle transitions, driven without a window.
//
// `desktop/ui/lifecycle.js` holds what HOST-003 adds to the window: what a failed start says,
// which button is live in each phase, whether a stop is refused, and — the card's own question —
// what closing the window does with a server this window started.
//
// The harness is the same shape as `tests/test_desktop_state.cjs`: read the shipped file, run it
// in a `vm`, drive it with plain objects, assert on what a person would see. Nothing here spawns
// a process, opens a socket or reads a state file; the Python suite proves those.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const UI = path.join(__dirname, "..", "desktop", "ui");
const source = fs.readFileSync(path.join(UI, "lifecycle.js"), "utf8");
const stateSource = fs.readFileSync(path.join(UI, "state.js"), "utf8");
const pythonSource = fs.readFileSync(
  path.join(__dirname, "..", "desktop", "host", "lifecycle.py"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

function loadInto(text) {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(text, ctx);
  return ctx;
}

const Lifecycle = loadInto(source).Lifecycle;
if (!Lifecycle) throw new Error("lifecycle.js did not publish Lifecycle");
const Host = loadInto(stateSource).Host;

const ADDRESS = { host: "127.0.0.1", port: 8470, url: "http://127.0.0.1:8470",
  listen: "127.0.0.1:8470" };

function fresh() { return Lifecycle.initial(ADDRESS); }
function run(state, ...events) {
  return events.reduce((current, event) => Lifecycle.reduce(current, event), state);
}

// A document stand-in: only the ids this card owns, with the properties `paint` touches.
function fakeDoc(ids) {
  const nodes = {};
  ids.forEach(id => {
    nodes[id] = {
      id, textContent: "", hidden: false, checked: false, dataset: {}, listeners: {},
      addEventListener(name, fn) { this.listeners[name] = fn; }
    };
  });
  return { nodes, getElementById(id) { return nodes[id] || null; } };
}

// ---- the untouched window -------------------------------------------------
{
  const view = Lifecycle.view(fresh());
  check("a window that has not asked says stopped", view.status.text === "Stopped", view.status.text);
  check("no phase claims ownership", view.ownership === "none", view.ownership);
  check("Start is live", view.buttons.start.enabled);
  check("Stop is not live: this window holds no process", !view.buttons.stop.enabled);
  check("every disabled button explains itself",
    view.buttons.stop.hint.length > 20, view.buttons.stop.hint);
  check("closing with nothing running does nothing",
    view.close.action === "nothing" && /No server/.test(view.close.text), view.close.text);
}

// ---- start → starting → up ------------------------------------------------
{
  const started = run(fresh(), { type: "start", result: { started: true, pid: 4242 } });
  check("a start shows progress, not 'stopped'", started.phase === "starting",
    started.phase);
  check("a start claims ownership", started.ownership === "ours", started.ownership);
  check("Start is disabled while starting", !Lifecycle.view(started).buttons.start.enabled);
  check("Stop is live while our own server is starting",
    Lifecycle.view(started).buttons.stop.enabled);
  check("the start is announced", /Starting/.test(started.notice), started.notice);

  const up = run(started, { type: "snapshot", snapshot: {
    phase: "up", ownership: "ours", pid: 4242, health_error: "" } });
  check("our own server reads as ours",
    Lifecycle.view(up).status.text === "Running (started by this window)",
    Lifecycle.view(up).status.text);
  check("an up server is green", Lifecycle.view(up).status.kind === "up");
  check("closing an up server we started hands off",
    Lifecycle.view(up).close.action === "handoff", Lifecycle.view(up).close.action);
  check("the close sentence names the pid",
    /pid 4242/.test(Lifecycle.view(up).close.text), Lifecycle.view(up).close.text);
}

// ---- an external server: not ours, not stoppable, not claimed -------------
{
  const external = run(fresh(), { type: "snapshot", snapshot: {
    phase: "up", ownership: "external", pid: null, health_error: "" } });
  const view = Lifecycle.view(external);
  check("the status line distinguishes an external server",
    view.status.text === "Running (started elsewhere)", view.status.text);
  check("Stop is refused before the click, not after it", !view.buttons.stop.enabled);
  check("Start does not offer a second server on the same port", !view.buttons.start.enabled);
  check("closing does not claim a server we did not start",
    view.close.action === "nothing" && /did not start/.test(view.close.text), view.close.text);
  check("an external server is announced as someone else's",
    /left alone/.test(external.notice), external.notice);

  const refused = run(external, { type: "stop", result: {
    stopped: false, reason: "not_started_here", phase: "up", ownership: "external",
    detail: "127.0.0.1:8470 is answered by a server this window did not start" } });
  check("a refused stop changes nothing",
    refused.phase === "up" && refused.ownership === "external",
    refused.phase + "/" + refused.ownership);
  check("a refused stop says the server was left alone",
    /left alone/.test(refused.notice), refused.notice);
  check("the refusal is a warning", refused.noticeKind === "warn", refused.noticeKind);
}

// ---- the named failures ---------------------------------------------------
{
  const already = run(fresh(), { type: "start", result: {
    started: false, reason: "already_running", phase: "up", ownership: "external",
    detail: "127.0.0.1:8470 answered" } });
  check("starting over a live server is refused and named",
    already.ownership === "external" && /did not start it/.test(already.notice),
    already.notice);

  const busy = run(fresh(), { type: "start", result: {
    started: false, reason: "port_in_use", phase: "failed", ownership: "external",
    detail: "127.0.0.1:8470 is already in use" } });
  check("a bound port is explained, not retried forever",
    /already in use/.test(busy.notice), busy.notice);
  check("a busy port does not offer Start again", !Lifecycle.view(busy).buttons.start.enabled);

  const missing = run(fresh(), { type: "start", result: {
    started: false, reason: "no_launcher", phase: "failed", ownership: "none",
    detail: "no sidecar was found" } });
  check("a missing launcher is explained, with the fix",
    /not found/.test(missing.notice) && /HARDLYLISTENING_SERVER/.test(missing.notice),
    missing.notice);
  check("a missing launcher does not pretend the server is running",
    missing.phase === "failed" && Lifecycle.view(missing).buttons.start.enabled);

  const spawnFailed = run(fresh(), { type: "start", result: {
    started: false, reason: "spawn_failed", phase: "failed", ownership: "none",
    detail: "EACCES" } });
  check("a spawn failure carries the OS reason", /EACCES/.test(spawnFailed.notice),
    spawnFailed.notice);
}

// ---- a start that never answers is not 'starting' forever -----------------
{
  const state = run(fresh(), { type: "start", result: { started: true, pid: 99 } },
    { type: "deadline" });
  check("a start past the deadline becomes failed", state.phase === "failed", state.phase);
  check("it is still ours to stop", state.ownership === "ours" &&
    Lifecycle.view(state).buttons.stop.enabled);
  check("the timeout is explained in seconds", /20 s/.test(state.notice), state.notice);
  check("a failed but live server does not offer a second Start",
    !Lifecycle.view(state).buttons.start.enabled);

  const hostTimeout = run(fresh(), { type: "start", result: {
    started: false, reason: "timeout", phase: "failed", ownership: "ours", pid: 99,
    detail: "no answer from http://127.0.0.1:8470 within 20 s" } });
  check("the host's timeout reason is the same sentence",
    /within 20 s/.test(hostTimeout.notice), hostTimeout.notice);
}

// ---- stopping -------------------------------------------------------------
{
  const up = run(fresh(), { type: "snapshot", snapshot: { phase: "up", ownership: "ours" } });
  const stopped = run(up, { type: "stop", result: { stopped: true, phase: "idle" } });
  check("stopping returns to stopped", stopped.phase === "idle" && stopped.ownership === "none",
    stopped.phase + "/" + stopped.ownership);
  check("stopping says so", /Local server stopped/.test(stopped.notice), stopped.notice);
  check("a stopped server cannot be stopped twice", !Lifecycle.view(stopped).buttons.stop.enabled);

  const killed = run(up, { type: "stop", result: { stopped: true, forced: true, phase: "idle" } });
  check("a killed server is not described as a graceful stop",
    /killed/.test(killed.notice), killed.notice);
}

// ---- a dead child is released, not defended -------------------------------
{
  const ours = run(fresh(), { type: "start", result: { started: true, pid: 7 } },
    { type: "snapshot", snapshot: { phase: "up", ownership: "ours" } });
  const died = run(ours, { type: "snapshot", snapshot: {
    phase: "failed", ownership: "none", pid: null, reason: "exited",
    detail: "the server this window started is no longer running" } });
  check("a server that exited becomes a warning", /exited|no longer running/.test(died.notice),
    died.notice);
  check("its exit releases ownership", died.ownership === "none", died.ownership);
  check("Start is live again after our server exits", Lifecycle.view(died).buttons.start.enabled);
  check("a dead server cannot be stopped", !Lifecycle.view(died).buttons.stop.enabled);
}

// ---- closing: hand off by default, stop only when chosen ------------------
{
  const up = run(fresh(), { type: "snapshot", snapshot: { phase: "up", ownership: "ours",
    pid: 4242 } });
  check("closing does not stop a server we started", Lifecycle.view(up).close.action === "handoff");

  const chosen = run(up, { type: "stopOnClose", value: true });
  check("the choice is kept and shown", chosen.stopOnClose === true);
  check("choosing to stop on close explains itself",
    /will stop the server/.test(chosen.notice), chosen.notice);
  check("the close decision follows the choice", Lifecycle.view(chosen).close.action === "stop");

  const handed = run(up, { type: "close", decision: { action: "handoff", detail: "pid 4242 keeps serving 127.0.0.1:8470", handoff: { pid: 4242 } } });
  check("a handoff says the server keeps running", /Left running/.test(handed.notice),
    handed.notice);
  check("a handed-off window is no longer running anything",
    handed.phase === "idle" && handed.ownership === "none", handed.phase + "/" + handed.ownership);
  check("the record is kept for the next window",
    handed.handoff && handed.handoff.pid === 4242, JSON.stringify(handed.handoff));

  const closedWithStop = run(chosen, { type: "close", decision: {
    action: "stop", stopped: true, detail: "the server exited on SIGTERM" } });
  check("a stop on close says it stopped", /Stopped on close/.test(closedWithStop.notice),
    closedWithStop.notice);
}

// ---- a record left by an earlier window explains an unowned server --------
{
  const state = run(fresh(), { type: "snapshot", snapshot: {
    phase: "up", ownership: "external",
    handoff: { pid: 1234, listen: "127.0.0.1:8470" } } });
  const view = Lifecycle.view(state);
  check("a handed-off server is described, not adopted", view.handoff.active &&
    /earlier window/.test(view.handoff.text), view.handoff.text);
  check("it is still not stoppable from here", !view.buttons.stop.enabled);
  check("its pid is named for the person", /pid 1234/.test(view.handoff.text), view.handoff.text);
}

// ---- painting touches only this card's nodes -----------------------------
{
  const doc = fakeDoc(["lifecycle-status", "lifecycle-reason", "lifecycle-handoff",
    "lifecycle-close", "stop-on-close"]);
  const state = run(fresh(), { type: "snapshot", snapshot: {
    phase: "failed", ownership: "ours", reason: "timeout",
    handoff: { pid: 9, listen: "127.0.0.1:8470" } } }, { type: "stopOnClose", value: true });
  const touched = Lifecycle.paint(doc, Lifecycle.view(state));
  check("the status node carries the phase's colour",
    doc.nodes["lifecycle-status"].dataset.state === "down",
    doc.nodes["lifecycle-status"].dataset.state);
  check("the reason is painted as a sentence",
    /did not answer/.test(doc.nodes["lifecycle-reason"].textContent),
    doc.nodes["lifecycle-reason"].textContent);
  check("the handoff sentence is painted", /pid 9/.test(doc.nodes["lifecycle-handoff"].textContent),
    doc.nodes["lifecycle-handoff"].textContent);
  check("the checkbox reflects the stored preference", doc.nodes["stop-on-close"].checked === true);
  check("only nodes this card owns are touched",
    touched.every(id => id.indexOf("lifecycle-") === 0 || id === "stop-on-close"),
    touched.join(","));
  check("an absent node is skipped rather than thrown at",
    Lifecycle.paint(fakeDoc([]), Lifecycle.view(state)).length === 0);
}

// ---- attach is inert without the checkbox, wiring only when it is there ---
{
  check("attach does nothing outside the shell", Lifecycle.attach(fakeDoc([]), null) === false);
  const doc = fakeDoc(["stop-on-close"]);
  const calls = [];
  check("attach wires the checkbox when it exists", Lifecycle.attach(doc, (cmd, args) => calls.push([cmd, args])) === true);
  doc.nodes["stop-on-close"].checked = true;
  doc.nodes["stop-on-close"].listeners.change();
  check("the checkbox sends the choice to the host",
    calls.length === 1 && calls[0][0] === "lifecycle_step" &&
    calls[0][1].action === "close_behaviour" && calls[0][1].value === true,
    JSON.stringify(calls));
}

// ---- the reducer does not mutate its input ------------------------------
{
  const state = fresh();
  const before = JSON.stringify(state);
  Lifecycle.reduce(state, { type: "start", result: { started: true, pid: 1 } });
  check("reducing returns a new state, it does not edit the old one",
    JSON.stringify(state) === before);
}

// ---- the deadline is one number, in three files -------------------------
{
  check("the window and the host share the start deadline",
    Lifecycle.START_DEADLINE_MS === Host.STATUS_AFTER_START_MS,
    Lifecycle.START_DEADLINE_MS + " vs " + Host.STATUS_AFTER_START_MS);
  const pythonDeadline = /START_DEADLINE = ([\d.]+)/.exec(pythonSource);
  const pythonGrace = /STOP_GRACE = ([\d.]+)/.exec(pythonSource);
  check("the Python deadline matches the window's",
    pythonDeadline && Number(pythonDeadline[1]) * 1000 === Lifecycle.START_DEADLINE_MS,
    pythonDeadline && pythonDeadline[1]);
  check("the Python grace matches the window's",
    pythonGrace && Number(pythonGrace[1]) * 1000 === Lifecycle.STOP_GRACE_MS,
    pythonGrace && pythonGrace[1]);
}

// ---- no domain rules and no requests of its own -------------------------
{
  check("the lifecycle module asks the host, it does not fetch",
    !/\bfetch\(|XMLHttpRequest|\/api\//.test(source));
  check("the lifecycle module holds no library vocabulary",
    !/\btracks?\b|\bsongs?\b|\bplaylists?\b|\bmembers?\s*=|queue/i.test(
      source.replace(/\/\*[\s\S]*?\*\//g, "")));
  check("the module never spawns a process or touches a file",
    !/child_process|\bfs\.|exec\(|spawn\(/.test(source));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nall lifecycle checks passed");