// The platter turns with the deck: its rotation speed is the deck's playback rate times
// 33 1/3 RPM, read live, so a pitch fader move is picked up on the next frame. The
// library's record is the fixed one — 1.8 s a turn whatever the player is doing — and
// that stays that way on purpose.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const WEB = path.join(__dirname, "..", "web");
const html = fs.readFileSync(path.join(WEB, "dj.html"), "utf8");
const css = fs.readFileSync(path.join(WEB, "styles", "components", "player.css"), "utf8");
const page = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const start = script.indexOf("// ---------------------------------------------------------------- jog discs");
const end = script.indexOf("function wireDeck(");
if (start < 0 || end < 0) { console.log("FAIL cannot slice the jog block"); process.exit(1); }

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

function makeContext(rate = 1) {
  const face = side => ({ style: {} });
  const disc = () => {
    const f = face();
    return {
      classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
        contains(c) { return this.set.has(c); }, toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
      handlers: {}, addEventListener() {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 60, height: 60 }),
      setPointerCapture() {}, hasPointerCapture: () => true, releasePointerCapture() {},
      querySelector: () => f, setAttribute() {}, face: f
    };
  };
  const nodes = {};
  const audios = {};
  for (const id of ["A", "B"]) {
    nodes["#jog" + id] = disc();
    nodes["#seek" + id] = { value: 0 };
    nodes["#time" + id] = { textContent: "" };
    audios[id] = nodes["#audio" + id] = { duration: 200, currentTime: 0, paused: false, playbackRate: rate };
  }
  const frames = [];
  const ctx = {
    $: sel => nodes[sel] || null,
    document: { getElementById: id => nodes["#" + id] || null },
    requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
    fmt: s => `${Math.floor((s || 0) / 60)}:${String(Math.floor((s || 0) % 60)).padStart(2, "0")}`,
    console
  };
  vm.createContext(ctx);
  vm.runInContext(script.slice(start, end) +
    "\nthis.jogSpinTick = jogSpinTick; this.paintJog = paintJog; this.JOG_TURNS = JOG_TURNS;" +
    " this.JOG_SPIN_DEG = JOG_SPIN_DEG;", ctx);
  return { ctx, nodes, audios, frames };
}

// Drive the spin loop by hand: the harness only has to supply the timestamps a real
// requestAnimationFrame would. Degrees are read off the disc face, which is what the
// eye sees, not an internal counter.
function spin(ctx, from, ms, step = 50) {
  let stamp = from;
  for (let i = 0; i < Math.round(ms / step); i++) ctx.jogSpinTick(stamp += step);
  return stamp;
}
function degOf(node, id) {
  const m = /rotate\((-?[\d.]+)deg\)/.exec(node.querySelector(".jogface").style.transform || "");
  return m ? +m[1] : NaN;
}
// Degrees per second measured from the drawn angle, so a change in the rate is what the
// test sees — the same thing a person watching the platter sees.
function degPerSecond(rate) {
  const { ctx, nodes } = makeContext(rate);
  const t0 = 1000;
  ctx.jogSpinTick(t0);                // first frame only seeds the clock
  const t1 = spin(ctx, t0, 500);
  const deg = degOf(nodes["#jogA"], "A");
  return { degPerSec: deg / ((t1 - t0) / 1000), deg, seconds: 360 / (deg / ((t1 - t0) / 1000)) };
}

// ---- the platter follows the deck rate ----------------------------------
{
  const one = degPerSecond(1);
  check("a platter at 1.00× takes 1.8 s a turn (33 1/3 RPM)",
        Math.abs(one.seconds - 1.8) < 0.02, one.seconds.toFixed(3) + " s");
  check("33 1/3 RPM is 200°/s at 1.00×", Math.abs(one.degPerSec - 200) < 2, one.degPerSec.toFixed(1) + "°/s");

  const fast = degPerSecond(1.08);
  check("pitch +8% spins the platter 8% faster",
        Math.abs(fast.degPerSec / one.degPerSec - 1.08) < 0.005,
        (fast.degPerSec / one.degPerSec).toFixed(4) + "×");
  check("so a turn takes 1.8/1.08 s", Math.abs(fast.seconds - 1.8 / 1.08) < 0.02, fast.seconds.toFixed(3) + " s");

  const slow = degPerSecond(0.92);
  check("pitch −8% spins the platter 8% slower",
        Math.abs(slow.degPerSec / one.degPerSec - 0.92) < 0.005,
        (slow.degPerSec / one.degPerSec).toFixed(4) + "×");
}

