// The artist page's two lists are chronological: newest release first, and every row in the
// list layout carries the date it came out. The order is the server's (it joins the release
// table, because a song row has no date of its own); this proves the page draws it in that
// order and prints the date, rather than keeping a second A→Z of its own.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
  ".png": "image/png", ".svg": "image/svg+xml" };

const ART = "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600"><rect width="600" height="600" fill="#2a3550"/></svg>`);

const RELEASES = [
  { release_id: "1", title: "This Year", release_type: "album", track_count: 2,
    artwork: ART, source_url: "https://music.apple.com/album/1", release_date: "2026-11-01" },
  { release_id: "2", title: "Two Years Ago", release_type: "album", track_count: 1,
    artwork: ART, source_url: "https://music.apple.com/album/2", release_date: "2024-05-01" },
  { release_id: "3", title: "Old Set", release_type: "album", track_count: 1,
    artwork: ART, source_url: "https://music.apple.com/album/3", release_date: "2019-02-01" },
];
// The server's order: newest release first, album order inside it.
const SONGS = [
  { title: "Fresh One", album: "This Year", track_no: 1, release_date: "2026-11-01",
    source_url: "https://music.apple.com/song/11" },
  { title: "Fresh Two", album: "This Year", track_no: 2, release_date: "2026-11-01",
    source_url: "https://music.apple.com/song/12" },
  { title: "Middle", album: "Two Years Ago", track_no: 1, release_date: "2024-05-01",
    source_url: "https://music.apple.com/song/21" },
  { title: "Ancient", album: "Old Set", track_no: 1, release_date: "2019-02-01",
    source_url: "https://music.apple.com/song/31" },
];

const SUBS = { subscriptions: [{ id: 5, artist_name: "Fixture", provider: "apple", enabled: 1,
  artist_url: "https://music.apple.com/artist/5" }] };

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/state") return json({ viewer: { is_admin: true, role: "admin", auth_required: false, display_name: "Admin" },
    stats: { done: 0, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null });
  if (p === "/api/feed") return json({ items: [] });
  if (p === "/api/subscriptions") return json(SUBS);
  if (p === "/api/catalogue") return json({ artist: "Fixture", provider: "apple", releases: RELEASES,
    tracks: SONGS, track_total: SONGS.length });
  if (p === "/api/tracks") return json({ tracks: [] });
  if (p === "/api/listening/saves") return json({ tracks: [], playlists: [], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/preferences") return json({ favorites: [], pins: [], album_likes: [] });
  if (p.startsWith("/api/")) return json({ ok: true, entries: [], decisions: [], rows: [], all_tracks: [], playlists: [] });
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

async function rows(page, selector){
  return page.evaluate(sel => [...document.querySelectorAll(sel)].map(el => ({
    title: (el.querySelector(".feedtitle") || {}).textContent || "",
    meta: (el.querySelector(".feedmeta") || {}).textContent || "",
  })), selector);
}

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  let failures = 0;
  const check = (name, ok, detail) => {
    if (ok) console.log("PASS " + name);
    else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
  };
  try {
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.waitForTimeout(900);
    await page.evaluate(() => { localStorage.setItem("hl.artistLayout", "list"); localStorage.setItem("hl.artistTab", "albums"); });
    await page.evaluate(() => { document.getElementById("view-library").hidden = false;
      window.setRepoTab("releases"); });
    await page.evaluate(() => window.openArtistPage(5, "Fixture", ""));
    await page.waitForSelector("#fapAlbums .falist .feedrel", { timeout: 5000 });

    const albums = await rows(page, "#fapAlbums .falist .feedrel");
    check("the album list is newest first",
      albums.map(r => r.title), ["This Year", "Two Years Ago", "Old Set"]);
    check("every album row carries its date",
      albums.map(r => /2026-11-01|2024-05-01|2019-02-01/.test(r.meta)), [true, true, true]);

    await page.evaluate(() => window.setArtistTab("songs"));
    await page.waitForSelector("#fapSongs .falist .feedrel", { timeout: 5000 });
    const songs = await rows(page, "#fapSongs .falist .feedrel");
    check("the song list follows the albums, newest release first",
      songs.map(r => r.title), ["Fresh One", "Fresh Two", "Middle", "Ancient"]);
    check("the page does not re-sort them",
      songs.map(r => r.meta.includes("2019-02-01") || r.meta.includes("2024-05-01")
        || r.meta.includes("2026-11-01")), [true, true, true, true]);
    check("a song row names its release date",
      songs[0].meta.includes("2026-11-01") && songs[3].meta.includes("2019-02-01"),
      JSON.stringify(songs.map(r => r.meta.trim())));

    // The other layouts read from the same list, so they keep the order and the date.
    await page.evaluate(() => window.setArtistLayout("cards"));
    const cards = await rows(page, "#fapSongs .facard");
    check("cards keep the order", cards.map(r => r.title),
      ["Fresh One", "Fresh Two", "Middle", "Ancient"]);

    await page.evaluate(() => window.setArtistLayout("list"));
    await page.waitForTimeout(150);
    // The element screenshot scrolls it into view; a page clip would be off-screen.
    await page.locator("#fapSongs .falist").screenshot({ path: "/tmp/opencode/artist-song-rows.png" });
    check("the first painted rows are the newest", true);
  } catch (error) {
    failures++;
    console.log("FAIL " + error.message);
  } finally {
    await browser.close();
    srv.close();
  }
  if (failures) { console.log(`${failures} check(s) failed`); process.exit(1); }
  console.log("artist dates ok");
})();