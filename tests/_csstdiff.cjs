// Compare the old stylesheet tree with the new one, element by element, on the same markup.
// The refactor's first stage is a move: every difference here is a regression to explain.
//
//   node tests/_csstdiff.cjs [state]
const fs = require("fs"), path = require("path"), http = require("http");
const { chromium } = require("playwright");
const NEWROOT = path.join(__dirname, "..", "web");
// The oracle needs the pre-refactor tree to compare against. Point HL_OLD_WEB at a checkout of
// the last release that shipped the old stylesheets, e.g.
//   git archive v0.16.6 web | tar -x -C /tmp/oldweb --strip-components=1
const OLDROOT = process.env.HL_OLD_WEB || "/tmp/oldweb";
if (!fs.existsSync(path.join(OLDROOT, "index.html"))) {
  console.log(`skipped: no reference tree at ${OLDROOT} (set HL_OLD_WEB; see the comment above)`);
  process.exit(0);
}
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml" };
const TRACKS = [
  { id: 1, title: "First Light", artist_name: "Baseline Artist", album: "Night Drive", album_id: "album-night-drive", duration: 181, added: 1700000001, playable: true, artwork: "", provider: "soundcloud", playlists: [{ id: 7, title: "Baseline Set", position: 1 }] },
  { id: 2, title: "Second Wind", artist_name: "Baseline Artist", album: "Night Drive", album_id: "album-night-drive", duration: 182, added: 1700000002, playable: true, artwork: "", provider: "soundcloud", playlists: [{ id: 7, title: "Baseline Set", position: 2 }] },
  { id: 3, title: "Third Rail", artist_name: "Baseline Artist", album: "Night Drive", album_id: "album-night-drive", duration: 183, added: 1700000003, playable: true, artwork: "", provider: "soundcloud", playlists: [] },
  { id: 4, title: "Harbour", artist_name: "Baseline Artist", album: "Low Tide", album_id: "album-low-tide", duration: 184, added: 1700000004, playable: true, artwork: "", provider: "soundcloud", source_status: "gone", playlists: [] },
  { id: 5, title: "Silt", artist_name: "Baseline Artist", album: "Low Tide", album_id: "album-low-tide", duration: 185, added: 1700000005, playable: true, artwork: "", provider: "soundcloud", playlists: [] },
  { id: 6, title: "Saltmarsh", artist_name: "Baseline Artist", album: "Low Tide", album_id: "album-low-tide", duration: 186, added: 1700000006, playable: true, artwork: "", provider: "soundcloud", playlists: [] },
];

const server = root => http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const json = b => res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(b));
  if (p === "/api/library") return json({ tracks: TRACKS, all_tracks: TRACKS, artist_art: {}, playlists: [{ id: 7, title: "Baseline Set" }] });
  if (p === "/api/listening/saves") return json({ tracks: TRACKS.map(t => t.id), playlists: [7], playlist_tracks: [], folder_tracks: [] });
  if (p === "/api/listening/folders") return json({ folders: [] });
  if (p === "/api/listening/preferences") return json({ favorites: [{ kind: "track", id: 2 }], pins: [{ kind: "track", id: 3 }], album_likes: [] });
  if (p === "/api/state") return json({ viewer: { is_admin: true, role: "admin", auth_required: false, display_name: "Admin" }, stats: { done: 0, queued: 0, review: 0, failed: 0 }, playlists: [], logs: [], subscriptions: [], current: null });
  if (p.startsWith("/api/")) return json({ ok: true, songs: [], queue: [], tracks: [], playlists: [], folders: [], favorites: [], entries: [], decisions: [], rows: [], all_tracks: [], playlist_tracks: [], folder_tracks: [] });
  const f = path.join(root, p === "/" ? "/index.html" : p);
  if (fs.existsSync(f) && fs.statSync(f).isFile())
    return res.writeHead(200, { "content-type": TYPES[path.extname(f)] || "application/octet-stream" }).end(fs.readFileSync(f));
  res.writeHead(404).end("no");
});

const PROPS = ["display", "position", "top", "left", "right", "bottom", "width", "height", "min-height",
  "max-height", "margin", "padding", "gap", "grid-template-columns", "grid-template-rows", "flex-direction",
  "align-items", "justify-content", "border", "border-bottom", "border-right", "border-radius", "background",
  "background-color", "color", "font-size", "font-weight", "line-height", "letter-spacing", "opacity",
  "overflow", "z-index", "box-shadow", "outline", "transform", "visibility", "content", "text-transform"];

