// The library save toggle: an album is saved when any of its songs is, so one click on the
// minus removes what is there instead of topping up the missing songs. A removal stays on
// the page — marked, dimmed, with a + — until you leave the view, and a tab, view or scope
// change is what makes a removed item actually gone.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };

const track = (id, albumId, album, title) => ({
  id, title, artist_name: "Fixture", album, album_id: albumId, artwork: "",
  playable: true, duration: 180, provider: "soundcloud", source_status: "ok",
  playlists: [], track_number: id, added: id,
});
const ALL = [track(1, "a1", "Alpha", "One"), track(2, "a1", "Alpha", "Two"),
  track(3, "a1", "Alpha", "Three"), track(4, "b1", "Beta", "Four"),
  track(5, "b1", "Beta", "Five")];
// Track 1 is already gone from Alpha: the album still reads as saved because 2 and 3 are.
const SAVED = new Set([2, 3, 4, 5]);
const posts = [];

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = body => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(body));
  if (p === "/api/listening/saves" && req.method === "POST") {
    let raw = "";
    req.on("data", c => raw += c);
    req.on("end", () => {
      const body = JSON.parse(raw || "{}");
      posts.push(body);
      if (body.saved) SAVED.add(Number(body.id)); else SAVED.delete(Number(body.id));
      json({ ok: true });
    });
    return;
  }
  if (p === "/api/listening/saves") return json({ tracks: [...SAVED], playlists: [], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/preferences") return json({ favorites: [], pins: [], album_likes: [] });
  if (p === "/api/library") return json({ tracks: ALL, all_tracks: ALL, playlists: [], artist_art: {} });
  if (p === "/api/state") return json({ viewer: { is_admin: false, role: "member", auth_required: false, display_name: "Member" },
    stats: { done: 0, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null });
  if (p === "/api/feed") return json({ items: [] });
  if (p === "/api/subscriptions") return json({ subscriptions: [] });
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
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on("pageerror", e => errors.push(String(e).slice(0, 200)));
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.waitForTimeout(600);
    await page.click('.tab[data-view="library"][data-scope="mine"]');
    await page.waitForTimeout(700);
    await page.evaluate(() => {
      detail = null; libView = "albums"; libSongLayout = "cards";
      document.querySelectorAll("#libviews button").forEach(b => b.classList.toggle("on", b.dataset.view === "albums"));
      renderLibrary();
    });
    await page.waitForTimeout(400);

    const albumState = key => page.evaluate(key => {
      const card = document.querySelector(`.acard[data-album="${key}"]`);
      if (!card) return null;
      const btn = card.querySelector(".lsave");
      const svg = btn.innerHTML;
      return {
        removed: card.classList.contains("removed"),
        glyph: svg.includes("m5 13") ? "check" : svg.includes("M12 5v14") ? "plus" : "minus",
        label: btn.getAttribute("aria-label"),
        chip: (card.querySelector(".removed-chip") || {}).textContent || "",
        text: (card.querySelector(".salbum") || {}).textContent || "",
      };
    }, key);
    const toast = () => page.evaluate(() => (document.querySelector("#toast") || {}).textContent || "");
    const rowState = id => page.evaluate(id => {
      const row = document.querySelector(`.songrow[data-id="${id}"]`);
      if (!row) return null;
      const btn = row.querySelector(".lsave");
      const svg = btn.innerHTML;
      return { removed: row.classList.contains("removed"),
        glyph: svg.includes("m5 13") ? "check" : svg.includes("M12 5v14") ? "plus" : "minus",
        badge: (row.querySelector(".removed-badge") || {}).textContent || "" };
    }, id);

    check("the album with a missing song still reads as saved",
      await albumState("a1"), { removed: false, glyph: "minus", label: "Remove album from library", chip: "", text: "2 songs" });

    // One click removes: no arm step, and it must not add the missing song back.
    posts.length = 0;
    await page.click('.acard[data-album="a1"] .lsave');
    await page.waitForTimeout(500);
    check("minus removes the saved songs in one click",
      posts.map(p => `${p.id}:${p.saved}`).sort(), ["2:false", "3:false"]);
    check("nothing is added back", posts.some(p => p.saved), false);
    check("the album stays on the page, marked removed",
      await albumState("a1"), { removed: true, glyph: "plus", label: "Add album back to library", chip: "Removed", text: "2 songs" });
    check("the toast says it was removed, not added", /album removed from your library/i.test(await toast()), true);
    check("the library no longer holds those songs", [...SAVED].sort(), [4, 5]);

    // The + puts it back, and the add is confirmed with a check for a beat.
    posts.length = 0;
    await page.click('.acard[data-album="a1"] .lsave');
    await page.waitForTimeout(400);
    check("plus adds the same songs back", posts.map(p => `${p.id}:${p.saved}`).sort(), ["2:true", "3:true"]);
    check("the add flashes a check", (await albumState("a1")).glyph, "check");
    await page.waitForTimeout(900);
    check("the check settles on the minus", (await albumState("a1")).glyph, "minus");
    check("the card is normal again", (await albumState("a1")).removed, false);

    // Removing again, then leaving the view, is what makes it disappear.
    await page.click('.acard[data-album="a1"] .lsave');
    await page.waitForTimeout(500);
    check("removed once more", (await albumState("a1")).removed, true);
    await page.click('#libviews button[data-view="songs"]');
    await page.waitForTimeout(400);
    await page.click('#libviews button[data-view="albums"]');
    await page.waitForTimeout(400);
    check("leaving the view drops the removed album", await albumState("a1"), null);
    check("the album that is still saved stays", (await albumState("b1")).removed, false);

    // A single song: the row stays put, dimmed, and the same button takes it back.
    await page.click('#libviews button[data-view="songs"]');
    await page.waitForTimeout(400);
    check("the saved song row is there", (await rowState(4)).glyph, "check");
    await page.click('.songrow[data-id="4"] .lsave');
    await page.waitForTimeout(250);
    check("the song control still arms first", (await rowState(4)).glyph, "minus");
    await page.click('.songrow[data-id="4"] .lsave');
    await page.waitForTimeout(500);
    check("the removed song row stays, marked", await rowState(4), { removed: true, glyph: "plus", badge: "Removed" });
    check("its toast says removed", /removed from your library/i.test(await toast()), true);
    await page.click('.songrow[data-id="4"] .lsave');
    await page.waitForTimeout(500);
    check("the row loses the removed mark when put back", await rowState(4), { removed: false, glyph: "check", badge: "" });

    // A tab change is the other way out of the undo window.
    await page.click('.songrow[data-id="4"] .lsave');
    await page.waitForTimeout(250);
    await page.click('.songrow[data-id="4"] .lsave');
    await page.waitForTimeout(500);
    check("removed before the tab change", (await rowState(4)).removed, true);
    await page.click('.tab[data-view="queue"]');
    await page.waitForTimeout(300);
    await page.click('.tab[data-view="library"][data-scope="mine"]');
    await page.waitForTimeout(700);
    await page.evaluate(() => { libView = "songs"; renderLibrary(); });
    await page.waitForTimeout(400);
    check("changing tabs drops the removed song", await rowState(4), null);
    check("the album left behind is gone too", await albumState("a1"), null);
    check("no page errors", errors, []);
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("library toggle ok");
})();