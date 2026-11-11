// Visual baselines for the player. The point is not prettiness: it is that a future edit to one
// control can be proven not to have moved anything else. Each state is rendered from the app's
// own markup, CSS and paint functions against a stub API, with animation stopped at a fixed
// point, and compared against a stored PNG.
//
//   node tests/visual/snap.cjs --update     rewrite the baselines
//   node tests/visual/snap.cjs              compare, exit 1 on any changed pixel
//   node tests/visual/snap.cjs --selfcheck  capture twice and prove the baseline is stable
const fs = require("fs"), path = require("path"), http = require("http"), assert = require("assert");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "..", "web");
const OUT = path.join(__dirname, "baseline");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };
const STUB = { ok: true, songs: [], queue: [], tracks: [], playlists: [], folders: [], favorites: [], library: [] };
const STATES = ["compact", "fullscreen", "queue"];
// The three states at the two phone widths and the desktop width, then the compact bar alone
// across the widths where its edges have to keep lining up with the library.
const VIEWPORTS = [[390, 844], [430, 932], [1280, 800]];
const WIDTHS = [[1024, 800], [1440, 900], [1920, 1080]];

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  if (p.startsWith("/api/")) return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(STUB));
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

// Everything that would differ between two runs: animation, transitions, the spinning disc and
// the canvas wave. Stop RAF after the state has been painted so the captured frame is fixed.
const FREEZE = `(() => {
  const s = document.createElement('style');
  s.textContent = '*,*::before,*::after{animation:none!important;transition:none!important}';
  document.head.appendChild(s);
})();`;

async function render(page, state) {
  await page.goto(page.__base, { waitUntil: "load" });
  await page.waitForTimeout(400);
  await page.evaluate(() => {
    const track = { id: 42, title: "Baseline Track", artist_name: "Artist", album: "Album",
      artwork: "", provider: "soundcloud", source_url: "" };
    try { libAll = [track]; } catch (_) {}
    try { libQueue = []; } catch (_) {}
    playingId = 42;
    const art = document.getElementById("plart"); if (art) art.src = "";
    const title = document.getElementById("pltitle"); if (title) title.textContent = track.title;
    const sub = document.getElementById("plsub"); if (sub) sub.textContent = "Artist · Album";
    document.getElementById("player").classList.add("on");
  });
  await page.waitForTimeout(250);
  if (state !== "compact") await page.evaluate(() => { if (typeof openFullPlayer === "function") openFullPlayer(); });
  await page.waitForTimeout(250);
  if (state === "queue") await page.evaluate(() => { if (typeof showFullQueue === "function") showFullQueue(true); });
  await page.evaluate(FREEZE);
  await page.waitForTimeout(200);
  await page.evaluate(() => { window.requestAnimationFrame = () => 0; });
  await page.waitForTimeout(120);
}

// Screenshot bytes in, changed-pixel count out. Decoding happens in the browser, which already
// owns a PNG decoder; nothing extra has to be installed.
async function diff(comparePage, a, b) {
  return comparePage.evaluate(async ([a, b]) => {
    const load = src => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = "data:image/png;base64," + src; });
    const [ia, ib] = await Promise.all([load(a), load(b)]);
    if (ia.width !== ib.width || ia.height !== ib.height) return { size: true, a: [ia.width, ia.height], b: [ib.width, ib.height] };
    const c = document.createElement("canvas"); c.width = ia.width; c.height = ia.height;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(ia, 0, 0); const da = g.getImageData(0, 0, c.width, c.height).data;
    g.clearRect(0, 0, c.width, c.height); g.drawImage(ib, 0, 0); const db = g.getImageData(0, 0, c.width, c.height).data;
    let n = 0, x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
    for (let i = 0; i < da.length; i += 4) {
      if (da[i] !== db[i] || da[i + 1] !== db[i + 1] || da[i + 2] !== db[i + 2]) {
        n++; const p = i / 4, x = p % c.width, y = (p / c.width) | 0;
        if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y;
      }
    }
    return { pixels: n, box: n ? [x0, y0, x1, y1] : null };
  }, [a, b]);
}

(async () => {
  const update = process.argv.includes("--update"), selfcheck = process.argv.includes("--selfcheck");
  const srv = server(); await new Promise(r => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}/`;
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const comparePage = await browser.newPage();
  await comparePage.goto("about:blank");
  let failed = 0;
  try {
    const plan = [
      ...VIEWPORTS.map(([w, h]) => [w, h, STATES]),
      ...WIDTHS.map(([w, h]) => [w, h, ["compact"]]),
    ];
    for (const [w, h, states] of plan) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      page.__base = base;
      for (const state of states) {
        const name = `${state}-${w}x${h}.png`;
        const file = path.join(OUT, name);
        await render(page, state);
        const shot = await page.screenshot({ animations: "disabled" });
        const b64 = shot.toString("base64");
        if (update || !fs.existsSync(file)) {
          fs.writeFileSync(file, shot);
          console.log("wrote", name);
          continue;
        }
        if (selfcheck) {
          await render(page, state);
          const again = (await page.screenshot({ animations: "disabled" })).toString("base64");
          const d = await diff(comparePage, b64, again);
          if (d.pixels) { failed++; console.log(`UNSTABLE ${name}: ${d.pixels} px differ between two captures`); }
          else console.log("stable", name);
          continue;
        }
        let d = await diff(comparePage, b64, fs.readFileSync(file).toString("base64"));
        if (d.pixels) {
          // One retry: the queue state's text can land on either side of a subpixel snap.
          await render(page, state);
          const again = (await page.screenshot({ animations: "disabled" })).toString("base64");
          const d2 = await diff(comparePage, again, fs.readFileSync(file).toString("base64"));
          if (!d2.pixels) { console.log(`ok ${name} (subpixel retry)`); continue; }
          d = d.pixels <= d2.pixels ? d : d2;
        }
        if (d.size) { failed++; console.log(`SIZE ${name}: ${d.a} vs ${d.b}`); continue; }
        if (d.pixels) {
          failed++;
          fs.writeFileSync(path.join(OUT, name.replace(".png", ".actual.png")), shot);
          console.log(`CHANGED ${name}: ${d.pixels} px, box ${d.box}`);
        } else console.log("ok", name);
      }
      await page.close();
    }
  } finally { await browser.close(); srv.close(); }
  if (failed) { console.log(`FAIL ${failed} state(s) differ`); process.exitCode = 1; }
  else console.log(update || selfcheck ? "Baseline written" : "Visual baseline PASS");
})().catch(e => { console.error("ERROR " + e.message); process.exitCode = 1; });