// Member profile: a page in the Feed section, not a dialog.
//
// It was a modal over the activity list, which on a phone was a box inside a box that could
// not use the screen it had. The contract now: the page occupies the Feed section (so tabs and
// the saved navigation state are untouched), it paints in bands — who, lifetime numbers, the
// listening card, then one list at a time behind a section switch — and leaving it puts the
// activity list back.
const fs = require("fs"), assert = require("assert"), { chromium } = require("playwright");
const { appCss } = require("./_css.cjs");

const html = fs.readFileSync("web/index.html", "utf8");

const section = html.slice(html.indexOf('<section id="view-feed"'), html.indexOf('<section id="view-library"'));
const script = fs.readFileSync("web/js/social-feed.js", "utf8");

const PAYLOAD = {
  member: { id: "9", name: "Whales", icon: "music", role: "admin" },
  counts: { listens: 4213, distinct_tracks: 512, adds: 77, votes: 31 },
  listens: [
    { id: 1, title: "Hands All Over Me", artist: "Whales", plays: 12 },
    { id: 2, title: "Reverie", artist: "Ilan Bluestone", plays: 7 },
  ],
  adds: [{ id: 3, title: "Atlas", artist: "Faux Tales", ts: 1794000000 }],
  playlists: [{ id: 5, title: "Sunday Service", tracks: 24 }],
  votes: [{ value: 1, label: "Hands All Over Me", kind: "track" }],
  party_votes: [],
  saves: [{ id: 4, title: "Nova", artist: "Zeds Dead" }],
  stats: {
    totals: { plays: 40, seconds: 9000, tracks: 18, artists: 9 },
    favourite_artist: { artist_name: "Whales", plays: 12 },
    days: [{ day: "2026-11-01", plays: 3 }, { day: "2026-11-02", plays: 5 }],
    top_tracks: [{ id: 1, title: "Hands All Over Me", artist_name: "Whales", plays: 12 }],
    top_albums: [{ album: "Monstercat", artist_name: "Whales", plays: 9 }],
    streak: { current: 4 },
  },
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  let failures = 0;
  const check = (name, ok, detail) => {
    if (ok) console.log("PASS " + name);
    else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
  };
  try {
    for (const vp of [{ width: 390, height: 844 }, { width: 1280, height: 900 }]) {
      const page = await browser.newPage({ viewport: vp });
      await page.route("**/*", r => r.abort());
      await page.goto("about:blank");
      await page.setContent(`<style>${appCss()}</style>${section}`);
      await page.addScriptTag({
        content: `
          const memory = {};
          const localStorage = { getItem: k => memory[k] ?? null, setItem: (k, v) => memory[k] = v };
          const sessionStorage = { getItem: () => null, setItem: () => {} };
          function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
          function profileSymbol(name){ return "♪"; }
          function showToast(m){ window.__toast = m; }
          const libAll = [{ id: 1, artwork: "/art/1.jpg" }];
          const played = [];
          function playTrackNow(id){ played.push(id); }
          window.__played = played;
          const opened = [];
          function openPlaylist(id){ opened.push(id); }
          window.__opened = opened;
          function showLibraryTab(){ window.__libtab = true; }
          window.__payload = ${JSON.stringify(PAYLOAD)};
          window.fetch = (url) => {
            const u = String(url);
            if (u.startsWith("/api/member/")) return Promise.resolve({ ok: true, json: () => Promise.resolve(window.__payload) });
            if (u.startsWith("/api/stats/")) return Promise.resolve({ ok: true, json: () => Promise.resolve(window.__payload.stats) });
            return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
          };
        `,
      });
      await page.addScriptTag({ content: script });

      const state = () => page.evaluate(() => ({
        page: !document.getElementById("memberpage").hidden,
        activity: !document.getElementById("feedpane-activity").hidden,
        name: document.getElementById("mpname").textContent,
        tiles: [...document.querySelectorAll("#mpbody .mptile")].map(t => t.textContent.trim()),
        tabs: [...document.querySelectorAll("#mpbody .mptab")].map(t => t.textContent.trim()),
        rows: document.querySelectorAll("#mpbody .mplist .mprow").length,
        rowLabel: document.querySelector("#mpbody .mplist .mprow")?.textContent.replace(/\s+/g, " ").trim() || "",
        hasStats: !!document.querySelector("#mpbody .mpstats"),
        periods: [...document.querySelectorAll("#mpbody [data-stats-period]")].map(b => b.textContent.trim()),
        cover: !!document.querySelector("#mpbody img.mpart"),
      }));

      await page.evaluate(() => openMember(9));
      await page.waitForTimeout(120);

      let s = await state();
      check(`${vp.width}: the profile is its own page`, s.page, JSON.stringify(s));
      check(`${vp.width}: and the activity list steps aside`, !s.activity);
      check(`${vp.width}: it names the member`, s.name === "Whales", s.name);
      check(`${vp.width}: four lifetime tiles`, s.tiles.length === 4, JSON.stringify(s.tiles));
      check(`${vp.width}: the tiles are counts, not a period`, /4,?213/.test(s.tiles[0]) && /plays/.test(s.tiles[0]),
        JSON.stringify(s.tiles));
      check(`${vp.width}: a listening card with four periods`, s.hasStats && s.periods.length === 4,
        JSON.stringify(s.periods));
      check(`${vp.width}: five sections with counts in the pill`,
        s.tabs.length === 5 && s.tabs.every(t => /\d/.test(t)), JSON.stringify(s.tabs));
      check(`${vp.width}: one list at a time, starting with history`, s.rows === 2, String(s.rows));
      check(`${vp.width}: a row carries its cover and its play count`,
        s.cover && /12×/.test(s.rowLabel), s.rowLabel);

      // The switch swaps the single list instead of stacking five.
      await page.evaluate(() => setMemberSection("playlists"));
      s = await state();
      check(`${vp.width}: the switch replaces the list`, s.rows === 1 && /Sunday Service/.test(s.rowLabel),
        s.rowLabel);
      check(`${vp.width}: the pill says which section is showing`,
        await page.evaluate(() => document.querySelector('#mpbody .mptab[aria-pressed="true"]').textContent.startsWith("Playlists")));

      // A row is a play target; a playlist row is a destination.
      await page.evaluate(() => setMemberSection("history"));
      await page.click("#mpbody .mplist .mprow");
      check(`${vp.width}: a song row plays the song`,
        JSON.stringify(await page.evaluate(() => window.__played)) === "[1]",
        JSON.stringify(await page.evaluate(() => window.__played)));
      await page.evaluate(() => setMemberSection("playlists"));
      await page.evaluate(() => openPlaylistFromMember(5));
      check(`${vp.width}: a playlist row opens the playlist`,
        JSON.stringify(await page.evaluate(() => window.__opened)) === "[5]");
      check(`${vp.width}: and hands over to the library section`,
        await page.evaluate(() => !!window.__libtab));

      // Leaving: the page goes, the activity list comes back.
      await page.evaluate(() => { window.history.replaceState({}, ""); openMember(9); });
      await page.waitForTimeout(80);
      await page.click("#memberpage .mpback");
      await page.waitForTimeout(80);
      s = await state();
      check(`${vp.width}: Back restores the list it was opened from`, !s.page && s.activity);

      // Escape is the other way out.
      await page.evaluate(() => openMember(9));
      await page.waitForTimeout(60);
      await page.keyboard.press("Escape");
      await page.waitForTimeout(80);
      check(`${vp.width}: Escape leaves it too`,
        await page.evaluate(() => document.getElementById("memberpage").hidden));

      // Nothing about it is a modal.
      check(`${vp.width}: it is not a dialog`, await page.evaluate(() =>
        document.getElementById("memberpage").tagName === "SECTION" &&
        !document.querySelector("dialog #memberpage")));

      await page.close();
    }
  } finally {
    await browser.close();
  }
  if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
  console.log("\nall member-page assertions passed");
})();