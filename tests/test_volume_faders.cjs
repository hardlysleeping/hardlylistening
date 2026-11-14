// Desktop volume: the header carries a speaker that opens a fader, the fullscreen view
// carries its own, and both drive the one audio element. The opener is a fixed point --
// opening the fader must not move shuffle -- and the open state survives a reload. Phone
// widths keep the hardware buttons: no toggle and no fader there.
const fs = require("fs"), path = require("path"), http = require("http"), assert = require("assert");
const { chromium } = require("./_pw.cjs");
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

    // The bar's own speaker sits right of mute. It was painted from a fader's input handler,
    // so it used to boot as an empty box until someone dragged one of the faders.
    check("the bar's volume speaker sits right of mute, drawn at boot", await page.evaluate(() => {
      const b = document.getElementById("plvolmore"), m = document.getElementById("plmute");
      const bb = b.getBoundingClientRect(), mb = m.getBoundingClientRect();
      return { svg: !!b.querySelector("svg"), box: [Math.round(bb.width), Math.round(bb.height)],
        gap: Math.round(bb.left - mb.right), shown: getComputedStyle(b).display };
    }), { svg: true, box: [30, 30], gap: 0, shown: "grid" });

    // The header's speaker opens a compact fader to its left; closed, it is not laid out.
    const closed = await page.evaluate(() => ({
      shown: getComputedStyle(document.getElementById("plvol")).display,
      shuffle: Math.round(document.getElementById("btn-shuffle").getBoundingClientRect().left),
      gap: (() => {
        const a = document.getElementById("plvolbtn").getBoundingClientRect();
        const b = document.getElementById("btn-shuffle").getBoundingClientRect();
        return Math.round(b.left - a.right);
      })(),
    }));
    check("the fader starts closed", closed.shown, "none");
    check("the speaker sits 10px left of shuffle", closed.gap, 10);

    await page.click("#plvolbtn");
    await page.waitForTimeout(200);
    const opened = await page.evaluate(() => {
      const b = document.getElementById("plvol").getBoundingClientRect();
      return { shown: getComputedStyle(document.getElementById("plvol")).display,
        box: [Math.round(b.width), Math.round(b.height)],
        pressed: document.getElementById("plvolbtn").getAttribute("aria-pressed"),
        shuffle: Math.round(document.getElementById("btn-shuffle").getBoundingClientRect().left),
        stored: localStorage.getItem("hl.compactVolume") };
    });
    check("the toggle opens a 72x18 fader", [opened.shown, opened.box, opened.pressed], ["block", [72, 18], "true"]);
    check("opening the fader does not move shuffle", opened.shuffle, closed.shuffle);
    check("the open state is stored", opened.stored, "1");

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

    // The toggle and the level are both remembered.
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(600);
    check("the toggle and level survive a reload", await page.evaluate(() => ({
      open: document.getElementById("plvolslot").classList.contains("open"),
      shown: getComputedStyle(document.getElementById("plvol")).display,
      pressed: document.getElementById("plvolbtn").getAttribute("aria-pressed"),
      level: +playerAudio.volume.toFixed(2),
    })), { open: true, shown: "block", pressed: "true", level: 0.7 });
    await start();

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
          queue: box("#pfqueue"), fullVol: box("#pfvol"), volBox: vol,
          like: box("#pflike"),
          // Horizontal fit plus the top edge; the row is the last line of the panel, so on a
          // short window it can hang a few pixels past the fold while the track stays visible.
          inside: vol.t >= 0 && vol.l >= 0 && vol.r <= innerWidth,
          clipped: Math.max(0, vol.b - innerHeight),
          overflowX: document.documentElement.scrollWidth > innerWidth };
      });
      // The wide desktop bar puts the right clock on the scrub row and the fader in the
      // cluster above it, so they may share columns — what must never happen is overlap.
      const hit = (a, c) => a.l < c.r && c.l < a.r && a.t < c.b && c.t < a.b;
      check(`${w}: duration does not sit under the bar fader`, hit(geo.dur, geo.barVol), false);
      check(`${w}: scrub clears the duration`, geo.seek.r <= geo.dur.l, true);
      check(`${w}: header fader stays 72x18`, [geo.barVol.w, geo.barVol.h], [72, 18]);
      // The fader lives on its own centred row under the foot, so a wide track reaches back
      // under the queue button's column without ever touching it: overlap is the test.
      check(`${w}: the fader row clears the queue button`, hit(geo.volBox, geo.queue), false);
      // 180 px: nothing shares the row, so the width is the fader's own business.
      check(`${w}: fullscreen fader is on screen`, [geo.fullVol.w, geo.fullVol.h, geo.inside], [180, 20, true]);
      check(`${w}: the fader's track stays in view`, geo.clipped <= 8, true);
      check(`${w}: the fader row clears the heart`, hit(geo.volBox, geo.like), false);
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
    check("phone hides the volume toggle", await page.evaluate(() => getComputedStyle(document.getElementById("plvolbtn")).display), "none");
    check("phone hides the header fader", await page.evaluate(() => getComputedStyle(document.getElementById("plvol")).display), "none");
    check("phone hides the fullscreen fader", await page.evaluate(() => getComputedStyle(document.querySelector("#player-full .pf-vol")).display), "none");
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("volume faders ok");
})();