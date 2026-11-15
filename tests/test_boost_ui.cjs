// Actual Feed renderer slice: no network, DB, audio or live account.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('web/js/social-feed.js','utf8');
const start=html.indexOf('function ago(ts)');
const end=html.indexOf('async function loadActivity()',start);
assert(start>=0&&end>start,'Feed renderer anchors exist');
const ctx=vm.createContext({
  esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
  profileSymbol:()=>'*'
});
vm.runInContext(html.slice(start,end),ctx);
const item={kind:'boost',member_id:'u1',member:'A <b>name</b>',member_icon:'star',ts:Date.now()/1000,
  expires_at:Date.now()/1000+3*3600,track:{id:12,title:'Song <test>',artist_name:'Artist'}};
const card=ctx.actCard(item);
assert(card.includes('class="actrow actboost"'));
assert(card.includes('Song &lt;test&gt;')&&card.includes('Artist'));
assert(card.includes('3h left')&&card.includes('Pinned at the top'));
assert(card.includes('A &lt;b&gt;name&lt;/b&gt;')&&!card.includes('A <b>name</b>'));
console.log('Boost Feed card: top-pin label, track/member escaping, remaining time PASS');
