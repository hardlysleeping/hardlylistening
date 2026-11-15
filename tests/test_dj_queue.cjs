// Queue state machine: what a queued track reports, how many downloads run at
// once, and what happens when one finishes, fails or is removed.
// The block is lifted straight out of web/dj.html so the test cannot drift from
// the shipped code.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
// The sandbox has its own Array prototype, so compare structural output as JSON.
const same = (actual, expected, msg) =>
  assert.strictEqual(JSON.stringify(actual), JSON.stringify(expected), msg);

const html = fs.readFileSync('web/dj.html', 'utf8');
const start = html.indexOf('const SIDES = ["A", "B"];');
const end = html.indexOf('// ------------------------------------------------------------ search results');
assert(start > 0 && end > start, 'queue block found in dj.html');
const block = html.slice(start, end);

const rows = new Map();          // id -> fake row node
function makeRow(id){
  const chip = {textContent: ''};
  const bar = {style: {width: ''}};
  const row = {dataset: {id: String(id), state: ''}, querySelector: sel => sel === '.chip' ? chip : bar,
               classList: {add(){}, remove(){}, contains(){return false}}};
  return row;
}
for (const id of [1, 2, 3, 4]) rows.set(String(id), makeRow(id));

const storage = new Map();
const store = {
  localStorage: {
    getItem: k => storage.has(k) ? storage.get(k) : null,
    setItem: (k, v) => storage.set(k, String(v))
  },
  // A queued row lives in whichever deck list it was sent to; the fake document
  // answers by id so both queues share one row object.
  document: {
    querySelectorAll: sel => {
      const m = /data-id="(\d+)"/.exec(sel);
      return m && rows.has(m[1]) ? [rows.get(m[1])] : [];
    },
    addEventListener(){}
  },
  library: [1, 2, 3, 4].map(id => ({id, title: 'T' + id, artist_name: 'A', album: 'B', duration: 180})),
  decks: {A: {track: null}, B: {track: null}},
  fmt: s => '3:00',
  esc: s => String(s ?? ''),
  setTimeout, clearTimeout, AbortController, URLSearchParams,
  URL: {createObjectURL: () => 'blob:fake-' + (++urlCount), revokeObjectURL(){}},
  Blob: class { constructor(parts, opts){ this.size = (parts || []).reduce((n, p) => n + p.length, 0); this.type = opts?.type; } },
};
let urlCount = 0;

// fetch is a controllable stub: each call parks until the test releases it.
let pending = [];
const context = vm.createContext({
  ...store,
  $: sel => ({ textContent: '', value: '' }),
  $$: () => [],
  fetch: url => new Promise((resolve, reject) => {
    const id = Number(/\/api\/track\/(\d+)\//.exec(url)[1]);
    pending.push({ id, resolve, reject, url });
  }),
  console,
});
vm.runInContext('function syncSendButtons(){}', context);   // DOM-only helper, not under test
vm.runInContext('function publishQueue(){}', context);      // room layer lives outside this slice
vm.runInContext(block, context);
const q = expr => vm.runInContext(expr, context);

// ---- one queued track starts downloading straight away
assert.strictEqual(q('addToQueue("A", 1)'), true);
same(q('queues.A'), [1]);
assert.strictEqual(q('chipText(1)'), 'preloading 0%');
assert.strictEqual(pending.length, 1, 'one fetch started');
assert.strictEqual(pending[0].url, '/api/track/1/audio');
assert.strictEqual(q('activeLoads'), 1);

// ---- duplicates are refused, the queue cap is respected
assert.strictEqual(q('addToQueue("A", 1)'), false);
assert.ok(q('activeLoads') <= 2);

// ---- a second and third track: only MAX_PARALLEL downloads run
q('addToQueue("A", 2)');
q('addToQueue("B", 3)');
assert.strictEqual(pending.length, 2, 'second slot only');
assert.strictEqual(q('chipText(3)'), 'queued', 'third waits its turn');
assert.strictEqual(q('activeLoads'), 2);

// the same track on both decks is allowed and shares one download
assert.strictEqual(q('addToQueue("B", 1)'), true);
same(q('queues.B'), [3, 1]);

// ---- progress is visible while the body streams in
pending[0].resolve({
  ok: true,
  headers: {get: h => h === 'content-length' ? '1000' : 'audio/mpeg'},
  body: {getReader: () => {
    let step = 0;
    return {read: async () => step++ === 0 ? {done: false, value: new Uint8Array(400)} : {done: true}};
  }}
});
// ---- finishing frees a slot and the waiting track starts
setTimeout(() => {
  try {
    assert.strictEqual(q('stateOf(1)') || q('media.get(1).state'), 'ready');
    assert.strictEqual(q('media.get(1).pct'), 1);
    assert.ok(String(q('media.get(1).url')).startsWith('blob:'));
    assert.strictEqual(pending.length, 3, 'waiting track started when a slot freed');
    assert.strictEqual(q('chipText(3)'), 'preloading 0%');

    // ---- a failed download is labelled, never silently "ready"
    pending.find(p => p.id === 2).resolve({ok: false, status: 404, headers: {get: () => null}});
    setTimeout(() => {
      try {
        assert.strictEqual(q('media.get(2).state'), 'failed');
        assert.strictEqual(q('chipText(2)'), 'no preload');

        // ---- removal drops the entry, keeps the other deck's copy, revokes the blob
        const url = q('media.get(1).url');
        q('removeFromQueue("A", 1)');
        same(q('queues.A'), [2]);
        same(q('queues.B'), [3, 1]);
        assert.strictEqual(q('media.get(1).url'), url, 'still needed by deck B');
        q('removeFromQueue("B", 1)');
        assert.strictEqual(q('media.has(1)'), false, 'released once unused');

        // ---- order is what gets persisted, so a reload keeps the arrangement
        assert.strictEqual(storage.get('hl.queueA'), '[2]');
        assert.strictEqual(storage.get('hl.queueB'), '[3]');

        console.log('DJ queue PASS: preload states, 2-at-a-time scheduling, failure label, dedupe, release, persistence');
      } catch (e) { console.error(e); process.exit(1); }
    }, 20);
  } catch (e) { console.error(e); process.exit(1); }
}, 20);
