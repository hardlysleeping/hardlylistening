// What the phone publishes to the lock screen and the car: title, artist, artwork and album.
// `tracks.album` is only the release, and most imported rows have none, so the published
// album is the same string the player's own line shows: the release, otherwise the set the
// song came from, otherwise nothing. iOS draws no album line at all, so the same string is
// also appended to the published artist, which is the line iOS does draw.
//
// Not covered here: what the head unit chooses to redraw. A test can only prove what the page
// publishes, which is why the republish milestones are asserted explicitly.
const fs = require("fs"), path = require("path"), http = require("http"), assert = require("assert");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = body => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(body));
  if (p.startsWith("/api/")) return json({ ok: true, songs: [], queue: [], tracks: [], playlists: [],
    folders: [], favorites: [], pins: [], likes: [], library: [], items: [], entries: [], results: [] });
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
    // Record every metadata assignment, then let the real one through.
    await page.addInitScript(() => {
      window.__meta = [];
      const proto = Object.getPrototypeOf(navigator.mediaSession);
      const desc = Object.getOwnPropertyDescriptor(proto, "metadata");
      if (!desc || !desc.set) return;
      Object.defineProperty(navigator.mediaSession, "metadata", {
        configurable: true,
        get() { return desc.get.call(this); },
        set(value) {
          const art = value ? (value.artwork || []) : [];
          window.__meta.push(value ? { title: value.title, artist: value.artist, album: value.album,
            artwork: art.length, artType: (art[0] || {}).type || "", artSrc: (art[0] || {}).src || "" } : null);
          return desc.set.call(this, value);
        }
      });
      // EXPERIMENT 2026-11-12: Chromium treats playbackState as read-only, so the assignment
      // a skip makes is recorded here instead of being observable on the real object.
      window.__state = [];
      try {
        Object.defineProperty(navigator.mediaSession, "playbackState", {
          configurable: true,
          get() { return window.__state.length ? window.__state[window.__state.length - 1] : "none"; },
          set(value) { window.__state.push(String(value)); }
        });
      } catch (_) {}
    });
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.waitForTimeout(600);

    await page.evaluate(() => {
      // A song pulled from a set: no release of its own, the set name lives in playlists.
      const set = { id: 1, title: "Set Song", artist_name: "Artist A", album: "",
        release_type: "playlist", artwork: "", playable: true, duration: 180, provider: "soundcloud",
        source_status: "ok", playlists: [{ id: 9, title: "Shuffle", position: 1 }] };
      const album = { id: 2, title: "Album Song", artist_name: "Artist B", album: "Real Album",
        release_type: "album", artwork: "", playable: true, duration: 200, provider: "soundcloud",
        source_status: "ok", playlists: [] };
      const bare = { id: 3, title: "Unreleased", artist_name: "Artist C", album: "",
        artwork: "", playable: true, duration: 200, provider: "soundcloud",
        source_status: "ok", playlists: [] };
      libAll = [set, album, bare]; lib = [1, 2, 3]; currentRows = libAll;
      libQueue = [set, album, bare]; libIndex = 0;
    });

    const meta = () => page.evaluate(() => window.__meta[window.__meta.length - 1]);
    const count = () => page.evaluate(() => window.__meta.length);

    // The audio element's own events, so the skip's teardown can be asserted.
    await page.evaluate(() => {
      window.__ev = [];
      for (const t of ["emptied", "loadstart", "playing", "pause"])
        document.getElementById("player-audio").addEventListener(t, () => window.__ev.push(t));
    });

    // A single with no release publishes the set it came from, the same string the
    // player's own line names.
    await page.evaluate(() => playFromLibrary(1));
    await page.waitForTimeout(300);
    check("the first song starts without tearing the element down",
      await page.evaluate(() => window.__ev.includes("emptied")), false);
    check("a single publishes its title", (await meta()).title, "Set Song");
    check("a single publishes the set as its album", (await meta()).album, "Shuffle");
    check("the artist line stays the artist, not artist and album",
      (await meta()).artist, "Artist A");
    check("the player line names the set it came from",
      await page.evaluate(() => $("#plsub").textContent), "Artist A · Shuffle");

    // A real release keeps its album.
    await page.evaluate(() => playFromLibrary(2));
    await page.waitForTimeout(300);
    check("a real album keeps its name", (await meta()).album, "Real Album");
    check("and does not repeat it on the artist line", (await meta()).artist, "Artist B");
    check("the player line is artist and album", await page.evaluate(() => $("#plsub").textContent),
      "Artist B · Real Album");
    check("the fullscreen line is artist and album too",
      await page.evaluate(() => {
        document.getElementById("player-full").classList.add("on");
        paintFullPlayer();
        return $("#pfsub").textContent;
      }), "Artist B · Real Album");
    await page.evaluate(() => document.getElementById("player-full").classList.remove("on"));

    // Neither album nor set: the line is the artist, and nothing invents a second half.
    await page.evaluate(() => playFromLibrary(3));
    await page.waitForTimeout(300);
    check("no album and no set publishes empty", (await meta()).album, "");
    check("and leaves the artist alone", (await meta()).artist, "Artist C");
    check("the player line is the artist alone", await page.evaluate(() => $("#plsub").textContent),
      "Artist C");

    // A skip publishes the new track, not the one that was playing.
    const before = await count();
    await page.evaluate(() => { libIndex = 0; window.__ev = []; playerNext(); });
    await page.waitForTimeout(300);
    check("a skip publishes a new tag", (await count()) > before, true);
    check("the tag after a skip is the new song", (await meta()).title, "Album Song");
    check("and its own album", (await meta()).album, "Real Album");
    // iOS only opens a fresh now-playing item when the old one is torn down, which is what a
    // track that ends on its own does and what a skip has to reproduce.
    check("a skip tears the old resource down first",
      await page.evaluate(() => window.__ev.includes("emptied")), true);
    check("the reset lands before the new source loads",
      await page.evaluate(() => {
        const ev = window.__ev;
        return ev.indexOf("emptied") >= 0 && ev.indexOf("loadstart") > ev.indexOf("emptied");
      }), true);

    // The milestones a latched head unit needs: the tag again once the new resource is
    // attached, and once playback actually starts.
    const afterSkip = await count();
    await page.evaluate(() => document.getElementById("player-audio")
      .dispatchEvent(new Event("loadedmetadata")));
    await page.waitForTimeout(120);
    const afterLoaded = await count();
    check("metadata is republished when the new resource is attached", afterLoaded > afterSkip, true);
    await page.evaluate(() => document.getElementById("player-audio")
      .dispatchEvent(new Event("playing")));
    await page.waitForTimeout(120);
    check("metadata is republished when playback starts", (await count()) > afterLoaded, true);
    check("the republished tag is still the new song", (await meta()).title, "Album Song");

    // Artwork is only ever an absolute URL: a car fetches it itself.
    await page.evaluate(() => {
      libQueue[1].artwork = "/api/track/2/artwork";
      playFromLibrary(2);
    });
    await page.waitForTimeout(300);
    check("artwork is published", (await meta()).artwork, 1);
    // WebKit draws no artwork entry without a declared type: the car shows an empty square.
    check("artwork declares its type", (await meta()).artType, "image/jpeg");
    check("artwork is an absolute URL the car can fetch", /^https?:\/\//.test((await meta()).artSrc), true);

    // The skip's teardown raises `emptied`, which is also what drops the now-playing tag on
    // WebKit. Re-publishing on it is what keeps the block from coming back blank.
    const beforeEmpty = await count();
    await page.evaluate(() => document.getElementById("player-audio")
      .dispatchEvent(new Event("emptied")));
    await page.waitForTimeout(120);
    check("the tag is re-claimed when the resource empties", (await count()) > beforeEmpty, true);

    // EXPERIMENT 2026-11-12 (media-session item boundary on a manual skip).
    // A user-initiated skip tells the platform the old item is gone before the teardown,
    // then hands over the new tag once the new resource reports in — the two halves are
    // asserted in order. Reverting the experiment makes the first two checks fail.
    await page.evaluate(() => { window.__state.length = 0; libIndex = 1; playerNext(); });
    await page.waitForTimeout(120);
    check("a manual skip declares the item boundary first",
      await page.evaluate(() => window.__state[0]), "none");
    check("the new tag is still published on the skip itself",
      await page.evaluate(() => window.__meta[window.__meta.length - 1].title), "Unreleased");
    await page.evaluate(() => document.getElementById("player-audio")
      .dispatchEvent(new Event("loadedmetadata")));
    await page.waitForTimeout(120);
    check("the item is claimed again once the new resource is attached",
      await page.evaluate(() => window.__state[window.__state.length - 1] !== "none"), true);
    // A natural advance must not do this: it already works on a latched head unit. The
    // republish milestones still assign "playing", so the assertion is that "none" is
    // never claimed — not that nothing is written.
    await page.evaluate(() => { window.__state.length = 0; stepTrack(1, "auto"); });
    await page.waitForTimeout(150);
    check("a natural advance never declares the boundary",
      await page.evaluate(() => window.__state.includes("none")), false);
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("media session ok");
})();