// The offline app shell (HL-309): the page the worker kept and the last library listing, so
// an installed app opens with no network instead of the "you're offline" note.
//
// What this pins:
//   * the entry page is kept only when it is the app (the marker on <html>) — a sign-in page
//     is a 200 as well, and must never overwrite the shell;
//   * /api/library is kept, and only that API;
//   * with the network genuinely off, the page still paints the last library and says so.
//
// Everything runs against a local server and a two-track fixture; no real library is touched.
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const ROOT = path.join(__dirname, "..", "web");
const APP = [path.join(__dirname, ".."), ROOT];
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                ".png": "image/png", ".svg": "image/svg+xml" };

const TRACKS = [
  { id: 1, title: "Kept Song", artist_name: "Artist", album: "Album", duration: 180,
    artwork: "", playable: true, status: "done", file: "/api/track/1/audio", votes: 0 },
  { id: 2, title: "Also Kept", artist_name: "Artist", album: "Album", duration: 200,
    artwork: "", playable: true, status: "done", file: "/api/track/2/audio", votes: 0 },
];

// `login` swaps the entry page for a page that is not the app, like a sign-in form.
const login = { on: false };

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = body => res.writeHead(200, { "content-type": "application/json" })
    .end(JSON.stringify(body));
  if (p === "/api/state") return json({ viewer: { is_admin: false, role: "member",
      auth_required: false, display_name: "Member" },
    stats: { done: 2, queued: 0, review: 0, failed: 0, feed_new: 0 }, playlists: [], logs: [],
    subscriptions: [], current: null });
  if (p === "/api/library") return json({ tracks: TRACKS, all_tracks: TRACKS,
    playlists: [], folders: [], artist_art: {} });
  if (p.startsWith("/api/")) return json({ ok: true, items: [], entries: [], results: [],
    tracks: [], playlists: [], folders: [], favorites: [], pins: [], album_likes: [],
    songs: [], queue: [], library: [] });
  if (p === "/" && login.on) {
    return res.writeHead(200, { "content-type": "text/html" })
      .end("<!doctype html><html lang=\"en\"><body><h1>Sign in</h1></body></html>");
  }
  const name = p === "/" ? "/index.html" : p;
  for (const dir of APP) {
    const f = path.join(dir, name);
    if (fs.existsSync(f) && fs.statSync(f).isFile())
      return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" })
        .end(fs.readFileSync(f));
  }
  res.writeHead(404).end("no");
});

let failures = 0;
const check = (name, ok, detail) => {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
};

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(String(e).slice(0, 200)));

    await page.goto(base + "/", { waitUntil: "load" });
    await page.evaluate(() => navigator.serviceWorker.ready);
    // The shell is written on a navigation the worker handles; the first load is not
    // controlled yet, so this reload is the one that stores it.
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(600);

    const kept = await page.evaluate(async () => {
      const shell = await caches.match("/");
      const library = await caches.match("/api/library");
      const state = await caches.match("/api/state");
      return {
        shell: shell ? await shell.text() : "",
        library: library ? await library.text() : "",
        state: Boolean(state),
      };
    });
    check("the entry page is kept", kept.shell.includes("data-hl-shell"), kept.shell.slice(0, 80));
    check("the library listing is kept", kept.library.includes("Kept Song"));
    check("other API responses are not kept", kept.state === false);

    // Genuinely offline: no network at all, and the page still has to open and paint.
    await context.setOffline(true);
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(800);
    const painted = await page.evaluate(() => ({
      shelf: !document.getElementById("shelfmode").hidden,
      rows: document.querySelectorAll("#liblist .songrow, #liblist .songcard, #liblist .strow").length,
      text: document.getElementById("liblist").textContent,
      title: document.title,
    }));
    check("offline: the app opens", painted.title.includes("HardlyListening"), painted.title);
    check("offline: it says it is offline", painted.shelf === true);
    check("offline: the last library is painted", /Kept Song|Also Kept/.test(painted.text),
      painted.text.slice(0, 120));
    check("offline: no page errors", errors.length === 0, errors.join(" | "));

    // And the reason the marker exists: a sign-in page is a 200, and must not replace the
    // shell. After a "login" response the kept copy is still the app.
    await context.setOffline(false);
    login.on = true;
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(400);
    const afterLogin = await page.evaluate(async () => {
      const shell = await caches.match("/");
      return shell ? await shell.text() : "";
    });
    check("a sign-in page never overwrites the shell", afterLogin.includes("data-hl-shell"),
      afterLogin.slice(0, 80));
    await context.close();
  } finally {
    await browser.close();
    srv.close();
  }
  console.log(failures ? `FAIL ${failures} check(s)` : "Offline shell PASS: entry page + last library open with no network");
  process.exit(failures ? 1 : 0);
})();