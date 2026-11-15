// The delete controls: two clicks before anything unlinks, the right buttons for
// the right person, and a disk banner that says which guard stopped the queue.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const page = require('./_page.cjs');
const indexHtml = page.indexHtml();
assert(indexHtml.includes('/js/library.js'), 'the page must load the file these helpers live in');
// Two homes now: the delete helpers moved to web/js/library.js (JS-032), while the banner and
// the playlist card they sit beside are still inline.
const html = page.feature('library.js');

const delStart = html.indexOf('// Deleting audio is destructive');
const delEnd = html.length;   // the delete helpers are the tail of the file
assert(delStart > 0 && delEnd > delStart, 'delete helpers not found');
assert(html.includes('async function deleteTrack('), 'deleteTrack must be in the slice');

const calls = [];
const toasts = [];
const el = () => ({ innerHTML: '', textContent: '', dataset: {}, style: {} });
const ctx = vm.createContext({
  $: () => el(),
  document: { getElementById: () => el() },
  esc: s => String(s ?? ''),
  fmtBytes: n => `${Math.round((Number(n) || 0) / 1024 / 1024)} MB`,
  fmtTime: s => String(s),
  isAdmin: () => true,
  api: async (path, body) => { calls.push([path, body]); return { deleting: 3, bytes: 3 * 1024 * 1024 }; },
  showToast: m => toasts.push(m),
  refresh: () => {}, renderLibrary: () => {},
  detail: { key: 'x' },
  setTimeout, clearTimeout, console,
  open: new Set(), editing: new Set(), busy: new Set(),
  reviewBlock: () => '', last: { stats: { now: 1000 } },
});
vm.runInContext('let detail = {key:"x"};', ctx);
vm.runInContext(html.slice(delStart, delEnd), ctx);
// renderBanners + playlistCard for the rest of the assertions
const bStart = indexHtml.indexOf('function banner(');
const bEnd = indexHtml.indexOf('async function loadDecisions');
assert(bStart > 0 && bEnd > bStart, 'banner block not found');
vm.runInContext(indexHtml.slice(bStart, bEnd), ctx);
const run = s => vm.runInContext(s, ctx);

// ---- two-step delete ----------------------------------------------------
const button = el();
const album = run('deleteAlbum');
const step1 = album(7, 4, button);
(async () => {
  await step1;
  assert.strictEqual(calls.length, 0, 'one click must not delete anything');
  assert.strictEqual(button.dataset.arm, '1');
  assert.strictEqual(button.textContent, 'Click again to confirm');
  await album(7, 4, button);
  assert.strictEqual(JSON.stringify(calls.pop()), JSON.stringify(['/api/release/7/remove', { delete_files: true }]));
  assert.strictEqual(button.dataset.arm, '');
  assert.strictEqual(button.textContent, 'Delete album');
  assert.strictEqual(toasts.pop(), 'Deleted 3 tracks, 3 MB freed');
  assert.strictEqual(run('detail'), null, 'album gone, so the detail page must close');

  // A playlist delete names its files and also takes two clicks.
  const pb = el();
  await run('deletePlaylistFiles')(12, 9, pb);
  assert.strictEqual(calls.length, 0);
  assert.strictEqual(pb.dataset.arm, '1');
  await run('deletePlaylistFiles')(12, 9, pb);
  assert.strictEqual(JSON.stringify(calls.pop()), JSON.stringify(['/api/playlist/12/remove', { delete_files: true }]));

  // ---- which buttons a row offers --------------------------------------
  const mine = run(`playlistCard({id:1,title:"Mine",platform:"soundcloud",declared:9,added:0,
    count:9,done:9,failed:0,review:0,can_remove:true,
    tracks:Array.from({length:9},(_,i)=>({id:i+1,position:i+1,title:"S"+i,artist:"A",status:"completed",size:1024}))})`);
  assert(mine.includes('Delete files'), 'an owner/admin gets a delete-files action');
  const theirs = run(`playlistCard({id:2,title:"Theirs",platform:"soundcloud",declared:1,added:0,
    count:1,done:1,failed:0,review:0,can_remove:false,tracks:[{id:9,position:1,title:"S",artist:"A",status:"completed"}]})`);
  assert(!theirs.includes('Delete files'), 'no delete action for a playlist you did not add');

  // ---- disk banner names the guard that fired --------------------------
  const host = { innerHTML: '' };
  ctx.$ = id => (id === '#banners' ? host : el());
  const percent = run(`(() => { renderBanners({stats:{disk_blocked:true,disk_used_pct:96.2,
    disk_limit_pct:95,disk_free_gb:9.4,disk_floor_gb:2,cooldown_until:0,paused:false}});
    return document.querySelector ? null : null; })()`);
  void percent;
  assert(host.innerHTML.includes('96.2% full') && host.innerHTML.includes('limit is 95%'),
    'banner must say the disk is over its percentage ceiling');
  const floorCase = run(`(() => { renderBanners({stats:{disk_blocked:true,disk_used_pct:40,
    disk_limit_pct:95,disk_free_gb:1.1,disk_floor_gb:2,cooldown_until:0,paused:false}});
    return 1; })()`);
  void floorCase;
  assert(host.innerHTML.includes('1.1 GB free'), 'banner must fall back to the GB floor');

  console.log('Delete UI: two-step confirm, files-vs-remove split, ownership-gated buttons, disk ceiling banner PASS');
})().catch(e => { console.error(e); process.exitCode = 1; });
