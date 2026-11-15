// The kept mark: a card says it with a light, a list row says it with a word. On a card the
// small accent dot sits at the top centre of the cover — the title line below already names
// the song, and the cover's corners are the tempo (top-left) and play-next (top-right).
const fs = require("fs"), path = require("path"), assert = require("assert");
const { chromium } = require("./_pw.cjs");
const css = require("./_css.cjs");

const library = fs.readFileSync(path.join(__dirname, "..", "web", "js", "library.js"), "utf8");

// Markup: the dot belongs to the cover, the word belongs to the row's title line.
assert(/<div class="thumb">[\s\S]*?class="kept-badge cardmark"[\s\S]*?<\/div>/.test(library),
  "a card's kept mark must live inside the cover");
assert(/\$\{card \? "" : `<span class="kept-badge"/.test(library),
  "the title-line pill is for rows only");
assert(/role="img" aria-label="Kept on this device"/.test(library),
  "the dot still says what it is to a screen reader");

const card = `
  <div class="songtable">
    <div class="songrow songcard" data-id="1">
      <div class="thumb">
        <span class="bpm" title="Detected tempo">174</span>
        <span class="kept-badge cardmark" role="img" aria-label="Kept on this device"></span>
      </div>
      <div class="st"><span class="sttext"><b>Song</b></span></div>
    </div>
  </div>`;
const row = `
  <div class="songtable">
    <div class="songrow" data-id="2">
      <div class="st"><span class="sttext"><b>Song</b><span class="kept-badge">Kept</span></span></div>
    </div>
  </div>`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  const checks = [];
  const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    checks.push(ok);
    console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`));
  };
  try {
    for (const width of [1280, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      await page.route("**/*", route => route.abort());
      await page.setContent(`${css.styleTag()}<main>${card}${row}</main>`);
      const dot = await page.evaluate(() => {
        const thumb = document.querySelector(".songcard .thumb").getBoundingClientRect();
        const badge = document.querySelector(".songcard .thumb .kept-badge");
        const r = badge.getBoundingClientRect();
        const style = getComputedStyle(badge);
        const before = getComputedStyle(badge, "::before");
        return {
          offset: [Math.round(r.left - thumb.left), Math.round(r.top - thumb.top)],
          size: [Math.round(r.width), Math.round(r.height)],
          centre: Math.round(r.left + r.width / 2 - thumb.left),
          half: Math.round(thumb.width / 2),
          box: [style.borderRadius, style.borderTopWidth],
          text: badge.textContent.trim(),
          before: before.display,
          position: style.position,
          events: style.pointerEvents,
          label: badge.getAttribute("aria-label"),
        };
      });
      check(`card at ${width}: 8 px dot`, dot.size, [8, 8]);
      check(`card at ${width}: no word`, dot.text, "");
      check(`card at ${width}: top centre of the cover`, [dot.offset[1], dot.centre, dot.half], [6, dot.half, dot.half]);
      check(`card at ${width}: a dot, not a pill`, [dot.box[0], dot.box[1], dot.before], ["50%", "0px", "none"]);
      check(`card at ${width}: it does not eat the tap`, [dot.position, dot.events], ["absolute", "none"]);
      check(`card at ${width}: it names itself`, dot.label, "Kept on this device");
      check(`card at ${width}: hidden until the copy exists`, await page.evaluate(() => {
        const badge = document.querySelector(".songcard .thumb .kept-badge");
        badge.hidden = true;
        return getComputedStyle(badge).display;
      }), "none");

      const pill = await page.evaluate(() => {
        const badge = document.querySelector(".songrow:not(.songcard) .kept-badge");
        const style = getComputedStyle(badge);
        return { text: badge.textContent.trim(), display: style.display, position: style.position,
          radius: style.borderRadius };
      });
      check(`row at ${width}: still says Kept`, pill.text, "Kept");
      check(`row at ${width}: still a pill in the title line`, [pill.display, pill.position, pill.radius], ["inline-flex", "static", "999px"]);
      await page.close();
    }
  } finally {
    await browser.close();
  }
  const failed = checks.filter(ok => !ok).length;
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("kept mark ok");
})();