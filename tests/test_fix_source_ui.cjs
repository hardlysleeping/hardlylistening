// "Wrong song?" — the dialog a person opens from a song row: the versions the sources
// offer, the link box, and the undo for a completed swap. No network, no audio.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('web/index.html', 'utf8');
const source = html.slice(html.indexOf('let fixTrackId'),
  html.indexOf('async function retryTrack'));

const dom = new JSDOM(`<dialog id="dlg-fix"><div id="fix-now"></div>
  <input id="fix-query"><button id="fix-search"></button>
  <p id="fix-status"></p><div id="fix-results"></div>
  <input id="fix-url"><button id="fix-use-url"></button><div id="fix-restore"></div></dialog>`,
  {runScripts: 'outside-only'});
const w = dom.window;
w.eval(`const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmtClock = s => { s = Math.max(0, Math.round(s)); return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0'); };
  let currentRows = [], libAll = [];
  window.setRows = rows => { currentRows = rows; };
  window.__api = [];
  const api = async (...args) => { window.__api.push(args); return {ok:true, replacement:'queued'}; };
  window.api = api;
  const showToast = () => {}; window.showToast = showToast;
  const refresh = () => {}; window.refresh = refresh;
  ${source}
  window.openFixSource = openFixSource; window.searchFixSource = searchFixSource;
`);
const dialog = w.document.querySelector('#dlg-fix');
dialog.showModal = () => { dialog.open = true; };

const hits = [
  {url:'https://soundcloud.com/x/right', title:'Atmosphere', artist:'FISHER', duration:220,
   provider:'soundcloud', match:0.91},
  {url:'https://soundcloud.com/x/remix', title:'Atmosphere (Remix)', artist:'Someone',
   duration:310, provider:'soundcloud', match:0.62},
];
let payload = {results: hits, replacement: null};
let seen = [];
w.fetch = async url => { seen.push(url); return {ok:true, json: async () => payload}; };
w.confirm = () => false;

const tick = () => new Promise(r => setTimeout(r, 20));
const buttons = () => [...w.document.querySelectorAll('#fix-results button')];

(async () => {
  w.setRows([{id:7, title:'Atmosphere', artist_name:'FISHER', duration:221,
              provider:'soundcloud', playable:true}]);
  w.openFixSource(7);
  await tick();
  assert.equal(w.document.querySelector('#fix-now').textContent, 'Atmosphere · FISHER · 3:41');
  assert.equal(w.document.querySelector('#fix-query').value, 'FISHER Atmosphere');
  assert.match(seen[0], /\/api\/track\/7\/candidates\?q=FISHER%20Atmosphere/);
  assert.equal(buttons().length, 2);
  assert.match(w.document.querySelector('#fix-status').textContent, /2 versions/);

  // A deliberate choice needs the confirmation; a declined one changes nothing.
  buttons()[0].onclick();
  await tick();
  assert.equal(w.__api.length, 0);
  w.confirm = () => true;
  buttons()[1].onclick();
  await tick();
  assert.equal(JSON.stringify(w.__api.at(-1)),
    JSON.stringify(['/api/track/7/source', {url:'https://soundcloud.com/x/remix'}]));
  assert.match(w.document.querySelector('#fix-status').textContent, /Downloading the replacement/);

  // A completed swap offers its retained previous file as the undo.
  payload = {results: hits, replacement: {id: 9, status: 'completed'}};
  w.openFixSource(7);
  await tick();
  const restore = w.document.querySelector('#fix-restore button');
  assert.ok(restore, 'restore button');
  restore.onclick();
  await tick();
  assert.equal(JSON.stringify(w.__api.at(-1)),
    JSON.stringify(['/api/decisions/reverse', {id: -9}]));

  // A replacement in flight says so instead of offering a second one blindly.
  payload = {results: hits, replacement: {id: 10, status: 'downloading'}};
  w.openFixSource(7);
  await tick();
  assert.match(w.document.querySelector('#fix-status').textContent, /already downloading/);
  assert.equal(w.document.querySelector('#fix-restore button'), null);

  // A failed search is reported on the dialog, not swallowed.
  payload = {results: [], replacement: null};
  w.fetch = async () => ({ok:false, json: async () => ({error:'Rate limited'})});
  w.openFixSource(7);
  await tick();
  assert.equal(w.document.querySelector('#fix-status').textContent, 'Rate limited');
  assert.equal(buttons().length, 0);

  console.log('Wrong song? dialog: options, link box, undo and failures PASS');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => dom.window.close());
