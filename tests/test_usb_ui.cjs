// The export dialog's drive half (HL-228): plan, refuse, copy, report.
//
// The page is served from `web/` and every `/api/*` answer is a stub, so nothing here touches
// a real drive, a real library or a real file. What it pins:
//
//   * the dialog is reachable — the More menu's "Send to a DJ app" opens it, and filling
//     `#expscope` happens on open rather than at parse time;
//   * Check is a read: it sends the folder and the scope and paints the answer, and it does
//     not submit the dialog's form (a `method="dialog"` submit would close it);
//   * a refused folder is a refusal with the reason, with no way to start a copy;
//   * a set that does not fit names what stays behind, and Copy stays hidden;
//   * a copy that fits reports progress from the job the server queued, then what happened —
//     and the controls come back when it ends;
//   * changing the scope or the folder drops a plan that no longer describes the work;
//   * at 390×844 every control in the dialog is inside the viewport and a real tap target.
//
//   node tests/test_usb_ui.cjs
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const ROOT = path.join(__dirname, "..", "web");
const APP = [path.join(__dirname, ".."), ROOT];
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                ".png": "image/png", ".svg": "image/svg+xml", ".mjs": "text/javascript" };

const FOLDER = "/HardlyListening";
const base = destination => ({
  destination, folder: destination + FOLDER, playlist: null, title: "HardlyListening library",
  tracks: 0, bytes: 0, free: 0, fits: false, blocked: "",
  copy: [], convert: [], missing: [], overflow: [], cues: 0, bpm: 0,
});

function plan(destination, playlist){
  if (!destination){
    return { ...base(""), blocked: "Give the folder the drive is mounted at." };
  }
  if (destination.includes("SMALL")){
    return { ...base(destination), tracks: 12, bytes: 5e9, free: 1e9, playlist,
             overflow: [{ name: "A - One.mp3" }, { name: "A - Two.mp3" }, { name: "B - Three.mp3" }] };
  }
  if (destination.includes("NOPE")){
    return { ...base(destination), blocked: `There is no folder at ${destination}.`, playlist };
  }
  return { ...base(destination), tracks: 3, bytes: 3145728, free: 8589934592, fits: true,
           playlist, cues: 2, bpm: 2,
           convert: [{ name: "Someone - Lossless.mp3", estimate: true }],
           missing: [{ title: "Deleted" }] };
}

const state = { plans: [], starts: [], polls: 0, jobs: [], next: 1 };

