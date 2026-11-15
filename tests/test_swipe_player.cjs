// The Swipe page shows the library's compact player. Tapping a card used to be a bare
// <audio> with nothing to steer it; now the bar appears with the song's cover, title and
// clocks, and play/pause, prev/next, the scrub and the volume all drive that one element.
// The deck still owns the queue: nothing here keeps or drops a song.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };

// Two seconds of silence as a real WAV: Chromium will not play without a decodable source,
// and `duration` is what the clocks and the scrub read.
const WAV = (() => {
  const rate = 8000, frames = rate * 2, bytes = frames * 2;
  const head = Buffer.alloc(44);
  head.write("RIFF", 0); head.writeUInt32LE(36 + bytes, 4); head.write("WAVE", 8);
  head.write("fmt ", 12); head.writeUInt32LE(16, 16); head.writeUInt16LE(1, 20);
  head.writeUInt16LE(1, 22); head.writeUInt32LE(rate, 24); head.writeUInt32LE(rate * 2, 28);
  head.writeUInt16LE(2, 32); head.writeUInt16LE(16, 34);
  head.write("data", 36); head.writeUInt32LE(bytes, 40);
  return Buffer.concat([head, Buffer.alloc(bytes)]);
})();

const TRACKS = [1, 2, 3].map(id => ({
  id, title: `Song ${id}`, artist_name: `Artist ${id}`, album: "Album",
  artwork: "data:image/svg+xml;base64," + Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#4a5568"/></svg>`).toString("base64"),
  duration: 2, bpm: 0, genres: [],
}));
const STATS = { keep: 0, like: 0, drop: 0, left: 3 };

