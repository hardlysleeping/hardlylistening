// AutoDJ's picker: tempo proximity, octave folding, drift, and what it refuses to repeat.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('web/index.html', 'utf8');

const start = html.indexOf('const AUTODJ_AHEAD');
const end = html.indexOf('function autoDjEnsure(');
assert(start > 0 && end > start, 'AutoDJ picker block not found');

const rateStart = html.indexOf('function autoDjRate(');
const rateEnd = html.indexOf('function autoDjMatch(');
assert(rateStart > 0 && rateEnd > rateStart, 'tempo-match block not found');

const ctx = vm.createContext({ Math, Number, console });
vm.runInContext(html.slice(start, end), ctx);
vm.runInContext(html.slice(rateStart, rateEnd), ctx);
const run = s => vm.runInContext(s, ctx);

const track = (id, bpm, extra) => Object.assign({ id, bpm, title: 'T' + id, playable: true }, extra || {});
ctx.libAll = [track(1, 120), track(2, 123), track(3, 90), track(4, 174), track(5, 0),
              track(6, 180), track(7, 0, { playable: false })];
ctx.libQueue = [];
ctx.playingId = 1;

// A fixed random term isolates the tempo part of the score.
run('Math.random = () => 0.5;');

// ---- tempo decides, not chance -------------------------------------------------------
run('autoDjPool = libAll;');
run('playingId = 1; autoDjRecent = [];');
assert.strictEqual(run('autoDjPick().id'), 2, 'a 3 BPM neighbour beats everything else');

// 180 is 90 doubled: the picker folds octaves before it measures the distance, so a
// half-tempo reference still finds the pairing.
run('playingId = 3;');
assert.strictEqual(run('autoDjPick().id'), 6, '180 BPM folds onto a 90 BPM reference');
assert.strictEqual(run('autoDjFold(174, 87)'), 87, 'a double-time pick folds down');
assert.strictEqual(run('autoDjFold(87, 174)'), 174, 'a half-tempo pick folds up');

// ---- drift: a random term is always present, so a set does not lock to one groove ----
run('playingId = 1;');
const scores = run('libAll.filter(t => t.playable).map(t => Math.round(autoDjScore(t, 120) * 100) / 100)');
// A tempo-close pick and its octave can tie (they are the same mix); what matters is
// that a distant tempo is pushed well below a near one, and that chance alone is not
// enough to outrank a good match.
assert(scores.every(s => s > -3 && s < 3), 'scores stay in a sane band: ' + scores);
const byId = id => scores[ctx.libAll.filter(t => t.playable).findIndex(t => t.id === id)];
assert(byId(2) > byId(5) && byId(5) > byId(4),
  'a 3 BPM neighbour beats an unknown tempo, which beats a distant one: ' + scores);
assert(run('autoDjScore({bpm: 0}, 120)') === 0.8,
  'an unanalysed track is neither rewarded nor punished for tempo');

// ---- no tempo nudge beyond what a pitch lock can hide --------------------------------
assert.strictEqual(run('autoDjRate(120, 123)'), 1.025, 'a 2.5 % nudge is applied');
assert.strictEqual(run('autoDjRate(120, 180)'), 1, 'a 50 % jump is refused');
assert.strictEqual(run('autoDjRate(0, 120)'), 1, 'no reference tempo means no nudge');
assert.strictEqual(run('autoDjRate(174, 87)'), 1, 'an octave is not a tempo change');

// ---- recently played is skipped while anything else is left -------------------------
run('playingId = 1; autoDjRecent = [2, 4];');
const fresh = run('autoDjPick()');
assert(fresh && ![2, 4].includes(fresh.id), 'recent tracks are skipped: ' + JSON.stringify(fresh));
// A set never stalls: once everything else is recent it picks again rather than stopping,
// but it still never repeats the song that is playing.
run('autoDjRecent = [2, 4, 5, 6, 3];');
const again = run('autoDjPick()');
assert(again && again.id !== 1, 'an exhausted recent list still picks a different song');
// One track cannot make a set, so a one-track pool falls back to the library instead of
// repeating itself.
run("autoDjRecent = []; playingId = 99; autoDjPool = [{id: 99, bpm: 120, playable: true}];");
const wide = run('autoDjPick()');
assert(wide && wide.id !== 99, 'a one-track pool falls back to the library');

// ---- seeding: the listener's own set when there is one, the library otherwise -------
ctx.libQueue = [track(1, 120), track(2, 123), track(3, 90), track(4, 174), track(5, 0)];
run('autoDjSeed();');
assert.strictEqual(run('autoDjPool.length'), 5, 'a five-track queue is the pool');
ctx.libQueue = [track(1, 120), track(2, 123)];
run('autoDjSeed();');
assert.strictEqual(run('autoDjPool.length'), ctx.libAll.filter(t => t.playable).length,
  'a short queue falls back to the library');

console.log('PASS AutoDJ picker: tempo wins, octaves fold, drift stays, recent is skipped, seeding');