// Queue rows: no × in the row; a left swipe reveals Remove.
//
// The × sat one thumb-width from the row's own tap target, so clearing a song by accident was
// easier than playing it. The row now slides left to reveal the same action, and the ⋯ menu
// keeps it for anyone without a pointer. A tap on an open row closes it instead of playing.
const http = require("http"), fs = require("fs"), path = require("path");
const { chromium } = require("./_pw.cjs");
const WEB = path.join(__dirname, "..", "web");
const types = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml" };

const server = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
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
const check = (ok, what, detail) => {
  if (ok) console.log("PASS " + what);
  else { failures++; console.log("FAIL " + what + (detail ? " — " + detail : "")); }
};

(async () => {
  await new Promise(r => server.listen(8487, r));
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [w, h, label] of [[1280, 900, "desktop"], [390, 844, "phone"]]) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      const errs = [];
      page.on("pageerror", e => errs.push(String(e)));
      await page.goto("http://127.0.0.1:8487/", { waitUntil: "load" });
      await page.waitForTimeout(500);

      const seed = await page.evaluate(() => {
        if (typeof togglePlaybackPanel === "function") togglePlaybackPanel(true, true);
        else document.querySelector("#playback-panel").classList.add("open");
        document.body.classList.add("hasplayer");
        const rows = Array.from({ length: 4 }, (_, i) => ({ id: 900 + i, title: "Queued " + i,
          artist_name: "Artist", album: "Album", duration: 180, playable: true, artwork: "" }));
        lib = rows; libAll = rows; libQueue = rows.slice(); libIndex = 0;
        window.__played = []; window.__moved = [];
        window.playTrackNow = id => window.__played.push(id);
        window.queueMove = (id, action) => window.__moved.push([id, action]);
        paintPlaybackQueue();
        const row = document.querySelector("#playback-items .pq-row");
        const remove = row.querySelector(".pq-remove");
        return {
          xRows: document.querySelectorAll(".pq-x").length,
          hasBody: !!row.querySelector(".pq-body"),
          hasRemove: !!remove,
          removeText: remove ? remove.textContent.trim() : "",
          removeTab: remove ? remove.getAttribute("tabindex") : null,
          rowId: row.dataset.id,
        };
      });

      check(seed.xRows === 0, `${label}: no × button in a queue row`, String(seed.xRows));
      check(seed.hasBody, `${label}: the row content sits in one sliding panel`);
      check(seed.hasRemove && seed.removeText === "Remove", `${label}: a Remove panel is behind the row`, seed.removeText);
      check(seed.removeTab === "-1", `${label}: the hidden panel is not in the tab order`, String(seed.removeTab));

      const geom = () => page.evaluate(() => {
        const row = document.querySelector("#playback-items .pq-row");
        const body = row.querySelector(".pq-body");
        const remove = row.querySelector(".pq-remove");
        const r = row.getBoundingClientRect(), b = body.getBoundingClientRect(), x = remove.getBoundingClientRect();
        return { open: row.classList.contains("pq-swiped"),
          row: [Math.round(r.left), Math.round(r.right)],
          body: [Math.round(b.left), Math.round(b.right)],
          remove: [Math.round(x.left), Math.round(x.right)],
          hit: (() => { const el = document.elementFromPoint(r.right - 20, r.top + r.height / 2);
            return el ? (el.closest(".pq-remove") ? "remove" : el.className) : "none"; })() };
      });

      const box = await page.evaluate(() => {
        const r = document.querySelector("#playback-items .pq-row .pq-name").getBoundingClientRect();
        return { x: r.left + Math.min(60, r.width / 2), y: r.top + r.height / 2 };
      });

      let g = await geom();
      check(!g.open, `${label}: a row starts closed`);
      check(g.remove[0] >= g.row[1] - 1, `${label}: the Remove panel starts off the row's right edge`,
        JSON.stringify(g.remove) + " vs " + JSON.stringify(g.row));

      // A swipe past halfway opens it.
      await page.mouse.move(box.x, box.y);
      await page.mouse.down();
      for (const dx of [10, 30, 60, 80]) { await page.mouse.move(box.x - dx, box.y, { steps: 2 }); }
      await page.mouse.up();
      await page.waitForTimeout(260);
      g = await geom();
      check(g.open, `${label}: a left swipe opens the row`);
      check(Math.abs(g.remove[1] - g.row[1]) <= 1, `${label}: the panel sits flush with the row's right edge`,
        JSON.stringify(g.remove) + " vs " + JSON.stringify(g.row));
      check(g.hit === "remove", `${label}: the revealed panel is what the pointer meets`, String(g.hit));

      // A tap on the row closes it and does not play the song.
      await page.mouse.click(box.x - 100, box.y);
      await page.waitForTimeout(260);
      const after = await page.evaluate(() => ({ open: !!document.querySelector("#playback-items .pq-row.pq-swiped"),
        played: window.__played }));
      check(!after.open, `${label}: a tap on the open row slides it back`);
      check(after.played.length === 0, `${label}: and does not play the song underneath`, JSON.stringify(after.played));

      // A short swipe springs back.
      await page.mouse.move(box.x, box.y);
      await page.mouse.down();
      await page.mouse.move(box.x - 30, box.y, { steps: 3 });
      await page.mouse.up();
      await page.waitForTimeout(240);
      check(!(await geom()).open, `${label}: a short swipe springs back`);

      // A vertical drag is a scroll, not a swipe.
      await page.mouse.move(box.x, box.y);
      await page.mouse.down();
      await page.mouse.move(box.x - 6, box.y + 60, { steps: 4 });
      await page.mouse.up();
      await page.waitForTimeout(240);
      check(!(await geom()).open, `${label}: a vertical drag leaves the row closed`);

      // Remove is the action, and it is the row's own id.
      await page.mouse.move(box.x, box.y);
      await page.mouse.down();
      await page.mouse.move(box.x - 90, box.y, { steps: 4 });
      await page.mouse.up();
      await page.waitForTimeout(240);
      const panel = await page.evaluate(() => {
        const row = document.querySelector("#playback-items .pq-row.pq-swiped");
        const r = row.querySelector(".pq-remove").getBoundingClientRect();
        return { id: Number(row.dataset.id), x: r.left + r.width / 2, y: r.top + r.height / 2 };
      });
      await page.mouse.click(panel.x, panel.y);
      await page.waitForTimeout(200);
      const removed = await page.evaluate(() => ({ moved: window.__moved, played: window.__played }));
      check(JSON.stringify(removed.moved) === `[[${panel.id},"remove"]]`, `${label}: Remove sends the row's id to the queue`,
        JSON.stringify(removed.moved));
      check(removed.played.length === 0, `${label}: Remove never plays the song`);

      check(errs.length === 0, `${label}: no page errors`, errs.join(" | "));
      await page.close();
    }
  } finally { await browser.close(); server.close(); }
  if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
  console.log("\nall queue-swipe assertions passed");
})().catch(e => { console.error("FAIL " + e.message); process.exitCode = 1; });