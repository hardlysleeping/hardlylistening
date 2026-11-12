// The Repository's three sections: Browse / Releases / Subscribed.
//
// Releases and the follows used to be Feed sub-tabs. They are catalogue, so they live in
// the shared Repository now, and Feed is Activity only (UI-020…023). What this pins:
// the row exists only in the shared scope, picking a section shows that pane and hides
// Browse, the choice survives a reload, and an artist release page still walks back into
// the Repository copy and out again (UI-024).
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };

const ART = "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#2a3550"/></svg>`);
const ROWS = [
  { id: 1, title: "Song 1", artist_name: "Late Night", album: "Album", album_id: "al-1",
    artwork: ART, duration: 200, playable: true, source_url: "https://example.test/1",
    added_at: "2026-11-01", status: "done" },
];
const WATCH = { id: 7, provider: "apple", artist_id: "42", artist_name: "Late Night",
  artist_url: "https://soundcloud.com/late-night", enabled: 1, interval: 21600,
  last_checked: Math.floor(Date.now() / 1000) - 600, new_items: 2, mine: true };
const ITEM = { id: 11, watch_id: 7, provider: "apple", release_id: "r1",
  artist: "Late Night", title: "New EP", release_type: "ep", track_count: 3,
  release_date: "2026-11-10", artwork: ART, source_url: "https://example.test/ep",
  first_seen: Math.floor(Date.now() / 1000) - 900, decision: "new", follow_name: "Late Night" };

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/state") return json({ viewer: { is_admin: false, role: "member", auth_required: false, display_name: "Member" },
    stats: { done: 1, queued: 0, review: 0, failed: 0, feed_new: 2 }, playlists: [], logs: [],
    subscriptions: [WATCH], current: null });
  if (p === "/api/library") return json({ tracks: ROWS, playlists: [], folders: [] });
  if (p === "/api/genres") return json({ taxonomy: [], status: {} });
  if (p === "/api/listening/saves") return json({ tracks: [], playlists: [], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/preferences") return json({ favorites: [], pins: [], album_likes: [] });
  if (p === "/api/feed") return json({ items: [ITEM] });
  if (p === "/api/subscriptions") return json({ subscriptions: [WATCH] });
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

const shown = (id) => `(() => { const el = document.getElementById(${JSON.stringify(id)});
  const r = el.getBoundingClientRect(); return !el.hidden && !!r.width && !!r.height; })()`;

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
      await page.waitForTimeout(1200);
      await page.evaluate(async () => {
        listeningSaves = { tracks: [], playlist_tracks: [], folder_tracks: [], playlists: [], folders: [] };
        refresh = () => Promise.resolve();
        await loadLibrarySnapshot();
        document.querySelector('.tab[data-view="library"][data-scope="shared"]').click();
        setLibraryScope("shared");
        renderLibrary();
      });
      await page.waitForTimeout(250);

      // The row: three sections, in the Repository, named for what they hold.
      const row = await page.evaluate(() => {
        const nav = document.getElementById("repotabs");
        const labels = nav ? [...nav.querySelectorAll("button")].map(b => b.textContent.trim()) : [];
        const tabs = [...document.querySelectorAll('.tab[data-view="library"]')].map(t => t.textContent.trim());
        const feed = document.querySelector('.tab[data-view="feed"]').textContent.trim();
        return { labels, tabs, feed, visible: nav ? !nav.hidden : false,
          badge: (document.getElementById("feedcount") || {}).textContent };
      });
      check(`${label}: the Repository offers Browse, Releases, Subscribed`,
        row.labels.join(",") === "Browse,Releases,Subscribed" && row.visible, JSON.stringify(row));
      check(`${label}: new releases count rides on Repo, not Feed`,
        row.feed === "Feed" && row.badge === "(2)", JSON.stringify(row));

      // Browse is what opens: the pool is the default, not a section from last time.
      check(`${label}: Browse is the default section`,
        await page.evaluate(() => activeRepoTab()) === "browse");
      check(`${label}: the browse body shows and the other panes do not`,
        await page.evaluate(shown("repopane-browse")) &&
        !(await page.evaluate(shown("repopane-releases"))) &&
        !(await page.evaluate(shown("repopane-subscribed"))));

      // Releases: the pane swaps, the pool's own controls go with it.
      await page.evaluate(() => { setRepoTab("releases"); return new Promise(r => setTimeout(r, 500)); });
      const releases = await page.evaluate(() => ({
        tab: activeRepoTab(),
        pane: !document.getElementById("repopane-releases").hidden,
        browse: !document.getElementById("repopane-browse").hidden,
        title: document.getElementById("libtitle").textContent,
        count: document.getElementById("libcount").hidden,
        cards: document.querySelectorAll("#feedlist .feedcard").length,
        pressed: document.getElementById("rtab-releases").getAttribute("aria-selected"),
      }));
      check(`${label}: Releases shows the release list, not the pool`,
        releases.pane && !releases.browse && releases.cards > 0, JSON.stringify(releases));
      check(`${label}: the heading says Releases and hides the song count`,
        releases.title === "Releases" && releases.count, JSON.stringify(releases));
      check(`${label}: the chosen section is the one marked selected`,
        releases.pressed === "true", JSON.stringify(releases));

      // Subscribed: the follows, where the subscribe box lives now.
      await page.evaluate(() => { setRepoTab("subscribed"); return new Promise(r => setTimeout(r, 400)); });
      const subs = await page.evaluate(() => ({
        pane: !document.getElementById("repopane-subscribed").hidden,
        releases: !document.getElementById("repopane-releases").hidden,
        rows: document.querySelectorAll("#followlist .followrow").length,
        title: document.getElementById("libtitle").textContent,
      }));
      check(`${label}: Subscribed lists the follows and says so`,
        subs.pane && !subs.releases && subs.rows === 1 && subs.title === "Subscribed",
        JSON.stringify(subs));

      // The personal Library has one list: the section row is not offered there.
      await page.evaluate(() => { setLibraryScope("mine"); });
      await page.waitForTimeout(200);
      const mine = await page.evaluate(() => ({
        row: document.getElementById("repotabs").hidden,
        browse: !document.getElementById("repopane-browse").hidden,
        releases: !document.getElementById("repopane-releases").hidden,
        title: document.getElementById("libtitle").textContent,
      }));
      check(`${label}: the Library hides the section row and shows one list`,
        mine.row && mine.browse && !mine.releases && mine.title === "Library", JSON.stringify(mine));
      await page.evaluate(() => { setLibraryScope("shared"); });
      await page.waitForTimeout(200);
      check(`${label}: going back to the Repository remembers the section`,
        await page.evaluate(() => activeRepoTab()) === "subscribed");

      // A reload lands on the section you left, with its content loaded.
      await page.evaluate(() => setRepoTab("releases"));
      await page.waitForTimeout(300);
      await page.reload({ waitUntil: "load" });
      await page.waitForTimeout(1600);
      const after = await page.evaluate(() => ({
        view: !document.getElementById("view-library").hidden ? "library" : "other",
        tab: activeRepoTab(),
        cards: document.querySelectorAll("#feedlist .feedcard").length,
      }));
      check(`${label}: a reload comes back to Releases with its cards`,
        after.view === "library" && after.tab === "releases" && after.cards > 0, JSON.stringify(after));

      // Feed is Activity only: no tabs, and the release list is not part of it.
      await page.evaluate(() => document.querySelector('.tab[data-view="feed"]').click());
      await page.waitForTimeout(600);
      const feed = await page.evaluate(() => ({
        tabs: document.querySelectorAll("#view-feed .seg button").length,
        feedlist: !!document.querySelector('#view-feed #feedlist'),
        activity: !document.getElementById("feedpane-activity").hidden,
      }));
      check(`${label}: Feed carries no sub-tabs and no release list`,
        feed.tabs === 0 && !feed.feedlist && feed.activity, JSON.stringify(feed));

      // UI-024: the release page walks into the Repo copy and back out.
      await page.evaluate(async () => {
        document.querySelector('.tab[data-view="library"][data-scope="shared"]').click();
        setRepoTab("releases");
        await loadSubscriptions(); await loadFeed();
      });
      await page.waitForTimeout(500);
      const back = await page.evaluate(async () => {
        document.querySelector("#feedlist .feedgname").click();
        await new Promise(r => setTimeout(r, 500));
        const pageEl = document.getElementById("feedartistpage");
        const opened = !pageEl.hidden;
        const inRepo = document.getElementById("faprepo");
        inRepo.click();                       // "In Repo" — the downloaded-artist page
        await new Promise(r => setTimeout(r, 500));
        const walk = { opened, library: !document.getElementById("view-library").hidden,
          section: activeRepoTab(), detail: (document.querySelector("#liblist") || {}).textContent || "" };
        returnToFeedArtist(feedArtistReturn);
        await new Promise(r => setTimeout(r, 500));
        walk.backSection = activeRepoTab();
        walk.backPage = !document.getElementById("feedartistpage").hidden;
        return walk;
      });
      check(`${label}: the release page opens inside Releases`,
        back.opened && back.library && back.section === "releases", JSON.stringify(back));
      check(`${label}: In Repo and Back return to that same release page`,
        back.backSection === "releases" && back.backPage, JSON.stringify(back));

      check(`${label}: no page errors`, errors.length === 0, errors.join(" | "));
      await page.close();
    }
  } finally {
    await browser.close();
    srv.close();
  }
  console.log(failures ? `Repository sections: ${failures} FAILED` : "Repository sections PASS: Browse / Releases / Subscribed, Feed is Activity only");
  process.exit(failures ? 1 : 0);
})();