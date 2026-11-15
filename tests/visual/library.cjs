// Visual baselines for the Library. snap.cjs covers the player; this covers the surfaces the
// CSS architecture refactor touches hardest: the list row (including the playing row's border)
// and the card wall. Same harness as snap.cjs — stub API, animation frozen, stored PNG.
//
//   node tests/visual/library.cjs --update     rewrite the baselines
//   node tests/visual/library.cjs              compare, exit 1 on any changed pixel
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("../_pw.cjs");
const ROOT = path.join(__dirname, "..", "..", "web");
const OUT = path.join(__dirname, "library");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };
const STUB = { ok: true, songs: [], queue: [], tracks: [], playlists: [], folders: [], favorites: [], library: [],
  entries: [], decisions: [], rows: [], all_tracks: [], playlist_tracks: [], folder_tracks: [] };

// Six tracks over two albums: enough rows to show separators, one dead source, one playlist, and
// one pinned/liked set so the preference markers are in frame.
const track = (id, title, album, extra = {}) => ({ id, title, artist_name: "Baseline Artist",
  album, album_id: "album-" + album.toLowerCase().replace(/\W+/g, "-"), duration: 180 + id,
  added: 1700000000 + id, playable: true, artwork: "", provider: "soundcloud",
  source_url: "", playlists: [{ id: 7, title: "Baseline Set", position: id }], ...extra });
const LIBRARY = {
  tracks: [
    track(1, "First Light", "Night Drive"),
    track(2, "Second Wind", "Night Drive"),
    track(3, "Third Rail", "Night Drive"),
    track(4, "Harbour", "Low Tide", { source_status: "gone", source_checked: 1700009999 }),
    track(5, "Silt", "Low Tide"),
    track(6, "Saltmarsh", "Low Tide"),
  ],
  artist_art: {},
  playlists: [{ id: 7, title: "Baseline Set" }],
};
LIBRARY.all_tracks = LIBRARY.tracks;

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/library") return json(LIBRARY);
  // The personal library is the saved subset of the catalogue, so the stub marks every track
  // saved; without this the Library renders empty whatever /api/library returns.
  if (p === "/api/listening/saves") return json({ tracks: LIBRARY.tracks.map(t => t.id),
    playlists: [7], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/preferences") return json({ favorites: [{ kind: "track", id: 2 }],
    pins: [{ kind: "track", id: 3 }], album_likes: [] });
  if (p === "/api/state") return json({ viewer: { is_admin: true, role: "admin", auth_required: false, display_name: "Admin" },
    stats: { done: 0, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null });
  if (p.startsWith("/api/")) return json(STUB);
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

const FREEZE = `(() => {
  const s = document.createElement('style');
  s.textContent = '*,*::before,*::after{animation:none!important;transition:none!important}';
  document.head.appendChild(s);
})();`;

// states: list, cards, list-playing
async function render(page, state) {
  await page.goto(page.__base, { waitUntil: "load" });
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    const tab = document.querySelector('.tab[data-view="library"][data-scope="mine"]');
    if (tab) tab.click();
  });
  await page.waitForTimeout(500);
  await page.evaluate(state => {
    if (typeof setSongLayout === "function") setSongLayout(state === "cards" ? "grid" : "list");
    playingId = state === "list-playing" ? 2 : null;
    detail = null;
    if (typeof renderLibrary === "function") renderLibrary();
  }, state);
  await page.waitForTimeout(350);
  await page.evaluate(FREEZE);
  await page.waitForTimeout(150);
  await page.evaluate(() => { window.requestAnimationFrame = () => 0; });
  await page.waitForTimeout(120);
}

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

const PLAN = [
  [1280, 800, ["list", "cards", "list-playing"]],
  [390, 844, ["list", "cards"]],
];

(async () => {
  const update = process.argv.includes("--update");
  const srv = server(); await new Promise(r => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}/`;
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const comparePage = await browser.newPage();
  await comparePage.goto("about:blank");
  let failed = 0;
  try {
    for (const [w, h, states] of PLAN) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      page.__base = base;
      for (const state of states) {
        const name = `library-${state}-${w}x${h}.png`;
        const file = path.join(OUT, name);
        await render(page, state);
        const shot = await page.screenshot({ animations: "disabled" });
        if (update || !fs.existsSync(file)) { fs.writeFileSync(file, shot); console.log("wrote", name); continue; }
        const d = await diff(comparePage, shot.toString("base64"), fs.readFileSync(file).toString("base64"));
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
  else console.log(update ? "Baseline written" : "Library baseline PASS");
})().catch(e => { console.error("ERROR " + e.message); process.exitCode = 1; });