// "Where you left off": a reload brings back the song and its position.
//
// The code had been in place (`hl.now.v1` written on every change, `/api/listening/now` as the
// new-device fallback) but the card sat blocked on "one check on a phone". A phone is not a
// test: both sources are checked here in a real browser, including the case that matters most —
// a page that is already playing must not be clobbered by a stale saved state.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };

// 30 s of silence at 8 kHz: long enough that seeking to 22 s leaves room to read the clock, and
// short enough that a test never waits on it.
const WAV = (() => {
  const rate = 8000, frames = rate * 30, bytes = frames * 2;
  const head = Buffer.alloc(44);
  head.write("RIFF", 0); head.writeUInt32LE(36 + bytes, 4); head.write("WAVE", 8);
  head.write("fmt ", 12); head.writeUInt32LE(16, 16); head.writeUInt16LE(1, 20);
  head.writeUInt16LE(1, 22); head.writeUInt32LE(rate, 24); head.writeUInt32LE(rate * 2, 28);
  head.writeUInt16LE(2, 32); head.writeUInt16LE(16, 34);
  head.write("data", 36); head.writeUInt32LE(bytes, 40);
  return Buffer.concat([head, Buffer.alloc(bytes)]);
})();

const TRACKS = [1, 2, 3].map(id => ({
  id, title: `Song ${id}`, artist_name: `Artist ${id}`, album: "Album", album_id: "a".repeat(64),
  artwork: "", art_hash: "b".repeat(32), playable: true, duration: 30, added: id, playlists: [],
}));
// A 1x1 PNG: the cover has to actually load, or `#plart`'s own error handler drops the src.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");

// Every /api route answers something harmless; the two that matter are named below.
const EMPTY = { ok: true, songs: [], queue: [], tracks: [], playlists: [], folders: [], favorites: [],
  pins: [], likes: [], library: [], items: [], entries: [], results: [], all_tracks: [] };

const sendAudio = (req, res) => {
  const m = /bytes=(\d*)-(\d*)/.exec(req.headers.range || "");
  if (m) {
    const start = m[1] ? Number(m[1]) : 0;
    const end = m[2] ? Math.min(Number(m[2]), WAV.length - 1) : WAV.length - 1;
    return res.writeHead(206, {
      "content-type": "audio/wav", "accept-ranges": "bytes",
      "content-range": `bytes ${start}-${end}/${WAV.length}`,
      "content-length": end - start + 1,
    }).end(WAV.subarray(start, end + 1));
  }
  res.writeHead(200, { "content-type": "audio/wav", "accept-ranges": "bytes", "content-length": WAV.length }).end(WAV);
};

function server(state) {
  return http.createServer((req, res) => {
    const p = req.url.split("?")[0];
    const json = body => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(body));
    if (p === "/api/library")
      return json({ tracks: TRACKS, all_tracks: TRACKS, playlists: [], artist_art: {} });
    if (p === "/api/listening/now") {
      if (req.method === "POST") { state.posted.push(req.headers["content-length"]); return json({ ok: true }); }
      return json({ state: state.remote });
    }
    if (/^\/api\/track\/\d+\/audio$/.test(p)) return sendAudio(req, res);
    if (/^\/api\/art\/[a-f0-9]{32}$/.test(p))
      return res.writeHead(200, { "content-type": "image/png", "content-length": PNG.length }).end(PNG);
    if (p.startsWith("/api/")) return json(EMPTY);
    const f = path.join(ROOT, p === "/" ? "/index.html" : p);
    if (fs.existsSync(f) && fs.statSync(f).isFile())
      return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
    res.writeHead(404).end("no");
  });
}

const bar = page => page.evaluate(() => {
  const root = document.getElementById("player");
  const audio = document.getElementById("player-audio");
  return {
    on: root.classList.contains("on"),
    title: document.getElementById("pltitle").textContent,
    art: document.getElementById("plart").getAttribute("src") ? "set" : "",
    paused: audio.paused,
    id: (audio.getAttribute("src") || "").match(/\/api\/track\/(\d+)\/audio/)?.[1] || "",
    at: Math.round((audio.currentTime || 0) * 10) / 10,
    toast: (document.getElementById("toast")?.textContent || "").trim(),
  };
});

(async () => {
  const state = { remote: null, posted: [] };
  const srv = server(state);
  await new Promise(r => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const browser = await chromium.launch({ headless: true });
  let failed = 0;
  const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failed++;
    console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`));
  };
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    page.on("pageerror", error => { failed++; console.log("FAIL pageerror: " + error.message); });
    await page.goto(base, { waitUntil: "load" });
    await page.waitForTimeout(900);

    // Play song 2 and stop at 22 s, the way a person would.
    const saved = await page.evaluate(() => {
      const row = libAll.find(t => t.id === 2);
      libQueue = libAll.filter(t => t.playable);
      libIndex = libQueue.indexOf(row);
      playingId = row.id;
      playerAudio.src = `/api/track/${row.id}/audio`;
      playerAudio.currentTime = 22;
      const stored = () => { try { return JSON.parse(localStorage.getItem("hl.now.v1") || "null"); } catch (_) { return null; } };
      return new Promise(resolve => {
        playerAudio.addEventListener("timeupdate", () => { saveNowState(false); resolve(stored()); }, { once: true });
      });
    });
    check("the position is written locally while it plays", [saved && saved.id, saved && saved.time], [2, 22]);
    check("and to the server, so a new device can pick it up", state.posted.length > 0, true);

    // Reload: the local copy restores the song, paused, at the same second.
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(1400);
    let seen = await bar(page);
    check("a reload brings the bar back", seen.on, true);
    check("with the song that was playing", [seen.id, seen.title], ["2", "Song 2"]);
    check("and its artwork slot filled", seen.art, "set");
    check("at the position it was stopped", Math.abs(seen.at - 22) <= 1.5, true);
    check("paused, because a browser will not autoplay", seen.paused, true);
    check("and it says what it did", /Picked up where you left off/.test(seen.toast), true);

    // A second device has no localStorage: the server's copy is the same answer.
    await page.evaluate(() => localStorage.removeItem("hl.now.v1"));
    state.remote = { id: 3, queue: [1, 2, 3], time: 9.5, updated: Date.now() };
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(1400);
    seen = await bar(page);
    check("a new device restores from the server", [seen.on, seen.id], [true, "3"]);
    check("with the server's position", Math.abs(seen.at - 9.5) <= 1.5, true);

    // A page already playing is never clobbered by a stale saved state.
    await page.evaluate(() => {
      const row = libAll.find(t => t.id === 1);
      playingId = row.id;
      playerAudio.src = `/api/track/${row.id}/audio`;
    });
    state.remote = { id: 3, queue: [1, 2, 3], time: 9.5, updated: Date.now() };
    const clobbered = await page.evaluate(async () => {
      await restoreNowState();
      return { playing: playingId, src: playerAudio.getAttribute("src") || playerAudio.src };
    });
    check("a live session is left alone", [clobbered.playing, /\/1\/audio$/.test(clobbered.src)], [1, true]);
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("resume position ok");
})();