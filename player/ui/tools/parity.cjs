// Player parity: prove the ported surfaces are the same surfaces.
//
//   node tools/parity.cjs            compare against tests/visual/baseline/*.png
//   node tools/parity.cjs --write    write tools/actual/*.png for inspection
//
// Three surfaces, at the sizes the visual baselines cover: the compact bar (six widths), the
// fullscreen player and the fullscreen queue (390x844, 430x932, 1280x800). Each is graded three
// ways: every control both sides share is measured element for element, the pixels inside the
// surface's own rectangle are compared, and the host capture itself is checked against the
// stored baseline so the harness cannot silently drift into proving nothing.
//
// The host page is rendered with the same fixture `tests/visual/snap.cjs` uses, on the same page
// object navigated per state, so the baseline is re-proved under the conditions it was captured
// in. If the host capture no longer matches its own baseline, this harness is wrong and says so
// before it grades the port.
//
// What is excluded, by rectangle and for a stated reason:
//
//   - `#appnav` — the host's tab bar (Queue / Feed / Repo / Library) below the player. It is not
//     part of the bar and does not exist in this client, so the bar's region stops at the bar's
//     own bottom edge rather than at the bottom of the screen.
//
//   - `#plart`, `#pfart` — the fixture leaves the artwork empty. `#plart` in the baseline carries
//     the browser's broken-image glyph, which paints or does not paint depending on when `src`
//     was last assigned; `#pfart` is a record label the host hides when there is no art. Both
//     boxes are masked, but `#plartwrap` and `#pf-disc` are graded, so moved or resized artwork
//     still fails.
//
//   - `#pf-autodj`, `#pfcues`, `#pfeq`, `#pfsave`, `.pf-cues`, `#pf-eq`, `#history-details` —
//     controls that belong to the host app rather than to a Subsonic client: cue pads, the
//     equalizer, AutoDJ, the "add to library" chip and the recently-played drawer. Everything the
//     port kept still has to match pixel for pixel.
//
// `#playback-details` is compared on position and width only: the host's box also contains the
// 11 px drawer above.
const fs = require("fs");
const path = require("path");
const http = require("http");
const { chromium } = require("../../../tests/_pw.cjs");

const HERE = __dirname;
const UI = path.join(HERE, "..");
const DIST = path.join(UI, "dist");
const WEB = path.join(HERE, "..", "..", "..", "web");
const BASELINE = path.join(HERE, "..", "..", "..", "tests", "visual", "baseline");
const ACTUAL = path.join(HERE, "actual");

const TYPES = {
  ".html": "text/html",
  ".css": "text/css",
  ".js": "text/javascript",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

const STUB = {
  ok: true,
  songs: [],
  queue: [],
  tracks: [],
  playlists: [],
  folders: [],
  favorites: [],
  library: [],
};

function serve(root, { stub = false } = {}) {
  return http.createServer((req, res) => {
    const p = decodeURIComponent(req.url.split("?")[0]);
    if (stub && p.startsWith("/api/")) {
      res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(STUB));
      return;
    }
    const file = path.join(root, p === "/" ? "/index.html" : p);
    if (fs.existsSync(file) && fs.statSync(file).isFile()) {
      res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
      res.end(fs.readFileSync(file));
      return;
    }
    res.writeHead(404).end("no");
  });
}

// The host's own fixture, verbatim from tests/visual/snap.cjs, so the two captures are the same
// screen with different internals.
const HOST_FIXTURE = () => {
  const track = {
    id: 42,
    title: "Baseline Track",
    artist_name: "Artist",
    album: "Album",
    artwork: "",
    provider: "soundcloud",
    source_url: "",
  };
  try {
    libAll = [track];
  } catch (_) {}
  try {
    libQueue = [];
  } catch (_) {}
  playingId = 42;
  const art = document.getElementById("plart");
  if (art) art.src = "";
  const title = document.getElementById("pltitle");
  if (title) title.textContent = track.title;
  const sub = document.getElementById("plsub");
  if (sub) sub.textContent = "Artist · Album";
  document.getElementById("player").classList.add("on");
};