const SNAP = ([props]) => {
  const out = [];
  const walk = el => {
    for (const child of el.children) {
      if (child.tagName === "SCRIPT" || child.tagName === "STYLE" || child.tagName === "LINK") continue;
      const cs = getComputedStyle(child);
      const r = child.getBoundingClientRect();
      const rec = { path: pathOf(child), tag: child.tagName.toLowerCase(),
        cls: child.getAttribute("class") || "", id: child.id || "",
        box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
      for (const p of props) rec[p] = cs.getPropertyValue(p);
      const after = getComputedStyle(child, "::after");
      if (after.content !== "none" && after.content !== "normal") {
        rec.after = ["content", "background-color", "height", "left", "right", "bottom", "display"]
          .map(p => after.getPropertyValue(p)).join("|");
      }
      out.push(rec);
      walk(child);
    }
  };
  function pathOf(el) {
    const bits = [];
    while (el && el.nodeType === 1) {
      let i = 1, s = el;
      while ((s = s.previousElementSibling)) if (s.tagName === el.tagName) i++;
      bits.unshift(el.tagName.toLowerCase() + ":" + i);
      el = el.parentElement;
    }
    return bits.join(">");
  }
  walk(document.body);
  return out;
};

// The playing row's bars animate; a comparison that samples them twice reads noise as a
// difference. Motion is frozen before the snapshot, exactly as the visual suites do.
const FREEZE = `(() => {
  const s = document.createElement('style');
  s.textContent = '*,*::before,*::after{animation:none!important;transition:none!important}';
  document.head.appendChild(s);
  // The playing bars are SMIL, so CSS cannot freeze them: drop the animation nodes and the
  // rects fall back to their base geometry.
  document.querySelectorAll('animate,animateTransform,animateMotion').forEach(n => n.remove());
})();`;

async function snap(page, state) {
  await page.goto(page.__base, { waitUntil: "load" });
  await page.waitForTimeout(500);
  await page.evaluate(() => document.querySelector('.tab[data-view="library"][data-scope="mine"]')?.click());
  await page.waitForTimeout(500);
  await page.evaluate(state => {
    if (typeof setSongLayout === "function") setSongLayout(state === "cards" ? "grid" : "list");
    playingId = state === "list-playing" ? 2 : null;
    detail = null;
    if (typeof renderLibrary === "function") renderLibrary();
  }, state);
  await page.waitForTimeout(300);
  await page.evaluate(FREEZE);
  await page.waitForTimeout(150);
  return page.evaluate(SNAP, [PROPS]);
}

(async () => {
  const state = process.argv[2] || "list";
  const W = Number(process.argv[3] || 1280), H = W < 500 ? 844 : 800;
  const a = server(OLDROOT), b = server(NEWROOT);
  await new Promise(r => a.listen(0, r)); await new Promise(r => b.listen(0, r));
  const browser = await chromium.launch({ headless: true });
  const pa = await browser.newPage({ viewport: { width: W, height: H }, hasTouch: W < 500 });
  const pb = await browser.newPage({ viewport: { width: W, height: H }, hasTouch: W < 500 });
  pa.__base = `http://127.0.0.1:${a.address().port}/`; pb.__base = `http://127.0.0.1:${b.address().port}/`;
  try {
    const [oldSnap, newSnap] = [await snap(pa, state), await snap(pb, state)];
    if (oldSnap.length !== newSnap.length) console.log(`element count ${oldSnap.length} -> ${newSnap.length}`);
    const diffs = [];
    for (let i = 0; i < Math.min(oldSnap.length, newSnap.length); i++) {
      const o = oldSnap[i], n = newSnap[i];
      const changed = {};
      for (const p of PROPS) if (o[p] !== n[p]) changed[p] = [o[p], n[p]];
      if (o.after !== n.after) changed.after = [o.after, n.after];
      if (o.box.join() !== n.box.join()) changed.box = [o.box.join(), n.box.join()];
      if (Object.keys(changed).length) diffs.push({ el: `${n.tag}${n.id ? "#" + n.id : ""}${n.cls ? "." + n.cls.trim().split(/\s+/).join(".") : ""}`, changed });
    }
    console.log(`${diffs.length} element(s) differ in state "${state}"`);
    for (const d of diffs.slice(0, 40)) {
      console.log("\n" + d.el.slice(0, 120));
      for (const [p, v] of Object.entries(d.changed)) console.log(`   ${p}: ${JSON.stringify(v[0])} -> ${JSON.stringify(v[1])}`);
    }
  } finally { await browser.close(); a.close(); b.close(); }
})().catch(e => { console.error("ERROR " + e.message); process.exitCode = 1; });