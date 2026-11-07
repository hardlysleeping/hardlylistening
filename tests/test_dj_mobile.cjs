// Phone / PWA mixer layout: decks stay side by side, the crossfader sits under
// them, queues keep two columns, and nothing is removed to make it fit.
// jsdom has no layout engine, so this pins the rules and the deck's controls.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const file = path.join(__dirname, "..", "web", "dj.html");
const html = fs.readFileSync(file, "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

// The phone block is the last @media (max-width:900px) in the stylesheet.
const phoneStart = html.indexOf("/* ------------------------------------------------------------------ phone");
const phone = html.slice(phoneStart, html.indexOf("/* Landscape on a phone"));
if (phoneStart < 0) { console.log("FAIL cannot locate the phone block"); process.exit(1); }
check("phone block exists", phone.length > 500, String(phone.length));

const must = [
  ["decks share a row", ".decks{grid-template-columns:1fr 1fr"],
  ["deck A pinned to column 1", ".deck.a{grid-column:1"],
  ["deck B pinned to column 2", ".deck.b{grid-column:2"],
  ["crossfader panel spans both decks", ".mid{grid-column:1 / -1"],
  ["queues stay two columns", ".queues{grid-template-columns:1fr 1fr"],
  ["tour card moves below the board", "#learnbar{order:9"],
  ["tempo row stays on one line", ".bpmrow{flex-wrap:nowrap"],
  ["track title gets a full line", ".deckhead > div{flex:1 0"],
  ["EQ sliders keep a real touch target", ".ctl input[type=range]{height:46px;width:26px"]
];
for (const [name, needle] of must) check(name, phone.includes(needle), needle);

// Nothing inside a deck may be hidden on a phone except the scrub disc, which he
// asked for on desktop only (the seek bar it sits beside stays).
const hidden = [...phone.matchAll(/([^{}\n]+)\{[^}]*display:none/g)].map(m => m[1].trim());
const deckHidden = hidden.filter(sel => (sel.includes(".deck") || sel.includes(".bank") ||
  sel.includes(".ctl") || sel.includes(".fader") || sel.includes("button")) && !sel.includes(".jog"));
check("no deck control is hidden on a phone", deckHidden.length === 0, deckHidden.join(" | "));
check("only the scrub disc, long copy and idle counters are hidden",
      hidden.every(sel => [".jog", ".learnbar .when", ".qcol .qhint", ".qrow .dur", ".qhead .qcount"]
        .some(ok => sel.includes(ok))),
      hidden.join(" | "));
check("the seek bars themselves survive on a phone",
      !hidden.some(sel => sel.includes(".scrub")));

// Structural guarantee: the mark-up that the CSS reflows still has every control.
const dom = new JSDOM(html);
const doc = dom.window.document;
for (const side of ["A", "B"]) {
  const deck = doc.querySelector(".deck." + side.toLowerCase());
  check(`deck ${side} keeps its transport`,
        ["play" + side, "cue" + side, "syncdeck" + side].every(id => doc.getElementById(id)),
        side);
  check(`deck ${side} keeps all eight faders`,
        deck.querySelectorAll(".ctl input[type=range]").length === 8,
        String(deck.querySelectorAll(".ctl input[type=range]").length));
  // EQ controls label with <b>, the fader controls with a <label><span>.
  const labels = [...deck.querySelectorAll(".ctl b, .ctl > label > span")].map(n => n.textContent.trim()).join(",");
  check(`deck ${side} keeps every control label`,
        labels === "LOW,MID,HIGH,FILTER,REVERB,DELAY,PITCH,VOLUME", labels);
  check(`deck ${side} keeps BPM, SYNC and rate`,
        ["bpm" + side, "sync" + side, "rate" + side].every(id => doc.getElementById(id)), side);
}
check("crossfader and master survive", doc.getElementById("crossfader") && doc.getElementById("master"));
check("the tour card is still in the page", doc.getElementById("learnbar"));
check("each queue keeps its header, list and clear button",
      ["qA", "qB", "qclearA", "qclearB", "qcA", "qcB"].every(id => doc.getElementById(id)));

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nMixer mobile PASS: side-by-side decks and queues, crossfader below, no hidden controls");
