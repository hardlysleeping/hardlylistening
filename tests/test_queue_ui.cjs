// Run with jsdom available on NODE_PATH. No server, polling requests or audio.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('web/index.html', 'utf8');
// Slice from the row-state helpers so trackState/renderNow come along with trackRow.
const source = html.slice(html.indexOf('// One line of truth per track.'), html.indexOf('function renderLogs('));
const dom = new JSDOM('<div id="list"></div><div id="nowbar"></div>', {runScripts: 'outside-only'});
const w = dom.window;
w.eval(`const $ = s => document.querySelector(s); const open = new Set([1,2]);
const editing = new Set(); const busy = new Set(); let last = {stats:{now:1000}};
const showToast = () => {}; const esc = s => String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const fmtTime = s => String(s); const fmtBytes = s => String(s);
const isAdmin = () => true; const adminUI = () => true; const reviewBlock = () => '';
${source}
window.renderQueue = renderQueue; window.openPlaylists = open; window.editing = editing;
window.ensureDecisionHistory = ensureDecisionHistory; window.loadDecisions = loadDecisions;`);
const p = id => ({id, title:'FavoriteFavorites', platform:'apple', done:0, count:120, tracks:Array.from({length:120}, (_, i) => ({id:id*1000+i,position:i+1,title:'Song '+i,artist:'Artist',status:'queued'}))});
const playlists = [p(1),p(2)];
w.renderQueue(playlists);
const list = w.document.querySelector('#list');
let tracks = list.querySelector('.tracks');
tracks.scrollTop = 1800;
// Positive control: the original polling replacement loses scroll state and identity.
const old = tracks; const saved = list.innerHTML; list.innerHTML = saved;
assert.notEqual(list.querySelector('.tracks'), old);
assert.equal(list.querySelector('.tracks').scrollTop, 0);
tracks = list.querySelector('.tracks'); tracks.scrollTop = 1800;
const row = list.querySelector('[data-queue-key="track-1080"]');
for (let i=0;i<5;i++){
  playlists[0].done = i;
  playlists[0].tracks[0].status = 'downloading'; playlists[0].tracks[0].progress = i*20;
  w.renderQueue(playlists);
  assert.equal(list.querySelector('.tracks'), tracks);
  assert.equal(tracks.scrollTop, 1800);
  assert.equal(list.querySelector('[data-queue-key="track-1080"]'), row);
  assert.equal(w.openPlaylists.size, 2);
}
// Reordering playlists and rows keeps keyed identity; source drafts stay focused.
w.editing.add(1080); w.renderQueue(playlists);
const input = row.querySelector('input'); input.value = 'https://example.test/draft'; input.focus(); input.setSelectionRange(5,10);
w.renderQueue(playlists);
assert.equal(row.querySelector('input'), input); assert.equal(w.document.activeElement,input);
assert.equal(input.value,'https://example.test/draft'); assert.equal(input.selectionStart,5);
playlists[0].tracks.reverse(); w.renderQueue(playlists.slice().reverse());
assert.equal(list.querySelector('[data-queue-key="track-1080"]'), row);
assert.equal(tracks.scrollTop,1800);
w.openPlaylists.delete(2); w.renderQueue(playlists);
assert.equal(list.querySelector('[data-queue-key="playlist-2"] .tracks'),null);
assert.equal(list.querySelector('[data-queue-key="playlist-1"] .tracks'),tracks);
// HL-304: the moment between yt-dlp finishing and ffprobe accepting the file is its own
// state. It used to read as "downloading 100%", which is what a hung queue looks like.
const busyRow = list.querySelector('[data-queue-key="track-1000"]');
const verifying = p(3); verifying.tracks[0] = {id: 3000, position: 1, title: 'Almost', artist: 'A', status: 'verifying', progress: 100};
w.openPlaylists.add(3);
w.renderQueue([verifying]);
const vrow = w.document.querySelector('[data-queue-key="track-3000"]');
assert.match(vrow.querySelector('.state').textContent, /checking the file/);
assert.notEqual(vrow.querySelector('.state').textContent, '100%');
assert.equal(vrow.querySelector('.tprog'), null, 'a verifying row is not a progress bar at 100%');
assert.match(w.document.querySelector('[data-queue-key="playlist-3"] .livenow').textContent, /checking the file/);
assert.match(w.document.querySelector('[data-queue-key="playlist-3"] .livebadge').textContent, /Checking the file/);
// The strip above it, which reads the same status from the snapshot.
w.last = {stats: {now: 1000, running: 1, queued: 0, waiting: 0, review: 0, failed: 0},
          current: {status: 'verifying', title: 'Almost', artist: 'A', progress: 100}};
w.renderNow(w.last);
assert.match(w.document.querySelector('#nowbar').textContent, /checking the file/);
assert.equal(busyRow && busyRow.isConnected, false, 'the previous render is gone');

w.ensureDecisionHistory();
assert.match(w.document.querySelector('#decision-history').textContent,/Automatic source decision history/);
w.fetch = async (url, options) => { assert.equal(url,'/api/decisions'); assert.equal(options,undefined); return {ok:true,json:async () => ({entries:[{id:1,track_id:1080,artist:'Artist',title:'<img src=x onerror=bad()>',status:'selected',reason:'Metadata',chosen_url:'https://example.test/song',prior_url:'',candidates:'[]'}]})}; };
w.loadDecisions().then(() => {
  assert.equal(w.document.querySelector('#decision-entries img'),null);
  assert.deepEqual(Array.from(w.document.querySelectorAll('#decision-entries button')).map(n=>n.textContent),['Change source','Reverse decision','Clear entry']);
  console.log('Queue DOM: positive-control reset, 5 polls retain scroll/identity, reorder, expansion, draft/focus, verifing state, safe history controls PASS');
  dom.window.close();
}).catch(e=>{ console.error(e); dom.window.close(); process.exitCode=1; });
