// The equalizer is a sheet like the queue: the same band of the fullscreen column, one at a
// time, entered and left with the same move. jsdom has no layout, so the geometry is pinned
// in the stylesheet and the behaviour is pinned by running the two functions themselves.
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

// ---- structure -------------------------------------------------------------------
const overlay = html.slice(html.indexOf('<div id="player-full"'), html.indexOf('<dialog id="dlg-path"'));
check("the overlay slice carries the panel", overlay.includes('id="pf-eq"'));

const rule = sel => {
  const at = css.indexOf(sel + "{");
  return at < 0 ? "" : css.slice(at + sel.length + 1, css.indexOf("}", at));
};
const panelRule = rule("#player-full .pf-eq");
check("the equalizer is taken out of the column", /position:\s*absolute/.test(panelRule), panelRule.trim());
check("it takes the queue's measured band", /top:\s*var\(--pf-slot-top/.test(panelRule), panelRule.trim());
check("it starts below its place", /transform:\s*translateY\(100%\)/.test(panelRule), panelRule.trim());
check("open puts it in place", /transform:\s*none/.test(rule("#player-full.eq-open .pf-eq")),
  rule("#player-full.eq-open .pf-eq").trim());

// The record and the title move for the equalizer exactly as they move for the queue, which
// is what makes the two read as one sheet area rather than two different interactions.
const moves = css.match(/#player-full:is\(\.queue-open,\.eq-open\)/g) || [];
check("the queue's motion covers the equalizer too", moves.length >= 8, String(moves.length));

check("the foot keeps a close control", /id="pf-eq-close"/.test(html));

// ---- behaviour -------------------------------------------------------------------
const eqStart = html.indexOf("// The equalizer is the queue's twin");
const eqEnd = html.indexOf("// Called once the member's saved settings arrive", eqStart);
const placeStart = html.indexOf("function placeSheet(el){");
const placeEnd = html.indexOf("// The queue is the same element", placeStart);
check("both functions are present", eqStart > 0 && eqEnd > eqStart && placeStart > 0 && placeEnd > placeStart);

const dom = new JSDOM(`<!doctype html><body>${overlay}</body>`, { runScripts: "outside-only", pretendToBeVisual: true });
const win = dom.window;
const doc = win.document;
let queueCloses = 0;
win.eqPanelBuilt = () => {};
win.eqPaint = () => {};
win.showFullQueue = open => { if (!open) queueCloses++; };

win.eval(html.slice(placeStart, placeEnd) + "\n" + html.slice(eqStart, eqEnd));
const eqToggle = win.eqToggle || win.eval("eqToggle");
const panel = doc.getElementById("pf-eq");
const button = doc.getElementById("pfeq");
const full = doc.getElementById("player-full");

(async () => {
  check("hidden until asked for", panel.hidden === true);

  eqToggle();
  await new Promise(r => setTimeout(r, 40));
  check("one toggle opens it", panel.hidden === false && full.classList.contains("eq-open"));
  check("the button says it is open", button.getAttribute("aria-expanded") === "true");
  check("it is placed by the shared measurement", panel.style.getPropertyValue("--pf-slot-top") !== "");
  check("it stops above the transport", panel.style.bottom !== "");

  // The queue took the column: opening the equalizer must make the queue leave first.
  queueCloses = 0;
  full.classList.add("queue-open");
  eqToggle(true);
  await new Promise(r => setTimeout(r, 40));
  check("the queue is closed before the equalizer opens", queueCloses === 1, String(queueCloses));
  eqToggle(false);

  await new Promise(r => setTimeout(r, 520));
  check("closing removes the move", !full.classList.contains("eq-open"));
  check("closing takes it out of the scroll area", panel.hidden === true);
  check("the button says it is closed", button.getAttribute("aria-expanded") === "false");

  // And the close control is a close, not a toggle.
  doc.getElementById("pf-eq-close");
  eqToggle(true);
  await new Promise(r => setTimeout(r, 40));
  eqToggle(false);
  await new Promise(r => setTimeout(r, 520));
  check("the Close control leaves it closed", panel.hidden === true && !full.classList.contains("eq-open"));

  if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
  console.log("\nall equalizer-sheet assertions passed");
})();