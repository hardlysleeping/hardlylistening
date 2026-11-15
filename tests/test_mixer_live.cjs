// The room layer on the mixer page: remote values reach the controls, local
// movement is published, claims paint a badge, and a hand on a control is never
// overwritten while it is holding it.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(path.join(__dirname, "..", "web", "dj.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const start = script.indexOf("// ---------------------------------------------------------------- shared room");
if (start < 0) { console.log("FAIL cannot slice the room layer"); process.exit(1); }
const block = script.slice(start);

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

class FakeNode {
  constructor(tag = "div") {
    this.tag = tag; this.value = "0"; this.dataset = {}; this.style = {
      setProperty(k, v) { this[k] = v; }, removeProperty(k) { delete this[k]; }, getPropertyValue(k) { return this[k] || ""; }
    };
    this.children = []; this.classes = new Set(); this.handlers = {}; this.host = null;
    this.classList = {
      add: c => this.classes.add(c), remove: c => this.classes.delete(c),
      contains: c => this.classes.has(c), toggle: (c, on) => (on ? this.classes.add(c) : this.classes.delete(c))
    };
  }
  get className() { return [...this.classes].join(" "); }
  set className(v) { this.classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  set textContent(v) { this._text = v; } get textContent() { return this._text || ""; }
  set innerHTML(v) { this._html = v; } get innerHTML() { return this._html || ""; }
  closest(sel) {
    if (sel === ".deck") return this.deckNode || null;
    return this.host && this.host.matches(sel) ? this.host : null;
  }
  matches(sel) { return (this.selectors || []).includes(sel); }
  querySelector(sel) {
    if (sel === ":scope > .claim") return this.children.find(n => n.classes.has("claim")) || null;
    return null;
  }
  appendChild(node) { this.children.push(node); node.host = this; return node; }
  remove() { if (this.host) this.host.children = this.host.children.filter(n => n !== this); }
  addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); }
  dispatchEvent(event) { (this.handlers[event.type] || []).forEach(fn => fn(event)); return true; }
  setAttribute() {}
}

function build() {
  const posts = [];
  const hosts = {};
  const controls = {};
  const keys = ["A.low", "A.mid", "A.high", "A.filter", "A.reverb", "A.delay", "A.pitch", "A.volume",
                "B.low", "B.mid", "B.high", "B.filter", "B.reverb", "B.delay", "B.pitch", "B.volume"];
  for (const key of keys) {
    const [side, name] = key.split(".");
    const input = new FakeNode("input");
    input.dataset = { ctl: name };
    const host = new FakeNode();
    host.selectors = [".ctl"];
    input.host = host;
    input.deckNode = { dataset: { deck: side } };
    controls[key] = input;
  }
  const cross = new FakeNode("input"); cross.id = "crossfader";
  const crossHost = new FakeNode(); crossHost.selectors = [".cross"]; cross.host = crossHost;
  const master = new FakeNode("input"); master.id = "master";
  const masterHost = new FakeNode(); masterHost.selectors = [".cross"]; master.host = masterHost;

  const nodes = {
    "#crossfader": cross, "#master": master, "#roster": new FakeNode(), "#livechip": new FakeNode(),
    "#joinaudio": new FakeNode(), "#playA": new FakeNode(), "#playB": new FakeNode()
  };
  for (const side of ["A", "B"]) {
    nodes["#audio" + side] = Object.assign(new FakeNode("audio"), {
      duration: 200, currentTime: 0, src: "/api/track/1/audio", paused: true,
      playCalls: 0,
      play() { this.playCalls++; this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; }
    });
    nodes["#seek" + side] = new FakeNode("input");
    nodes["#jog" + side] = new FakeNode();
    nodes["#qclear" + side] = new FakeNode();
  }

  const ctx = {
    console,
    MAX_QUEUE: 12,
    SIDES: ["A", "B"],
    queues: { A: [], B: [] },
    decks: { A: { track: null }, B: { track: null } },
    library: [{ id: 1, title: "One" }, { id: 2, title: "Two" }],
    trackById: id => ctx.library.find(t => t.id === +id),
    loadTrack(side, track) { ctx.decks[side] = { track }; ctx.loaded = (ctx.loaded || []).concat([[side, track.id]]); },
    renderQueues() { ctx.rendered = (ctx.rendered || 0) + 1; },
    saveQueues() { ctx.saved = (ctx.saved || 0) + 1; },
    pump() {},
    esc: s => String(s ?? ""),
    // No real Web Audio or media decoding: gesture handling stays silent.
    bootAudio() { ctx.audioBoots = (ctx.audioBoots || 0) + 1; },
    ctx: { resume: () => { ctx.audioResumes = (ctx.audioResumes || 0) + 1; return Promise.resolve(); } },
    localStorage: { getItem: () => null, setItem: () => {} },
    sessionStorage: { getItem: () => null, setItem: () => {} },
    URLSearchParams,
    Event: class { constructor(type) { this.type = type; } },
    EventSource: class {
      constructor(url) { this.url = url; ctx.source = this; }
      close() { this.closed = true; }
    },
    fetch: (url, options) => {
      posts.push({ url, body: options && options.body ? JSON.parse(options.body) : null });
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    },
    // run timers inline: the real page throttles sends, the test wants the result
    setInterval: () => 0, setTimeout: fn => { fn(); return 1; }, clearTimeout: () => {},
    document: {
      listeners: {},
      addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
      querySelector(sel) {
        const match = /^\.deck\[data-deck="([AB])"\] \[data-ctl="([a-z]+)"\]$/.exec(sel);
        if (match) return controls[match[1] + "." + match[2]] || null;
        return null;
      },
      createElement: tag => new FakeNode(tag)
    },
    $: sel => nodes[sel] || new FakeNode()
  };
  ctx.posts = posts; ctx.nodes = nodes; ctx.controls = controls;
  ctx.fire = (type, target) => (ctx.document.listeners[type] || []).forEach(fn => fn({ type, target }));
  vm.createContext(ctx);
  vm.runInContext(block + "\nthis.__api = {applyValues, applyRemoteDeck, applyQueue, paintRoster, mergeClaims, keyOfControl, controlElement, roomEvent, claimsClear, sendValuesSoon, publishQueue, initials};", ctx);
  return ctx;
}

