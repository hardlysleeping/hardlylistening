// A detail page starts under the section nav, not under an empty toolbar row.
//
// Album, song and artist detail pages hide the view switch, the layout switch and the funnel.
// The toolbar row that held them (`#view-library .liblayout`) still reserved its 40 px minimum
// height plus its margin, so a blank band — 66 px at every width — sat between the section nav
// and Back. The row has to keep its place when a detail page still has a control in it, though:
// the grid-shape button on an artist page and New playlist on a playlist page both live there.
const fs = require("fs"), { chromium } = require("./_pw.cjs");
const { appCss } = require("./_css.cjs");

const html = fs.readFileSync("web/index.html", "utf8");
const open = html.indexOf('<section id="view-library"');
const section = html.slice(open, html.indexOf("</section>", open) + "</section>".length);

const BACK = '<button class="backbtn" onclick="closeDetail()">‹ Back</button>';

(async () => {
  const browser = await chromium.launch({ headless: true });
  let failures = 0;
  const check = (name, ok, detail) => {
    if (ok) console.log("PASS " + name);
    else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
  };
  try {
    for (const vp of [{ width: 390, height: 844 }, { width: 1280, height: 900 }]) {
      const page = await browser.newPage({ viewport: vp });
      await page.route("**/*", r => r.abort());
      await page.goto("about:blank");
      await page.setContent(`<style>${appCss()}</style>${section}`);

      // The Repo's Browse section, opened on a detail page: the section row is on screen and
      // the detail's own Back heads `#liblist`.
      await page.evaluate(back => {
        const lib = document.getElementById("view-library");
        lib.hidden = false;
        document.getElementById("repotabs").hidden = false;
        document.getElementById("liblist").innerHTML = back;
      }, BACK);
      await page.evaluate(() => document.getElementById("view-library").classList.add("detail-open"));
      await page.waitForTimeout(60);

      const gap = () => page.evaluate(() => {
        const lib = document.getElementById("view-library");
        const back = lib.querySelector(".backbtn");
        const nav = document.getElementById("repotabs");
        const row = lib.querySelector(".liblayout");
        const box = el => el.getBoundingClientRect();
        return {
          band: +(box(back).top - box(nav).bottom).toFixed(1),
          row: +box(row).height.toFixed(1),
          display: getComputedStyle(row).display,
          backUnderRow: box(back).top >= box(row).bottom,
        };
      });

      // Album / song detail: nothing is left in the toolbar row, so it must not be drawn.
      let s = await gap();
      check(`${vp.width}: a detail page has no empty toolbar band above Back`,
        s.band <= 16, JSON.stringify(s));
      check(`${vp.width}: and the row itself is gone`, s.row === 0 && s.display === "none",
        JSON.stringify(s));

      // New playlist and the grid-shape button still own the row: it keeps its place.
      for (const [what, id] of [["New playlist", "playlist-edit-btn"], ["the grid shape", "gridstylebtn"]]) {
        await page.evaluate(button => { document.getElementById(button).hidden = false; }, id);
        s = await gap();
        check(`${vp.width}: ${what} still gets its row`, s.row >= 30 && s.display !== "none",
          JSON.stringify(s));
        check(`${vp.width}: and Back sits below it`, s.backUnderRow && s.band >= 40, JSON.stringify(s));
        await page.evaluate(button => { document.getElementById(button).hidden = true; }, id);
      }

      // Leaving the detail page brings the toolbar back where it was.
      await page.evaluate(() => document.getElementById("view-library").classList.remove("detail-open"));
      s = await gap();
      check(`${vp.width}: the list's toolbar row is back outside a detail page`,
        s.row >= 30 && s.display !== "none", JSON.stringify(s));

      await page.close();
    }
  } finally {
    await browser.close();
  }
  if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
  console.log("\nall detail-spacing assertions passed");
})();