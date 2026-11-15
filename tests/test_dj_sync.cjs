// SYNC maths: matching tempos must produce the pitch percent that makes the two
// decks agree, and must refuse rather than half-apply when they are too far apart.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync('web/dj.html', 'utf8');
const start = html.indexOf('// What SYNC should do, as a pure calculation');
const end = html.indexOf('function syncDeck(');
assert(start > 0 && end > start, 'syncPercent block found');
const block = html.slice(start, end).replace(/^\/\/.*$/gm, '');

const context = vm.createContext({ Math });
vm.runInContext('const PITCH_LIMIT = 8;', context);
vm.runInContext(block, context);
const q = expr => vm.runInContext(expr, context);

// ---- already matched: no movement, inside range
assert.deepStrictEqual(JSON.parse(q('JSON.stringify(syncPercent(128, 128))')), { percent: 0, inRange: true });

// ---- faster target needs a positive pitch, slower a negative one
assert.deepStrictEqual(JSON.parse(q('JSON.stringify(syncPercent(130, 128))')), { percent: 1.6, inRange: true });
assert.deepStrictEqual(JSON.parse(q('JSON.stringify(syncPercent(124, 128))')), { percent: -3.1, inRange: true });

// ---- applying the percent really lands on the target tempo
for (const [target, mine] of [[130, 128], [124, 128], [142, 140], [87, 90]]){
  const { percent, inRange } = JSON.parse(q(`JSON.stringify(syncPercent(${target}, ${mine}))`));
  assert.ok(inRange);
  assert.ok(Math.abs(mine * (1 + percent / 100) - target) < 0.15,
    `${mine} @ ${percent}% == ${mine * (1 + percent / 100)}, wanted ${target}`);
}

// ---- beyond the fader it reports out of range instead of clamping silently
const far = JSON.parse(q('JSON.stringify(syncPercent(150, 87))'));
assert.strictEqual(far.inRange, false);
assert.ok(far.percent > 8);
const down = JSON.parse(q('JSON.stringify(syncPercent(87, 150))'));
assert.strictEqual(down.inRange, false);
assert.ok(down.percent < -8);

// ---- exactly at the limit still counts as reachable
assert.strictEqual(JSON.parse(q('JSON.stringify(syncPercent(108, 100))')).inRange, true);
assert.strictEqual(JSON.parse(q('JSON.stringify(syncPercent(92, 100))')).inRange, true);
assert.strictEqual(JSON.parse(q('JSON.stringify(syncPercent(110, 100))')).inRange, false);

// ---- no tempo on either deck is never a match
assert.deepStrictEqual(JSON.parse(q('JSON.stringify(syncPercent(0, 128))')), { percent: 0, inRange: false });
assert.deepStrictEqual(JSON.parse(q('JSON.stringify(syncPercent(128, 0))')), { percent: 0, inRange: false });

console.log('DJ sync PASS: percent maths, ±8% limit, refusal outside range');