// Tests read promise-based behaviour, so the body is async.
(async () => {
// ---- control mapping ----------------------------------------------------
{
  const ctx = build();
  check("a deck fader maps to A.low", ctx.__api.keyOfControl(ctx.controls["A.low"]) === "A.low");
  check("the crossfader maps to cross", ctx.__api.keyOfControl(ctx.nodes["#crossfader"]) === "cross");
  check("the master maps to master", ctx.__api.keyOfControl(ctx.nodes["#master"]) === "master");
  check("an unrelated node has no key", ctx.__api.keyOfControl({ closest: () => null }) === "");
  check("a control resolves back to its element", ctx.__api.controlElement("A.low") === ctx.controls["A.low"]);
}

// ---- room -> controls ---------------------------------------------------
{
  const ctx = build();
  let handled = 0;
  ctx.controls["A.low"].addEventListener("input", () => { handled += 1; });
  ctx.__api.applyValues({ "A.low": -6 });
  check("a remote value reaches the input", ctx.controls["A.low"].value === -6, ctx.controls["A.low"].value);
  check("the page's own input handler runs so audio follows", handled === 1, String(handled));
}
{
  const ctx = build();
  ctx.__api.roomEvent({ type: "patch", values: { "B.volume": 30 } });
  check("patches apply as well as full state", ctx.controls["B.volume"].value === 30);
}
{
  const ctx = build();
  // A hand on A.high holds it: a remote value must not pull it away mid-drag.
  ctx.fire("pointerdown", ctx.controls["A.high"]);
  ctx.__api.applyValues({ "A.high": 9 });
  check("a control being dragged is never overwritten",
        ctx.controls["A.high"].value === "0", ctx.controls["A.high"].value);
  check("another control still follows the room", (() => {
    ctx.__api.applyValues({ "A.mid": 4 });
    return ctx.controls["A.mid"].value === 4;
  })());
}

// ---- local movement -> room ---------------------------------------------
{
  const ctx = build();
  ctx.controls["A.mid"].value = "-3";
  ctx.fire("input", ctx.controls["A.mid"]);
  const sent = ctx.posts.map(p => p.body).filter(Boolean);
  check("moving a fader publishes its value",
        sent.some(b => b.op === "values" && b.values["A.mid"] === -3), JSON.stringify(sent));
  check("the publish carries the tab identity", !!ctx.posts[0] && ctx.posts[0].url.includes("cid="),
        ctx.posts[0] && ctx.posts[0].url);
}
{
  const ctx = build();
  ctx.nodes["#crossfader"].value = "70";
  ctx.fire("input", ctx.nodes["#crossfader"]);
  const sent = ctx.posts.map(p => p.body).filter(Boolean);
  check("the crossfader publishes too", sent.some(b => b.values && b.values.cross === 70), JSON.stringify(sent));
}
{
  const ctx = build();
  ctx.queues.A = [1, 2];
  ctx.__api.publishQueue("A");
  const sent = ctx.posts.map(p => p.body).filter(Boolean);
  check("a queue edit publishes the whole order",
        sent.some(b => b.op === "queue" && b.side === "A" && b.action === "set" && String(b.ids) === "1,2"),
        JSON.stringify(sent));
}
{
  const ctx = build();
  ctx.fire("pointerdown", ctx.controls["B.low"]);
  check("touching a control announces the claim",
        ctx.posts.some(p => p.body && p.body.op === "claim"), JSON.stringify(ctx.posts.map(p => p.body)));
}

// ---- decks --------------------------------------------------------------
{
  const ctx = build();
  ctx.__api.applyRemoteDeck("A", { track_id: 2, playing: false, pos: 0, at: Date.now() / 1000, rate: 1 });
  check("a remote deck load loads the track locally",
        ctx.decks.A.track && ctx.decks.A.track.id === 2, JSON.stringify(ctx.decks.A));
}
{
  const ctx = build();
  ctx.decks.B = { track: { id: 2 } };
  ctx.__api.applyRemoteDeck("B", { track_id: 2, playing: false, pos: 40, at: Date.now() / 1000, rate: 1 });
  check("a stopped deck seeks to the room's position",
        Math.abs(ctx.nodes["#audioB"].currentTime - 40) < 0.05, String(ctx.nodes["#audioB"].currentTime));
}
{
  const ctx = build();
  ctx.nodes["#joinaudio"].hidden = true;
  ctx.nodes["#playA"].textContent = "Play";
  ctx.__api.applyRemoteDeck("A", { track_id: 1, playing: true, pos: 10, at: Date.now() / 1000 - 10, rate: 1 });
  await Promise.resolve();
  check("a playing remote deck waits for the Join audio gesture",
        ctx.nodes["#audioA"].paused === true && ctx.nodes["#audioA"].playCalls === 0);
  check("the gesture-required Join audio button is shown", ctx.nodes["#joinaudio"].hidden === false);
  check("the play button stays Play before the gesture", ctx.nodes["#playA"].textContent === "Play");
  check("remote playback does not boot or resume audio before the gesture", !ctx.audioBoots && !ctx.audioResumes);
  // Invoke the actual production click handler, not a test-only gate override.
  await ctx.nodes["#joinaudio"].onclick();
  await Promise.resolve();
  check("Join audio boots and resumes the fake context", ctx.audioBoots === 1 && ctx.audioResumes === 1);
  check("a playing deck starts playing after Join audio",
        ctx.nodes["#audioA"].paused === false && ctx.nodes["#audioA"].playCalls === 1);
  check("its play button flips", ctx.nodes["#playA"].textContent === "Pause", ctx.nodes["#playA"].textContent);
  check("Join audio hides after the gesture", ctx.nodes["#joinaudio"].hidden === true);
  ctx.__api.applyRemoteDeck("A", { track_id: 1, playing: false, pos: 20, at: Date.now() / 1000, rate: 1 });
  check("stopping the deck pauses it", ctx.nodes["#audioA"].paused === true);
}

// ---- queues -------------------------------------------------------------
{
  const ctx = build();
  ctx.__api.applyQueue("B", [2, 1]);
  check("a remote queue order replaces the local one", String(ctx.queues.B) === "2,1", String(ctx.queues.B));
  check("the queue is repainted", ctx.rendered >= 1);
  const painted = ctx.rendered;
  ctx.__api.applyQueue("B", [2, 1]);
  check("an identical order causes no extra repaint", ctx.rendered === painted);
}

// ---- people and claims --------------------------------------------------
{
  const ctx = build();
  const members = [
    { id: "local:me", name: "Alex Rider", colour: "#58d3a8" },
    { id: "local:other", name: "Sam", colour: "#7aa2ff" }
  ];
  ctx.__api.roomEvent({ type: "state", you: "local:me", values: {}, claims: {}, members, queues: {}, decks: {} });
  const html = ctx.nodes["#roster"].innerHTML;
  check("the roster lists everyone", html.includes("Alex Rider") && html.includes("Sam"), html);
  check("the roster marks you", html.includes("· you"), html);
  check("each person carries their colour", html.includes("#58d3a8") && html.includes("#7aa2ff"), html);
  check("names are shown as initials in the avatar", html.includes(">AR<") || html.includes("AR"), html);
}
{
  const ctx = build();
  ctx.__api.mergeClaims({ "A.low": { id: "local:other", name: "Sam", colour: "#7aa2ff" } });
  const host = ctx.controls["A.low"].host;
  const badge = host.children.find(n => n.classes.has("claim"));
  check("someone holding a control gets a badge", !!badge);
  check("the badge is their initial", badge && badge.textContent === "S", badge && badge.textContent);
  check("the badge uses their colour", badge && badge.style.getPropertyValue("--claim") === "#7aa2ff");
  check("the control is marked as claimed", ctx.controls["A.low"].classes.has("claimed"));
  ctx.__api.mergeClaims({ "A.low": null });
  check("releasing removes the badge", !host.children.some(n => n.classes.has("claim")));
  check("and the claimed mark", !ctx.controls["A.low"].classes.has("claimed"));
}
{
  const ctx = build();
  ctx.__api.roomEvent({ type: "state", you: "local:me", values: {}, claims: {}, members: [], queues: {}, decks: {} });
  ctx.__api.mergeClaims({ "cross": { id: "local:me", name: "Alex", colour: "#58d3a8" } });
  const badge = ctx.nodes["#crossfader"].host.children.find(n => n.classes.has("claim"));
  check("your own claim reads 'you'", badge && badge.textContent === "you", badge && badge.textContent);
}


if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nMixer room UI PASS: mapping, remote values, drag guard, publishing, decks, queues, roster, claims");
})().catch(error => { console.log('FAIL harness — ' + error.message); process.exit(1); });
