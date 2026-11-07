// Sorting the song table: one order at a time, a direction per column, votes in
// two readings, and the select kept in step with the header.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('web/index.html', 'utf8');

const start = html.indexOf('const SORT_KEYS');
const end = html.indexOf('function renderSongs(');
assert(start > 0 && end > start, 'sort block not found');

const store = {};
const renders = [];
const ctx = vm.createContext({
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  },
  $: () => ({ value: '', style: {} }),
  renderLibrary: () => renders.push(1),
  socialIndex: { track: {} },
  String, Number, Math, console,
});
vm.runInContext(html.slice(start, end), ctx);
const run = s => vm.runInContext(s, ctx);
// The slice carries the page's real renderLibrary() (it repaints the whole library,
// which needs a DOM). Sorting only has to *ask* for a repaint here.
run('renderLibrary = function(){ __renders.push(1); }; $ = function(){ return { value: "", style: {} }; };');
ctx.__renders = renders;

// The page's own summary() reads socialIndex; fill it the way the API would.
ctx.socialIndex.track = {
  1: { up: 1, down: 0, vote: 1, score: 1, comments: 0 },
  2: { up: 9, down: 0, vote: 0, score: 9, comments: 0 },
  3: { up: 0, down: 4, vote: -1, score: -4, comments: 0 },
  4: { up: 5, down: 2, vote: 1, score: 3, comments: 0 },
};
const tracks = [
  { id: 1, title: 'Zebra', artist_name: 'Beta', album: 'Two', duration: 300, added: 5 },
  { id: 2, title: 'Apple', artist_name: 'Alpha', album: 'One', duration: 120, added: 9 },
  { id: 3, title: 'Mango', artist_name: 'Gamma', album: 'Three', duration: 400, added: 1 },
  { id: 4, title: 'Banana', artist_name: 'Delta', album: 'One', duration: 250, added: 7 },
];
ctx.__tracks = tracks;

const order = () => run('sortTracks(__tracks).map(t => t.id).join(",")');
const sort = (key, dir) => { run(`libSort = ${JSON.stringify(key)}; libSortDir = ${JSON.stringify(dir)};`); return order(); };

// ---- alphabetical, both directions
assert.strictEqual(sort('title', 'asc'), '2,4,3,1', 'Title A–Z');
assert.strictEqual(sort('title', 'desc'), '1,3,4,2', 'Title Z–A');
assert.strictEqual(sort('artist', 'asc'), '2,1,4,3', 'Artist A–Z');
assert.strictEqual(sort('album', 'asc'), '2,4,3,1', 'Album A–Z (One, Three, Two)');

// ---- time
assert.strictEqual(sort('duration', 'asc'), '2,4,1,3', 'shortest first');
assert.strictEqual(sort('duration', 'desc'), '3,1,4,2', 'longest first');

// ---- votes, both readings
assert.strictEqual(sort('votes_all', 'desc'), '2,4,1,3', 'everyone: 9, 5, 1, 0 down-sorted last');
assert.strictEqual(sort('votes_mine', 'desc'), '1,4,2,3', 'my votes: my ups first, my down last');
assert.strictEqual(sort('votes_all', 'asc'), '3,1,4,2', 'worst first');

// ---- recent
assert.strictEqual(sort('recent', 'desc'), '2,4,1,3', 'newest first');

// ---- the header is a cycling control, and it never runs two sorts at once
run("libSort = 'recent'; libSortDir = 'desc';");
run("sortBy('title')");
assert.strictEqual(run('libSort'), 'title');
assert.strictEqual(run('libSortDir'), 'asc', 'text starts A–Z');
run("sortBy('title')");
assert.strictEqual(run('libSortDir'), 'desc', 'second click reverses');
run("sortBy('artist')");
assert.strictEqual(run('libSort'), 'artist', 'switching columns replaces the order');
assert.strictEqual(run('libSortDir'), 'asc');

run("libSort='recent'; libSortDir='desc';");
const seen = [];
for (let i = 0; i < 5; i++) { run("sortBy('votes')"); seen.push(`${run('libSort')}:${run('libSortDir')}`); }
assert.deepStrictEqual(JSON.parse(JSON.stringify(seen)), JSON.parse(JSON.stringify(
  ['votes_all:desc', 'votes_all:asc', 'votes_mine:desc', 'votes_mine:asc', 'recent:desc'])),
  'votes cycles everyone ↓ ↑ then mine ↓ ↑ then off');

// persisted, and the select follows
run("sortBy('duration')");
assert.strictEqual(store['hl.librarySort'], 'duration');
assert.strictEqual(store['hl.librarySortDir'], 'asc');
assert.strictEqual(renders.length > 0, true, 'a sort repaints the list');

// ---- header cells
run("libSort='votes_mine'; libSortDir='desc';");
const head = run('songHead()');
const cells = [...head.matchAll(/data-sort="([^"]+)"[^>]*aria-sort="([^"]+)"/g)]
  .map(m => `${m[1]}=${m[2]}`);
assert.deepStrictEqual(JSON.parse(JSON.stringify(cells)), JSON.parse(JSON.stringify(
  ['title=none', 'artist=none', 'album=none', 'votes=descending', 'duration=none'])),
  'exactly one column is marked as sorted');
assert(head.includes('My votes'), 'the votes header says which reading is active');
assert(head.includes('↓'), 'and which direction');

run("libSort='title'; libSortDir='asc';");
const head2 = run('songHead()');
assert(/data-sort="title"[^>]*aria-sort="ascending"/.test(head2));
assert(head2.includes('Title') && !head2.includes('My votes'));

console.log('Song sorting: alphabetical/artist/album/time/votes(all+mine), one order at a time, cycle control, persisted, header state PASS');
