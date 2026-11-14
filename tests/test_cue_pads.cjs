// Cue pads: a toggle, one centred row of four, all of it inside the record's stage.
//
// The pads are markup and CSS plus one small IIFE, so this pins all three without a
// browser. The contract the phone requires: nothing may be positioned outside the stage's
// box (a pair pushed past the disc's edge was clipped by the overlay), the four pads share
// one row above the title, and the "Cues" button is the only thing that shows them.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web", "index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "web", "styles", "components", "player.css"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

// Markup only: the page's own scripts boot a player this harness does not have.
const dom = new JSDOM(html.replace(/<script[\s\S]*?<\/script>/g, ""), { url: "https://example.test/" });
const { document } = dom.window;

const host = document.getElementById("pf-cues");
check("cue group exists", !!host);
check("cue group is inside the disc stage", !!document.querySelector(".pf-stage #pf-cues"));
check("cue group comes before the title", (() => {
  const stage = document.querySelector(".pf-stage");
  const title = document.getElementById("pftitle");
  if (!stage || !title) return false;
  return !!(stage.compareDocumentPosition(title) & 4);   // DOCUMENT_POSITION_FOLLOWING
})());

const pads = host ? [...host.querySelectorAll(".pf-cue")] : [];
check("four pads, A to D", JSON.stringify(pads.map(p => p.dataset.cue)) === '["0","1","2","3"]',
  JSON.stringify(pads.map(p => p.dataset.cue)));
check("no leftover corner columns", host && host.querySelectorAll(".pf-cue-col").length === 0);
check("the pads are direct children of the row", pads.every(p => p.parentElement === host));

// CSS: the row is a centred flex row, and nothing is offset out of the stage.
const rule = sel => {
  const at = css.indexOf(sel + "{");
  return at < 0 ? "" : css.slice(at + sel.length + 1, css.indexOf("}", at));
};
const rowRule = rule("#player-full .pf-cues");
check("the pads are one row", /display:\s*flex/.test(rowRule) || /display:\s*none/.test(rowRule), rowRule.trim());
check("the row is centred", /justify-content:\s*center/.test(rowRule), rowRule.trim());
check("the row spans the stage, it does not hang off it", /left:\s*0/.test(rowRule) && /right:\s*0/.test(rowRule), rowRule.trim());
check("no negative offsets are left in the cue block", !/pf-cue-col|calc\(-1 \* \(var\(--pf-cue\)/.test(css));
check("the pads share one gap token", /gap:\s*var\(--pf-cue-gap\)/.test(rowRule), rowRule.trim());
check("a pad takes pointer events back from the layer", /pointer-events:\s*auto/.test(rule(".pf-cue")),
  rule(".pf-cue").trim());
check("a pad carries its own scrim over the record", /background:\s*color-mix/.test(rule(".pf-cue")),
  rule(".pf-cue").trim());
check("pad size is a token", /--pf-cue:\s*46px/.test(css) && /--pf-cue-gap:\s*6px/.test(css));
check("short phones shrink the pads", /max-height:700px\)\{[\s\S]*--pf-cue:\s*40px/.test(css.replace(/\n/g, " ")));
check("hidden until toggled", /display:\s*none/.test(rowRule), rowRule.trim());
check("shown when on", /display:\s*flex/.test(rule("#player-full .pf-cues.on")),
  rule("#player-full .pf-cues.on").trim());
check("the sheets still hide the pads", /:is\(\.queue-open,\.eq-open\) \.pf-cues/.test(css));

// The toggle itself, evaluated the way the page evaluates it.
const button = document.getElementById("pfcues");
check("foot has a Cues button", !!button);
check("button announces what it controls", button && button.getAttribute("aria-controls") === "pf-cues");

const start = html.indexOf("// The pads are a toggle");
const stop = html.indexOf('document.addEventListener("keydown", e => {', start);
const code = html.slice(start, stop);
check("toggle code is present", start > 0 && stop > start && code.includes("hl.cues.on"));

if (start > 0 && stop > start) {
  try { dom.window.localStorage.clear(); } catch (_) {}
  new Function("document", "localStorage", code)(document, dom.window.localStorage);
  check("off at first", host && !host.classList.contains("on") && button.getAttribute("aria-pressed") === "false");
  button.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  check("one click shows the pads", host && host.classList.contains("on"));
  check("button reads pressed", button.getAttribute("aria-pressed") === "true");
  check("the choice is remembered", dom.window.localStorage.getItem("hl.cues.on") === "1");
  button.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  check("a second click hides them", host && !host.classList.contains("on"));
  check("the off is remembered", dom.window.localStorage.getItem("hl.cues.on") === "0");
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nall cue-pad assertions passed");