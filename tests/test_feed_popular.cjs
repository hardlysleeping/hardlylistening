// The Feed's Popular section: the community chart, built from the same play log Activity
// lists. Two selectors, both the Repository's own row (`nav.seg`): the section, and the
// window. Nothing here invents its own lookup — the slice under test is the page's own.
const fs = require("fs"), assert = require("assert"), path = require("path"), http = require("http");
const { chromium } = require("./_pw.cjs");
const css = require("./_css.cjs");

const indexHtml = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
const source = fs.readFileSync(path.join(__dirname, "..", "web", "js", "social-feed.js"), "utf8");
assert(indexHtml.includes("/js/social-feed.js"), "the page must load the file this test slices");

// From the first line of the section to `loadActivity`, which needs the rest of the page.
const slice = source.slice(source.indexOf("// ---- Feed sections:"), source.indexOf("async function loadActivity()"));
assert(slice.includes("function loadPopular"), "the Popular section must be in the slice");
// The profile is a page inside this section, so it steps the section row aside and puts it
// back on close. Those two functions are the page's own, not re-stated here.
const profile = source.slice(source.indexOf("function memberShowView"), source.indexOf("async function openMember"));
assert(profile.includes("function memberClosePage"), "the profile open/close pair must be in the slice");

const feed = indexHtml.slice(indexHtml.indexOf('<section id="view-feed" hidden>'),
                              indexHtml.indexOf('<section id="view-library" hidden>'));
assert(feed.includes('id="feedtabs"') && feed.includes('id="feedperiods"'), "both selectors live in the Feed");
// The Feed is one of the views the app shows by switching `hidden`; here it is the only one.
const shown = feed.replace(' id="view-feed" hidden', ' id="view-feed"');

// The page's helpers the slice leans on, and a `loadActivity` that records instead of fetching.
const helpers = `
let activityLoads = 0;
async function loadActivity(){ activityLoads++; }
function esc(s){ return String(s ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;"); }
let libAll = [];
function coverArt(track){ return track.artwork || ""; }
const memberEl = id => document.getElementById(id);
`;

// A fetch that answers `/api/popular` and remembers every URL it was asked for.
const stub = `
const asked = []; let next = [];
function queue(items, delay){ next.push({ items, delay: delay || 0 }); }
window.fetch = async url => {
  asked.push(url);
  const entry = next.shift() || { items: [], delay: 0 };
  if (entry.delay) await new Promise(r => setTimeout(r, entry.delay));
  return { ok: true, status: 200, json: async () => ({ items: entry.items }) };
};
`;

