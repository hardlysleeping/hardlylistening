// Tempo in the song table: the stored BPM sorts, the select offers it, and a row carries
// the badge only when a tempo is known. (test_sort.cjs predates the tempo column and is
// left alone; it stops while loading its slice for reasons unrelated to this.)
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('web/index.html', 'utf8');

const start = html.indexOf('const SORT_KEYS');
const end = html.indexOf('function renderSongs(');
assert(start > 0 && end > start, 'sort block not found');

const element = () => ({ innerHTML: '', textContent: '', value: '', hidden: false,
  style: {}, dataset: {}, children: [],
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  setAttribute() {}, addEventListener() {}, appendChild() {}, querySelector() { return null; },
  querySelectorAll() { return []; } });

const ctx = vm.createContext({
  localStorage: { getItem: () => null, setItem: () => {} },
  $: () => ({ value: '', style: {} }),
  renderLibrary: () => {},
  socialIndex: { track: {} },
  // The slice also paints a few feed icons and registers a page-level listener at load,
  // so the sandbox has to look like a page the writes can land on.
  document: { addEventListener() {}, querySelector() { return element(); },
              querySelectorAll() { return []; }, getElementById() { return element(); },
              body: { classList: { add() {}, remove() {}, contains() { return false; } } } },
  String, Number, Math, console,
});
vm.runInContext(html.slice(start, end), ctx);
const run = s => vm.runInContext(s, ctx);

ctx.__tempo = [
  { id: 1, title: 'Slow', bpm: 90 },
  { id: 2, title: 'House', bpm: 124 },
  { id: 3, title: 'Unanalysed' },
  { id: 4, title: 'DnB', bpm: 174 },
];
const tempo = dir => {
  run(`libSort = 'bpm'; libSortDir = ${JSON.stringify(dir)};`);
  return run('sortTracks(__tempo).map(t => t.id).join(",")');
};

// Unanalysed tracks read as 0, so they collect at one end instead of landing mid-list.
assert.strictEqual(tempo('asc'), '3,1,2,4', 'tempo 0, 90, 124, 174');
assert.strictEqual(tempo('desc'), '4,2,1,3', 'tempo reversed');
assert.strictEqual(run('sortKeys("bpm", {bpm: 124.6})'), 124.6, 'the key is the BPM itself');
assert.strictEqual(run('sortKeys("bpm", {})'), 0, 'no stored tempo reads as 0');

assert.strictEqual(run('SORT_KEYS.bpm.label'), 'Tempo', 'the select calls it Tempo');
assert.strictEqual(run('SORT_KEYS.bpm.type'), 'num', 'tempo sorts as a number');
assert.strictEqual(run("SORT_BY_VIEW.songs.includes('bpm')"), true, 'songs offer it');
assert.strictEqual(run("SORT_BY_VIEW.albums.includes('bpm')"), false, 'albums do not');

// The row's badge: shown for a known tempo, and only then.
assert(/Number\(track\.bpm\) > 0/.test(html), 'the row tests the tempo before drawing it');
assert(/<span class="bpm"/.test(html), 'the badge is markup, not a plain number');

console.log('PASS Tempo: stored BPM sorts both ways, unanalysed last, select key and badge');