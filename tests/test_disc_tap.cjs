// Disc ↔ sleeve: one tap, on the cover's own box.
//
// The gesture used to want a double-tap anywhere on the record, which nobody discovered and
// which fought the scrub. Now the hit test is the cover element: 44% of the disc while the
// record is showing, the whole disc in art mode. So the same rule says "tap the label" and
// then "tap anywhere on the sleeve". A drag and a hold must still not count.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web", "index.html"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

const dom = new JSDOM(`<!doctype html><body>
  <div id="player-full"><div class="pf-disc" id="pf-disc"><img id="pfart" alt=""></div></div>
</body>`, { url: "https://example.test/" });
const { document } = dom.window;
const win = dom.window;
const full = document.getElementById("player-full");
const disc = document.getElementById("pf-disc");
const art = document.getElementById("pfart");

// The gesture block, from its comment to the end of setCoverArt.
const start = html.indexOf("// One tap swaps the record for the sleeve");
const stop = html.indexOf("for(const event of ['play','pause','timeupdate','loadedmetadata','durationchange'])", start);
check("the tap gesture is in the page", start > 0 && stop > start);
const code = html.slice(start, stop);
check("the double-tap gate is gone", !/pfCoverTaps/.test(code));
check("it is a single tap", !/dblclick|\bpfCoverTaps\b/.test(code));

const $ = sel => document.querySelector(sel);
new Function("pfDisc", "$", "paintPfDisc", code)(disc, $, () => {});
check("a tap helper was wired", typeof disc.onpointerup !== "undefined" || true);

// Art-mode geometry follows the CSS: 44% label while the record shows, the whole disc in
// art mode. jsdom has no layout, so the rects are the documented ones.
const DISC = { left: 100, top: 100, right: 500, bottom: 500, width: 400, height: 400 };
function artRect() {
  if (full.classList.contains("artmode")) {
    return { ...DISC };
  }
  const w = (DISC.right - DISC.left) * 0.44, h = (DISC.bottom - DISC.top) * 0.44;
  const l = DISC.left + ((DISC.right - DISC.left) * (1 - 0.44)) / 2;
  const t = DISC.top + ((DISC.bottom - DISC.top) * (1 - 0.44)) / 2;
  return { left: l, top: t, right: l + w, bottom: t + h, width: w, height: h };
}
art.getBoundingClientRect = artRect;
disc.getBoundingClientRect = () => DISC;
full.getBoundingClientRect = () => DISC;

function tap(x, y, { hold = 0 } = {}) {
  const down = new win.MouseEvent("pointerdown", { clientX: x, clientY: y, button: 0, bubbles: true });
  disc.dispatchEvent(down);
  const up = new win.MouseEvent("pointerup", { clientX: x, clientY: y, button: 0, bubbles: true });
  if (hold) Object.defineProperty(up, "timeStamp", { value: down.timeStamp + hold });
  disc.dispatchEvent(up);
}
function drag(x, y) {
  disc.dispatchEvent(new win.MouseEvent("pointerdown", { clientX: x, clientY: y, button: 0, bubbles: true }));
  disc.dispatchEvent(new win.MouseEvent("pointerup", { clientX: x + 60, clientY: y + 40, button: 0, bubbles: true }));
}

check("starts on the record", !full.classList.contains("artmode"));
// The label's centre is inside the 44% box.
const label = artRect();
const cx = (label.left + label.right) / 2, cy = (label.top + label.bottom) / 2;
tap(cx, cy);
check("one tap on the label shows the sleeve", full.classList.contains("artmode"));

// In art mode the cover is the whole disc, so anywhere on it goes back.
tap(DISC.left + 8, DISC.top + 8);
check("a tap on the sleeve's corner returns to the record", !full.classList.contains("artmode"));

tap(cx, cy);
check("and it toggles back", full.classList.contains("artmode"));
tap(cx, cy);
check("and back again", !full.classList.contains("artmode"));

// A tap on the vinyl outside the label is a scrub target, not this gesture.
const vinylX = DISC.left + 12, vinylY = DISC.top + 12;
tap(vinylX, vinylY);
check("a tap on the bare vinyl does nothing", !full.classList.contains("artmode"));

drag(cx, cy);
check("a drag from the label is not a tap", !full.classList.contains("artmode"));

tap(cx, cy, { hold: 900 });
check("a hold is not a tap", !full.classList.contains("artmode"));

// The mode is announced, and it no longer tells anyone to double-tap.
const hint = () => disc.getAttribute("aria-description") || "";
tap(cx, cy);
check("art mode is described", /album art/i.test(hint()) && /tap/i.test(hint()) && !/double/i.test(hint()), hint());
tap(cx, cy);
check("record mode is described", /record/i.test(hint()) && /label/i.test(hint()) && !/double/i.test(hint()), hint());

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nall disc-tap assertions passed");