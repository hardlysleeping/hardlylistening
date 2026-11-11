// The profile menu drops out of the header and lands over the sticky nav on desktop.
// z-index on the menu alone cannot win that: a child never escapes its parent's stacking
// context, so this asserts the hit test on the overlap, not the numbers.
const fs = require("fs"), assert = require("assert"), { chromium } = require("playwright");
const html = fs.readFileSync("web/index.html", "utf8");
const css = require("./_css.cjs").appCss();
const prefs = "", inlineStyle = "";
const doc = html.replace(/<script[\s\S]*?<\/script>/g, "").replace(/<link[^>]*>/g, "")
  .replace("</head>", `<style>${inlineStyle}${css}${prefs}</style></head>`);

const openMenu = () => {
  const who = document.getElementById("who"), menu = document.getElementById("usermenu");
  who.hidden = false; who.textContent = "Member"; menu.hidden = false;
  const m = menu.getBoundingClientRect(), n = document.getElementById("appnav").getBoundingClientRect();
  const top = Math.max(m.top, n.top), bottom = Math.min(m.bottom, n.bottom);
  const overlap = Math.max(0, bottom - top);
  const x = m.left + m.width / 2, y = overlap > 0 ? (top + bottom) / 2 : m.top + 40;
  const hit = document.elementFromPoint(x, y);
  const owner = el => { while (el) { if (el.id === "usermenu") return "usermenu"; if (el.id === "appnav") return "appnav"; el = el.parentElement; } return "none"; };
  return { overlap, owner: owner(hit), menu: { top: m.top, bottom: m.bottom, left: m.left, right: m.right },
    viewport: { w: innerWidth, h: innerHeight } };
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [w, h] of [[1280, 800], [390, 844]]) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      await page.route("**/*", r => r.abort());
      await page.setContent(doc);
      const r = await page.evaluate(openMenu);
      assert(r.menu.bottom > r.menu.top && r.menu.right > r.menu.left, `${w}px: the menu has no box`);
      assert(r.menu.left >= 0 && r.menu.right <= r.viewport.w, `${w}px: menu overflows the viewport: ${JSON.stringify(r.menu)}`);
      assert.strictEqual(r.owner, "usermenu", `${w}px: the menu is painted under the nav (hit ${r.owner}, overlap ${r.overlap}px)`);
      if (w === 1280) assert(r.overlap > 20, `1280px: expected the menu to overlap the nav, got ${r.overlap}px`);
      console.log(`ok   ${w}px: the profile menu paints over the nav (overlap ${r.overlap}px)`);
      await page.close();
    }
    console.log("Profile menu stacking PASS");
  } finally { await browser.close(); }
})().catch(e => { console.error("FAIL " + e.message); process.exitCode = 1; });