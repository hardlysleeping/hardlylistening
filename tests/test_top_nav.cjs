// The top row: five centred destinations, More opens a vertical menu below it, the
// secondary pages stay right-aligned on the Library only, and Export asks first.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml" };
const TRACK = { id: 1, title: "Song", artist_name: "A", album: "B", artwork: "", playable: true,
  status: "ok", duration: 120, provider: "apple", playlists: [] };

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/state") return json({ viewer: { is_admin: true, role: "admin", auth_required: false, display_name: "Admin" },
    stats: { done: 0, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null });
  if (p === "/api/library") return json({ tracks: [TRACK], all_tracks: [TRACK], playlists: [], artist_art: {} });
  if (p === "/api/listening/saves") return json({ tracks: [TRACK], playlists: [], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/preferences") return json({ favorites: [], pins: [], album_likes: [] });
  if (p === "/api/listening/export.zip") return res.writeHead(200,
    { "content-type": "application/zip", "content-disposition": 'attachment; filename="library.zip"' }).end("PK");
  if (p.startsWith("/api/")) return json({ ok: true, entries: [], decisions: [], rows: [], all_tracks: [], playlists: [] });
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

let failures = 0;
const check = (n, ok, d) => ok ? console.log("PASS " + n + (d ? " — " + d : ""))
  : (failures++, console.log("FAIL " + n + (d ? " — " + d : "")));

(async () => {
  const srv = server(); await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  try {
    const p = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
    const errors = [];
    p.on("pageerror", e => errors.push(String(e).slice(0, 140)));
    const downloads = [];
    p.on("download", d => downloads.push(d.url()));
    await p.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await p.waitForTimeout(500);

    // ---- geometry: centred group, tighter row ------------------------------
    const geo = await p.evaluate(() => {
      const nav = document.getElementById("appnav").getBoundingClientRect();
      const group = document.querySelector("#appnav .navmain").getBoundingClientRect();
      // More draws `...` and names itself with aria-label; read the name, not the glyph.
      const tabs = [...document.querySelectorAll("#appnav .navmain .tab")]
        .map(b => (b.getAttribute("aria-label") || b.textContent).trim());
      const btn = [...document.querySelectorAll("#appnav .navmain .tab")].find(b => b.id === "btn-more").getBoundingClientRect();
      return { nav: [Math.round(nav.top), Math.round(nav.height)],
        groupCentre: Math.round(group.left + group.width / 2), rowCentre: Math.round(nav.left + nav.width / 2),
        tabs, moreBottom: Math.round(btn.bottom), moreRight: Math.round(btn.right),
        pad: getComputedStyle(document.querySelector("#appnav .navmain .tab")).padding };
    });
    check("the row lists Queue, Feed, Repo, Library, More",
      geo.tabs.join(",") === "Queue,Feed,Repo,Library,More", geo.tabs.join(","));
    check("the group is centred on the row", Math.abs(geo.groupCentre - geo.rowCentre) <= 2,
      `${geo.groupCentre} vs ${geo.rowCentre}`);
    check("the row is tighter than before", geo.nav[1] <= 52, String(geo.nav[1]));
    check("buttons carry less padding", geo.pad === "8px 14px", geo.pad);

    // ---- More menu -------------------------------------------------------
    await p.click("#btn-more");
    await p.waitForTimeout(150);
    const menu = await p.evaluate(() => {
      const panel = document.getElementById("navpanel");
      const r = panel.getBoundingClientRect(), btn = document.getElementById("btn-more").getBoundingClientRect();
      const items = [...panel.querySelectorAll(".tab")].filter(el => el.getBoundingClientRect().height > 0)
        .map(el => ({ label: el.textContent.trim(), r: el.getBoundingClientRect() }));
      return { shown: getComputedStyle(panel).display !== "none",
        below: r.top >= btn.bottom, rightAligned: Math.abs(r.right - btn.right) <= 2,
        vertical: items.every((it, i) => i === 0 || it.r.top >= items[i - 1].r.bottom - 1),
        labels: items.map(i => i.label), height: Math.round(r.height) };
    });
    check("More opens the menu", menu.shown);
    check("the menu hangs below the More button", menu.below, JSON.stringify(menu));
    check("the menu is right-aligned to the button", menu.rightAligned);
    check("the menu is vertical", menu.vertical);
    check("the menu holds the secondary pages",
      menu.labels.join("|") === "Settings|Party|Mixer|Swipe|Export library (.zip)|Send to a DJ app",
      menu.labels.join("|"));
    await p.keyboard.press("Escape");
    await p.evaluate(() => document.querySelector("#appnav").classList.remove("more-open"));

    // ---- one row of five, and the pages only in the panel ------------------
    // The secondary pages used to repeat right-aligned in the row on the Library tab.
    // They do not any more: the panel is the only place they live, which is what the
    // five-destination row above asserts. A page that grew a second cluster would show up
    // here as an element in `#appnav` outside `.navmain` and `#navpanel`.
    await p.evaluate(() => document.querySelector('.tab[data-view="library"][data-scope="mine"]').click());
    await p.waitForTimeout(400);
    const clusters = await p.evaluate(() => [...document.getElementById("appnav").children]
      .map(el => el.id || el.className).filter(name => !["navmain", "navpanel"].includes(name)));
    check("Library: no second cluster in the row", clusters.length === 0, clusters.join(","));

    // ---- export asks before it downloads ---------------------------------
    downloads.length = 0;
    await p.click("#btn-more");
    await p.waitForTimeout(150);
    await p.click("#exportbtn");
    await p.waitForTimeout(500);
    const ask = await p.evaluate(() => ({
      open: document.getElementById("dlg-export-confirm").open,
      text: document.getElementById("exp-confirm-text").textContent,
    }));
    check("Export opens a confirmation", ask.open, JSON.stringify(ask));
    check("nothing downloaded on the first click", downloads.length === 0, downloads.join(","));
    await p.evaluate(() => document.getElementById("dlg-export-confirm").close("cancel"));
    await p.waitForTimeout(300);
    check("cancel downloads nothing", downloads.length === 0, downloads.join(","));
    await p.click("#btn-more");
    await p.waitForTimeout(150);
    await p.click("#exportbtn");
    await p.waitForTimeout(200);
    await p.click("#exp-confirm-go");
    await p.waitForTimeout(700);
    check("confirming starts the ZIP", downloads.length === 1 && /listening\/export\.zip/.test(downloads[0]),
      downloads.join(","));

    // the same guard behind the More menu, off the Library
    await p.evaluate(() => { document.querySelector('.tab[data-view="queue"]').click();
      document.querySelector("#appnav").classList.add("more-open"); });
    await p.waitForTimeout(200);
    downloads.length = 0;
    await p.evaluate(() => document.getElementById("exportbtn").click());
    await p.waitForTimeout(400);
    const viaMenu = await p.evaluate(() => document.getElementById("dlg-export-confirm").open);
    check("the menu's Export asks too", viaMenu && downloads.length === 0, downloads.join(","));
    await p.evaluate(() => document.getElementById("dlg-export-confirm").close("cancel"));
    check("no page errors", errors.length === 0, errors.join(" | "));
    await p.close();

    // ---- phone: five tabs, More opens the sheet --------------------------
    const m = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    await m.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await m.waitForTimeout(400);
    const phone = await m.evaluate(() => {
      const tabs = [...document.querySelectorAll("#appnav > .navmain > .tab")]
        .map(b => (b.getAttribute("aria-label") || b.textContent).trim());
      const bar = document.getElementById("appnav").getBoundingClientRect();
      return { tabs, bottomBar: Math.round(bar.bottom) === window.innerHeight,
        clusters: [...document.getElementById("appnav").children].length };
    });
    check("phone keeps the five destinations", phone.tabs.join(",") === "Queue,Feed,Repo,Library,More", phone.tabs.join(","));
    check("phone: the bar is still the bottom dock", phone.bottomBar);
    check("phone: the nav is the dock and the panel, nothing else", phone.clusters === 2, String(phone.clusters));
    await m.click("#btn-more");
    await m.waitForTimeout(200);
    const sheet = await m.evaluate(() => {
      const panel = document.getElementById("navpanel"), r = panel.getBoundingClientRect();
      return { shown: getComputedStyle(panel).display !== "none",
        aboveBar: r.bottom <= document.getElementById("appnav").getBoundingClientRect().top + 1,
        labels: [...panel.querySelectorAll(".tab")].filter(e => e.getBoundingClientRect().height > 0)
          .map(e => e.textContent.trim()).join("|") };
    });
    check("phone: More opens the sheet above the dock", sheet.shown && sheet.aboveBar, JSON.stringify(sheet));
    check("phone: same pages in the sheet",
      sheet.labels === "Settings|Party|Mixer|Swipe|Export library (.zip)|Send to a DJ app", sheet.labels);
    await m.close();
  } finally { await browser.close(); srv.close(); }
  console.log(failures ? failures + " FAILED" : "top row PASS: centred, More menu, one row, export confirm");
  process.exit(failures ? 1 : 0);
})();