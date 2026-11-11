// The fullscreen queue button is a toggle. Open must look open after the pointer leaves it,
// and shut must look shut even while the pointer still rests on the button — hover is a
// hint, not a state.
const fs = require("fs"), path = require("path"), http = require("http"), assert = require("assert");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };
const STUB = { ok: true, songs: [], queue: [], tracks: [], playlists: [], folders: [], favorites: [], library: [] };
const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  if (p.startsWith("/api/")) return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(STUB));
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
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.waitForTimeout(400);
    await page.evaluate(() => {
      const tracks = [];
      for (let i = 1; i <= 3; i++) tracks.push({ id: i, title: "Track " + i, artist_name: "A", album: "A",
        artwork: "", playable: true, duration: 180, provider: "soundcloud", source_status: "ok", playlists: [] });
      libAll = tracks; libQueue = tracks.slice(); playingId = 1;
      document.getElementById("player").classList.add("on");
      openFullPlayer();
    });
    await page.waitForTimeout(500);

    const look = () => page.evaluate(() => {
      const btn = document.getElementById("pfqueue");
      const c = getComputedStyle(btn);
      const full = document.getElementById("player-full");
      const panel = document.getElementById("playback-panel");
      const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
      const probe = document.createElement("span");
      probe.style.color = accent; document.body.append(probe);
      const accentRGB = getComputedStyle(probe).color;
      probe.remove();
      return { aria: btn.getAttribute("aria-expanded"), color: c.color, accent: accentRGB,
        ring: c.boxShadow, queueOpen: full.classList.contains("queue-open"),
        panelOpen: panel.classList.contains("open"),
        panelHome: panel.parentElement.id || panel.parentElement.className,
        inView: btn.getBoundingClientRect().top >= 0 && btn.getBoundingClientRect().bottom <= innerHeight };
    });

    // Closed, pointer parked well away from the button.
    await page.mouse.move(40, 40);
    await page.waitForTimeout(120);
    let s = await look();
    check("starts shut", [s.aria, s.queueOpen, s.panelOpen], ["false", false, false]);
    check("shut: glyph is not the accent", s.color === s.accent, false);
    check("shut: no ring", s.ring, "none");
    const shut = s.color;

    // Hover alone must not impersonate the open state.
    await page.hover("#pfqueue");
    await page.waitForTimeout(120);
    s = await look();
    check("hover: still reads as shut", [s.aria, s.color === s.accent, s.ring], ["false", false, "none"]);

    // Open.
    await page.click("#pfqueue");
    await page.waitForTimeout(500);
    await page.mouse.move(40, 40);
    await page.waitForTimeout(150);
    s = await look();
    const open = s.color;
    check("open: aria and layout", [s.aria, s.queueOpen, s.panelOpen], ["true", true, true]);
    check("open: glyph takes the accent", open === s.accent, true);
    check("open: ring marks the state", /inset/.test(s.ring), true);
    check("open: state survives the pointer leaving", open !== shut, true);
    check("open: the button is still reachable", s.inView, true);

    // Close with the pointer left resting on the button — the reported case.
    await page.click("#pfqueue");
    await page.waitForTimeout(600);
    s = await look();
    check("shut again: aria and layout", [s.aria, s.queueOpen, s.panelOpen], ["false", false, false]);
    check("shut again: panel is back home", s.panelHome, "fullplayer");
    check("shut again: pointer on the button does not look open", [s.color === s.accent, s.ring], [false, "none"]);
    check("shut again: the two states are distinguishable", s.color !== open, true);
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("fullscreen queue toggle ok");
})();