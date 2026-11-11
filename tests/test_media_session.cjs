// What the phone publishes to the lock screen and the car: title, artist, artwork and an
// album that is a real album. A SoundCloud set's name sits in `album`, so a row whose release
// is a playlist must publish no album rather than the set's name.
//
// Not covered here: what the head unit chooses to redraw. A test can only prove what the page
// publishes, which is why the republish milestones are asserted explicitly.
const fs = require("fs"), path = require("path"), http = require("http"), assert = require("assert");
const { chromium } = require("playwright");
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
          window.__meta.push(value ? { title: value.title, artist: value.artist, album: value.album,
            artwork: (value.artwork || []).length } : null);
          return desc.set.call(this, value);
        }
      });
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

    // A single with no release publishes no album; the set it came from is what the
    // player's own line names, so it never collapses to the artist alone.
    await page.evaluate(() => playFromLibrary(1));
    await page.waitForTimeout(300);
    check("a single publishes its title", (await meta()).title, "Set Song");
    check("a single publishes no album", (await meta()).album, "");
    check("the player line names the set it came from",
      await page.evaluate(() => $("#plsub").textContent), "Artist A · Shuffle");

    // A real release keeps its album.
    await page.evaluate(() => playFromLibrary(2));
    await page.waitForTimeout(300);
    check("a real album keeps its name", (await meta()).album, "Real Album");
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
    check("the player line is the artist alone", await page.evaluate(() => $("#plsub").textContent),
      "Artist C");

    // A skip publishes the new track, not the one that was playing.
    const before = await count();
    await page.evaluate(() => { libIndex = 0; playerNext(); });
    await page.waitForTimeout(300);
    check("a skip publishes a new tag", (await count()) > before, true);
    check("the tag after a skip is the new song", (await meta()).title, "Album Song");
    check("and its own album", (await meta()).album, "Real Album");

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
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("media session ok");
})();