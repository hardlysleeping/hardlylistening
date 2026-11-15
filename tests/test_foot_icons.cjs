// The fullscreen foot: one centred row — Cue, equalizer, queue, library, heart.
//
// Cues and the equalizer used to be pills with a word in them, then both became bare glyphs.
// The contract now: Cue keeps its word (it opens a set of pads, which a glyph does not
// explain) at the same 55 px height as the glyph boxes; queue and equalizer share one box
// token; the heart is a real box, the same 44 px bordered one the library save draws beside
// it, so the pair read as one kind; an engaged control is accent + ring (never a permanent
// accent from hover); and the library add/remove sits immediately left of the heart, which
// stays last in the row.
//
// The lyrics reading is deliberately *not* in this row any more. It has its own line under
// the volume: at the foot's 55 px and filled it read as a fourth transport button, which is
// exactly what it is not.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web", "index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "web", "styles", "components", "player.css"), "utf8");
const lib = fs.readFileSync(path.join(root, "web", "styles", "components", "library-row.css"), "utf8");
const lyr = fs.readFileSync(path.join(root, "web", "styles", "components", "lyrics.css"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

const dom = new JSDOM(html.replace(/<script[\s\S]*?<\/script>/g, ""), { url: "https://example.test/" });
const { document } = dom.window;

const foot = document.querySelector("#player-full .pf-foot");
check("the fullscreen foot exists", !!foot);

// The row in reading order. Two of the five sit inside `.pf-side` groups (the DJ tools on the
// left, the library pair on the right), so the walk has to open those groups rather than read
// the foot's direct children — the earlier version of this test read `foot.children` and had
// been silently asserting against a shape the markup no longer had.
const walk = el => [...el.children].flatMap(n =>
  n.classList && n.classList.contains("pf-side") ? walk(n) : [n.id || n.className.split(" ")[0]]);
const order = foot ? walk(foot) : [];
check("the row runs cue, eq, queue, library, heart",
  JSON.stringify(order) === '["pfcues","pfeq","pfqueue","pfsave","pflike"]',
  JSON.stringify(order));

const cues = document.getElementById("pfcues");
const eq = document.getElementById("pfeq");
const like = document.getElementById("pflike");
const queue = document.getElementById("pfqueue");

check("cue is a button in the foot", !!cues && foot.contains(cues));
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

// CSS: one rule sizes the two bare glyphs, so no later block can make one of them a different box.
const rule = sel => {
  const at = css.indexOf(sel + "{");
  return at < 0 ? "" : css.slice(at + sel.length + 1, css.indexOf("}", at));
};
const sizes = /#player-full \.pf-foot #pfqueue,\s*#player-full \.pf-foot #pfeq\{/.test(css);
check("one rule owns the two bare glyph buttons", sizes);
const sizeRule = rule("#player-full .pf-foot #pfqueue,\n#player-full .pf-foot #pfeq");
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

// The heart is the library save's twin: same 44 px box, same 1 px border and 12 px corner, so
// the two right-hand controls read as a pair instead of a bordered button beside a loose glyph.
const likeRule = rule("#player-full .pf-foot #pflike");
check("the heart is a 44 px box", /width:44px/.test(likeRule) && /height:44px/.test(likeRule),
  likeRule.trim().slice(0, 140));
check("the heart draws the save's border and corner",
  /border:1px solid var\(--line\)/.test(likeRule) && /border-radius:12px/.test(likeRule),
  likeRule.trim().slice(0, 140));
check("the heart is not sized from the foot token any more",
  !/var\(--pf-foot-size\)/.test(likeRule) && !/calc\(var\(--pf-foot-glyph\)/.test(likeRule));
check("the save is the same 44 px box the heart copies",
  /#player-full \.pf-foot \.pfsave \.ghost\.lsave\{[^}]*width:44px/.test(lib.replace(/\n/g, "")));

const likeOn = rule("#player-full .pf-foot #pflike.on");
check("a liked heart takes the accent", /color:\s*var\(--accent\)/.test(likeOn), likeOn.trim().slice(0, 140));
check("and the save's own on-colours, so the pair match",
  /#2f6b57/.test(likeOn) && /#16241f/.test(likeOn), likeOn.trim().slice(0, 140));

check("no pill styling is left on the equalizer",
  !/#player-full \.pf-foot #pfeq,#player-full \.pf-foot #pfcues\{/.test(css));

const onRule = rule("#player-full .pf-foot #pfqueue[aria-expanded=\"true\"],\n#player-full .pf-foot #pfcues.on,\n#player-full .pf-foot #pfeq.on");
check("an engaged control takes the accent", /color:\s*var\(--accent\)/.test(onRule), onRule.trim().slice(0, 120));
check("and a ring, so the state survives the pointer leaving",
  /inset 0 0 0 1px color-mix\(in srgb,var\(--accent\)/.test(onRule.replace(/\n/g, " ")),
  onRule.trim().slice(0, 160));
check("hover alone never lights one up",
  !/:(hover)[^{]*\{[^}]*color:\s*var\(--accent\)/.test(rule("#player-full .pf-foot :is(#pfqueue,#pfcues,#pfeq):hover")));

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

// ---- The lyrics line, under the volume ------------------------------------------------
const body = document.querySelector("#player-full .pf-body");
const lyrics = document.getElementById("pflyrics");
const lyricsRow = document.querySelector("#player-full .pf-lyrics");
const volRow = document.querySelector("#player-full .pf-vol");

check("the lyrics button is not in the foot", !!lyrics && !foot.contains(lyrics));
check("it has a row of its own", !!lyricsRow && !!lyrics && lyrics.parentElement === lyricsRow);
check("its row comes after the volume row",
  !!lyricsRow && !!volRow && volRow.parentElement === body &&
  [...body.children].indexOf(volRow) < [...body.children].indexOf(lyricsRow));
check("it is the last thing in the column",
  [...body.children].indexOf(lyricsRow) === body.children.length - 1);
check("the row centres it", /#player-full \.pf-lyrics\{display:flex;justify-content:center/.test(css));
check("it is a quiet 30 px pill, not a foot-sized button",
  /#player-full \.pf-lyrics #pflyrics\{[^}]*height:30px/.test(lyr.replace(/\n/g, "")) &&
  !/var\(--pf-foot-size\)/.test(rule("#player-full .pf-lyrics #pflyrics")),
  rule("#player-full .pf-lyrics #pflyrics").trim().slice(0, 140));
check("the lyrics rule is no longer scoped to the foot", !/\.pf-foot #pflyrics\{/.test(lyr));

// ---- The volume row -------------------------------------------------------------------
check("the volume keeps its own row under the foot",
  /#player-full \.pf-vol\{display:none\}/.test(css) &&
  /#player-full \.pf-vol\{position:relative;display:flex/.test(css));
check("the fader is half the scrub's width",
  /#player-full #pfvol\{[^}]*width:50%/.test(css.replace(/\n/g, "")));
check("the row is centred on the same line as the scrub",
  /#player-full \.pf-vol\{[^}]*justify-content:center/.test(css.replace(/\n/g, "")) &&
  /#player-full \.pf-vol\{[^}]*margin:6px auto 0/.test(css.replace(/\n/g, "")));

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nall foot-icon assertions passed");