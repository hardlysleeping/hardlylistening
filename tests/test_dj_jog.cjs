// The scrub discs: one turn of the record is a fixed number of seconds, drags
// measure from where they started, seeking clamps, and a phone never shows them.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(path.join(__dirname, "..", "web", "dj.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const start = script.indexOf("// ---------------------------------------------------------------- jog discs");
const end = script.indexOf("function wireDeck(");
if (start < 0 || end < 0) { console.log("FAIL cannot slice the jog block"); process.exit(1); }

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

function makeContext(duration = 200) {
  const handlers = {};
  const face = { style: {} };
  const disc = {
    classList: {
      set: new Set(["off"]),
      add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
      contains(c) { return this.set.has(c); },
      toggle(c, on) { const want = on === undefined ? !this.set.has(c) : !!on; want ? this.set.add(c) : this.set.delete(c); }
    },
    handlers,
    addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 60, height: 60 }),
    setPointerCapture() {}, hasPointerCapture: () => true, releasePointerCapture() {},
    querySelector: () => face, setAttribute(k, v) { this[k] = v; },
    face
  };
  const audio = { duration, currentTime: 0, paused: true };
  const seek = { value: 0 };
  const label = { textContent: "" };
  const nodes = { "#jogA": disc, "#seekA": seek, "#timeA": label, "#audioA": audio };
  const ctx = {
    $: sel => nodes[sel] || null,
    bootAudio() {}, fmt: s => `${Math.floor((s || 0) / 60)}:${String(Math.floor((s || 0) % 60)).padStart(2, "0")}`,
    console
  };
  vm.createContext(ctx);
  vm.runInContext(script.slice(start, end) + "\nthis.wireJog = wireJog; this.paintJog = paintJog;", ctx);
  ctx.wireJog("A");
  const fire = (type, x, y, extra = {}) =>
    (handlers[type] || []).forEach(fn => fn({ clientX: x, clientY: y, pointerId: 1, preventDefault() {}, ...extra }));
  return { ctx, disc, audio, seek, label, fire, face };
}

// Angles are measured from the disc centre (30,30) with atan2.
const RIGHT = [60, 30], DOWN = [30, 60], LEFT = [0, 30], UP = [30, 0];

// ---- pure maths ---------------------------------------------------------
{
  const { ctx } = makeContext();
  check("one turn is 12 seconds", ctx.jogSeconds(2 * Math.PI) === 12, String(ctx.jogSeconds(2 * Math.PI)));
  check("a quarter turn is 3 seconds", Math.round(ctx.jogSeconds(Math.PI / 2)) === 3);
  check("half a turn backwards is 6 seconds back", Math.round(ctx.jogSeconds(-Math.PI)) === -6);
  check("the seam wraps rather than jumping a full turn",
        Math.abs(ctx.jogWrap(Math.PI + 0.2) - (-Math.PI + 0.2)) < 1e-9, String(ctx.jogWrap(Math.PI + 0.2)));
  check("seeking clamps at the start", ctx.jogTarget(1, -Math.PI, 200) === 0);
  check("seeking clamps at the end", ctx.jogTarget(199, Math.PI, 200) === 200);
  check("no duration means no target", ctx.jogTarget(5, Math.PI, 0) === 0);
}

// ---- dragging -----------------------------------------------------------
{
  const { audio, fire, disc, label, seek } = makeContext(200);
  audio.currentTime = 100;
  fire("pointerdown", ...RIGHT);
  fire("pointermove", ...DOWN);          // +90° → +3 s
  check("a quarter turn forward moves 3 s", Math.round(audio.currentTime) === 103, String(audio.currentTime));
  check("the disc turns with the hand", disc.face.style.transform === "rotate(90.0deg)", disc.face.style.transform);
  check("the seek bar follows", Math.round(seek.value) === Math.round((103 / 200) * 1000), String(seek.value));
  check("the time label follows", label.textContent.startsWith("1:43"), label.textContent);
  fire("pointermove", ...LEFT);          // +180° total → +6 s
  check("half a turn moves 6 s", Math.round(audio.currentTime) === 106, String(audio.currentTime));
  fire("pointerup", ...LEFT);

  audio.currentTime = 1;
  fire("pointerdown", ...RIGHT);
  fire("pointermove", ...UP);            // -90° → clamps at 0
  check("dragging back past the start clamps to zero", audio.currentTime === 0, String(audio.currentTime));
  fire("pointerup", ...UP);

  audio.currentTime = 50;
  fire("pointerdown", ...RIGHT);
  fire("pointermove", ...DOWN);
  check("a second drag does not compound the first", Math.round(audio.currentTime) === 53, String(audio.currentTime));
  fire("pointerup", ...DOWN);
  check("releasing clears the grabbing state", !disc.classList.contains("dragging"));
}

// ---- keyboard -----------------------------------------------------------
{
  const { audio, disc, ctx } = makeContext(200);
  audio.currentTime = 20;
  const key = (k, extra = {}) => disc.handlers.keydown.forEach(fn => fn({ key: k, preventDefault() {}, ...extra }));
  key("ArrowRight");
  check("ArrowRight nudges one second", audio.currentTime === 21, String(audio.currentTime));
  key("ArrowRight", { shiftKey: true });
  check("Shift+Arrow nudges ten", audio.currentTime === 31, String(audio.currentTime));
  key("Home");
  check("Home rewinds", audio.currentTime === 0);
  key("Enter");
  check("other keys are ignored", audio.currentTime === 0);
}

// ---- nothing loaded -----------------------------------------------------
{
  const { audio, disc, fire } = makeContext(NaN);
  fire("pointerdown", ...RIGHT);
  fire("pointermove", ...DOWN);
  check("an empty deck does not seek", audio.currentTime === 0, String(audio.currentTime));
  check("an empty deck does not grab the pointer", !disc.classList.contains("dragging"));
  disc.classList.add("off");
  (disc.handlers.pointerup || []).forEach(fn => fn({ pointerId: 1 }));
  check("the disc stays dim with no track", disc.classList.contains("off"));
}

// ---- placement ----------------------------------------------------------
{
  const dom = new (require("jsdom").JSDOM)(html);
  const doc = dom.window.document;
  for (const side of ["A", "B"]) {
    const jog = doc.getElementById("jog" + side);
    check(`deck ${side} has a disc`, !!jog);
    check(`deck ${side} disc is keyboard reachable`, jog?.getAttribute("tabindex") === "0" && jog?.getAttribute("role") === "slider");
    check(`deck ${side} disc sits beside the seek bar`,
          jog?.parentElement?.classList.contains("scrubrow") && !!jog.parentElement.querySelector("#seek" + side), side);
  }
  check("discs are hidden on a phone", /@media \(max-width:900px\)\{[^}]*\.jog\{display:none\}/.test(html));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nJog discs PASS: 12 s per turn, per-drag baseline, clamping, keyboard, hidden on phones");