// The ported page has no globals to assign: the same text goes into the same nodes.
const PORT_FIXTURE = () => {
  const art = document.getElementById("plart");
  if (art) art.src = "";
  const title = document.getElementById("pltitle");
  if (title) title.textContent = "Baseline Track";
  const sub = document.getElementById("plsub");
  if (sub) sub.textContent = "Artist · Album";
};

const FREEZE = `(() => {
  const s = document.createElement('style');
  s.textContent = '*,*::before,*::after{animation:none!important;transition:none!important}';
  document.head.appendChild(s);
})();`;

async function diff(comparePage, a, b, region, masks) {
  return comparePage.evaluate(
    async ([a, b, region, masks]) => {
      const load = (src) =>
        new Promise((res, rej) => {
          const i = new Image();
          i.onload = () => res(i);
          i.onerror = rej;
          i.src = "data:image/png;base64," + src;
        });
      const [ia, ib] = await Promise.all([load(a), load(b)]);
      if (ia.width !== ib.width || ia.height !== ib.height) {
        return { size: true, a: [ia.width, ia.height], b: [ib.width, ib.height] };
      }
      const c = document.createElement("canvas");
      c.width = ia.width;
      c.height = ia.height;
      const g = c.getContext("2d", { willReadFrequently: true });
      g.drawImage(ia, 0, 0);
      const da = g.getImageData(0, 0, c.width, c.height).data;
      g.clearRect(0, 0, c.width, c.height);
      g.drawImage(ib, 0, 0);
      const db = g.getImageData(0, 0, c.width, c.height).data;

      const x0r = region ? Math.max(0, Math.floor(region[0])) : 0;
      const y0r = region ? Math.max(0, Math.floor(region[1])) : 0;
      const x1r = region ? Math.min(c.width - 1, Math.ceil(region[2])) : c.width - 1;
      const y1r = region ? Math.min(c.height - 1, Math.ceil(region[3])) : c.height - 1;
      const holes = (masks || []).map(([mx0, my0, mx1, my1]) => [mx0, my0, mx1, my1]);
      let n = 0;
      let x0 = 1e9;
      let y0min = 1e9;
      let x1 = -1;
      let y1 = -1;
      for (let i = 0; i < da.length; i += 4) {
        const pixel = i / 4;
        const y = (pixel / c.width) | 0;
        if (y < y0r || y > y1r) continue;
        const x = pixel % c.width;
        if (x < x0r || x > x1r) continue;
        if (holes.some(([mx0, my0, mx1, my1]) => x >= mx0 && x <= mx1 && y >= my0 && y <= my1)) continue;
        if (da[i] !== db[i] || da[i + 1] !== db[i + 1] || da[i + 2] !== db[i + 2]) {
          n++;
          if (x < x0) x0 = x;
          if (y < y0min) y0min = y;
          if (x > x1) x1 = x;
          if (y > y1) y1 = y;
        }
      }
      return { pixels: n, box: n ? [x0, y0min, x1, y1] : null };
    },
    [a, b, region ?? null, masks ?? []],
  );
}

