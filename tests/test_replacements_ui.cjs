// No browser audio or server. Exercise explicit replacement history actions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('web/index.html','utf8');
const source = html.slice(html.indexOf('async function loadDecisions(){'),html.indexOf('function ensureDecisionHistory(){'));
const dom = new JSDOM('<div id="decision-entries"></div>',{runScripts:'outside-only'});
const w = dom.window;
w.eval(`const $ = s => document.querySelector(s); const isAdmin = () => true; ${source}; window.loadDecisions = loadDecisions;`);
let status = 'replacement: queued';
let entry = {id:-1,track_id:42,artist:'Artist',title:'Song',status,reason:'Original retained',chosen_url:'https://example.test/new',prior_url:'https://example.test/old',candidates:'[]'};
w.fetch = async () => ({ok:true,json:async () => ({entries:[entry]})});
const calls = []; const errors = [];
w.api = async (...args) => {calls.push(args);}; w.refresh = async () => {};
w.alert = text => errors.push(text); w.prompt = () => 'https://example.test/change'; w.confirm = () => false;
const buttons = () => Array.from(w.document.querySelectorAll('button'));
(async () => {
  await w.loadDecisions();
  assert.deepEqual(buttons().map(b=>[b.textContent,b.disabled]),[['Change source',true],['Reverse decision',true],['Clear entry',false],['Cancel replacement',false]]);
  await buttons()[3].onclick();
  assert.equal(calls[0][0],'/api/track/42/skip');
  for (const state of ['failed','cancelled','interrupted','reversed']){
    entry.status = 'replacement: '+state; entry.reason = '<img src=x> explicit error';
    await w.loadDecisions();
    assert.equal(buttons()[0].disabled,false); assert.equal(buttons()[1].disabled,true);
    assert.equal(w.document.querySelector('img'),null);
    assert.match(w.document.body.textContent,/explicit error/);
  }
  entry.status = 'replacement: completed'; await w.loadDecisions();
  assert.equal(buttons()[1].disabled,false);
  const before = calls.length; await buttons()[0].onclick(); assert.equal(calls.length,before); // deliberate confirmation required
  w.confirm = () => true; await buttons()[0].onclick();
  assert.equal(calls.at(-1)[0],'/api/track/42/source');
  assert.equal(calls.at(-1)[1].url,'https://example.test/change');
  await buttons()[1].onclick(); assert.equal(calls.at(-1)[0],'/api/decisions/reverse'); assert.equal(calls.at(-1)[1].id,-1);
  w.api = async () => {throw new Error('Retained previous file missing');};
  await buttons()[1].onclick(); assert.equal(errors.at(-1),'Retained previous file missing');
  console.log('Replacement DOM: explicit confirmation, states, cancel/reverse and errors PASS');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>dom.window.close());
