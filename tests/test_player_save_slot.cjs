// The player's library control. 0.16.1 parked it in the compact bar's right-hand column at
// exactly the expand button's box (right 9 / top 36, z-index 3 over the expand's 2), so the
// fullscreen button sat under a save square and blinked as the saves poll repainted the slot.
// 0.16.6 gave it a real slot: the second of three stacked controls, between the heart and the
// expand. This pins that it stays a slot of its own — its own centre hit-tests into its own
// button, and it overlaps neither neighbour at either width — and that the fullscreen foot
// keeps the same control for the playing track.
const fs = require("fs"), http = require("http"), path = require("path"), assert = require("assert");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };

const srv = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  if (p.startsWith("/api/")) return res.writeHead(200, { "content-type": "application/json" })
    .end(JSON.stringify({ ok: true, songs: [], queue: [], tracks: [], playlists: [], folders: [],
      favorites: [], pins: [], likes: [], album_likes: [], library: [], items: [], entries: [], results: [] }));
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

const overlaps = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

(async () => {
  await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  try {
    // The slot must behave the same on the phone cluster and the desktop bar.
    for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 800 }]) {
      const page = await browser.newPage({ viewport });
      await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
      await page.waitForTimeout(600);
      const bar = await page.evaluate(() => {
        document.getElementById("player").classList.add("on");
        playingId = 42;
        listeningSaves = { tracks: [42], playlists: [], folders: [] };
        paintPlayerSave();
        const box = id => {
          const el = document.getElementById(id);
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return { id, left: r.left, top: r.top, right: r.right, bottom: r.bottom,
            hit: hit ? (hit.closest("button")?.id || hit.id || hit.tagName) : "none",
            // The slot's own button carries no id, so it is identified by containment.
            own: !!hit && el.contains(hit) };
        };
        const slot = document.getElementById("plsave");
        const button = slot.querySelector("button.lsave");
        return {
          boxes: ["pllike", "plsave", "plfull"].map(box),
          saveFill: button ? { key: button.dataset.saveKey, pressed: button.getAttribute("aria-pressed") } : null,
          slotDisplay: getComputedStyle(slot).display,
        };
      });
      const [like, save, full] = bar.boxes;
      const at = viewport.width + "px";
      assert.notStrictEqual(bar.slotDisplay, "none", `${at}: the compact save slot is not shown`);
      assert(bar.saveFill, `${at}: the compact save slot is empty for a playing track`);
      assert.strictEqual(bar.saveFill.key, "track42", `${at}: the compact save is bound to the playing track`);
      assert.strictEqual(bar.saveFill.pressed, "true", `${at}: a saved track reads as pressed`);
      assert.strictEqual(save.own, true, `${at}: the compact save is covered by ${save.hit}`);
      assert(!overlaps(save, like), `${at}: the compact save overlaps #pllike`);
      assert(!overlaps(save, full), `${at}: the compact save overlaps #plfull`);
      for (const box of [like, full]) assert.strictEqual(box.hit, box.id, `${at}: #${box.id} is covered by ${box.hit}`);
      await page.close();
    }

    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.waitForTimeout(600);

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

    console.log("Player save slot PASS: compact save in its own slot, heart and expand on top, fullscreen foot bound to the track");
  } finally { await browser.close(); srv.close(); }
})().catch(e => { console.error("FAIL " + e.message); process.exitCode = 1; });