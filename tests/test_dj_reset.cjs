// Reset-to-default ramp: the glide must be smooth, must take longer the further
// a control has travelled, and must land exactly on the default.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync('web/dj.html', 'utf8');
const start = html.indexOf('const easeOut = p =>');
const end = html.indexOf('function resetControls(');
assert(start > 0 && end > start, 'reset ramp block found');

const context = vm.createContext({ Math, performance: { now: () => 0 } });
vm.runInContext(html.slice(start, end), context);
const q = expr => vm.runInContext(expr, context);

// ---- rest state: no travel, no animation
assert.strictEqual(q('resetDuration(0, 0, 32, 1)'), 0);
assert.strictEqual(q('resetDuration(0.05, 0, 16, 0.1)'), 0, 'within half a step counts as home');

// ---- duration grows with distance, capped
const nudge = q('resetDuration(11, 12, 32, 1)');        // 1 dB on a 32 dB range
const half  = q('resetDuration(6, 12, 32, 1)');
const full  = q('resetDuration(0, 32, 32, 1)');         // the whole range
assert.ok(nudge < half && half < full, `monotonic: ${nudge} < ${half} < ${full}`);
assert.ok(nudge >= 140 && full <= 1250, `bounded: ${nudge} .. ${full}`);
assert.ok(full - nudge > 700, `a full cut takes noticeably longer than a nudge (${Math.round(nudge)} vs ${Math.round(full)} ms)`);

// a real EQ cut: 12 dB boost back to 0 on a -20..12 range
const eqBoost = q('resetDuration(12, 0, 32, 1)');
const eqCut = q('resetDuration(-20, 0, 32, 1)');
assert.ok(eqCut > eqBoost, 'a deeper cut takes longer to return');

// ---- the ramp is smooth and monotonic, and ends exactly on the target
const dur = q('resetDuration(-20, 0, 32, 1)');
let prev = -Infinity;   // ramp ascends from -20 to 0
for (let t = 0; t <= dur; t += dur / 20){
  const v = q(`resetValue(-20, 0, ${dur}, ${t})`);
  assert.ok(v >= prev - 1e-9, `never overshoots backwards at ${t}`);
  assert.ok(v >= -20 && v <= 0, `stays in range at ${t}`);
  prev = v;
}
assert.strictEqual(q(`resetValue(-20, 0, ${dur}, 0)`), -20);
assert.strictEqual(q(`resetValue(-20, 0, ${dur}, ${dur})`), 0);
assert.strictEqual(q(`resetValue(-20, 0, ${dur}, ${dur * 3})`), 0, 'clamps past the end');

// eased, not linear: the first half of the time covers most of the travel
const quarter = q(`resetValue(-20, 0, ${dur}, ${dur / 4})`);
assert.ok(quarter > -20 + 0.5 * (0 - -20), 'ease-out front-loads the movement');

// ---- step rounding reuses the control's own step
assert.strictEqual(q('Math.round(resetValue(8, 0, 500, 500) / 0.1) * 0.1'), 0);

console.log('DJ reset PASS: scaled duration, smooth monotonic ramp, exact landing');
