// HL-233: the plan's "BPM/sync" line and the mixer agree, and stay agreeing.
//
// The card was a reconciliation, not a feature: SYNC already ships and is already
// pinned mathematically by `test_dj_sync.cjs`. What was never pinned is its *scope* —
// SYNC matches tempo and nothing else. If somebody upgrades it to beat-align the decks
// (nudge `currentTime`) this test fails, and the plan text that says "tempo, not phase"
// has to be corrected in the same commit instead of quietly going stale.
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web', 'dj.html'), 'utf8');
const plan = fs.readFileSync(path.join(root, 'PLAN.md'), 'utf8');

// ---- what SYNC does, from the code that does it
const start = html.indexOf('function syncDeck(');
assert(start > 0, 'syncDeck found in web/dj.html');
const body = html.slice(start, html.indexOf('\n}\n', start));
assert.ok(body.includes('syncPercent(target, mine)'), 'SYNC solves the pitch percent it applies');
assert.ok(body.includes('pitch.value = String(percent)'), 'SYNC moves the pitch fader');
assert.ok(!/currentTime/.test(body), 'SYNC does not nudge playback position (that would be phase sync)');
assert.ok(!/nudge|beatOffset|phaseAlign/i.test(body), 'SYNC has no beat/phase alignment of its own');

// ---- the maths it leans on is the one the other suite pins
assert.ok(fs.existsSync(path.join(root, 'tests', 'test_dj_sync.cjs')),
  'the tempo maths is pinned by tests/test_dj_sync.cjs');
assert.ok(fs.existsSync(path.join(root, 'tests', 'test_dj_autodj.cjs')),
  'Auto DJ is pinned by tests/test_dj_autodj.cjs');

// ---- the plan says the same thing, and no longer lists it as roadmap
assert.ok(!/BPM\/sync \(\*\*HL-233\*\*\)/.test(plan), 'PLAN no longer carries BPM/sync as roadmap');
assert.ok(plan.includes('SYNC solves the pitch percent'), 'PLAN records what SYNC actually does');
assert.ok(plan.includes('not beat/phrase matching'), 'PLAN states the gap it does not close');
assert.ok(plan.includes('HL-233'), 'PLAN records the reconciliation by card');

console.log('PASS BPM/sync (HL-233): SYNC is tempo-only, the plan says so, and the pinned suites are named');