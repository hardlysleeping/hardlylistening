// Swipe gestures on the player: up opens, down closes, left/right change track — and
// only when the gesture really travelled.
const http = require("http"), fs = require("fs"), path = require("path");
const { chromium } = require("./_pw.cjs");
const WEB = path.join(__dirname, "..", "web");
const types = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml" };
const server = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
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
const check = (ok, what) => { if (ok) console.log("ok   " + what); else { failures++; console.log("FAIL " + what); } };
(async () => {
  await new Promise(r => server.listen(8487, r));
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const W = 390, H = 844;
  const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1, hasTouch: true, isMobile: true });
  const errs = []; page.on("pageerror", e => errs.push(String(e)));
  await page.goto("http://127.0.0.1:8487/", { waitUntil: "load" });
  await page.waitForTimeout(600);
  await page.evaluate(async () => {
    await loadLibrarySnapshot().catch(() => {});
    const made = Array.from({ length: 3 }, (_, i) => ({ id: 900 + i, title: "Track " + i, artist_name: "A", album: "Al",
      duration: 200, playable: true, artwork: "", playlists: [], album_id: "a".repeat(64), status: "ok", file: "x.flac" }));
    lib = made; libAll = made; currentRows = made; libQueue = made; libIndex = 0;
    window.__steps = [];
    const wrap = (name) => { const orig = window[name]; window[name] = (...a) => { window.__steps.push(name); return orig(...a); }; };
    wrap("playerNext"); wrap("playerPrev");
    playFromLibrary(900);
    await new Promise(r => setTimeout(r, 200));
  });

  // A pointer swipe from (x0,y0) by (dx,dy) in `steps` moves, finger down first.
  const swipe = async (sel, dx, dy, steps = 12) => {
    const box = await (await page.$(sel)).boundingBox();
    const x0 = box.x + box.width / 2, y0 = box.y + box.height / 2;
    await page.evaluate(async ({ x0, y0, dx, dy, steps }) => {
      const el = document.elementFromPoint(x0, y0) || document.body;
      const target = el.closest("#player-full") || el.closest("#player") || el;
      const send = (type, x, y) => target.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, isPrimary: true, pointerId: 5, pointerType: "touch", button: 0, buttons: 1, clientX: x, clientY: y }));
      send("pointerdown", x0, y0);
      for (let i = 1; i <= steps; i++) { send("pointermove", x0 + dx * i / steps, y0 + dy * i / steps); await new Promise(r => setTimeout(r, 8)); }
      send("pointerup", x0 + dx, y0 + dy);
    }, { x0, y0, dx, dy, steps });
    await page.waitForTimeout(120);
  };
  const state = () => page.evaluate(() => ({ open: fullPlayerOpen(), steps: window.__steps.slice() }));

  // 1. A short flick on the bar must NOT open the popup.
  await swipe("#player", 0, -60);
  check(!(await state()).open, "a short flick up on the bar does nothing");

  // 2. A long, deliberate swipe up the height of the screen opens it.
  await swipe("#player", 0, -H * 0.5);
  check((await state()).open, "a screen-length swipe up on the bar opens the popup");

  // 3. A short flick down must NOT close it.
  await swipe("#player-full", 0, 80);
  check((await state()).open, "a short flick down in the popup does nothing");

  // 4. A diagonal smear that is not one axis must not change track.
  await swipe("#player-full", -140, 120);
  check((await state()).steps.length === 0, "a diagonal gesture changes nothing");

  // 5. Left skips forward, right skips back, both needing most of the screen.
  await swipe("#player-full", -W * 0.45, 0);
  let s = await state();
  check(s.steps.includes("playerNext"), "swipe left skips to the next track, got " + JSON.stringify(s.steps));
  await swipe("#player-full", W * 0.45, 0);
  s = await state();
  check(s.steps.filter(x => x === "playerPrev").length === 1, "swipe right goes back, got " + JSON.stringify(s.steps));

  // 6. A third of the screen is not enough for a skip.
  const before = (await state()).steps.length;
  await swipe("#player-full", -W * 0.28, 0);
  check((await state()).steps.length === before, "a swipe under the threshold does not skip");

  // 7. Down over the full height closes; and the click that follows is swallowed.
  await swipe("#player-full", 0, H * 0.45);
  check(!(await state()).open, "a screen-length swipe down closes the popup");
  const shield = await page.evaluate(() => hlSwipeShieldedUntil > performance.now());
  check(shield, "the click that follows a swipe is swallowed");

  // 8. A gesture that starts on a control never counts (button / seek / record / rail).
  await page.evaluate(() => openFullPlayer());
  await page.waitForTimeout(150);
  for (const sel of ["#pfplay", "#pfseek", "#pf-disc", ".pf-vol"]) {
    const handle = await page.$(sel);
    const box = handle ? await handle.boundingBox() : null;
    if (!box) { check(false, sel + " is on screen for the control-swipe check"); continue; }
    await page.evaluate(async ({ box }) => {
      // Dispatch on the control itself, not its container: the gesture must be judged by
      // what the finger landed on, which is the whole point of this check.
      const el = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      const send = (type, x, y) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, isPrimary: true, pointerId: 9, pointerType: "touch", button: 0, buttons: 1, clientX: x, clientY: y }));
      const x0 = box.x + box.width / 2, y0 = box.y + box.height / 2;
      send("pointerdown", x0, y0);
      for (let i = 1; i <= 10; i++) { send("pointermove", x0, y0 + 40 * i); await new Promise(r => setTimeout(r, 8)); }
      send("pointerup", x0, y0 + 400);
    }, { box });
    await page.waitForTimeout(100);
  }
  check((await state()).open, "swipes that start on a control are ignored");
  check(errs.length === 0, "no page errors " + JSON.stringify(errs.slice(0, 2)));
  await page.screenshot({ path: "/tmp/hl-swipes.png" });
  console.log(failures ? failures + " FAILED" : "Player swipes: long gestures only, up/down open+close, left/right change track, controls untouched PASS");
  process.exitCode = failures ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; })
  .finally(() => setTimeout(() => process.exit(process.exitCode || 0), 200));
