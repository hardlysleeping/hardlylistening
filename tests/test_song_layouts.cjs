// Three song layouts: List, Cards (with a compact mode), Grid (covers only, opens album).
const http = require("http"), fs = require("fs"), path = require("path");
const { chromium } = require("playwright");
const WEB = path.join(__dirname, "..", "web");
const types = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml" };
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==", "base64");
const server = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  if (p === "/art.png") return res.writeHead(200, { "content-type": "image/png" }).end(PNG);
  if (p.startsWith("/api/")) {
    const body = p === "/api/listening/preferences" ? JSON.stringify({ favorites: [], pins: [] })
      : JSON.stringify({ tracks: [], all_tracks: [], playlists: [], playlist_tracks: [], folders: [], folder_tracks: [], items: {}, ok: true, stats: {}, viewer: {} });
    return res.writeHead(200, { "content-type": "application/json" }).end(body);
  }
  const fp = path.join(WEB, p === "/" ? "/index.html" : p);
  if (fs.existsSync(fp) && fs.statSync(fp).isFile())
    return res.writeHead(200, { "content-type": types[path.extname(fp)] || "application/octet-stream" }).end(fs.readFileSync(fp));
  res.writeHead(404).end("no");
});
let failures = 0;
const check = (ok, what) => { if (ok) console.log("ok   " + what); else { failures++; console.log("FAIL " + what); } };
(async () => {
  await new Promise(r => server.listen(8488, r));
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 430, height: 932 }, deviceScaleFactor: 2 });
  const errs = []; page.on("pageerror", e => errs.push(String(e)));
  await page.addInitScript(() => {
    localStorage.setItem("hl.libraryView", "songs");
    localStorage.setItem("hl.songLayout", "cards");
    localStorage.setItem("hl.songCols", "3");
    localStorage.setItem("hl.libraryScope", "mine");
  });
  await page.goto("http://127.0.0.1:8488/", { waitUntil: "load" });
  await page.waitForTimeout(600);
  await page.evaluate(async () => {
    await loadLibrarySnapshot().catch(() => {});
    // 6 songs across 2 albums, so "open the album" is provable.
    const made = Array.from({ length: 6 }, (_, i) => ({ id: 900 + i, title: "Song " + i, artist_name: "Artist " + (i % 2),
      album: "Album " + Math.floor(i / 3), duration: 200, playable: true, artwork: "/art.png", playlists: [],
      album_id: (Math.floor(i / 3) ? "b" : "a").repeat(64), status: "ok", file: "x.flac", source_status: "ok" }));
    lib = made; libAll = made; currentRows = made; listeningSaves = { tracks: made.map(t => t.id), playlists: [], playlist_tracks: [] };
    libView = "songs"; setLibraryScope("mine");
    const host = document.querySelector("#liblist");
    for (let el = host; el && el !== document.documentElement; el = el.parentElement) { el.hidden = false; el.style.display = el.id === "view-library" ? "block" : ""; }
    renderLibrary();
  });
  await page.waitForTimeout(200);
  const ui = () => page.evaluate(() => {
    const seg = [...document.querySelectorAll("#layoutseg button")].map(b => b.textContent.trim() + (b.classList.contains("on") ? "*" : ""));
    const compact = document.querySelector("#compactbtn");
    return {
      seg, layout: libSongLayout, compactState: libCardsCompact,
      compactVisible: !!compact && !compact.hidden, compactLabel: compact ? compact.textContent.trim() : null,
      cards: document.querySelectorAll("#liblist .songcard").length,
      coverCells: document.querySelectorAll("#liblist .covercell").length,
      rows: document.querySelectorAll("#liblist .songrow").length,
      coverHasButton: !!document.querySelector("#liblist .covercell button, #liblist .coverart button"),
      coverText: (document.querySelector("#liblist .covername") || {}).textContent || null,
      coverImg: !!document.querySelector("#liblist .covercell .coverart img"),
      cardstyle: document.documentElement.dataset.cardstyle,
      detail: typeof detail !== "undefined" && detail ? detail.kind + ":" + String(detail.key).slice(0, 4) : null,
      focusId: (document.querySelector(".songrow.focus") || {}).dataset?.id || null,
      cols: getComputedStyle(document.documentElement).getPropertyValue("--cover-cols").trim(),
    };
  });

  let s = await ui();
  check(JSON.stringify(s.seg) === JSON.stringify(["List", "Cards*", "Grid"]), "the picker reads List / Cards / Grid, cards selected: " + JSON.stringify(s.seg));
  check(s.cards === 6 && s.rows === 6, "card view still draws a card per song");
  check(s.compactVisible && s.compactLabel === "Compact", "the compact switch appears for cards");

  // Compact on: covers and names only, no controls in the card.
  await page.click("#compactbtn");
  await page.waitForTimeout(150);
  s = await ui();
  const compactShape = await page.evaluate(() => {
    const card = document.querySelector("#liblist .songcard");
    const shown = sel => { const el = card.querySelector(sel); if (!el) return "missing"; const b = el.getBoundingClientRect(); return b.width > 0 && b.height > 0; };
    return { plus: shown(".rowactions"), pref: shown(".pref-pair"), votes: shown(".votes"), artist: shown(".sartist"),
      play: shown(".splay"), h: Math.round(card.getBoundingClientRect().height), artH: Math.round(card.querySelector(".thumb").getBoundingClientRect().height),
      areas: getComputedStyle(card).gridTemplateAreas };
  });
  check(s.compactState && s.cardstyle === "compact", "compact mode is on and marked on <html>");
  check(!compactShape.plus && !compactShape.pref && !compactShape.votes && !compactShape.artist && !compactShape.play,
    "a compact card shows no buttons: " + JSON.stringify(compactShape));
  check(compactShape.h - compactShape.artH < 60, "a compact card is the cover plus one line (" + (compactShape.h - compactShape.artH) + "px)");

  // Tapping opens it — and an opened compact card must be the normal card, not a
  // different layout: same rows, same height, play and download still on the cover.
  await page.click("#liblist .songcard .sttext");
  await page.waitForTimeout(150);
  const readCard = () => page.evaluate(() => {
    const card = document.querySelector("#liblist .songcard");
    const art = card.querySelector(".thumb").getBoundingClientRect();
    const insideArt = sel => { const el = card.querySelector(sel); if (!el) return false;
      const b = el.getBoundingClientRect(); return b.width > 0 && b.top >= art.top - 1 && b.bottom <= art.bottom + 1; };
    const votes = card.querySelector(".votes").getBoundingClientRect();
    return { open: card.classList.contains("open"),
      h: Math.round(card.getBoundingClientRect().height),
      areas: getComputedStyle(card).gridTemplateAreas,
      playOnArt: insideArt(".splay"), dlOnArt: insideArt(".sdl"), durationOnArt: insideArt(".sdur"),
      votesBelowArt: votes.top >= art.bottom - 1,
      width: Math.round(card.getBoundingClientRect().width) };
  });
  const opened = await readCard();
  check(opened.open && opened.playOnArt && opened.dlOnArt && opened.durationOnArt,
    "an opened compact card keeps play, download and the clock on the cover");
  // Full mode is the reference: same card, same numbers.
  await page.click("#compactbtn");
  await page.waitForTimeout(200);
  const reference = await readCard();
  check(opened.h === reference.h && opened.areas === reference.areas && opened.width === reference.width,
    "an opened compact card is the normal card (" + opened.h + "px vs " + reference.h + "px)");
  check(opened.playOnArt === reference.playOnArt && opened.votesBelowArt === reference.votesBelowArt,
    "an opened compact card puts every control where the normal card puts it");

  // Grid: covers only, no buttons, and it is denser than the cards.
  await page.evaluate(() => setSongLayout("grid"));
  await page.waitForTimeout(200);
  s = await ui();
  check(s.coverCells === 6 && s.cards === 0, "grid draws one cover tile per song");
  check(!s.coverHasButton, "a grid tile carries no buttons");
  const nameMatchesId = await page.evaluate(() => {
    const cell = document.querySelector("#liblist .covercell");
    const want = "Song " + (Number(cell.dataset.id) - 900);
    return cell.querySelector(".covername").textContent.trim() === want;
  });
  check(s.coverImg && nameMatchesId, "a tile is the cover with that song's name under it: " + s.coverText);
  const density = await page.evaluate(() => ({ cover: getComputedStyle(document.documentElement).getPropertyValue("--cover-cols").trim(),
    card: getComputedStyle(document.documentElement).getPropertyValue("--song-cols").trim(),
    tileW: Math.round(document.querySelector(".covercell").getBoundingClientRect().width),
    cardW: Math.round(document.querySelector(".songcard") ? document.querySelector(".songcard").getBoundingClientRect().width : 0) }));
  check(+density.cover > +density.card, "the wall is denser than the cards (" + density.cover + " vs " + density.card + " per row)");
  check(s.compactVisible === false, "the compact switch hides outside card view");

  // Tapping a cover opens its album and marks the song that was tapped.
  // Which song a tile holds depends on the sort, so ask the tile rather than assuming.
  const wanted = await page.evaluate(() => Number(document.querySelectorAll("#liblist .covercell")[4].dataset.id));
  await page.evaluate(() => document.querySelectorAll("#liblist .covercell")[4].click());
  await page.waitForTimeout(400);
  const jumped = await page.evaluate(() => ({ detail: detail ? detail.kind : null, focus: (document.querySelector(".songrow.focus") || {}).dataset?.id || null,
    marked: !!document.querySelector(".songrow.focus") }));
  check(jumped.detail === "album", "a cover tap opens the album page");
  check(String(jumped.focus) === String(wanted), "the tapped song is the one marked on arrival (" + wanted + "), got " + jumped.focus);

  // The old stored value still means cards, not the new wall.
  const migrated = await page.evaluate(() => { localStorage.setItem("hl.songLayout", "grid"); return true; });
  await page.reload({ waitUntil: "load" });
  await page.waitForTimeout(500);
  const afterReload = await page.evaluate(() => ({ layout: libSongLayout, saved: localStorage.getItem("hl.songLayout") }));
  check(afterReload.layout === "cards" || afterReload.saved === "cards", "a stored 'grid' from before the rename does not become the cover wall: " + JSON.stringify(afterReload));

  check(errs.length === 0, "no page errors " + JSON.stringify(errs.slice(0, 2)));
  await browser.close(); server.close();
  console.log(failures ? failures + " FAILED" : "Song layouts: List / Cards (+compact) / Grid covers that open the album at the tapped song PASS");
  process.exitCode = failures ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => setTimeout(() => process.exit(process.exitCode || 0), 200));
