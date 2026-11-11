// Desktop volume: the bar and the fullscreen view each carry a fader, both drive the one
// audio element, and neither collides with the clocks, the scrub or the transport.
// Phone widths keep the hardware buttons: no fader there.
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
    const start = () => page.evaluate(() => {
      const t = { id: 42, title: "Track", artist_name: "Artist", album: "Album", artwork: "", provider: "soundcloud" };
      libAll = [t]; libQueue = []; playingId = 42;
      document.getElementById("player").classList.add("on");
      document.getElementById("playback-panel").classList.remove("open");
    });
    const state = () => page.evaluate(() => ({
      vol: +playerAudio.volume.toFixed(2), muted: playerAudio.muted,
      pl: +document.getElementById("plvol").value, pf: +document.getElementById("pfvol").value,
      plFill: document.getElementById("plvol").style.getPropertyValue("--vol"),
      pfFill: document.getElementById("pfvol").style.getPropertyValue("--vol"),
      stored: localStorage.getItem("hl.volume"),
    }));
    const nudge = (id, value) => page.evaluate(([id, value]) => {
      const el = document.getElementById(id);
      el.value = String(value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }, [id, value]);

    await start();
    assert.equal(await page.evaluate(() => getComputedStyle(document.getElementById("player-full")).display), "none");

    // The bar fader is a real 96x20 control, not a 4px line mistaken for the scrub.
    const bar = await page.evaluate(() => {
      const b = document.getElementById("plvol").getBoundingClientRect();
      return [Math.round(b.width), Math.round(b.height)];
    });
    check("bar fader has a usable hit box", bar, [96, 20]);

    await nudge("plvol", 40);
    let s = await state();
    check("bar fader sets the level", s.vol, 0.4);
    check("fullscreen fader follows the bar", [s.pf, s.pfFill], [40, "40%"]);
    check("level is remembered", s.stored, "0.4");

    await nudge("pfvol", 70);
    s = await state();
    check("fullscreen fader sets the level", s.vol, 0.7);
    check("bar fader follows fullscreen", [s.pl, s.plFill], [70, "70%"]);

    await page.click("#plmute");
    s = await state();
    check("mute empties both fills", [s.muted, s.plFill, s.pfFill], [true, "0%", "0%"]);
    await page.click("#plmute");
    s = await state();
    check("unmute restores the level", [s.muted, s.vol, s.pfFill], [false, 0.7, "70%"]);

    // Geometry at every desktop width: nothing sits on top of anything else.
    await page.evaluate(() => { if (typeof openFullPlayer === "function") openFullPlayer(); });
    await page.waitForTimeout(400);
    for (const [w, h] of [[1024, 768], [1280, 800], [1440, 900], [1920, 1080]]) {
      await page.setViewportSize({ width: w, height: h });
      await page.waitForTimeout(250);
      const geo = await page.evaluate(() => {
        const box = sel => { const b = document.querySelector(sel).getBoundingClientRect();
          return { l: Math.round(b.left), r: Math.round(b.right), t: Math.round(b.top), b: Math.round(b.bottom),
            w: Math.round(b.width), h: Math.round(b.height) }; };
        const vol = box("#player-full .pf-vol");
        return { seek: box("#plseek"), dur: box("#pldur"), barVol: box("#plvol"),
          wave: box("#pfwave"), fullVol: box("#pfvol"), volBox: vol,
          inside: vol.t >= 0 && vol.l >= 0 && vol.r <= innerWidth && vol.b <= innerHeight,
          overflowX: document.documentElement.scrollWidth > innerWidth };
      });
      check(`${w}: duration clears the bar fader`, geo.dur.r <= geo.barVol.l, true);
      check(`${w}: scrub clears the duration`, geo.seek.r <= geo.dur.l, true);
      check(`${w}: bar fader stays 96x20`, [geo.barVol.w, geo.barVol.h], [96, 20]);
      check(`${w}: fullscreen fader clears the visualizer button`, geo.wave.r <= geo.fullVol.l, true);
      check(`${w}: fullscreen fader is on screen`, [geo.fullVol.w, geo.fullVol.h, geo.inside], [130, 20, true]);
      check(`${w}: no horizontal overflow`, geo.overflowX, false);
    }

    // Dragging the fader must not be read as a dismiss gesture.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.waitForTimeout(250);
    const fader = await page.evaluate(() => {
      const b = document.getElementById("pfvol").getBoundingClientRect();
      return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) };
    });
    await page.mouse.move(fader.x, fader.y);
    await page.mouse.down();
    await page.mouse.move(fader.x - 40, fader.y, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(200);
    check("dragging the fader keeps the fullscreen view open",
      await page.evaluate(() => document.getElementById("player-full").classList.contains("on")), true);
    check("dragging the fader changed the level", (await state()).vol < 0.7, true);

    // Phones: no fader, for the same reason the mute button stays.
    await page.evaluate(() => { if (typeof closeFullPlayer === "function") closeFullPlayer(); });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    check("phone hides the bar fader", await page.evaluate(() => getComputedStyle(document.querySelector("#player .pvol-vert")).display), "none");
    check("phone hides the fullscreen fader", await page.evaluate(() => getComputedStyle(document.querySelector("#player-full .pf-vol")).display), "none");
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("volume faders ok");
})();