// ---- a pitch move on a live deck is picked up without a reload ----------
{
  const { ctx, nodes, audios } = makeContext(1);
  ctx.jogSpinTick(1000);
  let stamp = spin(ctx, 1000, 200);
  const before = degOf(nodes["#jogA"], "A");

  audios.A.playbackRate = 1.08;                 // the pitch fader, while the deck plays
  stamp = spin(ctx, stamp, 100);
  const after = degOf(nodes["#jogA"], "A");
  const ratio = ((after - before) / 100) / (before / 200);
  check("a pitch change mid-play changes the very next frames",
        Math.abs(ratio - 1.08) < 0.01, ratio.toFixed(4) + "×");
  check("the platter keeps turning from where it was (no jump)",
        after > before && Math.abs(after - before - 1.08 * (before / 2)) < 1.5, `${before} → ${after}`);

  audios.A.playbackRate = 1;                    // and back to 0%
  const back = degOf(nodes["#jogA"], "A");
  stamp = spin(ctx, stamp, 100);
  const ratioBack = ((degOf(nodes["#jogA"], "A") - back) / 100) / (before / 200);
  check("returning the fader to 0% brings the platter back to 1.8 s",
        Math.abs(ratioBack - 1) < 0.01, ratioBack.toFixed(4) + "×");
}

// ---- the hand owns the disc while it is being dragged -------------------
{
  const { ctx, nodes } = makeContext(1);
  nodes["#jogB"].classList.add("dragging");
  ctx.jogSpinTick(1000);
  ctx.jogSpinTick(1050);
  check("a disc under the hand does not also spin",
        !nodes["#jogB"].querySelector(".jogface").style.transform,
        String(nodes["#jogB"].querySelector(".jogface").style.transform));
  check("the other deck is unaffected", degOf(nodes["#jogA"], "A") > 0);
}

// ---- a paused deck's platter stops -------------------------------------
{
  const { ctx, nodes, audios } = makeContext(1);
  ctx.jogSpinTick(1000);
  let stamp = spin(ctx, 1000, 200);
  const turned = degOf(nodes["#jogA"], "A");
  audios.A.paused = true;
  stamp = spin(ctx, stamp, 200);
  check("pause stops the platter where it is", degOf(nodes["#jogA"], "A") === turned, String(turned));
}

// ---- the rate comes from the element, not a stored copy ----------------
{
  const block = script.slice(start, end);
  check("the spin loop reads the deck's playbackRate each frame",
        /const rate = audio\.playbackRate \|\| 1/.test(block) &&
        /JOG_SPIN_DEG \* rate/.test(block));
  check("no second, cached rate exists to go stale",
        !/JOG_RATE|deckRate|setInterval/.test(block));
  const { ctx } = makeContext(1);
  check("JOG_SPIN_DEG is 33 1/3 RPM against the 1.8 s library turn",
        Math.abs(ctx.JOG_SPIN_DEG * 1.8 - 360) < 1e-9, String(ctx.JOG_SPIN_DEG));
}

// ---- the library disc stays the fixed one ------------------------------
{
  check("the library record spins on a fixed 1.8 s CSS animation",
        /#player-full \.pf-disc\.spinning\{animation:pf-vinyl-spin 1\.8s linear infinite\}/.test(css));
  check("its phase is a function of the playhead alone",
        /PF_DISC_SECONDS_PER_ROTATION = 1\.8;/.test(page) &&
        /\(time%PF_DISC_SECONDS_PER_ROTATION\)/.test(page));
  check("nothing rescales that animation by a playback rate",
        !/pf-disc[^\n]*animationDuration/.test(page + css) &&
        !/pf-vinyl-spin[^\n]*playbackRate/.test(page + css));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nMixer platter PASS: 33 1/3 RPM × deck rate, live after a pitch move, library disc fixed at 1.8 s");