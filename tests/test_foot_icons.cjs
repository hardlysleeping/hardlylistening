// The fullscreen foot: one centred row — Cue, equalizer, queue, library, heart.
//
// Cues and the equalizer used to be pills with a word in them, then both became bare glyphs.
// The contract now: Cue keeps its word (it opens a set of pads, which a glyph does not
// explain) at the same 55 px height as the glyph boxes; the three glyph buttons share one box
// token; the heart is drawn 1.15x so its character outline matches the drawn weight; an
// engaged control is accent + ring (never a permanent accent from hover); and the library
// add/remove sits immediately left of the heart, which stays last in the row.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web", "index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "web", "styles", "components", "player.css"), "utf8");
const lib = fs.readFileSync(path.join(root, "web", "styles", "components", "library-row.css"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

const dom = new JSDOM(html.replace(/<script[\s\S]*?<\/script>/g, ""), { url: "https://example.test/" });
const { document } = dom.window;

const foot = document.querySelector("#player-full .pf-foot");
check("the fullscreen foot exists", !!foot);

// One row, in the order it reads. `#pflyrics` stays in the markup but `lyrics.css` sets it to
// display:none, so it takes no room and is not part of what a person sees.
const order = foot
  ? [...foot.children].map(n => n.id || n.className.split(" ")[0]).filter(id => id !== "pflyrics")
  : [];
check("the row runs cue, eq, queue, library, heart",
  JSON.stringify(order.slice(0, 5)) === '["pfcues","pfeq","pfqueue","pfsave","pflike"]',
  JSON.stringify(order));
check("the heart is the last control in the row", order[4] === "pflike", JSON.stringify(order));

const cues = document.getElementById("pfcues");
const eq = document.getElementById("pfeq");
const like = document.getElementById("pflike");
const queue = document.getElementById("pfqueue");

check("cue is a button in the foot", !!cues && cues.parentElement === foot);
check("cue carries its word", !!cues && cues.textContent.trim() === "Cue", cues ? cues.textContent.trim() : "missing");
check("cue names what it controls", cues && cues.getAttribute("aria-controls") === "pf-cues");
check("cue has a label for assistive tech", !!cues && !!cues.getAttribute("aria-label"));

for (const [name, button] of [["equalizer", eq], ["queue", queue], ["heart", like]]) {
  check(`${name} carries the shared icon-button class`, !!button && button.classList.contains("iconbtn"));
}

check("the equalizer draws a glyph, not a word", !!eq && !!(eq.querySelector("svg")) && eq.textContent.trim() === "");
check("the equalizer announces what it controls", eq && eq.getAttribute("aria-controls") === "pf-eq");
check("the queue says whether it is open", !!queue && queue.hasAttribute("aria-expanded"));
check("the heart says whether the song is liked", !!like && like.hasAttribute("aria-pressed"));

// CSS: one rule sizes the three glyphs, so no later block can make one of them a different box.
const rule = sel => {
  const at = css.indexOf(sel + "{");
  return at < 0 ? "" : css.slice(at + sel.length + 1, css.indexOf("}", at));
};
const sizes = /#player-full \.pf-foot #pflike,\s*#player-full \.pf-foot #pfqueue,\s*#player-full \.pf-foot #pfeq\{/.test(css);
check("one rule owns the three glyph buttons", sizes);
const sizeRule = rule("#player-full .pf-foot #pflike,\n#player-full .pf-foot #pfqueue,\n#player-full .pf-foot #pfeq");
check("they are sized from the shared token",
  /width:var\(--pf-foot-size\)/.test(sizeRule) && /height:var\(--pf-foot-size\)/.test(sizeRule),
  sizeRule.trim().slice(0, 120));
check("the token is the queue's 55 px", /--pf-foot-size:\s*55px/.test(css));

// Cue is its own rule: the word sets the width, the token sets the height.
const cueRule = rule("#player-full .pf-foot #pfcues");
check("cue is the same height as the glyphs", /height:var\(--pf-foot-size\)/.test(cueRule), cueRule.trim().slice(0, 120));
check("cue takes its width from its label", /padding:0 1\dpx/.test(cueRule), cueRule.trim().slice(0, 120));
check("cue is not a glyph box", !/width:var\(--pf-foot-size\)/.test(cueRule));

check("the drawn glyph is 27 px", /#player-full \.pf-foot #pfeq svg\{[^}]*width:\s*27px/.test(css.replace(/\n/g, "")));
// 1.15x on the shared glyph size, so the heart cannot drift from the row it sits in.
check("the heart is drawn 1.15x the shared glyph", /#player-full \.pf-foot #pflike\{font-size:calc\(var\(--pf-foot-glyph\) \* 1\.15\)\}/.test(css));
check("no pill styling is left on the equalizer",
  !/#player-full \.pf-foot #pfeq,#player-full \.pf-foot #pfcues\{/.test(css));

const onRule = rule("#player-full .pf-foot #pfqueue[aria-expanded=\"true\"],\n#player-full .pf-foot #pfcues.on,\n#player-full .pf-foot #pfeq.on,\n#player-full .pf-foot #pflike.on");
check("an engaged control takes the accent", /color:\s*var\(--accent\)/.test(onRule), onRule.trim().slice(0, 120));
check("and a ring, so the state survives the pointer leaving",
  /inset 0 0 0 1px color-mix\(in srgb,var\(--accent\)/.test(onRule.replace(/\n/g, " ")),
  onRule.trim().slice(0, 160));
check("hover alone never lights one up",
  !/:(hover)[^{]*\{[^}]*color:\s*var\(--accent\)/.test(rule("#player-full .pf-foot :is(#pflike,#pfqueue,#pfcues,#pfeq):hover")));

// The library control that sits immediately left of the heart: in flow in the fullscreen
// foot, in the right-hand cluster of the compact bar, and no blanket rule hides either.
check("the fullscreen save slot is laid out inline", /\.pf-foot \.pfsave\{display:inline-flex/.test(css));
check("the compact save slot is placed at every width", /body #player\.on \.plsave\{display:block;position:absolute/.test(css));
const hides = [...(css + lib).matchAll(/([^{}]*?(?:plsave|pfsave)[^{}]*)\{([^}]*)\}/g)]
  .filter(m => /display:\s*none/.test(m[2]));
check("nothing hides the save control anywhere", hides.length === 0,
  hides.map(m => m[1].trim()).join(" | "));
check("the fullscreen save button is a real box, not a bare glyph",
  /#player-full \.pf-foot \.pfsave \.ghost\.lsave\{[^}]*width:44px/.test(lib.replace(/\n/g, "")));

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nall foot-icon assertions passed");