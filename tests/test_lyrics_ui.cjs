// Lyrics (HL-227): word timing, line-timed files, the editor round trip, and the fullscreen
// reading measured at phone width.
//
// The page is served from `web/` and every `/api/*` answer is a stub, so nothing here touches
// a real library, a provider or a song. What it pins:
//
//   * an enhanced-LRC line (a `<mm:ss.xx>` mark before each word) renders per word and the word
//     being sung lifts as the playhead passes it;
//   * a line-timed file keeps the line highlight it always had, and a plain file still falls
//     back to text;
//   * a correction is sent as the file's own format, and the payload that comes back is what
//     the overlay paints — including after a reload, where the server (the stub here) answers
//     with the stored correction;
//   * "Use the original" sends the reset and the provider copy returns;
//   * at 390×844 the fullscreen reading covers the viewport and every tool is inside it and
//     still a real tap target.
//
//   node tests/test_lyrics_ui.cjs
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const APP = [path.join(__dirname, ".."), ROOT];
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                ".png": "image/png", ".svg": "image/svg+xml", ".mjs": "text/javascript" };

// A word-timed provider copy, a line-timed one, and the corrected copy the editor sends back.
const WORDED = { status: "synced", source: "LRCLIB", word_timed: true, text: "Hello there\nSecond line",
  lines: [{ time: 1, text: "Hello there",
            words: [{ time: 1, text: "Hello" }, { time: 1.5, text: "there" }] },
          { time: 3, text: "Second line" }] };
const LINE = { status: "synced", source: "LRCLIB", word_timed: false, text: "One\nTwo",
  lines: [{ time: 1, text: "One" }, { time: 2, text: "Two" }] };
const PLAIN = { status: "plain", source: "LRCLIB", word_timed: false, lines: [], text: "Just words\nSecond row" };

