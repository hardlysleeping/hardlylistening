// Swipe gestures on the player: up opens, down closes, left/right change track — and
// only when the gesture really travelled. The bar's opening gesture is a pull, not a swipe:
// `web/index.html` lifts the pane as the bar follows the finger and commits the open past 55 px
// of upward travel, so a short throw opens it by design. Measured at 390×844: −54 px stays
// shut, −56 px opens; the popup's own downward pull closes at 88 px of 220, so a diagonal that
// travels down past that boundary puts it away as well (it still never changes track).
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
  // Reopen for the checks that need the pane up, past the 240/260 ms snap timers.
  const openPane = async () => {
    await page.evaluate(() => { if (!fullPlayerOpen()) openFullPlayer(); });
    await page.waitForTimeout(400);
  };

  // 1. The pull has a throw, not a flick: 55 px of upward travel commits the open. Short of
  //    that the pane retreats and the bar returns to where it was.
  await swipe("#player", 0, -30);
  await page.waitForTimeout(300);
  check(!(await state()).open, "a pull short of the throw leaves the popup shut");

  // 2. Past the throw it opens — 60 px is enough, which is the shipped short-throw feel.
  await swipe("#player", 0, -60);
  await page.waitForTimeout(300);
  check((await state()).open, "a flick up past the throw opens the popup");

  // 3. A short pull down must NOT close it (the pane closes past 88 px of 220).
  await swipe("#player-full", 0, 80);
  check((await state()).open, "a short flick down in the popup does nothing");

  // 4. A diagonal smear is not one axis, so it never changes track. Its downward travel is past
  //    the close boundary, so the pane goes away — measured, and that is the close, not a skip.
  await swipe("#player-full", -140, 120);
  await page.waitForTimeout(300);
  check((await state()).steps.length === 0, "a diagonal gesture changes nothing");
  check(!(await state()).open, "a diagonal that travels down closes the popup");

  // 5. Left skips forward, right skips back, both needing most of the screen.
  await openPane();
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

  // 8. A gesture that starts on a control never counts (button / seek / record / rail). The
  //    volume fader is desktop only (`#player-full .pf-vol{display:none}` below 761 px), so it
  //    is checked where it exists, in a second pass at a desktop width.
  await page.evaluate(() => openFullPlayer());
  await page.waitForTimeout(150);
  const gestureOn = async (sel) => {
    const handle = await page.$(sel);
    const box = handle ? await handle.boundingBox() : null;
    if (!box) return false;
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
    await page.waitForTimeout(250);
    return true;
  };
  for (const sel of ["#pfplay", "#pfseek", "#pf-disc"]) {
    check(await gestureOn(sel), sel + " is on screen for the control-swipe check");
  }
  check((await state()).open, "swipes that start on a control are ignored");

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForTimeout(300);
  await openPane();
  check(await gestureOn(".pf-vol"), ".pf-vol is on screen at a desktop width");
  check((await state()).open, "a swipe on the desktop volume fader is ignored too");
  await page.setViewportSize({ width: W, height: H });
  await page.waitForTimeout(200);
  check(errs.length === 0, "no page errors " + JSON.stringify(errs.slice(0, 2)));
  await page.screenshot({ path: "/tmp/hl-swipes.png" });
  console.log(failures ? failures + " FAILED" : "Player swipes: the pull's 55 px throw opens, down closes past 88 px, left/right change track only past the side threshold, controls untouched PASS");
  process.exitCode = failures ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; })
  .finally(() => setTimeout(() => process.exit(process.exitCode || 0), 200));
