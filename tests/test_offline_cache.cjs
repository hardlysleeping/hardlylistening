// Offline audio: a played song is kept whole, the service worker answers the audio element's
// Range requests with 206 slices of that copy, and the budget evicts the oldest copy first.
// Everything here runs against a local server and a 4 KB fixture; no real library is touched.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
// The manifest and the two icons live beside the server, not inside web/.
const APP = [path.join(__dirname, ".."), ROOT];
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };
const SIZE = 4000;
const hits = {};                       // how many times each track's audio was asked for
const FILL = id => String.fromCharCode(65 + (id % 26));
const audio = id => Buffer.alloc(SIZE, FILL(id));

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = body => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(body));
  const track = /^\/api\/track\/(\d+)\/audio$/.exec(p);
  if (track && req.method === "GET") {
    hits[Number(track[1])] = (hits[Number(track[1])] || 0) + 1;
    return res.writeHead(200, { "content-type": "audio/mpeg", "accept-ranges": "bytes",
      "content-length": String(SIZE) }).end(audio(Number(track[1])));
  }
  if (p.startsWith("/api/")) return json({ ok: true, songs: [], queue: [], tracks: [], playlists: [],
    folders: [], favorites: [], pins: [], likes: [], library: [], items: [], entries: [], results: [] });
  const name = p === "/" ? "/index.html" : p;
  for (const dir of APP) {
    const f = path.join(dir, name);
    if (fs.existsSync(f) && fs.statSync(f).isFile())
      return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  }
  res.writeHead(404).end("no");
});

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const browser = await chromium.launch({ headless: true });
  let failed = 0;
  const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failed++;
    console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`));
  };
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page.goto(base + "/", { waitUntil: "load" });
    await page.waitForTimeout(600);
    check("the worker registers", await page.evaluate(() =>
      Promise.race([
        navigator.serviceWorker.ready.then(r => !!r.active),
        new Promise(resolve => setTimeout(() => resolve("never became active"), 8000))
      ])), true);
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(500);
    check("the page is controlled by the worker",
      await page.evaluate(() => !!navigator.serviceWorker.controller), true);

    // A played song is kept whole, under the key the worker looks up.
    await page.evaluate(() => cachePlayedAudio(1));
    await page.waitForTimeout(1200);
    check("a played song is kept whole", await page.evaluate(async () => {
      const cache = await caches.open("hardlylistening-audio-v1");
      const hit = await cache.match("/api/track/1/audio");
      return hit ? (await hit.blob()).size : 0;
    }), SIZE);
    check("the index records its size", await page.evaluate(() => offlineIndex()["1"].bytes), SIZE);

    // The element asks with a Range header; the copy is one 200, so the worker slices it.
    const slice = await page.evaluate(async () => {
      const r = await fetch("/api/track/1/audio", { headers: { Range: "bytes=10-19" } });
      return { status: r.status, range: r.headers.get("Content-Range"),
        length: r.headers.get("Content-Length"), body: await r.text() };
    });
    check("a range request is answered 206", slice.status, 206);
    check("with the right content range", slice.range, `bytes 10-19/${SIZE}`);
    check("and the right length", slice.length, "10");
    check("with the bytes at that offset", slice.body, FILL(1).repeat(10));

    const whole = await page.evaluate(async () => {
      const r = await fetch("/api/track/1/audio");
      const body = await r.text();
      return { status: r.status, length: r.headers.get("Content-Length"),
        ranges: r.headers.get("Accept-Ranges"), size: body.length };
    });
    check("a request with no range gets the whole copy", [whole.status, whole.size], [200, SIZE]);
    check("and says it takes ranges", whole.ranges, "bytes");

    const openEnd = await page.evaluate(async () => {
      const r = await fetch("/api/track/1/audio", { headers: { Range: "bytes=3990-" } });
      return [r.status, r.headers.get("Content-Range")];
    });
    check("an open-ended range runs to the end", openEnd, [206, `bytes 3990-3999/${SIZE}`]);
    const tail = await page.evaluate(async () => {
      const r = await fetch("/api/track/1/audio", { headers: { Range: "bytes=-5" } });
      return [r.status, r.headers.get("Content-Range")];
    });
    check("a suffix range is the last bytes", tail, [206, `bytes 3995-3999/${SIZE}`]);
    const past = await page.evaluate(() => fetch("/api/track/1/audio", { headers: { Range: "bytes=9999-" } })
      .then(r => [r.status, r.headers.get("Content-Range")]));
    check("a range past the end is refused", past, [416, `bytes */${SIZE}`]);

    // Nothing kept: the request goes to the server untouched.
    const uncached = await page.evaluate(async () => {
      const r = await fetch("/api/track/7/audio", { headers: { Range: "bytes=0-9" } });
      return [r.status, await r.text()];
    });
    check("an unkept song still streams", uncached[0], 200);

    // Budget: two 4 KB copies do not fit in 6000 bytes, so the older one goes.
    await page.evaluate(() => { playingId = null; localStorage.setItem("hl.offlineBudget", "6000"); });
    await page.evaluate(() => cachePlayedAudio(2));
    await page.waitForTimeout(1200);
    check("the oldest copy is evicted first", await page.evaluate(async () => {
      const cache = await caches.open("hardlylistening-audio-v1");
      return [(await cache.match("/api/track/1/audio")) ? "kept" : "gone",
        (await cache.match("/api/track/2/audio")) ? "kept" : "gone"];
    }), ["gone", "kept"]);
    check("and the index agrees", await page.evaluate(() => Object.keys(offlineIndex())), ["2"]);

    // Off means nothing new is kept.
    await page.evaluate(() => setOfflineBudget("0"));
    await page.waitForTimeout(400);
    check("turning it off removes the copies", await page.evaluate(() => offlineStats()), { bytes: 0, count: 0 });
    await page.evaluate(() => cachePlayedAudio(3));
    await page.waitForTimeout(800);
    check("and keeps nothing new", await page.evaluate(() => offlineStats().count), 0);
    check("the copies really are gone from the cache", await page.evaluate(async () => {
      const cache = await caches.open("hardlylistening-audio-v1");
      return (await cache.keys()).length;
    }), 0);

    // A claim with no copy behind it (the browser cleared storage) is dropped.
    await page.evaluate(() => { localStorage.setItem("hl.offlineBudget", String(2 * 1024 ** 3)); });
    await page.evaluate(() => {
      const index = offlineIndex();
      index["9"] = { bytes: 1234, at: Date.now() };
      saveOfflineIndex(index);
    });
    check("a claim with no copy behind it is dropped",
      await page.evaluate(() => offlineReconcile().then(s => s.count)), 0);
    check("and leaves the index empty", await page.evaluate(() => Object.keys(offlineIndex())), []);

    // The dialog says what is actually held.
    await page.evaluate(() => openOfflineDialog());
    await page.waitForTimeout(300);
    check("the dialog shows the budget",
      await page.evaluate(() => document.getElementById("offline-budget").value), String(2 * 1024 ** 3));
    check("and reports the held copies",
      /0 songs kept on this device/.test(await page.evaluate(() =>
        document.getElementById("offline-status").textContent)), true);
    await page.evaluate(() => document.getElementById("dlg-offline").close());

    // A prefetched song is already in memory: keeping it must not download it again.
    const askedBefore = hits[5] || 0;
    await page.evaluate(async () => {
      playingId = null;
      const url = URL.createObjectURL(new Blob([new Uint8Array(4000).fill(67)], { type: "audio/mpeg" }));
      aheadAudio.set(5, url);
      await cachePlayedAudio(5);
      URL.revokeObjectURL(url);
    });
    check("a prefetched song is kept without a second download", hits[5] || 0, askedBefore);
    check("and the kept bytes are the ones from memory", await page.evaluate(async () => {
      const cache = await caches.open("hardlylistening-audio-v1");
      const hit = await cache.match("/api/track/5/audio");
      return hit ? (await hit.blob()).size : 0;
    }), SIZE);

    // The automatic path: a song that starts playing is kept after it has settled.
    await page.evaluate(() => {
      playingId = 4;
      document.getElementById("player-audio").dispatchEvent(new Event("playing"));
    });
    await page.waitForTimeout(6500);
    check("a song is kept once it plays", await page.evaluate(() => !!offlineIndex()["4"]), true);
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("offline audio cache ok");
})();