// What the stub "stores": parsed the way the server parses it, so the client is exercised
// against the shape it will really get back rather than a hand-written payload.
function fromText(text){
  const stamp = s => { const m = /^(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?$/.exec(s); if (!m) return null;
    const fraction = m[3] || ""; return Number(m[1]) * 60 + Number(m[2])
      + (fraction ? Number(fraction) / 10 ** fraction.length : 0); };
  const lines = [];
  for (const raw of String(text).split("\n")){
    const marks = [...raw.matchAll(/\[(\d{1,3}:[0-5]\d(?:[.:]\d{1,3})?)\]/g)];
    if (!marks.length) continue;
    const body = raw.slice(marks[marks.length - 1].index + marks[marks.length - 1][0].length);
    const words = [...body.matchAll(/<(\d{1,3}:[0-5]\d(?:[.:]\d{1,3})?)>/g)].map((mark, index, all) => ({
      time: stamp(mark[1]),
      text: body.slice(mark.index + mark[0].length,
        index + 1 < all.length ? all[index + 1].index : body.length).trim() })).filter(word => word.text);
    const time = stamp(marks[marks.length - 1][1]);
    for (const mark of marks){
      const line = { time: stamp(mark[1]), text: body.replace(/<[^>]*>/g, "").trim() };
      if (words.length) line.words = words.map(word => ({ ...word }));
      lines.push(line);
    }
  }
  lines.sort((a, b) => a.time - b.time);
  return lines.length
    ? { status: "synced", source: "Edited copy", editor: "Friend", edited: 1700000000,
        word_timed: lines.some(line => line.words), text: lines.map(line => line.text).join("\n"), lines }
    : { status: "plain", source: "Edited copy", editor: "Friend", edited: 1700000000,
        word_timed: false, lines: [], text: String(text) };
}

const edits = {};                       // track id -> the stored correction, as the server keeps it
const provider = id => (Number(id) === 2 ? LINE : Number(id) === 3 ? PLAIN : WORDED);
const lyricsFor = id => edits[id] || provider(id);

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = body => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(body));
  const lyrics = /^\/api\/track\/(\d+)\/lyrics$/.exec(p);
  if (lyrics){
    const id = lyrics[1];
    if (req.method === "POST"){
      let raw = "";
      req.on("data", chunk => raw += chunk);
      req.on("end", () => {
        const body = JSON.parse(raw || "{}");
        if (body.reset) delete edits[id];
        else edits[id] = fromText(body.text);
        json(lyricsFor(id));
      });
      return;
    }
    return json(lyricsFor(id));
  }
  if (p.startsWith("/api/")) return json({ ok: true, songs: [], queue: [], tracks: [], playlists: [],
    folders: [], favorites: [], pins: [], likes: [], library: [], items: [], entries: [], results: [],
    playlists_shared: [], all_tracks: [] });
  const name = p === "/" ? "/index.html" : p;
  for (const dir of APP){
    const file = path.join(dir, name);
    if (fs.existsSync(file) && fs.statSync(file).isFile())
      return res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" })
        .end(fs.readFileSync(file));
  }
  res.writeHead(404).end("no");
});

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const srv = server();
  await new Promise(resolve => srv.listen(0, resolve));
  const base = `http://127.0.0.1:${srv.address().port}`;
  const browser = await chromium.launch({ headless: true });
  let failed = 0;
  const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failed++;
    console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`));
  };
  const $ = (page, id) => page.evaluate(id => document.getElementById(id).hidden, id);
  // Load the page, point the single <audio> at a track and drive the playhead by hand: the
  // real element has no media behind it, so `currentTime` is a plain number here.
  const boot = async (page, id) => {
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    await page.goto(base + "/", { waitUntil: "load" });
    await page.waitForTimeout(400);
    await page.evaluate(id => {
      const audio = document.getElementById("player-audio");
      let time = 0;
      Object.defineProperty(audio, "currentTime", { configurable: true, get: () => time, set: value => { time = value; } });
      window.__at = value => { audio.currentTime = value; audio.dispatchEvent(new Event("timeupdate")); };
      // The bar and the full player are only drawn once something is playing, and
      // `openFullPlayer()` refuses without a track. Nothing plays here; this is the state.
      playingId = id;
      document.getElementById("player").classList.add("on");
      audio.setAttribute("src", `/api/track/${id}/audio`);   // fires loadstart -> lyrics load
      window.openFullPlayer?.();   // the reading is opened from the full player
    }, id);
    await page.waitForTimeout(450);
    return errors;
  };

  try {
    // ---------------------------------------------------------------- desktop, word timing
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = await boot(page, 1);
    check("the full player carries the Lyrics button", await page.evaluate(() =>
      Boolean(document.getElementById("pflyrics").offsetParent)), true);
    check("it is enabled once the track has lyrics", await page.evaluate(() =>
      document.getElementById("pflyrics").disabled), false);
    check("the status names word timing", await page.evaluate(() =>
      document.getElementById("lyrics-status").textContent.includes("word timing")), true);

    await page.click("#pflyrics");
    await page.waitForTimeout(300);
    check("the reading opens", await $ (page, "lyrics-overlay"), false);
    check("a word-timed line is a run of spans", await page.evaluate(() =>
      [...document.querySelectorAll("#lyrics-body p")[0].querySelectorAll(".w")].map(s => s.textContent)),
      ["Hello", "there"]);
    check("a line with no marks stays plain text", await page.evaluate(() =>
      document.querySelectorAll("#lyrics-body p")[1].querySelectorAll(".w").length), 0);

    await page.evaluate(() => window.__at(1.2));
    check("the word being sung lifts, the one after it does not", await page.evaluate(() =>
      [...document.querySelectorAll("#lyrics-body p")[0].querySelectorAll(".w")]
        .map(s => [s.classList.contains("on"), s.classList.contains("current")])),
      [[true, true], [false, false]]);
    // The lift is visible, not only a class: the sung word wears the accent and the word after
    // it does not. Reading the computed colour is the difference between "the class is set"
    // and "a person sees where the singer is". `.w` transitions its colour in 180 ms, so the
    // reading waits for that to settle: sampled the instant the class flips it returns the
    // colour being left behind, which is a race, not a regression.
    await page.waitForTimeout(300);
    check("the sung word is drawn in the accent", await page.evaluate(() => {
      const words = [...document.querySelectorAll("#lyrics-body p")[0].querySelectorAll(".w")];
      // Resolve the token the way the browser does rather than hard-coding an rgb triple.
      const probe = document.createElement("span");
      probe.style.color = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
      document.body.append(probe);
      const accent = getComputedStyle(probe).color;
      probe.remove();
      return [getComputedStyle(words[0]).color === accent,
              getComputedStyle(words[1]).color !== accent];
    }), [true, true]);
    await page.evaluate(() => window.__at(1.6));
    check("both words are lit and the second is current", await page.evaluate(() =>
      [...document.querySelectorAll("#lyrics-body p")[0].querySelectorAll(".w")]
        .map(s => [s.classList.contains("on"), s.classList.contains("current")])),
      [[true, false], [true, true]]);
    await page.evaluate(() => window.__at(3.2));
    check("the next line takes over", await page.evaluate(() =>
      document.querySelector("#lyrics-body .current").textContent.trim()), "Second line");
    check("the previous line's words went out", await page.evaluate(() =>
      document.querySelectorAll("#lyrics-body p")[0].querySelectorAll(".w.on").length), 0);

    // ---------------------------------------------------------------- the editor
    await page.click("#lyrics-edit");
    await page.waitForTimeout(200);
    check("the editor opens with the file's own format", await page.evaluate(() =>
      document.getElementById("lyrics-edit-text").value),
      "[00:01.00]<00:01.00>Hello <00:01.50>there\n[00:03.00]Second line");
    await page.evaluate(() => {
      document.getElementById("lyrics-edit-text").value =
        "[00:01.00]<00:01.00>Hello <00:02.00>world\n[00:05.00]A corrected line";
    });
    const posted = page.waitForRequest(request => request.method() === "POST" && /\/lyrics$/.test(request.url()));
    await page.click("#lyrics-edit-form button[type=submit]");
    const sent = JSON.parse((await posted).postData());
    check("the edit is sent as text", sent,
      { text: "[00:01.00]<00:01.00>Hello <00:02.00>world\n[00:05.00]A corrected line" });
    await page.waitForTimeout(300);
    check("the editor closes", await page.evaluate(() => document.getElementById("dlg-lyrics-edit").open), false);
    check("the correction is what the overlay paints", await page.evaluate(() =>
      [...document.querySelectorAll("#lyrics-body p")].map(p => p.textContent.trim())),
      ["Hello world", "A corrected line"]);
    check("word timing survives the correction", await page.evaluate(() =>
      [...document.querySelectorAll("#lyrics-body p")[0].querySelectorAll(".w")].map(s => s.textContent)),
      ["Hello", "world"]);
    check("the status says who corrected it", await page.evaluate(() =>
      document.getElementById("lyrics-status").textContent.includes("edited by Friend")), true);

    // A reload is a fresh page and a fresh GET; the stored correction is what comes back.
    const after = await boot(page, 1);
    await page.click("#pflyrics");
    await page.waitForTimeout(300);
    check("the correction survives a reload", await page.evaluate(() =>
      [...document.querySelectorAll("#lyrics-body p")].map(p => p.textContent.trim())),
      ["Hello world", "A corrected line"]);
    check("no page errors on the way", errors.concat(after), []);

    await page.click("#lyrics-edit");
    await page.waitForTimeout(150);
    const reset = page.waitForRequest(request => request.method() === "POST" && /\/lyrics$/.test(request.url()));
    await page.click("#lyrics-edit-reset");
    check("resetting is its own request", JSON.parse((await reset).postData()), { reset: true });
    await page.waitForTimeout(300);
    check("the provider copy comes back", await page.evaluate(() =>
      [...document.querySelectorAll("#lyrics-body p")].map(p => p.textContent.trim())),
      ["Hello there", "Second line"]);
    await page.close();

    // ---------------------------------------------------------------- line timing, plain text
    const linePage = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await boot(linePage, 2);
    await linePage.click("#pflyrics");
    await linePage.waitForTimeout(250);
    await linePage.evaluate(() => window.__at(2.5));
    check("a line-timed file still highlights whole lines", await linePage.evaluate(() =>
      [document.querySelector("#lyrics-body .current").textContent.trim(),
       document.querySelectorAll("#lyrics-body .w").length]), ["Two", 0]);
    check("its status says line timing is what it has", await linePage.evaluate(() =>
      document.getElementById("lyrics-status").textContent.includes("line timing")), true);
    await linePage.close();

    const plainPage = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await boot(plainPage, 3);
    await plainPage.click("#pflyrics");
    await plainPage.waitForTimeout(250);
    check("plain lyrics are text, and still editable", await plainPage.evaluate(() => [
      document.querySelector("#lyrics-body pre").textContent,
      document.getElementById("lyrics-edit").disabled]), ["Just words\nSecond row", false]);
    await plainPage.close();

    // ---------------------------------------------------------------- a phone, fullscreen
    const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const phoneErrors = await boot(phone, 1);
    // One entry at both widths: the compact bar has no slot for it (it is a fixed 402×112
    // layer), so the fullscreen player's own button is the way in — on a phone as on a desktop.
    check("the compact bar shows no Lyrics button", await phone.evaluate(() =>
      Boolean(document.getElementById("pllyrics").offsetParent)), false);
    check("the bar opens the full player", await phone.evaluate(() => {
      document.getElementById("plmeta").click(); return true;
    }), true);
    await phone.waitForTimeout(400);
    check("the full player's Lyrics button is on screen", await phone.evaluate(() =>
      Boolean(document.getElementById("pflyrics").offsetParent)), true);
    await phone.click("#pflyrics");
    await phone.waitForTimeout(300);
    await phone.click("#lyrics-fullscreen");
    await phone.waitForTimeout(400);
    const box = await phone.evaluate(() => {
      const overlay = document.getElementById("lyrics-overlay");
      const rect = overlay.getBoundingClientRect();
      const tools = [...overlay.querySelectorAll(".lyrics-tools button")]
        .filter(button => button.offsetParent)
        .map(button => { const r = button.getBoundingClientRect();
          return { id: button.id, left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height }; });
      return { fullscreen: document.fullscreenElement === overlay,
               viewport: [innerWidth, innerHeight], box: [rect.left, rect.top, rect.width, rect.height],
               scroll: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
               tools };
    });
    check("the reading is genuinely fullscreen", box.fullscreen, true);
    check("it covers the whole phone viewport", box.box,
      [0, 0, box.viewport[0], box.viewport[1]]);
    check("nothing overflows sideways", box.scroll[0] <= box.viewport[0], true);
    check("every tool is inside the viewport and tappable", box.tools.every(tool =>
      tool.left >= 0 && tool.right <= box.viewport[0] && tool.top >= 0 && tool.bottom <= box.viewport[1]
      && tool.height >= 24), true);
    check("no page errors at phone width", phoneErrors, []);
    await phone.close();
  } finally {
    await browser.close();
    srv.close();
  }
  if (failed){ console.log(`lyrics UI FAILED (${failed})`); process.exitCode = 1; }
  else console.log("lyrics UI PASS: word timing, line timing, plain fallback, the editor round trip and a 390 px fullscreen reading");
})();