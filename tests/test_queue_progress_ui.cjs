// Queue page: per-row state, live download strip, partial playlist progress,
// retry feedback. Slices the real renderers out of web/index.html.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('web/index.html', 'utf8');

const start = html.indexOf('// One line of truth per track.');
const end = html.indexOf('async function loadDecisions');
const acts = html.indexOf('async function retryTrack');
const actsEnd = html.indexOf('async function togglePause');
assert(start > 0 && end > start, 'queue render block not found');
assert(acts > 0 && actsEnd > acts, 'queue action block not found');

const el = id => ({ value: '', innerHTML: '', textContent: '', classList: { toggle() {} }, querySelectorAll: () => [] });
const toasts = [];
const hosts = { '#nowbar': { innerHTML: '' } };
const ctx = vm.createContext({
  $: id => hosts[id] || el(id),
  esc: s => String(s ?? ''),
  fmtBytes: n => { const u = ['B', 'KB', 'MB', 'GB']; let v = Number(n) || 0, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
    return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`; },
  fmtTime: s => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; },
  open: new Set(), editing: new Set(), busy: new Set(),
  isAdmin: () => true,
  reviewBlock: () => '<div class="review"></div>',
  refresh: () => {}, showToast: m => toasts.push(m),
  api: async () => ({ ok: true }),
  last: { stats: { now: 1000 } },
  document: { querySelector: () => null },
  setTimeout, clearTimeout, console,
});
vm.runInContext('let last = {stats:{now:1000}};', ctx);
vm.runInContext(html.slice(start, end), ctx);
vm.runInContext(html.slice(acts, actsEnd), ctx);
const run = s => vm.runInContext(s, ctx);

// ---- per-row states ------------------------------------------------------
// Objects cross a vm realm boundary, so compare as JSON, not by prototype.
const state = t => JSON.parse(JSON.stringify(run(`trackState(${JSON.stringify(t)})`)));
assert.deepStrictEqual(state({ status: 'downloading', progress: 42.7, bytes: 3120000, speed: 845000, eta: 97 }),
  { cls: 'live', text: '43% · 3.0 MB · 825 KB/s · 1:37 left' }, 'downloading line must show pct, bytes, speed, eta');
assert.strictEqual(state({ status: 'resolving' }).text, 'finding a source…');
// Retry countdown is measured against the snapshot clock, not wall time.
assert.strictEqual(state({ status: 'retry_wait', next_try: 1031, attempts: 2, max_attempts: 4 }).text,
  'retrying in 0:31 · attempt 3/4');
assert.strictEqual(state({ status: 'retry_wait', next_try: 900, attempts: 0 }).text,
  'retrying in 0:00 · attempt 1/4', 'a past deadline must not print a negative clock');
assert.strictEqual(state({ status: 'failed', attempts: 4, max_attempts: 4 }).text, 'failed after 4/4');
assert.strictEqual(state({ status: 'completed', size: 7188735 }).text, 'done · 6.9 MB');
assert.strictEqual(state({ status: 'queued' }).text, 'queued');
assert.strictEqual(state({ status: 'review' }).cls, 'warn');
assert.strictEqual(state({ status: 'unavailable' }).cls, 'err');

// ---- rows ---------------------------------------------------------------
const row = t => run(`trackRow(${JSON.stringify(t)}, 1)`);
assert(row({ id: 5, position: 2, title: 'T', artist: 'A', status: 'downloading', progress: 42.7 })
  .includes('<span class="tprog"><i style="width:43%"></i></span>'), 'live row needs a real bar');
assert(!row({ id: 5, position: 2, title: 'T', artist: 'A', status: 'queued' }).includes('class="tprog"'),
  'queued row must not show a progress bar');
assert(row({ id: 6, position: 3, title: 'T', artist: 'A', status: 'failed', attempts: 4 })
  .includes('failed after 4/4'));
// Retry button answers while the request is in flight.
ctx.busy.add(7);
assert(row({ id: 7, position: 4, title: 'T', artist: 'A', status: 'failed' }).includes('Retrying…'));
assert(row({ id: 7, position: 4, title: 'T', artist: 'A', status: 'failed' }).includes('disabled'));
ctx.busy.delete(7);
// A finished track is not an invitation to download it again.
assert(!row({ id: 8, position: 5, title: 'T', artist: 'A', status: 'completed' }).includes('>Retry<'));

// ---- now strip ----------------------------------------------------------
const nowbar = hosts['#nowbar'];

run(`renderNow({stats:{running:1,queued:4,waiting:2,review:0,failed:1,paused:false},
  current:{status:'downloading',title:'Song',artist:'Artist',progress:43.7,bytes:3120000,speed:845000,eta:97,attempts:1,max_attempts:4,playlist:'Shuffle'}})`);
let out = nowbar.innerHTML;
assert(out.includes('Song') && out.includes('44%') && out.includes('825 KB/s') && out.includes('1:37 left'));
assert(out.includes('<i style="width:44%"></i>'), 'strip bar must follow progress');
assert(out.includes('1 downloading') && out.includes('4 queued') && out.includes('2 retrying') && out.includes('1 failed'));
assert(out.includes('attempt 1/4'));
// The headline names the playlist it is filling, not just the track inside it.
assert(out.includes('class="nfrom"') && out.includes('Shuffle'), 'now strip must name the playlist being downloaded');

// The queue object uses `phase` for cooldown/disk, the persisted row uses `status`.
run(`renderNow({stats:{running:0,queued:3},current:{phase:'cooldown',provider:'soundcloud'}})`);
assert(nowbar.innerHTML.includes('Waiting on soundcloud'), 'cooldown must be spelled out');
run(`renderNow({stats:{running:0,queued:3},current:{phase:'disk'}})`);
assert(nowbar.innerHTML.includes('Waiting for disk space'));
run(`renderNow({stats:{running:0,queued:0,waiting:1,failed:6,paused:true},current:null})`);
out = nowbar.innerHTML;
assert(out.includes('Idle') && out.includes('nothing downloading right now'));
assert(out.includes('1 retrying') && out.includes('6 failed') && out.includes('paused'));

// ---- playlist card ------------------------------------------------------
const card = run(`playlistCard({id:1,title:'P',platform:'soundcloud',declared:260,added:0,count:4,done:2,failed:0,review:0,
  tracks:[{id:1,status:'completed',size:100},{id:2,status:'completed',size:100},
          {id:3,status:'downloading',progress:50,title:'Live track'},{id:4,status:'retry_wait'}]})`);
assert(card.includes('Downloading now') && card.includes('1 retrying'), 'card must name live and waiting rows');
// The active card says which track it is on, and carries the accent edge.
assert(card.includes('class="card active"'), 'the downloading playlist must be marked');
assert(card.includes('class="livenow"') && card.includes('Live track') && card.includes('50%'),
  'the active card must name the track and its progress');
const idle = run(`playlistCard({id:2,title:'Q',platform:'apple',count:2,done:0,failed:0,review:0,
  tracks:[{id:5,status:'queued'},{id:6,status:'queued'}]})`);
assert(!idle.includes('card active') && !idle.includes('Downloading now'), 'an idle playlist must not claim to be downloading');
// 2 done + 0.5 in flight of 4 = 62.5% -> 63%
assert(card.includes('style="width:63%"'), 'card bar must count work in progress');

// ---- retry feedback -----------------------------------------------------
(async () => {
  ctx.api = async () => ({ ok: true, queued: true, max_attempts: 4 });
  toasts.length = 0;
  await ctx.retryTrack(9);
  assert.strictEqual(toasts.pop(), 'Back in the queue');
  assert(!ctx.busy.has(9), 'busy flag must clear even on success');

  ctx.api = async () => ({ ok: true, queued: false });
  toasts.length = 0;
  await ctx.retryTrack(9);
  assert.strictEqual(toasts.pop(), 'Nothing to retry — it is already queued or done');

  ctx.api = async () => { throw new Error('HTTP 500'); };
  toasts.length = 0;
  await ctx.retryTrack(9);
  assert.strictEqual(toasts.pop(), 'HTTP 500');
  assert(!ctx.busy.has(9), 'busy flag must clear on failure too');

  ctx.api = async () => ({ requeued: 5 });
  toasts.length = 0;
  await ctx.requeuePlaylist(1);
  assert.strictEqual(toasts.pop(), '5 tracks back in the queue');
  ctx.api = async () => ({ requeued: 0 });
  await ctx.requeuePlaylist(1);
  assert.strictEqual(toasts.pop(), 'Nothing to retry in this playlist');

  console.log('Queue progress: per-row state text, live bar, retry countdown, now strip, partial playlist progress, retry feedback PASS');
})().catch(e => { console.error(e); process.exitCode = 1; });