// Served over http, not setContent: the Feed remembers its section in localStorage, which
// an about:blank document does not have.
const fixture = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>feed popular</title>${css.styleTag()}</head><body><main>${shown}</main></body></html>`;
const server = () => http.createServer((req, res) => {
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" }).end(fixture);
});

(async () => {
  const srv = server();
  await new Promise(r => srv.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  const checks = [];
  const check = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    checks.push(ok);
    console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`));
  };
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on("pageerror", error => { checks.push(false); console.log("FAIL pageerror: " + error.message); });
    await page.goto(`http://127.0.0.1:${srv.address().port}/`, { waitUntil: "load" });
    await page.addScriptTag({ content: `${helpers}\n${stub}\n${slice}\n${profile}` });

    // The row is the Repository's: same class, same active button.
    const row = await page.evaluate(() => {
      const tabs = document.getElementById("feedtabs");
      return {
        cls: tabs.className,
        buttons: [...tabs.querySelectorAll("button")].map(b => b.textContent.trim()),
        on: tabs.querySelector("button.on")?.textContent.trim(),
        radius: getComputedStyle(tabs).borderRadius,
        panes: [document.getElementById("feedpane-activity").hidden,
                document.getElementById("feedpane-popular").hidden],
      };
    });
    check("the section row is the shared seg row", row.cls, "seg");
    check("it offers Activity and Popular", row.buttons, ["Activity", "Popular"]);
    check("Activity is the one on screen", [row.on, row.panes], ["Activity", [false, true]]);
    check("with the Repository row's shape", row.radius, "11px");

    // Popular: a fetch for the stored window, one row per track, ranked.
    await page.evaluate(() => queue([
      { id: 1, title: "Ghosts", artist_name: "Deadmau5", plays: 20, listeners: 3, artwork: "/api/art/aa" },
      { id: 2, title: "When Im Gone", artist_name: "3 Doors Down", plays: 2, listeners: 1, artwork: "" },
    ]));
    await page.evaluate(() => setFeedTab("popular"));
    await page.waitForFunction(() => document.querySelectorAll("#popularlist .mprow").length === 2);

    const popular = await page.evaluate(() => {
      const rows = [...document.querySelectorAll("#popularlist .mprow")];
      return {
        asked: asked.slice(),
        panes: [document.getElementById("feedpane-activity").hidden,
                document.getElementById("feedpane-popular").hidden],
        stored: localStorage.getItem("hl.feedTab"),
        ranks: rows.map(r => r.querySelector(".strank").textContent),
        titles: rows.map(r => r.querySelector(".mptext b").textContent),
        subs: rows.map(r => r.querySelector(".mptext small").textContent),
        plays: rows.map(r => r.querySelector(".mpmeta").textContent),
        art: rows.map(r => (r.querySelector("img.mpart") || {}).getAttribute?.("src") || ""),
        track: rows.map(r => r.dataset.stTrack),
        label: rows[0].getAttribute("aria-label"),
      };
    });
    check("it asks the chart for today", popular.asked, ["/api/popular?period=day"]);
    check("Popular takes the pane and remembers itself", [popular.panes, popular.stored], [[true, false], "popular"]);
    check("rows are ranked in order", popular.ranks, ["1", "2"]);
    check("they name the track", popular.titles, ["Ghosts", "When Im Gone"]);
    // "1 person" on every row of a one-member library would be noise, so it is left out.
    check("the sub-line counts people only when it took more than one", popular.subs,
      ["Deadmau5 · 3 people", "3 Doors Down"]);
    check("the count is the plays", popular.plays, ["20×", "2×"]);
    check("the cover is used when there is one", popular.art, ["/api/art/aa", ""]);
    check("a row carries the track it plays", popular.track, ["1", "2"]);
    check("and its label says so", popular.label, "Play Ghosts by Deadmau5");

    // The window: the second selector is the same row, and it re-asks.
    await page.evaluate(() => queue([{ id: 1, title: "Ghosts", artist_name: "Deadmau5", plays: 41, listeners: 2 }]));
    await page.click('#feedperiods [data-period="month"]');
    await page.waitForFunction(() => document.querySelectorAll("#popularlist .mprow").length === 1);
    const month = await page.evaluate(() => ({
      asked: asked.slice(-1)[0],
      stored: localStorage.getItem("hl.chartPeriod"),
      on: document.querySelector("#feedperiods button.on").dataset.period,
      pressed: [...document.querySelectorAll("#feedperiods button")].map(b => b.getAttribute("aria-pressed")),
      plays: document.querySelector("#popularlist .mpmeta").textContent,
    }));
    check("Month asks for this month", month.asked, "/api/popular?period=month");
    check("the window is remembered", [month.stored, month.on], ["month", "month"]);
    check("exactly one window button is pressed", month.pressed,
      ["false", "false", "true", "false", "false"]);
    check("and the chart is redrawn", month.plays, "41×");

    // The two long windows are the same selector, not a second control.
    for (const [period, label] of [["year", "Year"], ["all", "All time"]]) {
      await page.evaluate(() => queue([{ id: 3, title: "Long", artist_name: "Anon", plays: 3 }]));
      await page.click(`#feedperiods [data-period="${period}"]`);
      await page.waitForFunction(() => document.querySelector("#popularlist .mprow"));
      const long = await page.evaluate(p => ({
        asked: asked.slice(-1)[0],
        stored: localStorage.getItem("hl.chartPeriod"),
        on: document.querySelector("#feedperiods button.on").textContent,
        buttons: [...document.querySelectorAll("#feedperiods button")].map(b => b.textContent.trim()),
      }), period);
      check(`${label} asks for its own window`, long.asked, `/api/popular?period=${period}`);
      check(`${label} is remembered and shown as the active one`, [long.stored, long.on], [period, label]);
      check("the row offers all five windows", long.buttons,
        ["Day", "Week", "Month", "Year", "All time"]);
    }

    // Empty: the chart says which window is empty rather than showing a blank box.
    await page.evaluate(() => queue([]));
    await page.click('#feedperiods [data-period="week"]');
    await page.waitForFunction(() => !!document.querySelector("#popularlist .mpempty"));
    check("an empty chart names its window",
      await page.locator("#popularlist .mpempty").textContent(), "Nothing played this week yet.");
    // All time has no window to name, so the line stops at "yet".
    await page.evaluate(() => queue([]));
    await page.click('#feedperiods [data-period="all"]');
    await page.waitForFunction(() => !!document.querySelector("#popularlist .mpempty"));
    check("all time says nothing about a window",
      await page.locator("#popularlist .mpempty").textContent(), "Nothing played yet.");

    // A slow answer for the window you left must not overwrite the one you are on.
    const stale = await page.evaluate(async () => {
      queue([{ id: 9, title: "Slow answer", artist_name: "Old", plays: 1 }], 120);
      queue([{ id: 8, title: "Latest", artist_name: "New", plays: 7 }]);
      popularPeriod = "month";
      const slow = loadPopular();          // still in flight when the window changes
      popularPeriod = "day";
      await loadPopular();                 // the latest request wins
      await slow;
      await new Promise(r => setTimeout(r, 160));
      const rows = [...document.querySelectorAll("#popularlist .mprow")];
      return { count: rows.length, title: rows[0]?.querySelector(".mptext b").textContent };
    });
    check("an older answer is dropped", stale, { count: 1, title: "Latest" });

    // A member profile is a page: the section row steps aside, and comes back on close —
    // on the section it was opened from, not always Activity.
    await page.evaluate(() => memberShowView());
    check("the profile hides the section row and its panes",
      await page.evaluate(() => [document.getElementById("feedtabs").hidden,
        document.getElementById("feedpane-activity").hidden,
        document.getElementById("feedpane-popular").hidden,
        document.getElementById("memberpage").hidden]), [true, true, true, false]);
    await page.evaluate(() => memberClosePage());
    check("leaving it restores the section you came from",
      await page.evaluate(() => [document.getElementById("feedtabs").hidden,
        document.getElementById("feedpane-activity").hidden,
        document.getElementById("feedpane-popular").hidden, feedTab]), [false, true, false, "popular"]);

    // Widths: the row must not overflow its column at either size.
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const fits = await page.evaluate(() => {
        for (const id of ["feedtabs", "feedperiods"]) {
          const el = document.getElementById(id);
          el.hidden = false;
          const r = el.getBoundingClientRect();
          if (r.right > innerWidth + 1) return `${id} overflows at ${Math.round(r.right)}`;
        }
        return "ok";
      });
      check(`the selectors fit a ${width}px column`, fits, "ok");
    }
  } finally {
    await browser.close();
    srv.close();
  }
  const failed = checks.filter(ok => !ok).length;
  if (failed) { console.log(`${failed} check(s) failed`); process.exit(1); }
  console.log("feed popular ok");
})();