(async () => {
  const write = process.argv.includes("--write");
  fs.mkdirSync(ACTUAL, { recursive: true });

  const host = serve(WEB, { stub: true });
  const port = serve(DIST);
  await new Promise((r) => host.listen(0, "127.0.0.1", r));
  await new Promise((r) => port.listen(0, "127.0.0.1", r));
  const hostUrl = `http://127.0.0.1:${host.address().port}/`;
  const portBase = `http://127.0.0.1:${port.address().port}/index.html?save=1`;
  // One preview per surface. `save=1` keeps the host's add-to-library chip present so the
  // baseline's layout is reproduced; it is off in the shipped player.
  const portUrl = (state) => `${portBase}&preview=${state}`;

  const browser = await chromium.launch({ headless: true });
  const comparePage = await browser.newPage();
  await comparePage.goto("about:blank");

  const viewports = [
    [390, 844],
    [430, 932],
    [1024, 800],
    [1280, 800],
    [1440, 900],
    [1920, 1080],
  ];

  let failures = 0;

  try {
    for (const [w, h] of viewports) {
      // The stored baselines cover the fullscreen and queue surfaces at the two phone widths and
      // one desktop width; the extra desktop widths pin the compact bar's edges only.
      const states = w === 1024 || w === 1440 || w === 1920 ? ["compact"] : ["compact", "fullscreen", "queue"];

      // One page per side and per viewport, navigated for each state, exactly as
      // `tests/visual/snap.cjs` captures the baselines. A fresh page for every state lays the
      // queue's subtitle out a subpixel differently, which is enough to miss a pinned baseline.
      const hostPage = await browser.newPage({ viewport: { width: w, height: h } });
      const portPage = await browser.newPage({ viewport: { width: w, height: h } });

      for (const state of states) {
        const name = `${state}-${w}x${h}.png`;
        const baselineFile = path.join(BASELINE, name);

        // 1. The host capture. Everything is measured on the same page that was photographed,
        //    because the point is to compare what was actually painted.
        const nodes =
          state === "compact"
            ? ["#player", "#plartwrap", "#plmeta", "#pctrl", ".ptrans", "#pltime", ".plwave-seek", "#plseek", "#pldur"]
            : [
                "#player-full",
                "#pf-disc",
                ".pf-text",
                "#pftitle",
                "#pfsub",
                ".pf-queue-slot",
                "#pfseek",
                ".pf-times",
                ".pf-ctrl",
                ".pf-foot",
                "#pfqueue",
                "#pflike",
                ".pf-vol",
                ...(state === "queue" ? ["#playback-panel", "#playback-details"] : []),
              ];

        const measure = (selectors) => {
          const out = {};
          for (const selector of selectors) {
            const el = document.querySelector(selector);
            if (!el) {
              out[selector] = null;
              continue;
            }
            const r = el.getBoundingClientRect();
            out[selector] = [r.x, r.y, r.width, r.height].map((v) => Math.round(v * 100) / 100);
          }
          return out;
        };

        const collectBoxes = () => {
          const rect = (selector) => {
            const el = document.querySelector(selector);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) return null;
            return [r.x, r.y, r.x + r.width, r.y + r.height];
          };
          const hostOnly = ["#pf-autodj", "#pfcues", "#pfeq", "#pfsave", ".pf-cues", "#pf-eq", "#history-details"]
            .map(rect)
            .filter(Boolean);
          return {
            bar: rect("#player"),
            grip: rect("#qgrip"),
            art: rect("#plart"),
            full: rect("#player-full"),
            discArt: rect("#pfart"),
            hostOnly,
          };
        };

        const shootHost = async () => {
          const page = hostPage;
          await page.goto(hostUrl, { waitUntil: "load" });
          await page.waitForTimeout(400);
          await page.evaluate(HOST_FIXTURE);
          await page.waitForTimeout(300);
          if (state !== "compact") {
            await page.evaluate(() => {
              if (typeof openFullPlayer === "function") openFullPlayer();
            });
            await page.waitForTimeout(300);
          }
          if (state === "queue") {
            await page.evaluate(() => {
              if (typeof showFullQueue === "function") showFullQueue(true);
            });
            await page.waitForTimeout(300);
          }
          await page.evaluate(FREEZE);
          await page.waitForTimeout(200);
          await page.evaluate(() => {
            window.requestAnimationFrame = () => 0;
          });
          await page.waitForTimeout(120);
          const shot = await page.screenshot({ animations: "disabled" });
          const geometry = await page.evaluate(measure, nodes);
          const boxes = await page.evaluate(collectBoxes);
          return { shot, geometry, boxes };
        };

        let host = await shootHost();

        // The host capture must still equal its stored baseline, or this harness proves nothing.
        // One retry, exactly as `tests/visual/snap.cjs` does it: the queue state's text can land
        // on either side of a subpixel snap, and the baseline pinned one of them.
        //
        // The host-only rectangles are masked here too. They are already excluded from grading,
        // and one of them (`#pf-autodj`, a translucent pill with a backdrop filter) repaints a
        // level darker or lighter depending on what the compositor had ready, which is not a
        // change to the host page. A `#player-full` that moved, resized or lost a control still
        // fails: everything else on the page is compared.
        if (fs.existsSync(baselineFile)) {
          const baseline = fs.readFileSync(baselineFile).toString("base64");
          let check = await diff(comparePage, host.shot.toString("base64"), baseline, null, host.boxes.hostOnly);
          if (check.pixels || check.size) {
            const retry = await shootHost();
            const again = await diff(comparePage, retry.shot.toString("base64"), baseline, null, host.boxes.hostOnly);
            if (!again.pixels && !again.size) {
              host = retry;
              check = again;
            } else if ((again.pixels ?? Infinity) < (check.pixels ?? Infinity)) {
              host = retry;
              check = again;
            }
          }
          if (check.pixels || check.size) {
            failures++;
            console.log(`HARNESS ${name}: host page differs from its own baseline (${check.pixels ?? "size"})`);
          } else {
            console.log(`ok ${name}: host baseline reproduced`);
          }
        }

        // 2. The port, photographed and measured the same way.
        const shootPort = async () => {
          const page = portPage;
          await page.goto(portUrl(state), { waitUntil: "load" });
          await page.waitForTimeout(400);
          await page.evaluate(PORT_FIXTURE);
          await page.waitForTimeout(300);
          await page.evaluate(FREEZE);
          await page.waitForTimeout(200);
          await page.evaluate(() => {
            window.requestAnimationFrame = () => 0;
          });
          await page.waitForTimeout(120);
          const shot = await page.screenshot({ animations: "disabled" });
          const geometry = await page.evaluate(measure, nodes);
          return { shot, geometry };
        };
        const port = await shootPort();

        if (write) {
          fs.writeFileSync(path.join(ACTUAL, `host-${name}`), host.shot);
          fs.writeFileSync(path.join(ACTUAL, `port-${name}`), port.shot);
        }

        // 3. Geometry of every control both surfaces share, element for element.
        //    The queue's details box also contains the host's recently-played drawer, which this
        //    client does not have: the box is 11 px shorter here. Position and width are still
        //    compared, and every child that survived is compared in full.
        const compare = (selector, box) =>
          selector === "#playback-details" && box ? JSON.stringify(box.slice(0, 3)) : JSON.stringify(box);
        for (const selector of nodes) {
          const a = compare(selector, host.geometry[selector]);
          const b = compare(selector, port.geometry[selector]);
          if (a !== b) {
            failures++;
            console.log(`GEOMETRY ${name} ${selector}: host ${a} vs port ${b}`);
          }
        }

        // 4. Pixels inside the surface's own rectangle.
        //    Rect edges are exclusive at the right and bottom; a pixel region is inclusive, so
        //    both maxima lose one. Without that, the row of whatever sits below the bar is graded.
        const span = (a, b) => [
          Math.min(a[0], b[0]),
          Math.min(a[1], b[1]),
          Math.max(a[2], b[2]) - 1,
          Math.max(a[3], b[3]) - 1,
        ];

        // The compact bar is graded together with the grip above it, and not one row below: at
        // phone widths the host's tab bar starts on the very next row. The fullscreen surface is
        // the whole overlay. The empty artwork is masked either way (see the note at the top).
        const boxes = host.boxes;
        const region =
          state === "compact"
            ? boxes.bar
              ? span(boxes.bar, boxes.grip ?? boxes.bar)
              : null
            : boxes.full;
        const artMask = state === "compact" ? boxes.art : boxes.discArt;
        const masks = artMask ? [artMask, ...(state === "compact" ? [] : boxes.hostOnly)] : boxes.hostOnly;

        const result = await diff(
          comparePage,
          host.shot.toString("base64"),
          port.shot.toString("base64"),
          region,
          masks,
        );
        if (result.size) {
          failures++;
          console.log(`SIZE ${name}: ${result.a} vs ${result.b}`);
        } else if (result.pixels) {
          failures++;
          console.log(`CHANGED ${name}: ${result.pixels} px differ in ${state}, box ${result.box}`);
        } else {
          console.log(`ok ${name}: ${state} identical (region ${region.map(Math.round).join(",")})`);
        }
      }

      await hostPage.close();
      await portPage.close();
    }
  } finally {
    await browser.close();
    host.close();
    port.close();
  }

  if (failures) {
    console.log(`FAIL ${failures} check(s)`);
    process.exitCode = 1;
  } else {
    console.log("Player parity PASS");
  }
})().catch((error) => {
  console.error("ERROR " + error.message);
  process.exitCode = 1;
});