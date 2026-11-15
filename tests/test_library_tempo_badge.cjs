// Tempo on a card's cover: top-left, small, and only once the card is open in compact mode.
// The table keeps its badge in the tag column beside the source glyph — that is a different
// reading of the same number, not a second place for this one.
const fs = require("fs"), path = require("path"), assert = require("assert");
const { chromium } = require("./_pw.cjs");
const css = require("./_css.cjs");

const library = fs.readFileSync(path.join(__dirname, "..", "web", "js", "library.js"), "utf8");

// The card draws the badge inside `.thumb`; the tag column draws one only when it is not a card.
assert(/<div class="thumb">[\s\S]*?card && Number\(track\.bpm\) > 0[\s\S]*?class="bpm"/.test(library),
  "a card's badge belongs to the cover");
assert(/<div class="listtags">\$\{card \? "" : sourceTag\(track\)/.test(library),
  "the tag column keeps the badge for rows only");
assert(/root\.dataset\.cardstyle = \(libCardsCompact/.test(library),
  "compact cards are marked on the document root");

const card = (open) => `
  <div class="songtable">
    <div class="songrow songcard${open ? " open" : ""}" data-id="1">
      <div class="thumb">
        <span class="bpm" title="Detected tempo">174</span>
        <button type="button" class="snext" aria-label="Play next"></button>
      </div>
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

      // Full cards: the badge sits on the artwork's top-left corner.
      await page.setContent(`${css.styleTag()}<main>${card(true)}</main>`);
      const full = await page.evaluate(() => {
        const thumb = document.querySelector(".songcard .thumb").getBoundingClientRect();
        const badge = document.querySelector(".songcard .thumb .bpm");
        const r = badge.getBoundingClientRect();
        const style = getComputedStyle(badge);
        return {
          shown: style.display !== "none",
          offset: [Math.round(r.left - thumb.left), Math.round(r.top - thumb.top)],
          size: [Math.round(r.width), Math.round(r.height)],
          font: style.fontSize,
          title: badge.getAttribute("title"),
        };
      });
      check(`full card at ${width}: the badge is on the cover`, full.shown, true);
      check(`full card at ${width}: top-left corner`, full.offset, [6, 6]);
      check(`full card at ${width}: small, not a pill`, [full.font, full.size[1]], ["9px", 15]);
      check(`full card at ${width}: it names itself`, full.title, "Detected tempo");

      // Compact, collapsed: the card is one square of artwork, so the badge goes with the
      // rest of the detail. Open, it is a card again and the badge comes back.
      await page.setContent(`${css.styleTag()}<main>${card(false)}</main>`);
      await page.evaluate(() => { document.documentElement.dataset.cardstyle = "compact"; });
      check(`compact collapsed at ${width}: hidden`,
        await page.evaluate(() => getComputedStyle(document.querySelector(".thumb .bpm")).display), "none");
      await page.evaluate(() => document.querySelector(".songcard").classList.add("open"));
      check(`compact open at ${width}: shown`,
        await page.evaluate(() => getComputedStyle(document.querySelector(".thumb .bpm")).display !== "none"), true);

      await page.close();
    }

    // The table row's badge is untouched: still in the tag column, 10px, pointer-free.
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.route("**/*", route => route.abort());
    await page.setContent(`${css.styleTag()}<main><div class="songtable">
      <div class="songrow" data-id="1"><div class="listtags"><span class="bpm">174</span></div></div>
    </div></main>`);
    const row = await page.evaluate(() => {
      const badge = document.querySelector(".listtags .bpm");
      const style = getComputedStyle(badge);
      return { font: style.fontSize, height: Math.round(badge.getBoundingClientRect().height),
               position: style.position, events: style.pointerEvents,
               onCover: !!document.querySelector(".songrow .thumb .bpm") };
    });
    check("a table row keeps the badge in the tag column", [row.font, row.height], ["10px", 18]);
    check("it is still part of the row, not over the artwork", [row.position, row.onCover], ["static", false]);
    await page.close();
  } finally {
    await browser.close();
  }
  const failed = checks.filter(ok => !ok).length;
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("tempo badge ok");
})();