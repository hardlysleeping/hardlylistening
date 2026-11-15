// The pinned strip: the same cover the rows draw, the listing's own width, a carousel with
// two arrows instead of a scrollbar, and pins that never move anything in the library.
//
//   NODE_PATH=/tmp/hardlyui-public/node_modules:$HOME/apps/ada-bot/node_modules node tests/test_pinned_strip.cjs
const fs = require("fs"), path = require("path"), http = require("http"), assert = require("assert");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };

// Twelve songs over three releases. Every song has the server's 640 px digest, and the
// provider URL is the 100 px "-large" thumbnail — the copy the strip used to draw.
const HASH = c => c.repeat(64);
const tracks = [];
let id = 0;
for (const [album, artist, art, key] of [["Neon Drift", "Kessler", "a", "1"],
  ["Paper Moons", "Ada Vale", "b", "2"], ["Static Bloom", "Mira Kestrel", "c", "3"]]) {
  for (let n = 0; n < 4; n++) {
    id++;
    tracks.push({ id, title: `${album} ${n + 1}`, artist_name: artist, album, album_id: key,
      duration: 210 + n, track_number: n + 1, playable: true, art_hash: HASH(art),
      artwork: `https://i1.sndcdn.com/artworks-${id}-large.jpg`, provider: "soundcloud",
      source_url: "", added: 1700000000 + id, votes: 0, bpm: 124 });
  }
}
// Nine pins: the six songs at the END of the alphabet, two of the three releases and the
// second playlist. A pinned-first sort would lift every one of them above its neighbours,
// which is exactly what the row and card order checks look for.
const pins = [
  ...[4, 5, 6, 7, 8, 9, 10, 11, 12].map(n => ({ kind: "track", target: String(n) })),
  { kind: "album", target: "3" },
  { kind: "album", target: "2" },
  { kind: "playlist", target: "8" }];
assert.equal(pins.length, 12);
const prefs = { favorites: [], album_likes: [], pins, public_favorites: {}, playback: {}, theme: {} };

