// Queue drawer: the row of buttons under the list is gone, clear is a broom beside ×.
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
const check = (ok, what) => { if (ok) console.log("ok   " + what); else { failures++; console.log("FAIL " + what); } };
(async () => {
  await new Promise(r => server.listen(8486, r));
  const browser = await chromium.launch();
  for (const [w, h, label] of [[1280, 900, "desktop"], [390, 844, "phone"]]) {
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
    const errs = []; page.on("pageerror", e => errs.push(String(e)));
    await page.goto("http://127.0.0.1:8486/", { waitUntil: "load" });
    await page.waitForTimeout(600);
    console.log("--", label, "loaded");
    await page.evaluate(() => { document.querySelector("#playback-panel").classList.add("open"); document.body.classList.add("hasplayer"); });
    await page.waitForTimeout(200);
    const geo = await page.evaluate(() => {
      const panel = document.querySelector("#playback-panel");
      const rows = Array.from({ length: 4 }, (_, i) => ({ id: 900 + i, title: "Queued " + i, artist_name: "A", album: "Al", duration: 180, playable: true, artwork: "", playlists: [], album_id: "a".repeat(64), status: "ok", file: "x.flac" }));
      lib = rows; libAll = rows; libQueue = rows.slice(); libIndex = 0;
      paintPlaybackQueue();
      const b = s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      const text = panel.innerText;
      const clear = b("#pqclear"), close = b(".pq-close"), details = b("#playback-details");
      return {
        text, clear, close,
        clearLeftOfClose: !!clear && !!close && clear.x + clear.w <= close.x + 2,
        sameRow: !!clear && !!close && Math.abs((clear.y + clear.h / 2) - (close.y + close.h / 2)) < 4,
        clearInsideHeader: (() => { const s = document.querySelector("#playback-panel summary").getBoundingClientRect(); const r = document.querySelector("#pqclear").getBoundingClientRect();
          return r.top >= s.top - 1 && r.bottom <= s.bottom + 1; })(),
        noPlaylistEditorInPanel: !panel.querySelector('[onclick*="openPlaylistEditor"]'),
        noToolbar: !panel.querySelector(".pq-toolbar"),
        hasSvg: !!document.querySelector("#pqclear svg"),
        disabledWithQueue: document.querySelector("#pqclear").disabled,
        iters: document.querySelectorAll("#playback-items .pq-row").length,
        panelBottom: Math.round(panel.getBoundingClientRect().bottom),
        statusBottom: Math.round(document.querySelector("#playback-status").getBoundingClientRect().bottom),
        lastRowBottom: Math.round(document.querySelector("#playback-items").lastElementChild.getBoundingClientRect().bottom),
      };
    });
    check(geo.noPlaylistEditorInPanel, label + ": the playlist editor is not in the queue drawer");
    check(geo.noToolbar, label + ": the button row under the list is gone");
    check(geo.hasSvg, label + ": clear renders an icon, not a label");
    check(!/Clear queue/.test(geo.text), label + ": no visible Clear queue label");
    check(geo.clearLeftOfClose, label + ": the broom sits to the left of the close ×");
    check(geo.sameRow, label + ": broom and × share the header row");
    check(geo.clearInsideHeader, label + ": the broom lives in the drawer header");
    check(geo.disabledWithQueue === false, label + ": the broom is enabled while songs are queued");
    // Nothing under the list: the drawer ends at the last row + status line.
    const slack = geo.panelBottom - geo.statusBottom;
    check(slack >= 0 && slack < 24, label + ": the drawer ends at its last line of text, " + slack + "px of slack");

    // Clearing works from the icon, then the broom greys out.
    await page.evaluate(() => { libQueue = libQueue.slice(); paintPlaybackQueue(); });
    // Clearing is destructive, so the broom asks first: the dialog has to be accepted.
    await page.click("#pqclear", { timeout: 4000 });
    await page.waitForTimeout(80);
    check(await page.evaluate(() => document.getElementById("queue-confirm").open),
      label + ": the broom asks before it clears");
    await page.click("#queue-confirm button[value=\"go\"]");
    await page.waitForTimeout(120);
    const after = await page.evaluate(() => ({ len: libQueue.length, disabled: document.querySelector("#pqclear").disabled,
      status: document.querySelector("#playback-status").textContent }));
    check(after.len === 0, label + ": the broom clears the queue");
    check(after.disabled, label + ": an empty queue disables it");
    check(/cleared/i.test(after.status), label + ": the drawer says the queue was cleared");
    check(errs.length === 0, label + ": no page errors " + JSON.stringify(errs.slice(0, 2)));
    const rect = await page.evaluate(() => { const r = document.querySelector("#playback-panel").getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: Math.min(r.height + 10, 420) }; });
    if (rect.width > 0 && rect.height > 0) await page.screenshot({ path: `/tmp/hl-queue-${label}.png`, clip: rect });
    await page.close();
  }
  await browser.close(); server.close();
  console.log(failures ? failures + " FAILED" : "Queue drawer: clear is a broom beside ×, no button row, header only PASS");
  process.exitCode = failures ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; });
