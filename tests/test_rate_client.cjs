const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');

const html = fs.readFileSync('web/index.html', 'utf8');
const lifecycle = fs.readFileSync('web/lifecycle.js', 'utf8');
const loadStart = html.indexOf('async function loadLibrarySnapshot(){');
const loadEnd = html.indexOf('// ---- where you left off', loadStart);
assert.ok(loadStart >= 0 && loadEnd > loadStart, 'library snapshot function found');
const loadLibrarySnapshot = html.slice(loadStart, loadEnd);

(async () => {
  const dom = new JSDOM('<section id="view-library"><div id="libcooldown" hidden></div><select id="libplaylist"></select></section>', {runScripts: 'outside-only'});
  const w = dom.window;
  let calls = 0;
  let fetchStatus = 429;
  w.fetch = async () => {
    calls++;
    return fetchStatus === 429
      ? {status: 429, ok: false}
      : {status: 200, ok: true, json: async () => ({tracks: [{id: 2}], all_tracks: [{id: 2}], playlists: []})};
  };
  w.eval(`
    let lib = [{id: 1}], libAll = [{id: 1}], artistArt = {}, libPlaylists = [];
    const $ = s => document.querySelector(s);
    const esc = s => String(s ?? '');
    let rendered = 0;
    const renderLibrary = () => rendered++;
    const loadListeningSaves = async () => {};
    const loadSocialIndex = async () => {};
    const applyDeepLink = async () => {};
    const restoreNowState = async () => {};
    ${loadLibrarySnapshot}
    window.snapshotTest = loadLibrarySnapshot;
    window.snapshotState = () => ({lib, rendered});
  `);

  assert.equal(await w.snapshotTest(), false, '429 returns an unsuccessful poll');
  assert.equal(w.snapshotState().lib[0].id, 1, '429 preserves existing library rows');
  assert.equal(w.snapshotState().rendered, 0, '429 does not repaint empty data');
  assert.equal(w.document.querySelector('#libcooldown').hidden, false, '429 shows quiet cooldown note');

  fetchStatus = 200;
  assert.equal(await w.snapshotTest(), true, '200 completes a successful poll');
  assert.equal(w.snapshotState().lib[0].id, 2, '200 replaces library data');
  assert.equal(w.document.querySelector('#libcooldown').hidden, true, 'success clears cooldown note');
  assert.equal(calls, 2);

  const backoffStart = lifecycle.indexOf('function nextPollDelay(');
  const backoffEnd = lifecycle.indexOf('let statePoll', backoffStart);
  assert.ok(backoffStart >= 0 && backoffEnd > backoffStart, 'backoff helper found');
  const context = {};
  vm.runInNewContext(`${lifecycle.slice(backoffStart, backoffEnd)}; this.nextPollDelay = nextPollDelay;`, context);
  const results = [429, 429, 429, 429, 429, 200];
  let delay = 3000;
  const delays = [];
  for (const status of results){
    delay = context.nextPollDelay(delay, status === 200);
    delays.push(delay);
  }
  assert.deepEqual(delays, [6000, 12000, 30000, 30000, 30000, 3000]);
  assert.match(lifecycle, /healthy = \(await loadLibrary\(\)\) === true/,
    'poll scheduler consumes the library request outcome');
  console.log('Rate client: 429 preserves rows, shows quiet note, backs off and resets on 200 PASS');
  dom.window.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
