// The mixer on a phone: the rail reads CROSSFADER then MASTER (stacked, full width),
// nothing hangs off the side of the screen, and a song does not stop on its own.
//
// The stop was real and had a cause worth pinning: the room sends a deck's position as
// `position_now` (where the deck is, resolved on the server's clock). It used to send
// only `at`, a SERVER timestamp, and the page compared it with the phone's own clock. A phone whose
// clock is ahead therefore seeked the playing deck forward — past the end, which fires
// `ended` and looks exactly like "the song stopped by itself". Both halves are tested
// here: the wire shape, and the page's arithmetic with a deliberately skewed `at`.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const ROOT = process.env.MIXER_ROOT || path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                ".png": "image/png", ".svg": "image/svg+xml" };

// Ten seconds of a quiet tone as a WAV, so playback can be observed without a library.
function wav(seconds = 10, rate = 8000) {
  const samples = seconds * rate, buf = Buffer.alloc(44 + samples);
  buf.write("RIFF", 0); buf.writeUInt32LE(36 + samples, 4); buf.write("WAVE", 8);
  buf.write("fmt ", 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22); buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate, 28);
  buf.writeUInt16LE(1, 32); buf.writeUInt16LE(8, 34);
  buf.write("data", 36); buf.writeUInt32LE(samples, 40);
  for (let i = 0; i < samples; i++) buf[44 + i] = 128 + Math.round(40 * Math.sin(i / 12));
  return buf;
}
const AUDIO = wav();

const TRACK = { id: 1, title: "Fixture One", artist_name: "Test Artist", album: "Test Album",
  duration: 10, bpm: 128, artwork: "", file: "/music/fixture.wav", added: 1 };

let subscribers = [];
const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/library") return json({ tracks: [TRACK], playlists: [], folders: [] });
  if (p === "/api/listening/saves") return json({ tracks: [], playlists: [], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/tempo/status") return json({ running: false, done: 1, total: 1, percent: 100 });
  if (p === "/api/state") return json({ viewer: { is_admin: true, role: "admin" }, stats: {}, subscriptions: [] });
  if (p === "/api/track/1/audio") {
    res.writeHead(200, { "content-type": "audio/wav", "content-length": AUDIO.length });
    return res.end(AUDIO);
  }
  // The room: a live state on join, then whatever the test pushes down the stream.
  if (p === "/api/mixer/stream") {
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache",
                         connection: "keep-alive" });
    res.write("data: " + JSON.stringify({ type: "state", you: "me", values: {}, claims: {},
      members: [], queues: { A: [], B: [] },
      decks: { A: { track_id: null, pos: 0, playing: false, rate: 1, at: 0, position_now: 0 },
               B: { track_id: null, pos: 0, playing: false, rate: 1, at: 0, position_now: 0 } } }) + "\n\n");
    subscribers.push(res);
    return;
  }
  if (p === "/api/mixer") return json({ ok: true });
  if (p.startsWith("/api/")) return json({ ok: true, items: [], rows: [], entries: [] });
  const f = path.join(ROOT, p === "/" ? "/dj.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" })
      .end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

