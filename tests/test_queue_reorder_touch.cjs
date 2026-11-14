// Queue reordering must work with a finger, not just a mouse. The phone has no drag handle,
// so a row reorders after a short hold; a flick still scrolls, a tap still plays, and the
// reorder must not disturb the "recently played" drawer underneath the queue.
// No network and no media: the API is a stub and every track is a fixture.
const fs = require("fs"), path = require("path"), http = require("http"), assert = require("assert");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };
const STUB = { ok: true, songs: [], queue: [], tracks: [], playlists: [], folders: [], favorites: [], library: [] };
const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  if (p.startsWith("/api/")) return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(STUB));
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  let failed = 0;
  const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failed++;
    console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`));
  };
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
    const page = await ctx.newPage();
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.waitForTimeout(400);
    const seed = () => page.evaluate(() => {
      const tracks = [];
      for (let i = 1; i <= 5; i++) tracks.push({ id: i, title: "Track " + i, artist_name: "Artist", album: "Album",
        artwork: "", playable: true, duration: 180, provider: "soundcloud", source_status: "ok", playlists: [] });
      libAll = tracks; libQueue = tracks.slice(); playingId = null;
      const panel = document.getElementById("playback-panel");
      panel.hidden = false; panel.classList.add("open"); panel.classList.remove("history-open");
      const host = document.getElementById("playback-items");
      host.dataset.rendered = ""; paintPlaybackQueue();
    });
    const order = () => page.evaluate(() => libQueue.map(t => t.id));
    const rows = () => page.evaluate(() => [...document.querySelectorAll("#playback-items .pq-row")].map(n => {
      const r = n.getBoundingClientRect();
      return Math.round(r.top + r.height / 2);
    }));
    const client = await ctx.newCDPSession(page);
    const points = (x, y) => [{ x, y, radiusX: 2, radiusY: 2, force: 1, id: 1 }];
    const touch = async (x, y, dy, hold, steps = 8) => {
      await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: points(x, y) });
      await new Promise(r => setTimeout(r, hold));
      for (let i = 1; i <= steps; i++) {
        await client.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: points(x, y + dy * i / steps) });
        await new Promise(r => setTimeout(r, 16));
      }
      await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await new Promise(r => setTimeout(r, 220));
    };

    // The phone hides the drag handle; a hold on the row is the only way in.
    await seed();
    assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector("#playback-items .pq-handle")).display), "none",
      "this test only means something if the handle stays hidden on a phone");

    // A hold then a drag of one row height moves the row one place, and nothing else.
    let y = await rows();
    await touch(195, y[0], 60, 320);
    check("hold + drag down one row", await order(), [2, 1, 3, 4, 5]);
    check("reorder leaves the history drawer shut",
      await page.evaluate(() => document.getElementById("playback-panel").classList.contains("history-open")), false);

    // Dragging past two rows lands after both of them, not one place (the old placement
    // code could swap twice per move and slide the row several places).
    await seed(); y = await rows();
    await touch(195, y[0], 160, 320, 10);
    check("hold + drag past two rows", await order(), [2, 3, 4, 1, 5]);

    // Upward drags move up.
    await seed(); y = await rows();
    await touch(195, y[3], -100, 320, 8);
    check("hold + drag up", await order(), [1, 4, 2, 3, 5]);

    // A flick is a scroll, never a reorder.
    await seed(); y = await rows();
    await touch(195, y[0], 60, 20, 4);
    check("flick does not reorder", await order(), [1, 2, 3, 4, 5]);
    check("flick leaves nothing lifted",
      await page.evaluate(() => !!document.querySelector("#playback-items .pq-lift")), false);

    // A tap plays and leaves the order alone (no drag armed, no click swallowed).
    await seed();
    await page.evaluate(() => { window.__tapped = null; window.playTrackNow = id => { window.__tapped = id; }; });
    y = await rows();
    await touch(195, y[1], 0, 60, 1);
    check("tap plays the row", await page.evaluate(() => window.__tapped), 2);
    check("tap keeps the order", await order(), [1, 2, 3, 4, 5]);

    // Desktop keeps the mouse path: the handle drags immediately.
    await page.setViewportSize({ width: 1280, height: 800 });
    await seed(); y = await rows();
    const handle = await page.evaluate(() => {
      const r = document.querySelector("#playback-items .pq-handle").getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
    });
    await page.mouse.move(handle.x, handle.y);
    await page.mouse.down();
    for (let i = 1; i <= 8; i++) { await page.mouse.move(handle.x, handle.y + 8 * i); await new Promise(r => setTimeout(r, 16)); }
    await page.mouse.up();
    await page.waitForTimeout(220);
    check("mouse handle drag", await order(), [2, 1, 3, 4, 5]);
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("queue reorder (touch) ok");
})();