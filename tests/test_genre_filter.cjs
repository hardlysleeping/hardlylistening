// The genre filter lives on the library's filter row: it appears with the other filters
// when that row is open, opens a popup, and narrows every library section. Storage is the
// shared taxonomy; filtering is metadata-only (no audio, no inference at render time).
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };

const ART = "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#2a3550"/></svg>`);
const TAXONOMY = ["house", "dubstep", "bass", "techno"];
// Four albums of three songs, so every section has something to lose when a genre is picked.
const GENRE_OF = { 1: "house", 2: "house", 3: "dubstep", 4: "dubstep", 5: "bass",
  6: "bass", 7: "techno", 8: "techno" };
const ROWS = [];
for (let i = 1; i <= 12; i++) {
  const album = Math.ceil(i / 3), artist = album <= 2 ? "Artist A" : "Artist B";
  ROWS.push({
    id: i, title: `Song ${i}`, artist_name: artist,
    album: `Album ${album}`, album_id: `album-${album}`, release_type: "album",
    artwork: ART, duration: 200, playable: true,
    genres: GENRE_OF[i] ? [GENRE_OF[i]] : [],
    source_url: `https://music.apple.com/song/${i}`, added_at: "2026-11-01", status: "done",
  });
}
const has = (tag) => ROWS.filter(t => (t.genres || []).includes(tag));
const both = [...has("dubstep"), ...has("bass")].map(t => t.id).sort((a, b) => a - b);
// The album list drops one-song albums, so only albums with more than one matching
// song survive the filter.
const albumCounts = {};
for (const id of both) {
  const key = ROWS.find(t => t.id === id).album_id;
  albumCounts[key] = (albumCounts[key] || 0) + 1;
}
const expectedAlbums = Object.keys(albumCounts).filter(k => albumCounts[k] > 1);
const expectedArtists = [...new Set(both.map(id => ROWS.find(t => t.id === id).artist_name))].sort();

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/state") return json({ viewer: { is_admin: false, role: "member", auth_required: false, display_name: "Member" },
    stats: { done: 12, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null });
  if (p === "/api/library") return json({ tracks: ROWS, playlists: [], folders: [] });
  if (p === "/api/genres") return json({ taxonomy: TAXONOMY, status: {} });
  if (p === "/api/listening/saves") return json({ tracks: [], playlists: [], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/preferences") return json({ favorites: [], pins: [], album_likes: [] });
  if (p === "/api/feed") return json({ items: [] });
  if (p === "/api/subscriptions") return json({ subscriptions: [] });
  if (p.startsWith("/api/track/") && p.endsWith("/artwork"))
    return res.writeHead(200, { "content-type": "image/svg+xml" }).end(decodeURIComponent(ART.split(",")[1]));
  if (p.startsWith("/api/")) return json({ ok: true, entries: [], decisions: [], rows: [], all_tracks: [], playlists: [] });
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [label, viewport] of [["desktop", { width: 1280, height: 900 }], ["phone", { width: 390, height: 844 }]]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on("pageerror", e => errors.push(String(e).slice(0, 160)));
      await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
      await page.waitForTimeout(1500);           // genres.js initialises a second after load
      await page.evaluate(async () => {
        listeningSaves = { tracks: [], playlist_tracks: [], folder_tracks: [], playlists: [], folders: [] };
        refresh = () => Promise.resolve();
        await loadLibrarySnapshot();
        document.querySelector('.tab[data-view="library"][data-scope="shared"]').click();
        setLibraryScope("shared");
        libView = "songs";
        renderLibrary();
        toggleFilters(false);                    // the row starts closed
      });
      await page.waitForTimeout(250);

      // Closed: the chip belongs to the row, so it is not on screen yet.
      const closed = await page.evaluate(() => {
        const b = document.getElementById("genresbtn"), row = document.getElementById("libfilters");
        const r = b && b.getBoundingClientRect();
        return { exists: !!b, parent: b && b.parentElement.id,
          rowDisplay: row && getComputedStyle(row).display,
          visible: !!(r && r.width && r.height),
          label: b && b.textContent.trim(), popup: b && b.getAttribute("aria-haspopup") };
      });
      check(`${label}: chip exists in the filter row`, closed.exists && closed.parent === "libfilters",
        JSON.stringify(closed));
      check(`${label}: row is closed, chip not shown`, closed.rowDisplay === "none" && !closed.visible,
        JSON.stringify(closed));
      check(`${label}: chip is labelled and announces a dialog`,
        closed.label === "Genres" && closed.popup === "dialog", JSON.stringify(closed));

      // Open: it sits on the same line as the sort controls, at their height.
      await page.evaluate(() => document.getElementById("filterbtn").click());
      await page.waitForTimeout(250);
      const open = await page.evaluate(() => {
        const b = document.getElementById("genresbtn"), row = document.getElementById("libfilters");
        const r = b.getBoundingClientRect(), s = document.getElementById("libsortdir").getBoundingClientRect();
        return { display: getComputedStyle(row).display,
          children: [...row.children].map(c => c.id || c.className),
          visible: !!(r.width && r.height), inRow: !!b.closest("#libfilters"),
          top: Math.round(r.top), height: Math.round(r.height),
          sortTop: Math.round(s.top), sortHeight: Math.round(s.height) };
      });
      check(`${label}: row opens with the chip on it`,
        open.display === "flex" && open.visible && open.inRow &&
        open.children.includes("genresbtn"), JSON.stringify(open));
      check(`${label}: chip shares the sort controls' line and height`,
        open.top === open.sortTop && open.height === open.sortHeight && open.height === 32,
        JSON.stringify(open));

      // The popup: one checkbox per taxonomy entry, modal, opened from the chip.
      await page.evaluate(() => document.getElementById("genresbtn").click());
      await page.waitForTimeout(250);
      const dlg = await page.evaluate(() => {
        const pop = document.getElementById("genres-pop");
        return { open: !!pop.open, modal: pop.matches(":modal"),
          labels: [...pop.querySelectorAll(".glabels input")].map(i => i.value),
          focusInside: pop.contains(document.activeElement) };
      });
      check(`${label}: chip opens the genre popup`,
        dlg.open && dlg.modal, JSON.stringify(dlg));
      check(`${label}: every taxonomy genre is offered`,
        JSON.stringify(dlg.labels) === JSON.stringify(TAXONOMY), JSON.stringify(dlg));

      // Two genres, OR semantics, and every section narrows to matching tracks.
      await page.evaluate(() => {
        for (const value of ["dubstep", "bass"]) {
          const box = [...document.querySelectorAll("#genres-pop .glabels input")].find(i => i.value === value);
          box.checked = true;
          box.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
      await page.waitForTimeout(250);
      const songs = await page.evaluate(() =>
        [...document.querySelectorAll("#liblist .songrow[data-id]")].map(n => Number(n.dataset.id)).sort((a, b) => a - b));
      check(`${label}: songs narrow to the selected genres (OR)`,
        JSON.stringify(songs) === JSON.stringify(both), JSON.stringify(songs));
      const albums = await page.evaluate(() => {
        document.querySelector('#libviews [data-view="albums"]').click();
        return [...document.querySelectorAll("#liblist .acard")].map(n => n.dataset.album).sort();
      });
      check(`${label}: albums narrow to those with matching songs`,
        JSON.stringify(albums) === JSON.stringify(expectedAlbums.sort()), JSON.stringify(albums));
      const artists = await page.evaluate(() => {
        document.querySelector('#libviews [data-view="artists"]').click();
        return [...document.querySelectorAll("#liblist .arcard .aname")].map(n => n.textContent).sort();
      });
      check(`${label}: artists narrow to those with matching songs`,
        JSON.stringify(artists) === JSON.stringify(expectedArtists), JSON.stringify(artists));
      const playlists = await page.evaluate(() => {
        document.querySelector('#libviews [data-view="playlists"]').click();
        return document.querySelectorAll("#liblist .plcard").length;
      });
      const playlistsUnfiltered = await page.evaluate(() => {
        document.getElementById("genres-clear").click();   // the X closes the sheet
        document.querySelector("#genres-pop .gfoot .ghost").click();  // Clear resets the filter
        return document.querySelectorAll("#liblist .plcard").length;
      });
      check(`${label}: the playlist filter still applies where it has no matches`,
        playlists <= playlistsUnfiltered, `${playlists} of ${playlistsUnfiltered}`);

      // Clearing restores everything and the chip says so.
      const reset = await page.evaluate(() => {
        document.querySelector('#libviews [data-view="songs"]').click();
        return { label: document.getElementById("genresbtn").textContent.trim(),
          rows: document.querySelectorAll("#liblist .songrow[data-id]").length };
      });
      check(`${label}: Clear restores the full list and the plain label`,
        reset.rows === ROWS.length && reset.label === "Genres", JSON.stringify(reset));

      // The label states how many genres are active, and closing the popup keeps the filter.
      const kept = await page.evaluate(() => {
        document.getElementById("genresbtn").click();
        const box = [...document.querySelectorAll("#genres-pop .glabels input")].find(i => i.value === "techno");
        box.checked = true;
        box.dispatchEvent(new Event("change", { bubbles: true }));
        document.querySelector("#genres-pop .gfoot .primary").click();   // Done
        return { open: document.getElementById("genres-pop").open,
          label: document.getElementById("genresbtn").textContent.trim(),
          on: document.getElementById("genresbtn").classList.contains("on"),
          rows: document.querySelectorAll("#liblist .songrow[data-id]").length };
      });
      check(`${label}: Done closes the sheet and keeps the filter`,
        !kept.open && kept.label === "Genres (1)" && kept.on && kept.rows === has("techno").length,
        JSON.stringify(kept));

      check(`${label}: no page errors`, errors.length === 0, errors.join(" | "));
      await page.close();
    }
  } finally {
    await browser.close();
    srv.close();
  }
  console.log(failures ? `${failures} FAILED` : "Genre filter PASS");
  process.exit(failures ? 1 : 0);
})();