const server = () => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = body => res.writeHead(200, { "content-type": "application/json" })
    .end(JSON.stringify(body));
  const read = then => {
    let raw = "";
    req.on("data", chunk => raw += chunk);
    req.on("end", () => then(JSON.parse(raw || "{}")));
  };
  if (p === "/api/library/usb/plan"){
    return read(body => {
      state.plans.push(body);
      json(plan(body.destination, body.playlist ?? null));
    });
  }
  if (p === "/api/library/usb/start"){
    return read(body => {
      state.starts.push(body);
      const job = { id: state.next++, destination: body.destination, tracks: 3, copied: 0,
                    percent: 0, status: "copying", error: "", files: [],
                    plan: plan(body.destination, body.playlist ?? null) };
      state.jobs.push(job);
      json(job);
    });
  }
  if (p === "/api/library/usb"){
    state.polls++;
    const job = state.jobs[state.jobs.length - 1];
    if (job && job.status === "copying"){
      job.copied = Math.min(job.tracks, job.copied + 2);
      job.percent = Math.round(100 * job.copied / job.tracks);
      if (job.copied >= job.tracks){
        job.status = "finished";
        job.error = "";
      }
    }
    return json({ jobs: job ? [job] : [], worker: { running: false } });
  }
  if (p === "/api/library"){
    return json({ tracks: [], all_tracks: [], playlists: [{ id: 7, title: "Friday set" }],
                  artist_art: {}, folders: [] });
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

(async () => {
  const srv = server();
  await new Promise(resolve => srv.listen(0, resolve));
  const origin = `http://127.0.0.1:${srv.address().port}`;
  const browser = await chromium.launch({ headless: true });
  let failed = 0;
  const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) failed++;
    console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`));
  };
  const text = (page, id) => page.evaluate(id => (document.getElementById(id).textContent || "").trim(), id);
  const shown = (page, id) => page.evaluate(id => !document.getElementById(id).hidden, id);
  const open = async page => {
    await page.goto(origin + "/", { waitUntil: "load" });
    await page.waitForTimeout(400);
    await page.evaluate(() => document.getElementById("btn-more").click());
    await page.click("#djexportbtn");
    await page.waitForTimeout(150);
  };

  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    await open(page);

    // ---- the dialog is reachable, and it is the one with the export fields
    check("the More menu's item opens the export dialog", await page.evaluate(() =>
      document.getElementById("dlg-export").open), true);
    check("its scope lists the library's playlists", await page.evaluate(() =>
      [...document.getElementById("expscope").options].map(o => o.textContent)),
      ["Whole library", "Friday set"]);
    check("the drive plan starts empty", await shown(page, "usbplan"), false);
    check("and Copy is not offered before a check", await shown(page, "usbgo"), false);

    // ---- Check is a read, and it does not close the dialog
    await page.click("#usbcheck");
    await page.waitForTimeout(200);
    check("an empty folder is refused with the reason",
      await text(page, "usbplan"), "Give the folder the drive is mounted at.");
    check("the refusal carries no Copy button", await shown(page, "usbgo"), false);
    check("Check did not submit the form and close the dialog", await page.evaluate(() =>
      document.getElementById("dlg-export").open), true);
    check("the plan was asked for with the empty folder and no scope",
      state.plans[state.plans.length - 1], { destination: "", playlist: null });

    // ---- a folder that is not there
    await page.fill("#usbpath", "/run/NOPE");
    await page.click("#usbcheck");
    await page.waitForTimeout(200);
    check("a folder that is not there is refused, naming it",
      await text(page, "usbplan"), "There is no folder at /run/NOPE.");

    // ---- a set that does not fit
    await page.fill("#usbpath", "/run/SMALL");
    await page.click("#usbcheck");
    await page.waitForTimeout(200);
    const small = await text(page, "usbplan");
    check("a set that does not fit says so", small.includes("does not fit"), true);
    check("and names what stays behind", small.includes("A - One.mp3, A - Two.mp3, B - Three.mp3"), true);
    check("with no Copy button", await shown(page, "usbgo"), false);

    // ---- a copy that fits
    await page.fill("#usbpath", "/run/BIG");
    await page.click("#usbcheck");
    await page.waitForTimeout(200);
    const big = await text(page, "usbplan");
    check("a set that fits reports the size and the free space",
      big.includes("3 tracks, 3.0 MB") && big.includes("fits"), true);
    check("it names the conversion and the estimate",
      big.includes("1 will be converted to MP3 first"), true);
    check("it names the file that is missing", big.includes("1 file is missing on this machine"), true);
    check("it says what the XML carries", big.includes("2 hot cues and 2 tracks with a tempo"), true);
    check("it names the folder on the drive", big.includes("/run/BIG/HardlyListening"), true);
    check("Copy is offered now", await shown(page, "usbgo"), true);

    // ---- the copy itself
    await page.click("#usbgo");
    await page.waitForTimeout(200);
    check("starting sends the folder", state.starts[0].destination, "/run/BIG");
    const during = await text(page, "usbplan");
    check("progress comes from the queued job", /Copying \d of 3/.test(during), true);
    check("Copy is out of the way while it runs", await shown(page, "usbgo"), false);
    await page.waitForTimeout(2600);
    check("the status was polled more than once", state.polls > 2, true);
    const lines = await page.evaluate(() =>
      [...document.querySelectorAll("#usbplan p")].map(p => p.textContent.trim()));
    check("the ending is reported, not assumed", lines[0], "Copied 3 of 3 tracks to /run/BIG.");
    check("it names the files to open on the drive",
      lines[1], "Open HardlyListening.rekordbox.xml or HardlyListening.m3u8 from the drive's root.");
    check("Copy comes back when it is done", await page.evaluate(() =>
      !document.getElementById("usbgo").hidden && !document.getElementById("usbgo").disabled), true);

    // ---- a plan for one scope does not survive a different one
    await page.fill("#usbpath", "/run/BIG");
    await page.click("#usbcheck");
    await page.waitForTimeout(200);
    check("a fresh plan is on screen", await shown(page, "usbplan"), true);
    await page.evaluate(() => document.getElementById("expscope").dispatchEvent(new Event("change")));
    check("changing the scope drops it", await shown(page, "usbplan"), false);
    check("and takes Copy with it", await shown(page, "usbgo"), false);
    await page.click("#usbcheck");
    await page.waitForTimeout(200);
    await page.fill("#usbpath", "/run/BIG2");
    check("typing another folder drops it too", await shown(page, "usbplan"), false);
    check("no page errors", errors, []);

    // ---- the same dialog at phone width
    const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await open(phone);
    await phone.fill("#usbpath", "/run/BIG");
    await phone.click("#usbcheck");
    await phone.waitForTimeout(250);
    const boxes = await phone.evaluate(() => ["usbpath", "usbcheck", "usbgo", "expgo", "usbplan"].map(id => {
      const r = document.getElementById(id).getBoundingClientRect();
      return { id, left: Math.round(r.left), right: Math.round(r.right),
               top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height) };
    }));
    const wide = boxes.filter(box => box.left < 0 || box.right > 390);
    check("every control is inside the viewport at 390", wide, []);
    check("no control is below the fold", boxes.filter(box => box.bottom > 844), []);
    check("the buttons are still real tap targets",
      boxes.filter(box => ["usbcheck", "usbgo"].includes(box.id)).map(box => box.h >= 28), [true, true]);
    check("the plan is readable, not clipped", boxes.find(box => box.id === "usbplan").h > 60, true);
    await phone.screenshot({ path: "/tmp/opencode/usb-dialog-390.png" });
    await page.screenshot({ path: "/tmp/opencode/usb-dialog-1280.png" });
  } finally {
    await browser.close();
    srv.close();
  }
  console.log(failed ? `${failed} check(s) failed` : "all checks passed");
  process.exit(failed ? 1 : 0);
})();