const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = body => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(body));
  if (p === "/api/library") return json({ tracks, all_tracks: tracks, artist_art: {}, playlists: [
    { id: 7, title: "Basement Sets", added_by: "me", artwork: "https://i1.sndcdn.com/artworks-99-large.jpg" },
    { id: 8, title: "Attic Tapes", added_by: "me", artwork: "https://i1.sndcdn.com/artworks-98-large.jpg" }] });
  if (p === "/api/listening/preferences") return json(prefs);
  // My Library lists the songs saved to it, so every row is saved here.
  if (p === "/api/listening/saves") return json({ tracks: tracks.map(t => t.id), playlists: [7, 8] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/now") return json({ state: null });
  // A real (1 × 1) PNG: a 404 here would trip `artError` and the tile would fall back to
  // its initials, which hides the URL the tile actually asked for.
  if (p.startsWith("/api/art/")) return res.writeHead(200, { "content-type": "image/png" }).end(PNG);
  if (p.startsWith("/api/")) return json({ ok: true, songs: [], queue: [], tracks: [], playlists: [],
    folders: [], favorites: [], pins: [], likes: [], library: [], items: [], entries: [], results: [] });
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
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    page.on("pageerror", e => { failed++; console.log("FAIL page error: " + e.message); });
    // A playlist pin only has a provider URL, and the tile must keep it: a 404 here would
    // trip `artError` and delete the <img> before its src could be read.
    await page.route("https://i1.sndcdn.com/**", r => r.fulfill({ contentType: "image/png", body: PNG }));
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.waitForTimeout(600);
    await page.evaluate(() => {
      document.getElementById("view-library").hidden = false;
      setView("songs");
      setLibraryScope("mine");
      libSort = "title"; libSortDir = "asc";
      renderLibrary();
    });
    await page.waitForTimeout(500);

    // ---- a pin is not a rank -------------------------------------------------
    const rows = await page.$$eval("#liblist .songrow", ns => ns.map(n => n.dataset.id));
    check("the library keeps its own order with six songs pinned", rows.slice(0, 4), ["1", "2", "3", "4"]);
    check("pins do not lift the end of the alphabet to the top", rows.slice(-3), ["10", "11", "12"]);
    check("every song is still there", rows.length, 12);

    // Albums and playlists, same rule: the pin shows in the strip, not at the top of the grid.
    await page.evaluate(() => { setView("albums"); libSort = "title"; libSortDir = "asc"; renderLibrary(); });
    await page.waitForTimeout(300);
    const albums = await page.$$eval("#liblist .acard .st b", ns => ns.map(n => n.textContent.trim()));
    check("the album grid is not reordered by its pins", albums.slice(0, 3), ["Neon Drift", "Paper Moons", "Static Bloom"]);
    await page.evaluate(() => { setView("playlists"); renderLibrary(); });
    await page.waitForTimeout(300);
    const lists = await page.$$eval("#liblist .plcard:not(.dyn) .pt", ns => ns.map(n => n.textContent.trim()));
    check("the playlist grid is not reordered by its pin", lists.slice(0, 2), ["Basement Sets", "Attic Tapes"]);
    await page.evaluate(() => { setView("songs"); renderLibrary(); });
    await page.waitForTimeout(300);

    // ---- the strip draws the same cover as the rows --------------------------
    const art = await page.evaluate(() => {
      const car = document.getElementById("pinnedcarousel");
      const src = t => t.querySelector(".pinart img")?.getAttribute("src") || "";
      const byKind = k => [...car.querySelectorAll(`.pinned-tile[data-pin-kind="${k}"]`)].map(src);
      return { track: car.querySelector('.pinned-tile[data-pin-kind="track"][data-pin-target="9"] .pinart img')?.getAttribute("src") || "",
        albums: byKind("album"), playlist: byKind("playlist")[0],
        row: document.querySelector(`#liblist .songrow[data-id="9"] .thumb img`)?.getAttribute("src") || "",
        tiles: car.querySelectorAll(".pinned-tile").length };
    });
    check("a pinned song uses the server's digest, like its row", art.track, `/api/art/${HASH("c")}`);
    check("the row below it uses the same URL", art.row, art.track);
    check("pinned albums keep their digests, in pin order", art.albums,
      [`/api/art/${HASH("c")}`, `/api/art/${HASH("b")}`]);
    check("a pinned playlist asks for the 500 px render, not -large",
      art.playlist, "https://i1.sndcdn.com/artworks-98-t500x500.jpg");
    check("every pin renders a tile", art.tiles, 12);

    // ---- the strip is the listing's width ------------------------------------
    const box = await page.evaluate(() => {
      const w = n => Math.round(n.getBoundingClientRect().width);
      return { panel: w(document.querySelector(".mixed-pinned")), bar: w(document.querySelector("#view-library .libbar")),
        list: w(document.getElementById("liblist")), rows: getComputedStyle(document.getElementById("pinnedcarousel")).gridTemplateRows.split(" ").length };
    });
    check("the strip matches the bar's width", box.panel, box.bar);
    check("the strip matches the list's width", box.panel, box.list);
    check("desktop is one row of tiles", box.rows, 1);

    // ---- carousel, not scrollbar --------------------------------------------
    const car = await page.evaluate(() => {
      const c = document.getElementById("pinnedcarousel");
      return { overflowX: getComputedStyle(c).overflowX, bar: getComputedStyle(c).scrollbarWidth,
        chip: document.getElementById("pinpageinfo").textContent,
        prev: document.getElementById("pinprev").disabled, next: document.getElementById("pinnext").disabled };
    });
    check("the strip still scrolls under the arrows", car.overflowX, "auto");
    check("the browser's own scrollbar is gone", car.bar, "none");
    check("page 1 of 2", car.chip, "page 1/2");
    check("back is dead on the first page", car.prev, true);
    check("forward is live", car.next, false);

    const page2 = await page.evaluate(async () => {
      const c = document.getElementById("pinnedcarousel");
      const before = c.scrollLeft;
      document.getElementById("pinnext").click();
      await new Promise(r => setTimeout(r, 700));
      return { moved: Math.round(c.scrollLeft), width: c.clientWidth, before,
        chip: document.getElementById("pinpageinfo").textContent,
        prev: document.getElementById("pinprev").disabled };
    });
    check("the arrow moves exactly one page", page2.moved, page2.width);
    check("the chip follows", page2.chip, "page 2/2");
    check("back wakes up on page 2", page2.prev, false);

    const last = await page.evaluate(() => ({ chip: document.getElementById("pinpageinfo").textContent,
      next: document.getElementById("pinnext").disabled }));
    check("page 2 is the last", last.chip, "page 2/2");
    check("forward is dead on the last page", last.next, true);
    await page.close();

    // ---- a phone keeps its 3 × 2 page ---------------------------------------
    const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await phone.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await phone.waitForTimeout(500);
    await phone.evaluate(() => {
      document.getElementById("view-library").hidden = false;
      setView("songs"); setLibraryScope("mine"); renderLibrary();
    });
    await phone.waitForTimeout(400);
    const small = await phone.evaluate(() => {
      const c = document.getElementById("pinnedcarousel");
      return { rows: new Set([...c.querySelectorAll(".pinned-tile")].map(t => Math.round(t.offsetTop))).size,
        chip: document.getElementById("pinpageinfo").textContent,
        width: Math.round(document.querySelector(".mixed-pinned").getBoundingClientRect().width),
        viewport: innerWidth };
    });
    check("phone keeps two rows", small.rows, 2);
    check("phone pages six at a time", small.chip, "page 1/2");
    check("phone strip is still full width", small.width > small.viewport - 20, true);
    await phone.close();
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`FAILED ${failed}`); process.exitCode = 1; }
  else console.log("PASS Pinned strip: digest artwork, listing width, arrow carousel, pins never reorder the library");
})();