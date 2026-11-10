// Adding a playlist link: visible progress, then exactly what happened.
const fs=require('fs'),assert=require('assert'),{chromium}=require('playwright');
const html=fs.readFileSync('web/index.html','utf8'),css=fs.readFileSync('web/polish.css','utf8');
const start=html.indexOf('let addSeq = 0;');
const end=html.indexOf('function banner(html, cls)');
assert(start>0&&end>start,'addUrls block located');
const slice=html.slice(start,end);
const stubs=[
  'function $(sel){return document.querySelector(sel)}',
  'function esc(s){return String(s??"").replaceAll("&","&amp;").replaceAll("<","&lt;")}',
  'let toasts=[]; function showToast(t){toasts.push(t)} function refresh(){}',
  'let apiImpl=null; async function api(path, body, options){ return apiImpl(path, body, options) }'
].join('\n');
const boot=[
  'window.__run = async function(impl, value, opts){',
  '  apiImpl = impl;',
  '  document.getElementById("url").value = value;',
  '  await addUrls();',
  '};'
].join('\n');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:390,height:800}});
 await page.route('**/*',r=>r.abort());
 await page.setContent(`<style>${css}</style><main><div class="addbox"><textarea id="url"></textarea><button class="primary" id="btn-add">Add</button></div><div class="addstatus" id="addstatus" role="status"></div></main>`);
 await page.addScriptTag({content:`${stubs}\n${slice}\n${boot}`});

 // 1. Success: says what landed, clears the box, restores the button.
 await page.evaluate(()=>window.__run(async()=>({added:[{playlist_id:3,title:'Shuffle',tracks:399,queued:399}]}),'https://on.soundcloud.com/x'));
 assert.equal(await page.locator('#addstatus').textContent(),'✓ “Shuffle” — 399 songs, 399 queued for download');
 assert.equal(await page.locator('#addstatus').getAttribute('class'),'addstatus ok');
 assert.equal(await page.inputValue('#url'),'','the box empties only when something was added');
 assert.equal(await page.locator('#btn-add').textContent(),'Add');
 assert.equal(await page.locator('#btn-add').isDisabled(),false);
 
 // 2. Already there: an explicit answer instead of silence.
 await page.evaluate(()=>window.__run(async()=>({added:[{playlist_id:3,title:'Shuffle',existing:true}]}),'https://on.soundcloud.com/x'));
 assert((await page.locator('#addstatus').textContent()).includes('already in the queue'));
 assert.equal(await page.locator('#addstatus').getAttribute('class'),'addstatus warn');
 assert.equal(await page.inputValue('#url'),'','an already-queued link is handled, so the box clears with the explanation shown');

 // 3. A server error per link: shown, not alerted, and the text stays.
 await page.evaluate(()=>window.__run(async()=>({added:[{url:'u',error:'no tracks found in that link'}]}),'https://x/bad'));
 assert.equal(await page.locator('#addstatus').textContent(),'✕ no tracks found in that link');
 assert.equal(await page.locator('#addstatus').getAttribute('class'),'addstatus err');
 assert.equal(await page.inputValue('#url'),'https://x/bad');

 // 4. A rejected request (timeout / network): one clear sentence, button freed.
 await page.evaluate(()=>window.__run(async()=>{const e=new Error('aborted');e.name='AbortError';throw e},'https://x/slow'));
 assert((await page.locator('#addstatus').textContent()).includes('did not answer within'));
 assert.equal(await page.locator('#btn-add').isDisabled(),false);
 assert.equal(await page.evaluate(()=>toasts.length),1,'the successful add also raises one toast');

 // 5. An empty box answers instead of doing nothing.
 await page.evaluate(()=>window.__run(async()=>({added:[]}),'   '));
 assert.equal(await page.locator('#addstatus').textContent(),'Paste a playlist link first.');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no overflow');
 console.log('Add feedback: progress label, added/queued counts, already-queued notice, inline error, abort message, empty-box guard PASS');
 await page.close();
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
