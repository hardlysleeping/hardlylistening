// The artist page shows the whole stored catalogue as two sections — Albums and Songs —
// behind the library's icon tabs, with the same cards / covers / rows switch applied to
// both, a filter in the artist's own row, and the songs paged (60 at a time).
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };

const ART = "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#2a3550"/></svg>`);
const WATCH = 5;
const RELEASES = Array.from({ length: 6 }, (_, i) => ({
  release_id: `album:${i + 1}`, title: `Catalogue Album ${i + 1}`, release_type: "album",
  release_date: `2026-0${6 - i}-01`, track_count: 8, artwork: ART,
  source_url: `https://music.apple.com/album/${i + 1}`,
}));
const SONG = n => ({
  track_id: String(n), release_id: "album:1", title: `Catalogue Song ${String(n).padStart(3, "0")}`,
  artist: "Fixture", album: "Catalogue Album 1", track_no: n, duration: 210,
  artwork: ART, source_url: `https://music.apple.com/song/${n}`,
});
const TOTAL = 122;
const page = (offset) => ({
  artist: "Fixture", provider: "apple", releases: RELEASES,
  tracks: Array.from({ length: Math.max(0, Math.min(60, TOTAL - offset)) },
    (_, i) => SONG(offset + i + 1)),
  track_total: TOTAL, offset, page: 60, fetched: 1,
});
const FEED = { items: [{ id: 1, watch_id: WATCH, artist: "Fixture", provider: "apple",
  decision: "new", title: "Catalogue Album 1", release_type: "album", track_count: 8,
  artwork: ART, release_date: "2026-06-01", source_url: "https://music.apple.com/album/1" }] };
const SUBS = { subscriptions: [{ id: WATCH, artist_name: "Fixture", provider: "apple",
  enabled: 1, artist_url: "https://music.apple.com/artist/1", mine: true }] };

