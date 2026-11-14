// The profile menu drops out of the header and lands over the sticky nav on desktop.
// z-index on the menu alone cannot win that: a child never escapes its parent's stacking
// context, so this asserts the hit test on the overlap, not the numbers. It also pins the
// placement rule: the panel opens from the chip and is pulled back inside the viewport, which
// is `placeUserMenu()`'s job — so the page is loaded whole, with its script, rather than as a
// stripped document: a static copy could not have placed it.
const fs = require("fs"), path = require("path"), assert = require("assert"), http = require("http");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };
const STUB = {};

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  if (p.startsWith("/api/")) return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(STUB));
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

const openMenu = name => {
  const who = document.getElementById("who"), menu = document.getElementById("usermenu");
  who.hidden = false; who.textContent = name;
  hideUserMenu(); toggleUserMenu();
  const m = menu.getBoundingClientRect(), n = document.getElementById("appnav").getBoundingClientRect();
  const top = Math.max(m.top, n.top), bottom = Math.min(m.bottom, n.bottom);
  const overlap = Math.max(0, bottom - top);
  const x = m.left + m.width / 2, y = overlap > 0 ? (top + bottom) / 2 : m.top + 40;
  const hit = document.elementFromPoint(x, y);
  const owner = el => { while (el) { if (el.id === "usermenu") return "usermenu"; if (el.id === "appnav") return "appnav"; el = el.parentElement; } return "none"; };
  const chip = document.getElementById("who").getBoundingClientRect();
  return { overlap, owner: owner(hit), menu: { top: m.top, bottom: m.bottom, left: m.left, right: m.right },
    chip: { left: chip.left, right: chip.right },
    viewport: { w: innerWidth, h: innerHeight } };
};

(async () => {
  const srv = server(); await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [w, h] of [[1280, 800], [390, 844], [760, 420]]) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "domcontentloaded" });
      await page.waitForSelector("#who", { timeout: 15000 });
      // A short name keeps the chip on the right of the row; a long one wraps it to the left.
      // Both are real, and the panel has to open from whichever edge has the room.
      for (const name of ["Member", "a rather long display name"]) {
        const r = await page.evaluate(openMenu, name);
        assert(r.menu.bottom > r.menu.top && r.menu.right > r.menu.left, `${w}px: the menu has no box`);
        assert(r.menu.left >= 0 && r.menu.right <= r.viewport.w, `${w}px "${name}": menu overflows the viewport: ${JSON.stringify(r.menu)}`);
        assert.strictEqual(r.owner, "usermenu", `${w}px "${name}": the menu is painted under the nav (hit ${r.owner}, overlap ${r.overlap}px)`);
        if (w === 1280) assert(r.overlap > 20, `1280px: expected the menu to overlap the nav, got ${r.overlap}px`);
        if (r.chip.left + (r.chip.right - r.chip.left) / 2 < r.viewport.w / 2)
          assert(Math.abs(r.menu.left - r.chip.left) <= 1, `${w}px "${name}": the menu should open from the chip's left edge (${r.menu.left} vs ${r.chip.left})`);
        else
          assert(Math.abs(r.menu.right - r.chip.right) <= 1, `${w}px "${name}": the menu should hang off the chip's right edge (${r.menu.right} vs ${r.chip.right})`);
        console.log(`ok   ${w}px "${name}": over the nav (overlap ${r.overlap}px), chip ${Math.round(r.chip.left)}-${Math.round(r.chip.right)}`);
      }
      await page.close();
    }
    console.log("Profile menu stacking PASS");
  } finally { await browser.close(); srv.close(); }
})().catch(e => { console.error("FAIL " + e.message); process.exitCode = 1; });