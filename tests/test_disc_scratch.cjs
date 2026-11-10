// Dragging the now-playing record must play the track itself at the hand's speed: the
// drag borrows playerAudio.playbackRate (slow spin drags it down, a flick speeds it up),
// starts playback if it was paused, never opens a separate scratch source, and gives the
// track its own rate back when the hand lets go.
const http = require("http"), fs = require("fs"), path = require("path");
const { chromium } = require("playwright");
const WEB = require("path").join(__dirname, "..", "web");
const types = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml" };

// 6 s stereo 44.1 kHz WAV: a tone that changes pitch, so a decoded read is provable.
function wav(seconds = 6, rate = 44100) {
  const n = seconds * rate, data = Buffer.alloc(44 + n * 2 * 2), h = data;
  h.write("RIFF", 0); h.writeUInt32LE(36 + n * 4, 4); h.write("WAVEfmt ", 8);
  h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(2, 22);
  h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 4, 28); h.writeUInt16LE(4, 32); h.writeUInt16LE(16, 34);
  h.write("data", 36); h.writeUInt32LE(n * 4, 40);
  for (let i = 0; i < n; i++) {
    const v = Math.round(Math.sin(2 * Math.PI * (200 + i / n * 200) * i / rate) * 12000);
    data.writeInt16LE(v, 44 + i * 4); data.writeInt16LE(v, 46 + i * 4);
  }
  return data;
}
const WAV = wav();
const server = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  if (p.startsWith("/api/track/") && p.endsWith("/audio")) {
    return res.writeHead(200, { "content-type": "audio/wav", "content-length": String(WAV.length), "accept-ranges": "bytes" }).end(WAV);
  }
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
function check(ok, what){ if(ok) console.log("ok   " + what); else { failures++; console.log("FAIL " + what); } }
(async () => {
  await new Promise(r => server.listen(8485, r));
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const page = await browser.newPage({ viewport: { width: 430, height: 900 }, deviceScaleFactor: 1 });
  const errs = []; page.on("pageerror", e => errs.push(String(e)));
  // Watch every buffer source: what it plays, at which rate, forwards or reversed.
  await page.addInitScript(() => {
    window.__plays = [];
    const proto = AudioContext.prototype;
    const origBuf = proto.createBufferSource;
    proto.createBufferSource = function () {
      const node = origBuf.call(this);
      const start = node.start.bind(node);
      node.start = function (when, offset) {
        window.__plays.push({ offset: offset ?? 0, bufLen: node.buffer ? +node.buffer.duration.toFixed(2) : 0, rate: +node.playbackRate.value.toFixed(3) });
        return start(when, offset);
      };
      return node;
    };
  });
  await page.goto("http://127.0.0.1:8485/", { waitUntil: "load" });
  await page.waitForTimeout(600);
  // Load a track into the player the way playback does, then open the popup.
  await page.evaluate(async () => {
    await loadLibrarySnapshot().catch(() => {});
    const track = { id: 900, title: "Scratch me", artist_name: "A", album: "Al", duration: 6, playable: true,
      artwork: "", playlists: [], album_id: "a".repeat(64), status: "ok", file: "x.wav" };
    // After the snapshot, or it overwrites the fixture with the stub's empty library.
    lib = [track]; libAll = [track]; currentRows = [track];
    playFromLibrary(900);
    await new Promise(r => setTimeout(r, 700));
    openFullPlayer();
    await new Promise(r => setTimeout(r, 700));   // decode
  });
  const prepared = await page.evaluate(() => ({ decoded: !!pfScratchBuffer, len: pfScratchBuffer ? +pfScratchBuffer.duration.toFixed(2) : null,
    sampleRate: pfScratchBuffer ? pfScratchBuffer.sampleRate : null,
    duration: playerAudio.duration, open: fullPlayerOpen() }));

  // Turn the record forwards, then backwards, then hold it still.
  const diag = await page.evaluate(() => ({ open: fullPlayerOpen(), cls: $("#player-full").className,
    discRect: (() => { const b = $("#pf-disc").getBoundingClientRect(); return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }; })(),
    playingId: playingId, dur: playerAudio.duration, err: window.__bootErr || null }));
  check(diag.open && diag.dur > 0, "the popup opens on a loaded track");
  const disc = await page.$("#pf-disc");
  const box = await disc.boundingBox();
  if (!box) { console.log("no disc box"); await browser.close(); server.close(); return; }
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2, r = box.width * 0.35;
  const at = (deg) => [cx + Math.cos(deg * Math.PI / 180) * r, cy + Math.sin(deg * Math.PI / 180) * r];
  const drag = async (from, to, stepMs) => {
    const [x0, y0] = at(from);
    await page.mouse.move(x0, y0);
    await page.mouse.down();
    const step = to >= from ? 10 : -10;
    for (let d = from; step > 0 ? d <= to : d >= to; d += step) {
      const [x, y] = at(d);
      await page.mouse.move(x, y);
      await page.waitForTimeout(stepMs);
    }
    await page.waitForTimeout(30);
    const mid = await page.evaluate(() => ({ plays: window.__plays.slice(-3), voice: !!pfScratchVoice,
      rate: +playerAudio.playbackRate.toFixed(3), paused: playerAudio.paused,
      drag: pfDiscDrag ? { moved: pfDiscDrag.moved, omega: +pfDiscDrag.omega.toFixed(2), target: +pfDiscDrag.target.toFixed(2) } : null,
      buf: !!pfScratchBuffer, rev: !!pfScratchReversed, raf: pfScratchRaf, plays: window.__plays.length }));
    await page.mouse.up();
    await page.waitForTimeout(150);
    return mid;
  };
  const forward = await drag(0, 120, 12);
  const backward = await drag(200, 80, 12);
  // A slow turn (10 degrees per half second, well under one turn per 12 s) must read the
  // track slower than real time rather than snapping back to normal speed.
  const slow = await drag(300, 330, 500);
  const afterUp = await page.evaluate(() => ({ voice: !!pfScratchVoice, rate: +playerAudio.playbackRate.toFixed(3),
    plays: window.__plays.length }));

  check(prepared.decoded && prepared.len > 0, "the track is decoded while the popup is open");
  check(prepared.sampleRate === 22050, "the scratch copy is decoded at 22.05 kHz, got " + prepared.sampleRate);
  check(forward.drag && forward.drag.omega > 0, "the drag measures a forward angular speed");
  check(forward.rate > 1, "a fast forward turn speeds the track up, got " + forward.rate);
  check(!forward.paused, "the drag starts the track instead of seeking silently");
  check(!forward.voice, "no separate scratch source is started during the drag");
  check(backward.drag && backward.drag.omega < 0 && backward.drag.target < forward.drag.target, "backwards drag moves the position back");
  check(backward.rate >= 0.0625 && backward.rate <= 4, "the borrowed rate stays inside the 0.0625-4 range, got " + backward.rate);
  check(slow.rate < 1 && slow.rate >= 0.0625, "a slow turn reads the track slower than real time, got " + slow.rate);
  check(afterUp.rate === 1, "letting go gives the track its own rate back, got " + afterUp.rate);
  check(!afterUp.voice, "letting go leaves no scratch voice behind");
  check(afterUp.plays === 0, "the drag never creates its own audio source (" + afterUp.plays + ")");
  check(errs.length === 0, "no page errors: " + JSON.stringify(errs.slice(0, 3)));
  await browser.close(); server.close();
  console.log(failures ? failures + " FAILED" : "Disc drag: the track itself follows the hand's speed, rate restored on release, no scratch source PASS");
  process.exitCode = failures ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; });