// The real /audio route answers Range requests, and Chromium marks a stream without them
// as unseekable: setting currentTime then restarts the file from zero, which reads as "the
// scrub does nothing". The stub has to speak the same protocol as the server it stands in for.
const sendAudio = (req, res) => {
  const match = /bytes=(\d*)-(\d*)/.exec(req.headers.range || "");
  if (match) {
    const start = match[1] ? Number(match[1]) : 0;
    const end = match[2] ? Math.min(Number(match[2]), WAV.length - 1) : WAV.length - 1;
    return res.writeHead(206, {
      "content-type": "audio/wav", "accept-ranges": "bytes",
      "content-range": `bytes ${start}-${end}/${WAV.length}`,
      "content-length": end - start + 1,
    }).end(WAV.subarray(start, end + 1));
  }
  res.writeHead(200, { "content-type": "audio/wav", "accept-ranges": "bytes", "content-length": WAV.length }).end(WAV);
};

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = data => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(data));
  if (p === "/api/swipe/next") return json({ tracks: TRACKS, stats: STATS });
  if (p === "/api/swipe/decide") return json({ ok: true, stats: STATS });
  if (/^\/api\/track\/\d+\/audio$/.test(p)) return sendAudio(req, res);
  if (p === "/swipe") {
    return res.writeHead(200, { "content-type": "text/html" }).end(fs.readFileSync(path.join(ROOT, "swipe.html")));
  }
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}/swipe`;
  const browser = await chromium.launch({ headless: true });
  let failed = 0;
  const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failed++;
    console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`));
  };
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    page.on("pageerror", error => { failed++; console.log("FAIL pageerror: " + error.message); });
    await page.goto(base, { waitUntil: "load" });
    await page.waitForTimeout(400);

    const bar = () => page.evaluate(() => {
      const root = document.getElementById("player");
      const r = root.getBoundingClientRect();
      return {
        on: root.classList.contains("on"),
        shown: getComputedStyle(root).display,
        title: document.getElementById("pltitle").textContent,
        sub: document.getElementById("plsub").textContent,
        art: document.getElementById("plart").getAttribute("src") ? "set" : "",
        time: document.getElementById("pltime").textContent,
        dur: document.getElementById("pldur").textContent,
        played: document.getElementById("plseek").style.getPropertyValue("--bar-played"),
        box: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
        playLabel: document.getElementById("plplay").getAttribute("aria-label"),
        nextDisabled: document.getElementById("plnext").disabled,
      };
    });

    // Nothing is playing, so there is no bar — not an empty one.
    check("the bar starts hidden", (await bar()).on, false);
    check("three cards came back", await page.evaluate(() => document.querySelectorAll("#deck .card").length), 2);

    // Tap the card: it plays and the bar appears with that song. A tap is a pointer pair —
    // the card listens for pointerdown/pointerup, not click, because a drag starts the same way.
    const tap = async () => {
      await page.evaluate(() => {
        const node = document.querySelector("#deck .card:not(.back)");
        for (const type of ["pointerdown", "pointerup"])
          node.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, clientX: 100, clientY: 100 }));
      });
    };
    await tap();
    await page.waitForTimeout(600);
    let b = await bar();
    check("the bar appears on a tap", [b.on, b.shown], [true, "block"]);
    check("it names the song that is playing", [b.title, b.sub], ["Song 1", "Artist 1 · Album"]);
    check("it carries the cover", b.art, "set");
    check("it is playing, so the button offers pause", b.playLabel, "Pause");
    check("the duration is the file's", b.dur, "0:02");

    // The scrub is the progress line and the seek: half way is one second. Read in the same
    // turn as the event, or playback has already moved the line past the value just set.
    const scrub = await page.evaluate(() => {
      const seek = document.getElementById("plseek");
      seek.value = "500";
      seek.dispatchEvent(new Event("input", { bubbles: true }));
      return {
        at: document.getElementById("audio").currentTime,
        played: seek.style.getPropertyValue("--bar-played"),
        clock: document.getElementById("pltime").textContent,
      };
    });
    check("half way is one second of two", Math.round(scrub.at), 1);
    check("the line paints the position", scrub.played, "50.00%");
    check("and the clock reads it", scrub.clock, "0:01");

    // Volume: one level, the same key the library uses.
    await page.evaluate(() => {
      const fader = document.getElementById("plvolv");
      fader.value = "40";
      fader.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForTimeout(200);
    check("the fader sets the level and remembers it", await page.evaluate(() => ({
      volume: +document.getElementById("audio").volume.toFixed(2),
      stored: localStorage.getItem("hl.volume"),
    })), { volume: 0.4, stored: "0.4" });

    await page.click("#plmute");
    await page.waitForTimeout(200);
    check("mute is a flag on the element", await page.evaluate(() => ({
      muted: document.getElementById("audio").muted,
      pressed: document.getElementById("plmute").getAttribute("aria-pressed"),
      fill: document.getElementById("plvolv").value,
    })), { muted: true, pressed: "true", fill: "0" });
    await page.click("#plmute");
    await page.waitForTimeout(200);
    check("unmute restores the level", await page.evaluate(() => +document.getElementById("audio").volume.toFixed(2)), 0.4);

    // The speaker opens the same vertical fader as the library, placed above the button.
    await page.click("#plvolmore");
    await page.waitForTimeout(300);
    const menu = await page.evaluate(() => {
      const m = document.getElementById("plvolmenu"), b = document.getElementById("plvolmore");
      const mr = m.getBoundingClientRect(), br = b.getBoundingClientRect();
      return { open: m.classList.contains("open"), above: Math.round(mr.bottom) <= Math.round(br.top),
        inside: mr.left >= 0 && mr.right <= innerWidth };
    });
    check("the speaker opens a fader above itself, on screen", [menu.open, menu.above, menu.inside], [true, true, true]);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    check("Escape closes it", await page.evaluate(() => document.getElementById("plvolmenu").classList.contains("open")), false);

    // Play/pause is the element, not a second flag. Rewind first: the clip is two seconds
    // and the checks above spend longer than that, so a click would be "start it again".
    await page.evaluate(() => {
      const a = document.getElementById("audio");
      a.currentTime = 0.2;
      a.play();
    });
    await page.waitForTimeout(200);
    check("still playing at the start", await page.evaluate(() => document.getElementById("audio").paused), false);
    await page.click("#plplay");
    await page.waitForTimeout(200);
    check("the bar pauses the song", await page.evaluate(() => ({
      paused: document.getElementById("audio").paused,
      label: document.getElementById("plplay").getAttribute("aria-label"),
    })), { paused: true, label: "Play" });
    await page.click("#plplay");
    await page.waitForTimeout(300);
    check("and starts it again", await page.evaluate(() => document.getElementById("audio").paused), false);

    // Next auditions the card under this one without deciding anything.
    await page.click("#plnext");
    await page.waitForTimeout(600);
    b = await bar();
    check("next plays the following card", b.title, "Song 2");
    check("next did not keep or drop anything", await page.evaluate(() => document.querySelectorAll("#deck .card").length), 2);

    // Previous goes back to the song auditioned before this one.
    await page.click("#plprev");
    await page.waitForTimeout(600);
    check("prev returns to the song before it", (await bar()).title, "Song 1");

    // Arrow keys on the bar's own controls must not decide anything: the scrub is a range
    // input and Space belongs to the focused button.
    const before = await page.evaluate(() => document.querySelectorAll("#deck .card").length);
    await page.focus("#plseek");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowLeft");
    await page.waitForTimeout(300);
    check("arrows on the scrub do not keep or drop", await page.evaluate(() => document.querySelectorAll("#deck .card").length), before);

    // Swiping the playing card away takes the bar with it: no card, no player.
    await page.evaluate(() => document.getElementById("drop").click());
    await page.waitForTimeout(600);
    check("a drop stops it and hides the bar", (await bar()).on, false);

    // Desktop: the bar is the deck's width, not the library's 1072 px row.
    await page.setViewportSize({ width: 1280, height: 800 });
    await tap();
    await page.waitForTimeout(600);
    const wide = await page.evaluate(() => {
      const r = document.getElementById("player").getBoundingClientRect();
      const main = document.querySelector("main").getBoundingClientRect();
      return { width: Math.round(r.width), main: Math.round(main.width), gap: Math.round(r.left - main.left) };
    });
    check("desktop holds the bar to the deck's column", [wide.width, wide.gap], [520, 0]);
    const onScreen = await page.evaluate(() => {
      const r = document.getElementById("player").getBoundingClientRect();
      return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
    });
    check("and keeps it on screen", onScreen, true);

    // The page still has its own controls: the bar must not cover the deck's action row.
    const clear = await page.evaluate(() => {
      const bar = document.getElementById("player").getBoundingClientRect();
      const actions = document.getElementById("actions").getBoundingClientRect();
      return Math.round(actions.bottom) <= Math.round(bar.top) || getComputedStyle(document.getElementById("actions")).display === "none";
    });
    check("the action row is not under the bar", clear, true);
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("swipe player ok");
})();