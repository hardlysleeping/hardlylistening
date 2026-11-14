// The Feed artist page: the portrait OR the name opens it, an album opens an album
// page shaped like the Repo's (hero + tracklist + download), and Back walks out one
// level at a time. A fold-out under the grid is what this replaces.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };

const ART = "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600"><rect width="600" height="600" fill="#2a3550"/></svg>`);
const ALBUMS = [
  { release_id: "1", title: "Fibonacci Pt 2: Infinity", release_type: "album", track_count: 3,
    artwork: ART, source_url: "https://music.apple.com/album/1", release_date: "2026-11-01" },
  { release_id: "2", title: "Antifractals", release_type: "album", track_count: 2,
    artwork: ART, source_url: "https://music.apple.com/album/2", release_date: "2026-09-01" },
];
const TRACKS = {
  "https://music.apple.com/album/1": [
    { n: 1, title: "Infinity", artist: "Subtronics", duration: 214, url: "https://music.apple.com/song/11" },
    { n: 2, title: "Anxious", artist: "Subtronics", duration: 198, url: "https://music.apple.com/song/12" },
    { n: 3, title: "Got Away", artist: "Subtronics", duration: 240, url: "https://music.apple.com/song/13" },
  ],
  "https://music.apple.com/album/2": [
    { n: 1, title: "Spacetime", artist: "Subtronics", duration: 203, url: "https://music.apple.com/song/21" },
    { n: 2, title: "Gangstas", artist: "Subtronics", duration: 187, url: "https://music.apple.com/song/22" },
  ],
};
const FEED = { items: [{
  id: 1, watch_id: 5, artist: "Subtronics", provider: "apple", decision: "new",
  title: "Fibonacci Pt 2: Infinity", release_type: "album", track_count: 3,
  artwork: ART, release_date: "2026-11-01", source_url: "https://music.apple.com/album/1",
}] };
const SUBS = { subscriptions: [{
  id: 5, artist_name: "Subtronics", provider: "apple", enabled: 1,
  artist_url: "https://music.apple.com/artist/1",
}] };

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/state") return json({ viewer: { is_admin: true, role: "admin", auth_required: false, display_name: "Admin" },
    stats: { done: 0, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null });
  if (p === "/api/feed") return json(FEED);
  if (p === "/api/subscriptions") return json(SUBS);
  if (p === "/api/catalogue") return json({ artist: "Subtronics", provider: "apple", releases: ALBUMS });
  if (p === "/api/tracks") {
    const url = decodeURIComponent((req.url.split("url=")[1] || "").split("&")[0]);
    return json({ tracks: TRACKS[url] || [] });
  }
  if (p === "/api/listening/saves") return json({ tracks: [], playlists: [], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/preferences") return json({ favorites: [], pins: [], album_likes: [] });
  if (p.startsWith("/api/")) return json({ ok: true, entries: [], decisions: [], rows: [], all_tracks: [], playlists: [] });
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

const wait = ms => new Promise(r => setTimeout(r, ms));
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
      await page.waitForTimeout(400);
      const out = await page.evaluate(async () => {
        const wait = ms => new Promise(r => setTimeout(r, ms));
        listeningSaves = { tracks: [], playlist_tracks: [], folder_tracks: [], playlists: [], folders: [] };
        refresh = () => Promise.resolve();
        // Releases are the Repository's section now, not a Feed tab (UI-021).
        document.querySelector('.tab[data-view="library"][data-scope="shared"]').click();
        setRepoTab("releases");
        await loadSubscriptions();
        await wait(500);
        const pageEl = document.getElementById("feedartistpage");
        const out = {};

        // The name is a door; the rest of the row is still the folding control.
        document.querySelector("#feedlist .feedgname").click();
        await wait(500);
        out.nameOpensPage = !!pageEl && !pageEl.hidden;
        out.heads = pageEl.querySelectorAll(".fahead").length;
        out.tiles = pageEl.querySelectorAll("[data-faalbum]").length;

        pageEl.querySelector("[data-faalbum]").click();
        await wait(600);
        const view = document.getElementById("fapalbumview");
        out.albumOpen = !!view && !view.hidden;
        out.headHidden = !!pageEl.querySelector(".fahead").hidden;
        out.bodyHidden = document.getElementById("fapbody").hidden;
        out.kicker = (view.querySelector(".hero .kicker") || {}).textContent;
        out.title = (view.querySelector(".hero h3") || {}).textContent;
        out.sub = (view.querySelector(".hero .hsub") || {}).textContent;
        out.rows = view.querySelectorAll(".fapalbumtable .songrow").length;
        out.rowDownloads = view.querySelectorAll(".fapalbumtable [data-track-url]").length;
        out.rowTitles = [...view.querySelectorAll(".fapalbumtable .st b")].map(b => b.textContent);
        out.albumDownload = !!view.querySelector(".hacts [data-cat-url]");

        document.getElementById("fapback").click();
        await wait(300);
        out.backToArtist = view.hidden && !document.getElementById("fapbody").hidden
          && !pageEl.querySelector(".fahead").hidden && !pageEl.hidden;
        document.getElementById("fapback").click();
        await wait(300);
        out.backOut = pageEl.hidden;

        const foldBody = document.querySelector("#feedlist .feedgbody");
        const before = foldBody.hidden;
        document.querySelector("#feedlist .feedghead").click();
        out.rowStillFolds = foldBody.hidden !== before;
        return out;
      });

      const tag = `[${label}] `;
      check(tag + "the artist name opens the artist page", out.nameOpensPage);
      check(tag + "the artist page lists their albums", out.tiles === 2, String(out.tiles));
      check(tag + "an album opens a page, not a fold-out", out.albumOpen && out.bodyHidden);
      check(tag + "the album page leads with the Repo-shaped hero",
            out.kicker === "Album" && out.title === "Fibonacci Pt 2: Infinity"
            && /3 songs · 11 min/.test(out.sub || ""), out.sub);
      check(tag + "every song is listed with its own download",
            out.rows === 3 && out.rowDownloads === 3
            && out.rowTitles.join("|") === "Infinity|Anxious|Got Away", out.rowTitles.join("|"));
      check(tag + "the album can be grabbed whole", out.albumDownload);
      check(tag + "Back returns to the artist page", out.backToArtist);
      check(tag + "Back again leaves the artist page", out.backOut);
      check(tag + "the rest of the group row still folds", out.rowStillFolds);
      check(tag + "no page errors", errors.length === 0, errors.join(" / "));
      await page.close();
    }
  } finally {
    await browser.close();
    srv.close();
  }
  if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
  console.log("\nFeed artist page PASS: name opens it, album opens a page, Back walks out, row still folds");
})();