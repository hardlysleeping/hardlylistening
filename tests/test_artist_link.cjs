// The player's artist name opens that artist's page, whichever scope the Library happens to
// be showing. The repository is what is downloaded; the "mine" scope is a saved subset of it,
// so looking only at the saved subset dead-ends on an artist you are listening to right now.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web", "index.html"), "utf8");
const discovery = fs.readFileSync(path.join(root, "web", "js", "repo-discovery.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("PASS " + name);
  else { failures++; console.log("FAIL " + name + (detail ? " — " + detail : "")); }
}

// ---- the two functions under test -------------------------------------------------
const from = (text, start, end) => {
  const a = text.indexOf(start);
  const b = text.indexOf(end, a);
  return a >= 0 && b > a ? text.slice(a, b) : "";
};
const discoveryCode = from(discovery, "// Which downloaded artist this name belongs to", "// \"In Repo\" opens the downloaded-artist page");
const playerCode = from(html, "function openArtistFromPlayer(name){", "function openAlbumFromPlayer(trackId){");
check("the player's artist opener is present", playerCode.length > 100);
check("the repository's name lookup is present", discoveryCode.length > 200);

const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
const win = dom.window;
const calls = { scope: [], tab: 0, opened: [], toasts: [], closed: 0 };

// The real `artistsOf` groups by name; the test only needs that contract, not the artwork.
win.artistsOf = rows => [...new Set(rows.map(r => r.artist_name))].map(name => ({ name }));
win.libAll = [{ id: 1, artist_name: "PLSMA" }, { id: 2, artist_name: "Apashe" }];
win.scopedTracks = () => [{ id: 3, artist_name: "Apashe" }];
win.setLibraryScope = scope => calls.scope.push(scope);
win.showLibraryTab = () => { calls.tab++; };
win.openArtistByName = name => calls.opened.push(name);
win.showToast = message => calls.toasts.push(message);
win.closeFullPlayer = () => { calls.closed++; };
win.artistPageWatch = null;
win.artistPageArt = "";

win.eval(discoveryCode);
win.eval(playerCode);
const openFromPlayer = win.eval("openArtistFromPlayer");
const openInRepo = win.eval("openArtistInRepo");

const reset = () => { calls.scope.length = 0; calls.opened.length = 0; calls.toasts.length = 0; calls.tab = 0; calls.closed = 0; };

// 1. Saved in this Library: open it here, no scope change.
reset();
openFromPlayer("Apashe");
check("a saved artist opens without changing scope", calls.opened[0] === "Apashe" && calls.scope.length === 0,
  JSON.stringify(calls));

// 2. Only in the repository — the reported case: the player is playing them, the tab shows saves.
reset();
openFromPlayer("PLSMA");
check("an artist only in the repository still opens", calls.opened[0] === "PLSMA", JSON.stringify(calls));
check("the page is shown from the shared pool", calls.scope[0] === "shared", JSON.stringify(calls.scope));
check("no dead-end toast", calls.toasts.length === 0, JSON.stringify(calls.toasts));

// 3. Name matching ignores case and surrounding space, as the server's query does.
reset();
openFromPlayer("  plsma ");
check("the name is matched case-insensitively", calls.opened[0] === "PLSMA", JSON.stringify(calls));

// 4. Nothing downloaded under that name: the toast is the truth, not a bug.
reset();
openFromPlayer("Nobody At All");
check("an unknown artist says so and opens nothing", calls.opened.length === 0 && calls.toasts.length === 1,
  JSON.stringify(calls));

// 5. The Feed's "In Repo" is a repository action too.
reset();
win.feedArtistReturn = null;
openInRepo("PLSMA");
check("In Repo finds the shared pool", calls.opened[0] === "PLSMA", JSON.stringify(calls));
check("In Repo shows the repository", calls.scope[0] === "shared", JSON.stringify(calls.scope));

// 6. A caller can pin the rows it means; an empty set is a miss, not a crash.
check("the lookup takes its rows", win.repoArtistNamed("PLSMA", []) === null);
check("and returns the stored spelling", win.repoArtistNamed("plsma", win.libAll) === "PLSMA");

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log("\nall artist-link assertions passed");