const asked = [];

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/state") return json({ viewer: { is_admin: true, role: "admin", auth_required: false, display_name: "Admin" },
    stats: { done: 0, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null });
  if (p === "/api/feed") return json(FEED);
  if (p === "/api/subscriptions") return json(SUBS);
  if (p === "/api/catalogue") {
    const query = new URL(req.url, "http://x").searchParams;
    const offset = Number(query.get("offset") || 0);
    asked.push(offset);
    return json(page(offset));
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
      asked.length = 0;
      const page1 = await browser.newPage({ viewport });
      const errors = [];
      page1.on("pageerror", e => errors.push(String(e).slice(0, 160)));
      await page1.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
      await page1.waitForTimeout(400);
      const out = await page1.evaluate(async () => {
        const wait = ms => new Promise(r => setTimeout(r, ms));
        listeningSaves = { tracks: [], playlist_tracks: [], folder_tracks: [], playlists: [], folders: [] };
        refresh = () => Promise.resolve();
        document.querySelector('.tab[data-view="feed"]').click();
        setFeedTab("artists");
        await loadSubscriptions();
        await wait(400);
        document.querySelector("#feedlist .feedgname").click();
        await wait(500);
        const el = document.getElementById("feedartistpage");
        const search = () => document.getElementById("fapsearch");
        const type = async (value) => { search().value = value; search().dispatchEvent(new Event("input")); await wait(80); };
        const read = () => ({
          tabs: [...el.querySelectorAll("[data-fatab]")].map(b => b.dataset.fatab).join(","),
          active: el.querySelector("[data-fatab].on")?.dataset.fatab,
          tiles: el.querySelectorAll("#fapAlbums [data-faalbum]").length,
          cards: el.querySelectorAll("#fapAlbums .facard").length,
          covers: el.querySelectorAll("#fapAlbums .covercell").length,
          rows: el.querySelectorAll("#fapSongs .falist .feedrel").length,
          meter: (document.getElementById("fap-song-meter") || {}).textContent,
          meta: (document.querySelector("#fapmeta") || {}).textContent,
          more: (document.getElementById("fap-more") || {}).textContent || "",
        });
        // The tabs and the layout switch sit on the one row.
        const row = el.querySelector(".fatop");
        const tabs = document.getElementById("fapTabs").getBoundingClientRect();
        const layout = document.getElementById("fapLayout").getBoundingClientRect();
        const name = document.querySelector(".fahead .faname").getBoundingClientRect();
        const box = search().getBoundingClientRect();
        const head = document.querySelector(".fahead").getBoundingClientRect();
        // Same band, tops within a few px (the two segs differ by their padding).
        const geometry = {
          sameRow: Math.abs(tabs.top - layout.top) <= 4 && Math.abs(tabs.bottom - layout.bottom) <= 4,
          tops: [Math.round(tabs.top), Math.round(layout.top)],
          tabsLeftOfLayout: tabs.right <= layout.left,
          layoutAtRight: Math.abs(layout.right - row.getBoundingClientRect().right) < 2,
          searchInHead: box.top >= head.top - 1 && box.bottom <= head.bottom + 1,
          searchAtRight: Math.abs(box.right - head.right) < 2,
          searchOnNameRow: Math.abs((box.top + box.bottom) / 2 - (name.top + name.bottom) / 2) < 20,
          centres: [Math.round((box.top + box.bottom) / 2), Math.round((name.top + name.bottom) / 2)],
          wide: innerWidth > 560,
        };

        const first = read();                       // Albums, default list layout
        el.querySelector('[data-falayout="cards"]').click(); await wait(60);
        const cards = read();
        el.querySelector('[data-falayout="grid"]').click(); await wait(60);
        const covers = read();
        el.querySelector('[data-falayout="list"]').click(); await wait(60);
        await type("Album 3");
        const albumFilter = read();
        await type("");

        el.querySelector('[data-fatab="songs"]').click(); await wait(120);
        const songsFirst = read();
        await type("001");
        const songFilter = read();
        await type("");
        document.getElementById("fap-more").click();
        await wait(500);
        const second = read();
        const again = document.getElementById("fap-more");
        if (again) { again.click(); await wait(500); }
        const third = read();
        return { first, cards, covers, albumFilter, songsFirst, songFilter, second, third,
          moreGone: !document.getElementById("fap-more"), geometry };
      });

      const tag = `[${label}] `;
      check(tag + "Albums and Songs are the two tabs", out.first.tabs === "albums,songs", out.first.tabs);
      check(tag + "Albums is the opening tab", out.first.active === "albums", out.first.active);
      check(tag + "the tabs and the layout switch share a row",
        out.geometry.sameRow && out.geometry.tabsLeftOfLayout && out.geometry.layoutAtRight,
        JSON.stringify(out.geometry));
      check(tag + "the filter sits on the artist's name row, right-aligned",
        out.geometry.searchInHead && out.geometry.searchAtRight &&
        (!out.geometry.wide || out.geometry.searchOnNameRow),
        JSON.stringify(out.geometry));
      check(tag + "every album in the catalogue is listed",
        out.first.tiles === 6, String(out.first.tiles));
      check(tag + "albums follow the cards layout", out.cards.cards === 6, String(out.cards.cards));
      check(tag + "albums follow the covers layout", out.covers.covers === 6, String(out.covers.covers));
      check(tag + "the filter narrows the album list",
        out.albumFilter.tiles === 1 && /1 album shown/.test(out.albumFilter.meter || ""),
        `${out.albumFilter.tiles} / ${out.albumFilter.meter}`);
      check(tag + "the head states the whole catalogue",
        /6 albums · 122 songs/.test(out.first.meta || ""), out.first.meta);
      check(tag + "the Songs tab lists the first page",
        out.songsFirst.rows === 60 && out.songsFirst.meter === "60 of 122 songs",
        `${out.songsFirst.rows} / ${out.songsFirst.meter}`);
      check(tag + "the filter narrows the songs",
        out.songFilter.rows === 1 && out.songFilter.meter === "1 of 122 songs",
        `${out.songFilter.rows} / ${out.songFilter.meter}`);
      check(tag + "the rest is offered, not rendered",
        /Load more songs \(60 of 122\)/.test(out.songsFirst.more), out.songsFirst.more);
      check(tag + "Load more appends the next page",
        out.second.rows === 120 && out.second.meter === "120 of 122 songs",
        `${out.second.rows} / ${out.second.meter}`);
      check(tag + "the last page finishes the catalogue",
        out.third.rows === 122 && out.third.meter === "122 of 122 songs" && out.moreGone,
        `${out.third.rows} / ${out.third.meter} / moreGone=${out.moreGone}`);
      check(tag + "pages were asked for in order",
        JSON.stringify(asked) === JSON.stringify([0, 60, 120]), JSON.stringify(asked));
      check(tag + "no page errors", errors.length === 0, errors.join(" | "));
      await page1.close();
    }
  } finally {
    await browser.close();
    srv.close();
  }
  console.log(failures ? `${failures} FAILED` : "Artist catalogue PASS: tabs, layouts, filter, paging");
  process.exit(failures ? 1 : 0);
})();