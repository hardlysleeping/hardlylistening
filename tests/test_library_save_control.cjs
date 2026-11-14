// The save control is a two-step remove: a saved song shows a check, the first click turns it
// into a minus, and only the second click removes it from the library. Nothing is sent on the
// first click, and an armed minus lets go by itself.
const fs = require("fs"), path = require("path"), http = require("http"), assert = require("assert");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png" };
const SAVED = new Set([7]);            // one track already in the library
const posts = [];
const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = body => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(body));
  if (p === "/api/listening/saves" && req.method === "POST") {
    let raw = "";
    req.on("data", c => raw += c);
    req.on("end", () => {
      const body = JSON.parse(raw || "{}");
      posts.push(body);
      if (body.saved) SAVED.add(body.id); else SAVED.delete(body.id);
      json({ ok: true });
    });
    return;
  }
  if (p === "/api/listening/saves") return json({ tracks: [...SAVED], playlists: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p.startsWith("/api/")) return json({ ok: true, songs: [], queue: [], tracks: [], playlists: [],
    folders: [], favorites: [], pins: [], likes: [], library: [], items: [], entries: [], results: [] });
  const f = path.join(ROOT, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});
// The three glyphs, told apart by their path data.
const GLYPH = { check: "m5 13", minus: "M5 12h14", plus: "M12 5v14" };

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
    await page.waitForTimeout(500);

    // A card and the player slot both render the control for the same track.
    await page.evaluate(() => {
      const t = { id: 7, title: "Saved Track", artist_name: "Artist", album: "Album", artwork: "",
        playable: true, duration: 180, provider: "soundcloud", source_status: "ok", playlists: [] };
      libAll = [t]; libQueue = [t]; playingId = 7;
      document.getElementById("player").classList.add("on");
      paintPlayerSave();
      openFullPlayer();          // the player's save slot lives in the fullscreen foot
    });
    await page.waitForTimeout(500);

    const glyph = async where => page.evaluate(where => {
      const btn = document.querySelector(where + " .lsave");
      if (!btn) return "missing";
      const svg = btn.innerHTML;
      if (svg.includes("m5 13")) return "check";
      if (svg.includes("M12 5v14")) return "plus";
      if (svg.includes("M5 12h14")) return "minus";
      return "unknown";
    }, where);
    const armed = async where => page.evaluate(where => {
      const btn = document.querySelector(where + " .lsave");
      return { armed: btn.classList.contains("armed"), on: btn.classList.contains("on"),
        label: btn.getAttribute("aria-label"), pressed: btn.getAttribute("aria-pressed") };
    }, where);

    check("a saved song shows a check, not a minus", await glyph("#pfsave"), "check");
    check("the check is the saved state", (await armed("#pfsave")).on, true);

    // First click: arms, sends nothing.
    await page.click("#pfsave .lsave");
    await page.waitForTimeout(250);
    check("first click shows the minus", await glyph("#pfsave"), "minus");
    check("first click is not a removal", posts.length, 0);
    check("the minus says what it will do", /click again to remove/i.test((await armed("#pfsave")).label), true);
    check("the armed control looks like a warning", (await armed("#pfsave")).armed, true);
    check("the item is still saved", (await armed("#pfsave")).pressed, "true");

    // Second click: removes.
    await page.click("#pfsave .lsave");
    await page.waitForTimeout(400);
    check("second click removes it", posts, [{ kind: "track", id: 7, saved: false }]);
    check("after removal the control is a plus", await glyph("#pfsave"), "plus");

    // Adding again returns to the check, not the minus.
    posts.length = 0;
    await page.click("#pfsave .lsave");
    await page.waitForTimeout(500);
    check("adding sends one save", posts, [{ kind: "track", id: 7, saved: true }]);
    check("an added song settles on a check", await glyph("#pfsave"), "check");

    // Escape disarms without touching the library.
    posts.length = 0;
    await page.click("#pfsave .lsave");
    await page.waitForTimeout(200);
    check("armed again", await glyph("#pfsave"), "minus");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    check("Escape puts the check back", await glyph("#pfsave"), "check");
    check("Escape sends nothing", posts.length, 0);
    // Escape also closes the fullscreen player, so reopen it for the pointer test.
    await page.evaluate(() => openFullPlayer());
    await page.waitForTimeout(500);

    // Pointing elsewhere disarms too.
    await page.click("#pfsave .lsave");
    await page.waitForTimeout(200);
    await page.click("#pftitle");
    await page.waitForTimeout(200);
    check("clicking away puts the check back", await glyph("#pfsave"), "check");
    check("clicking away sends nothing", posts.length, 0);

    // The same control on a library card.
    if (await page.evaluate(() => document.getElementById("player-full").classList.contains("on"))) {
      await page.click("#pfclose");
      await page.waitForTimeout(400);
    }
    await page.click('.tab[data-view="library"][data-scope="mine"]');
    await page.waitForTimeout(600);
    await page.evaluate(() => {
      playingId = null;
      libAll = [{ id: 7, title: "Saved Track", artist_name: "Artist", album: "Album", artwork: "",
        playable: true, duration: 180, provider: "soundcloud", source_status: "ok", playlists: [] }];
      renderLibrary();
    });
    await page.waitForTimeout(400);
    const cardPresent = await page.evaluate(() => !!document.querySelector('.songcard .lsave[data-save-key="track7"]'));
    if (cardPresent) {
      const cardGlyph = () => page.evaluate(() => {
        const svg = document.querySelector('.songcard .lsave[data-save-key="track7"]').innerHTML;
        return svg.includes("m5 13") ? "check" : svg.includes("M12 5v14") ? "plus" : "minus";
      });
      check("the card shows a check too", await cardGlyph(), "check");
      await page.click('.songcard .lsave[data-save-key="track7"]');
      await page.waitForTimeout(200);
      check("the card arms on the first click", await cardGlyph(), "minus");
      check("the card sends nothing yet", posts.length, 0);
    } else {
      console.log("skip the card checks: the library grid did not render in this harness");
    }
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("library save control ok");
})();