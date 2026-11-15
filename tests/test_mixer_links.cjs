// Mixer -> library links: every name in the mixer points at the page it names,
// in the scope the finder is showing, and never steals a queue row's click.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(path.join(__dirname, "..", "web", "dj.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

// Slice the link helpers plus the two row builders out of the real file.
function slice(startMarker, endMarker) {
  const start = script.indexOf(startMarker);
  const end = script.indexOf(endMarker, start);
  if (start < 0 || end < 0) throw new Error("cannot slice " + startMarker);
  return script.slice(start, end);
}

const helpers = slice("// Every name in the mixer links back", "// ------------------------------------------------------------ queue state");
const rows = slice("function resultRow(t){", "function renderResults(");
const queueRow = slice("function rowHtml(side, id){", "function renderQueues(");

function makeScope(value, playlists = [{ id: 7, title: "Set A" }], selection = "") {
  const els = {
    "#scopeMain": { value },
    "#plselMain": { value: selection, innerHTML: "", options: [] },
    "#plopen": { hidden: true, href: "", }
  };
  return { els, playlists };
}

function sandboxFor(scopeValue, playlists = [], selection = "") {
  const { els } = makeScope(scopeValue);
  const ctx = {
    URLSearchParams,
    esc: s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    $: sel => els[sel] || { value: "", hidden: true, href: "" },
    playlists,
    personal: { tracks: new Set(), playlists_set: new Set(), playlist_tracks: [] },
    queues: { A: [], B: [] },
    decks: { A: null, B: null },
    library: [],
    trackById: id => ctx.library.find(t => t.id === +id),
    stateOf: () => "ready",
    chipText: () => "ready",
    other: side => (side === "A" ? "B" : "A"),
    fmt: () => "3:00",
    media: new Map()
  };
  vm.createContext(ctx);
  vm.runInContext(helpers + "\n" + rows + "\n" + queueRow, ctx);
  return { ctx, els };
}

// --- hrefs carry the scope ------------------------------------------------
{
  const { ctx } = sandboxFor("repository");
  check("repository scope in href", ctx.browseHref({ artist: "A" }) === "/?scope=shared&artist=A",
        ctx.browseHref({ artist: "A" }));
}
{
  const { ctx } = sandboxFor("personal");
  check("personal scope in href", ctx.browseHref({ artist: "A" }) === "/?scope=mine&artist=A",
        ctx.browseHref({ artist: "A" }));
}
{
  const { ctx } = sandboxFor("repository");
  const href = ctx.trackHref({ id: 12, album_id: "abc" });
  check("track href opens its album and marks the row",
        href === "/?scope=shared&album=abc&track=12", href);
  const noAlbum = ctx.trackHref({ id: 12, album_id: null });
  check("track without an album id links by track only", noAlbum === "/?scope=shared&track=12", noAlbum);
  check("artist href encodes spaces", ctx.artistHref({ artist_name: "Anna Lunoe" })
        === "/?scope=shared&artist=Anna+Lunoe", ctx.artistHref({ artist_name: "Anna Lunoe" }));
  check("no artist name means no link", ctx.artistHref({ artist_name: "" }) === "");
  check("links open a new tab so the mix keeps playing",
        ctx.link("x", "/?a=1").includes('target="_blank"'));
  check("link escapes the text", ctx.link("a<b", "/?a=1").includes("a&lt;b"));
  check("no destination means plain text", ctx.link("Plain", "") === "Plain");
}

// --- the rows actually use the links -------------------------------------
{
  const { ctx } = sandboxFor("repository");
  const track = { id: 5, title: "Tune", artist_name: "Artist", album: "Set", album_id: "k", duration: 180, bpm: 128 };
  const row = ctx.resultRow(track);
  check("finder title links", row.includes('href="/?scope=shared&amp;album=k&amp;track=5"'));
  check("finder artist links", row.includes('href="/?scope=shared&amp;artist=Artist"'));
  check("finder album links", row.includes(">Set</a>"));
  ctx.library = [track];
  const queued = ctx.rowHtml("A", 5);
  check("queue title links", queued.includes('>Tune</a>'));
  check("queue artist links", queued.includes('href="/?scope=shared&amp;artist=Artist"'));
}

// --- the playlist chip in the finder -------------------------------------
{
  const { ctx, els } = sandboxFor("personal", [{ id: 7, title: "Set A" }], "7");
  els["#plselMain"].value = "7";
  ctx.paintPlaylistLink();
  check("selected playlist gets an open link",
        els["#plopen"].href === "/?scope=mine&playlist=7" && els["#plopen"].hidden === false,
        JSON.stringify(els["#plopen"]));
  els["#plselMain"].value = "";
  ctx.paintPlaylistLink();
  check("no playlist hides the link", els["#plopen"].hidden === true);
}

// --- a link click never loads the deck -----------------------------------
{
  const block = script.slice(script.indexOf("for (const side of SIDES){\n  $(\"#q\" + side)"),
                             script.indexOf("function other("));
  const lines = block.split("\n").filter(line => line.includes(".grip"));
  check("queue click ignores drag handles and links",
        lines.length > 0 && lines.every(line => line.includes(".grip") && line.includes("closest(\"a\")")),
        lines.join(" | "));
}

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nMixer links PASS: scopes, album/track/artist/playlist hrefs, new-tab, escaping, queue-click isolation");
