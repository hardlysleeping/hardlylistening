// Deep links: a mixer row opens the same song / artist / playlist in the
// library page, in the scope it was clicked from.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const start = script.indexOf("// ------------------------------------------------------------------ deep links");
const end = script.indexOf("// ------------------------------------------------------------------ playback");
if (start < 0 || end < 0) { console.log("FAIL cannot slice the deep-link block"); process.exit(1); }
const block = script.slice(start, end);

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

const ALBUM = "186604ffb8b35a58ac38b25c56ba269e61f993acbcf0c2b23982dd856b2f8742";
const TRACKS = [
  { id: 289, title: "Full Send", artist_name: "1991", album: "Favorites", album_id: ALBUM },
  { id: 307, title: "Fiyah", artist_name: "AHEE", album: "Other Set" }
];

function run(search, opts = {}) {
  const state = { scope: "shared", detail: null, rendered: 0, replaced: null, focused: [], toast: null, saved: 0 };
  const nodes = {
    "#view-queue": { hidden: false }, "#view-feed": { hidden: false },
    "#view-following": { hidden: false }, "#view-library": { hidden: true, classList: { contains: () => false } }
  };
  const timers = [];
  const ctx = {
    location: { search, pathname: "/" },
    history: { replaceState: (a, b, url) => { state.replaced = url; ctx.location.search = ""; } },
    URLSearchParams,
    document: {
      querySelectorAll: () => [{ dataset: { view: "library", scope: "shared" }, classList: { toggle() {} } }],
      querySelector: sel => (opts.rowExists === false ? null
        : { classList: { add: c => state.focused.push(c), remove() {} }, scrollIntoView() {} })
    },
    $: sel => nodes[sel] || { value: "", hidden: true },
    setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
    requestAnimationFrame: fn => { throw new Error("rAF must not gate the highlight"); },
    libraryScope: "shared",
    scopedTracks: () => TRACKS,
    setLibraryScope: scope => { state.scope = scope; },
    renderLibrary: () => { state.rendered++; },
    openPlaylist: id => { state.detail = { kind: "playlist", key: String(id) }; },
    openArtistByName: async name => { state.detail = { kind: "artist", key: name }; },
    showToast: msg => { state.toast = msg; },
    lib: TRACKS
  };
  vm.createContext(ctx);
  vm.runInContext(block + "\nthis.applyDeepLink = applyDeepLink; this.detailSet = () => detailRef();", ctx);
  // `detail` is a module-level binding in index.html; mirror it for assertions.
  vm.runInContext("function detailRef(){ return typeof detail === 'undefined' ? null : detail; }", ctx);
  return { ctx, state, nodes, timers };
}

(async () => {
  {
    const { ctx, state, nodes, timers } = run("?scope=shared&album=" + ALBUM + "&track=289");
    await ctx.applyDeepLink();
    check("album link opens that album", JSON.stringify(ctx.detailSet()) === JSON.stringify({ kind: "album", key: ALBUM }),
          JSON.stringify(ctx.detailSet()));
    check("album link shows the library tab", nodes["#view-library"].hidden === false);
    check("album link keeps the shared scope", state.scope === "shared");
    check("query string is stripped after applying", state.replaced === "/", state.replaced);
    check("highlight is deferred with a timer, not a frame", timers.length === 1);
  }
  {
    // openArtistByName / openPlaylist own `detail` themselves, so assert on the stub's record.
    const { ctx, state } = run("?scope=mine&artist=AHEE");
    await ctx.applyDeepLink();
    check("artist link opens the profile", JSON.stringify(state.detail) === JSON.stringify({ kind: "artist", key: "AHEE" }),
          JSON.stringify(state.detail));
    check("artist link honours the personal scope", state.scope === "mine");
  }
  {
    const { ctx, state } = run("?scope=mine&playlist=7");
    await ctx.applyDeepLink();
    check("playlist link opens the playlist", JSON.stringify(state.detail) === JSON.stringify({ kind: "playlist", key: "7" }),
          JSON.stringify(state.detail));
  }
  {
    const { ctx } = run("?scope=shared&track=289");
    await ctx.applyDeepLink();
    check("track-only link resolves its album", JSON.stringify(ctx.detailSet()) === JSON.stringify({ kind: "album", key: ALBUM }),
          JSON.stringify(ctx.detailSet()));
  }
  {
    const { ctx, state, timers } = run("?scope=shared&album=" + ALBUM + "&track=289");
    await ctx.applyDeepLink();
    timers.forEach(fn => fn());
    check("the arriving row is marked", state.focused.includes("focus"), JSON.stringify(state.focused));
  }
  {
    const { ctx, state } = run("?scope=mine&track=999");
    await ctx.applyDeepLink();
    check("a track outside the scope explains itself instead of opening nothing",
          state.toast && state.toast.includes("scope"), state.toast);
  }
  {
    const { ctx, state } = run("");
    await ctx.applyDeepLink();
    check("no query means no navigation", state.rendered === 0 && state.replaced === null);
  }
  {
    const { ctx, state } = run("?scope=shared&album=" + ALBUM);
    await ctx.applyDeepLink();
    await ctx.applyDeepLink();
    check("applying twice is a no-op", state.rendered === 1, String(state.rendered));
  }

  if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
  console.log("\nDeep links PASS: album, track, artist, playlist, scope, one-shot, missing scope notice");
})();
