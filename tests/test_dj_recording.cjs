// In-tab recordings: a finished mix must stay in memory for review, never be
// downloaded automatically, and must be discardable.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync('web/dj.html', 'utf8');
const start = html.indexOf('function recName(){');
const end = html.indexOf('function addRecording(');   // escapeName sits just after renderRecordings
assert(start > 0 && end > start, 'recording helpers found');
const block = html.slice(start, end);

// recName checks existing takes, so the array has to exist in the sandbox
const context = vm.createContext({ Date, Math, String, Number, recordings: [] });
vm.runInContext(block, context);
const q = expr => vm.runInContext(expr, context);

// ---- file name is stamped and always ends in .webm
const name = q('recName()');
assert.match(name, /^hardlylistening-mix-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.webm$/);

// ---- sizes are human, and small files never read as "0 KB"
assert.strictEqual(q('recSize(512)'), '1 KB');
assert.strictEqual(q('recSize(4096)'), '4 KB');
assert.strictEqual(q('recSize(1048576)'), '1.0 MB');
assert.strictEqual(q('recSize(7 * 1048576)'), '7.0 MB');

// ---- clocks: minute rollover, unknown/zero/infinite never render as numbers
assert.strictEqual(q('clock(0)'), '—');
assert.strictEqual(q('clock(9.4)'), '0:09');
assert.strictEqual(q('clock(61)'), '1:01');
assert.strictEqual(q('clock(600)'), '10:00');
assert.strictEqual(q('clock(NaN)'), '—');
assert.strictEqual(q('clock(Infinity)'), '—');

// ---- the panel markup has no auto-download hook: only an explicit link
assert.ok(!/a\.click\(\)/.test(html), 'recording must not auto-download');
assert.ok(/id="recplay"[^>]*controls/.test(html), 'shared player exposes scrub controls');
assert.ok(/data-download=/.test(html), 'an explicit download link exists');
assert.ok(/onbeforeunload/.test(html), 'leaving the page warns while recordings exist');
assert.ok(/kept in this tab/i.test(html), 'the panel says the recording is tab-local');
assert.ok(!/api\/(recording|upload)/.test(html), 'recordings are never posted to the server');

// ---- names from a file system are escaped before entering the row markup
assert.strictEqual(q(`escapeName('<img src=x onerror=1>')`), '&lt;img src=x onerror=1&gt;');

console.log('DJ recording PASS: naming, sizes, clocks, no auto-download, tab-local warning');
