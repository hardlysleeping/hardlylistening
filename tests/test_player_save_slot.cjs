// The player's library control. 0.16.1 parked it in the compact bar's right-hand column at
// exactly the expand button's box (right 9 / top 36, z-index 3 over the expand's 2), so the
// fullscreen button sat under a save square and blinked as the saves poll repainted the slot.
// It now belongs to the fullscreen foot only; this pins that, and pins that the compact bar's
// heart, queue and expand are each the top element at their own centre.
const fs = require("fs"), http = require("http"), path = require("path"), assert = require("assert");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };

const srv = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  if (p.startsWith("/api/")) return res.writeHead(200, { "content-type": "application/json" })
    .end(JSON.stringify({ ok: true, tracks: [], playlists: [], folders: [], favorites: [], songs: [], queue: [] }));
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

(async () => {
  const css = require("./_css.cjs").appCss();
  assert(!/\.plsave/.test(css), "no rule may place a save control in the compact bar");

  await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.waitForTimeout(600);
    const bar = await page.evaluate(() => {
      document.getElementById("player").classList.add("on");
      const centre = el => { const r = el.getBoundingClientRect(); const n = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return n ? (n.closest("button")?.id || n.id || n.tagName) : "none"; };
      return {
        saveSlot: !!document.querySelector("#player .plsave"),
        hit: ["pllike", "plqueue", "plfull"].map(id => [id, centre(document.getElementById(id))]),
        rects: ["pllike", "plqueue", "plfull"].map(id => { const r = document.getElementById(id).getBoundingClientRect(); return [id, Math.round(r.left), Math.round(r.top), Math.round(r.width)]; }),
      };
    });
    assert.strictEqual(bar.saveSlot, false, "the compact bar must not carry a save slot");
    for (const [id, hit] of bar.hit) assert.strictEqual(hit, id, `#${id} is covered by ${hit} (${JSON.stringify(bar.rects)})`);

    // Fullscreen foot: the same control, repainted for the playing track.
    const foot = await page.evaluate(() => {
      playingId = 42;
      listeningSaves = { tracks: [42], playlists: [], folders: [] };
      paintPlayerSave();
      const slot = document.getElementById("pfsave");
      const button = slot.querySelector("button.lsave");
      const r = button.getBoundingClientRect();
      const siblings = [...slot.parentElement.children].filter(el => el !== slot)
        .map(el => { const b = el.getBoundingClientRect(); return [el.id, Math.round(b.left), Math.round(b.right)]; });
      return { key: button.dataset.saveKey, pressed: button.getAttribute("aria-pressed"),
        rect: [Math.round(r.left), Math.round(r.right)], siblings, overflow: document.documentElement.scrollWidth - innerWidth };
    });
    assert.strictEqual(foot.key, "track42", "the foot button is bound to the playing track");
    assert.strictEqual(foot.pressed, "true", "a saved track reads as pressed");
    for (const [id, left, right] of foot.siblings)
      assert(foot.rect[1] <= left || foot.rect[0] >= right, `the save control overlaps #${id}`);
    assert.strictEqual(foot.overflow, 0, "the foot row overflows the viewport");

    // A repaint with the same state must not replace the button under a finger.
    const stable = await page.evaluate(() => {
      const before = document.querySelector("#pfsave button.lsave");
      paintPlayerSave();
      return before === document.querySelector("#pfsave button.lsave");
    });
    assert.strictEqual(stable, true, "an unchanged state must not rebuild the button");

    console.log("Player save slot PASS: compact bar free of it, expand on top, fullscreen foot bound to the track");
  } finally { await browser.close(); srv.close(); }
})().catch(e => { console.error("FAIL " + e.message); process.exitCode = 1; });