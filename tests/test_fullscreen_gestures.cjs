// Fullscreen playback is opened and closed by gestures on touch, and by controls on a
// desktop. A mouse drag over the bar or the pane used to open/dismiss it, and a dismiss
// timer could close a player that had been reopened while the old pane was still leaving.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };

const ART = "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#2a3550"/></svg>`);
const ROWS = Array.from({ length: 40 }, (_, i) => ({
  id: i + 1, title: `Song ${i + 1}`, artist_name: "Subtronics", album: "Album One",
  album_id: "a1", artwork: ART, duration: 200, release_type: "album", playable: true,
  source_url: `https://music.apple.com/song/${i + 1}`, added_at: "2026-11-01", status: "done",
}));

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/state") return json({
    viewer: { is_admin: true, role: "admin", auth_required: false, display_name: "Admin" },
    stats: { done: 40, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null,
  });
  if (p === "/api/library") return json({ tracks: ROWS, playlists: [], folders: [] });
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

const swipe = async (cdp, from, to, steps = 12) => {
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: from.x, y: from.y }] });
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove", touchPoints: [{ x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t }],
    });
    await new Promise(r => setTimeout(r, 12));
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
};

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail !== undefined ? " — " + detail : "")); }
}

const snapshot = page => page.evaluate(() => {
  const full = document.getElementById("player-full");
  return {
    open: full.classList.contains("on"),
    bodyFull: document.body.classList.contains("fullplayer"),
    bodyTop: document.body.style.top,
    scrollY: Math.round(window.scrollY),
    pull: full.style.getPropertyValue("--pf-pull"),
  };
});

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [label, viewport] of [["desktop", { width: 1280, height: 900 }], ["phone", { width: 390, height: 844 }]]) {
      const ctx = await browser.newContext({ viewport, hasTouch: true });
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", e => errors.push(String(e).slice(0, 160)));
      const cdp = await ctx.newCDPSession(page);
      const tag = `[${label}] `;

      const reset = async (y = 400) => {
        await page.evaluate(scrollToY => {
          if (document.body.classList.contains("fullplayer")) closeFullPlayer();
          document.getElementById("player").style.display = "flex";
          playingId = 1;
          window.scrollTo(0, scrollToY);
        }, y);
        await page.waitForTimeout(280);
      };

      await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
      await page.waitForTimeout(500);
      await page.evaluate(async () => {
        listeningSaves = { tracks: [], playlist_tracks: [], folder_tracks: [], playlists: [], folders: [] };
        refresh = () => Promise.resolve();
        await loadLibrarySnapshot();
        document.querySelector('.tab[data-view="library"][data-scope="shared"]').click();
        setLibraryScope("shared");
        libView = "songs";
        renderLibrary();
      });
      await reset(400);
      const bar = await page.evaluate(() => {
        const r = document.getElementById("plartwrap").getBoundingClientRect();
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
      });
      // A dismiss starts on bare pane, never on the record (which is the scrub handle) or a
      // control. The record is large and turns, so the point is found rather than assumed.
      const barePane = () => page.evaluate(() => {
        for (const y of [120, 160, 90, 200, 700, 740, 60])
          for (const x of [8, 30, innerWidth - 30, innerWidth - 8])
            if ((document.elementFromPoint(x, y) || {}).id === "player-full") return { x, y };
        return null;
      });

      // A finger opens the pane and the page behind keeps its place.
      await swipe(cdp, { x: bar.x, y: bar.y }, { x: bar.x, y: bar.y - 220 });
      await page.waitForTimeout(450);
      let s = await snapshot(page);
      check(tag + "a swipe up opens fullscreen", s.open && s.bodyFull, JSON.stringify(s));
      check(tag + "the page behind keeps its scroll", s.bodyTop === "-400px", s.bodyTop);

      // A full swipe down closes it and puts the page back.
      const down = await barePane();
      await swipe(cdp, down, { x: down.x, y: down.y + Math.round(viewport.height * 0.6) });
      await page.waitForTimeout(500);
      s = await snapshot(page);
      check(tag + "a swipe down closes it", !s.open && !s.bodyFull, JSON.stringify(s));
      check(tag + "closing restores the scroll", s.scrollY === 400 && s.bodyTop === "", JSON.stringify(s));

      // A short drag down dismisses through the pane's own follow-through.
      await reset(400);
      await page.evaluate(() => openFullPlayer());
      await page.waitForTimeout(300);
      const short = await barePane();
      await swipe(cdp, short, { x: short.x, y: short.y + 150 }, 8);
      await page.waitForTimeout(500);
      s = await snapshot(page);
      check(tag + "a short pull still dismisses", !s.open && s.scrollY === 400, JSON.stringify(s));

      // A mouse is not a finger: dragging the bar must not open the pane.
      await reset(400);
      await page.mouse.move(bar.x, bar.y);
      await page.mouse.down();
      await page.mouse.move(bar.x, bar.y - 130, { steps: 10 });
      await page.mouse.up();
      await page.waitForTimeout(400);
      s = await snapshot(page);
      check(tag + "a mouse drag on the bar does nothing", !s.open && s.scrollY === 400, JSON.stringify(s));

      // Nor does dragging inside the open pane dismiss it.
      await page.evaluate(() => openFullPlayer());
      await page.waitForTimeout(300);
      const drag = await barePane();
      await page.mouse.move(drag.x, drag.y);
      await page.mouse.down();
      await page.mouse.move(drag.x, drag.y + 320, { steps: 12 });
      await page.mouse.up();
      await page.waitForTimeout(500);
      s = await snapshot(page);
      check(tag + "a mouse drag in the pane leaves it open", s.open && !s.pull, JSON.stringify(s));

      // Its controls still take a mouse click, and closing restores the page.
      await page.mouse.move(viewport.width / 2, Math.round(viewport.height * 0.3));
      await page.mouse.down();
      await page.mouse.up();
      await page.waitForTimeout(120);
      await page.click("#pfclose");
      await page.waitForTimeout(450);
      s = await snapshot(page);
      check(tag + "the close control closes it", !s.open && s.scrollY === 400 && s.bodyTop === "", JSON.stringify(s));

      // Opening twice must not re-save the scroll as 0, which stranded the page at the top.
      await reset(1200);
      await page.evaluate(() => { openFullPlayer(); openFullPlayer(); });
      await page.waitForTimeout(300);
      s = await snapshot(page);
      check(tag + "a second open keeps the saved scroll", s.bodyTop === "-1200px", s.bodyTop);
      await page.evaluate(() => closeFullPlayer());
      await page.waitForTimeout(300);
      s = await snapshot(page);
      check(tag + "and closing still lands there", s.scrollY === 1200, String(s.scrollY));

      // Reopening inside the old pane's exit must survive the pending dismiss.
      await reset(400);
      await swipe(cdp, { x: bar.x, y: bar.y }, { x: bar.x, y: bar.y - 220 });
      await page.waitForTimeout(400);
      const again = await barePane();
      await swipe(cdp, again, { x: again.x, y: again.y + Math.round(viewport.height * 0.6) });
      await page.waitForTimeout(70);
      await page.evaluate(() => openFullPlayer());
      await page.waitForTimeout(650);
      s = await snapshot(page);
      check(tag + "reopening during the exit stays open", s.open && s.bodyTop === "-400px", JSON.stringify(s));

      check(tag + "no page errors", errors.length === 0, errors.join(" / "));
      await ctx.close();
    }
  } finally {
    await browser.close();
    srv.close();
  }
  if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
  console.log("\nFullscreen gestures PASS: touch opens and closes, mouse does neither, scroll survives");
})();