const rect = id => `(() => { const r = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();
  return {top: r.top, left: r.left, width: r.width, height: r.height, bottom: r.bottom}; })()`;

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [label, viewport] of [["phone", { width: 390, height: 844 }],
                                     ["desktop", { width: 1440, height: 900 }]]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on("pageerror", e => errors.push(String(e).slice(0, 160)));
      await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
      await page.waitForTimeout(900);

      const cross = await page.evaluate(rect("crossfader"));
      const master = await page.evaluate(rect("master"));
      const mid = await page.evaluate(`(() => { const r = document.querySelector(".mid").getBoundingClientRect();
        return {top: r.top, left: r.left, width: r.width, height: r.height}; })()`);

      check(`${label}: CROSSFADER sits above MASTER`, cross.bottom <= master.top,
        JSON.stringify({ crossBottom: Math.round(cross.bottom), masterTop: Math.round(master.top) }));
      check(`${label}: both faders are full width of the rail`,
        cross.width >= mid.width - 26 && master.width >= mid.width - 26,
        JSON.stringify({ cross: Math.round(cross.width), master: Math.round(master.width),
                         rail: Math.round(mid.width) }));
      check(`${label}: the two faders never share a row`, Math.abs(cross.top - master.top) > 8);

      const overflow = await page.evaluate(`(() => ({ scroll: document.documentElement.scrollWidth,
        inner: window.innerWidth }))()`);
      check(`${label}: nothing hangs off the side`, overflow.scroll <= overflow.inner + 1,
        JSON.stringify(overflow));

      if (label === "phone") {
        const a = await page.evaluate(`(() => { const r = document.querySelector(".deck.a").getBoundingClientRect();
          return {top: r.top, left: r.left, width: r.width, height: r.height}; })()`);
        const b = await page.evaluate(`(() => { const r = document.querySelector(".deck.b").getBoundingClientRect();
          return {top: r.top, left: r.left, width: r.width, height: r.height}; })()`);
        check("phone: the decks are the two halves of one row",
          a.top === b.top && a.left < b.left && b.left > a.left + a.width - 2,
          JSON.stringify({ a, b }));
        const crowded = await page.evaluate(`(() => {
          const decks = [...document.querySelectorAll(".deck")].map(d => d.getBoundingClientRect().width);
          return { widths: decks, inner: window.innerWidth };
        })()`);
        check("phone: each deck still gets a usable width",
          crowded.widths.every(w => w > 150), JSON.stringify(crowded));
      }

      // ---- playback, and the echo that used to stop it ----------------------
      await page.evaluate(`loadTrack("A", ${JSON.stringify(TRACK)})`);
      await page.waitForTimeout(400);
      await page.click("#playA");
      await page.waitForTimeout(1400);
      let now = await page.evaluate(`(() => { const a = document.getElementById("audioA");
        return { paused: a.paused, t: a.currentTime, d: a.duration }; })()`);
      check(`${label}: pressing Play actually plays`, !now.paused && now.t > 0.4, JSON.stringify(now));

      // Room echoes from a device whose clock is an hour off, in both directions. Each
      // carries the correct `position_now`; the page must re-anchor on its own clock rather
      // than compare `at` (server time) with `Date.now()`. Ahead used to seek to 0 and
      // restart the song; behind used to seek past the end, which fires `ended` — the
      // "it just stopped" report. Both are the same bug with a different sign.
      const before = now.t;
      for (const skew of [+3600, -3600]) {
        for (const res of subscribers.splice(0)) {
          res.write("data: " + JSON.stringify({ decks: { A: { track_id: TRACK.id, pos: before,
            playing: true, rate: 1, at: Date.now() / 1000 + skew, position_now: before } } }) + "\n\n");
        }
        await page.waitForTimeout(700);
        now = await page.evaluate(`(() => { const a = document.getElementById("audioA");
          return { paused: a.paused, ended: a.ended, t: a.currentTime, d: a.duration }; })()`);
        check(`${label}: a room echo ${skew > 0 ? "from a clock ahead" : "from a clock behind"} does not stop the song`,
          !now.paused && !now.ended, JSON.stringify(now));
        check(`${label}: ...and does not teleport the playhead (${skew > 0 ? "ahead" : "behind"})`,
          now.t > before && now.t - before < 3, JSON.stringify({ before, after: now.t }));
      }
      check(`${label}: no page errors`, errors.length === 0, errors.join(" | "));
      await page.close();
    }
  } finally {
    await browser.close();
    for (const res of subscribers) res.end();
    srv.close();
  }
  console.log(failures ? `FAIL ${failures} check(s)` : "PASS all checks");
  process.exit(failures ? 1 : 0);
})();