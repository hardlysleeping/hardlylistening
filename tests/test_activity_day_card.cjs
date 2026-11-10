// Activity Feed day cards: collapsed by default, five at a time, no flooding.
const fs=require('fs'),assert=require('assert'),{chromium}=require('playwright');
const html=fs.readFileSync('web/index.html','utf8'),css=fs.readFileSync('web/polish.css','utf8');
const slice=html.slice(html.indexOf('function actWho(item){'),html.indexOf('async function loadActivity(){'));
const helpers=[
  'const openDays=new Set(); const openedMembers=[];',
  'function esc(s){return String(s??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(\'"\',"&quot;")}',
  'function ago(ts){return "ago"}',
  'function profileSymbol(){return ""}',
  'function openMember(id){openedMembers.push(id)}'
].join('\n');
const render=[
  'const now=new Date(); const day=now.getFullYear()+"-"+String(now.getMonth()+1).padStart(2,"0")+"-"+String(now.getDate()).padStart(2,"0");',
  'const card={kind:"day",member_id:"m-ann",member:"Ann",member_icon:"",day:day,ts:Date.now()/1000,',
  '  counts:{listen:8,add:3,playlist:2,other:0},',
  '  groups:{listen:Array.from({length:8},(_,i)=>({id:i+1,title:"Song "+(i+1),artist_name:"Artist"})),',
  '          add:[{id:21,title:"Added A",artist_name:"Artist"},{id:22,title:"Added B",artist_name:"Artist"}],',
  '          playlist:[{kind:"playlist_new",title:"Mix one"},{kind:"playlist_edit",title:"Mix two"},{kind:"playlist_new",title:"Mix three"}],',
  '          other:[]}};',
  'const host=document.getElementById("activitylist"); host.innerHTML=actCard(card); bindActivityCards(host);'
].join('\n');
(async()=>{const browser=await chromium.launch({headless:true});try{
 for(const width of [1280,390]){
  const page=await browser.newPage({viewport:{width,height:900}});
  await page.route('**/*',r=>r.abort());
  await page.setContent(`<style>${html.match(/<style>([\s\S]*?)<\/style>/)[1]}${css}</style><main><div id="activitylist"></div></main>`);
  await page.addScriptTag({content: `${helpers}\n${slice}\n${render}`});

  assert.equal(await page.locator('#activitylist .actday').count(),1);
  assert.equal(await page.locator('.actbody').isHidden(),true,'detail starts collapsed');
  const summary=await page.locator('.actsum').textContent();
  assert(summary.includes('8 songs played')&&summary.includes('3 added')&&summary.includes('2 playlists'),'glance summary carries the counts: '+summary);
  assert.equal(await page.locator('.actwhen').textContent(),'Today','day label');
  const collapsedHeight=await page.locator('#activitylist .actday').evaluate(e=>e.getBoundingClientRect().height);
  // One line on desktop, name + counts on two short lines on a phone.
  assert(collapsedHeight < (width < 760 ? 80 : 55), 'collapsed card stays compact, measured '+collapsedHeight);

  await page.locator('.acttop').click();
  assert.equal(await page.locator('.actbody').isVisible(),true);
  assert.equal(await page.locator('.acttop').getAttribute('aria-expanded'),'true');
  assert.equal(await page.locator('.actgroup').count(),3,'played / added / playlists');
  const played=page.locator('.actgroup').first();
  assert.equal(await played.locator('.actitems').first().locator('li').count(),5,'five rows before Show more');
  assert.equal(await played.locator('.actrest').isHidden(),true,'the rest stays hidden until asked');
  assert.equal(await played.locator('.actmore').textContent(),'Show 3 more');
  await played.locator('.actmore').click();
  assert.equal(await played.locator('.actitems li').count(),8,'the rest is revealed on demand');
  assert.equal(await played.locator('.actrest').isVisible(),true);
  assert.equal(await played.locator('.actmore').count(),0,'the button goes away');
  assert.equal(await page.locator('.actgroup').nth(1).locator('.actitems li').count(),2,'small groups show everything');
  assert.equal(await page.locator('.actgroup').nth(1).locator('.actmore').count(),0);

  await page.locator('.acttop').click();
  assert.equal(await page.locator('.actbody').isHidden(),true,'re-collapses');
  await page.locator('.actwho').click();
  assert.deepEqual(await page.evaluate(()=>openedMembers),['m-ann']);
  assert.equal(await page.locator('.actbody').isHidden(),true,'a name tap must not expand the card');

  // A day whose only event is a join must say "joined", never "1 other action".
  const joinCard=await page.evaluate(()=>{
    const card={kind:'day',member_id:'m-new',member:'Nina',day:'2026-11-08',ts:Date.now()/1000,
      counts:{other:1},groups:{listen:[],add:[],playlist:[],other:[{kind:'join',detail:'Nina'}]}};
    const host=document.getElementById('activitylist');
    host.innerHTML=actCard(card); bindActivityCards(host);
    const node=document.querySelector('#activitylist .actday');
    return {sum:node.querySelector('.actsum').textContent.trim(), body:node.querySelector('.actbody').textContent.replace(/\s+/g,' ').trim()};
  });
  assert.equal(joinCard.sum,'joined','a join is named, not counted as an anonymous action');
  assert(joinCard.body.includes('joined the library'),'the detail names it too: '+joinCard.body);
  assert(!/other action/i.test(joinCard.sum+joinCard.body),'no "other action" anywhere: '+joinCard.sum+' / '+joinCard.body);
  const unknown=await page.evaluate(()=>{
    const card={kind:'day',member_id:'m-x',member:'X',day:'2026-11-08',ts:Date.now()/1000,
      counts:{other:1},groups:{listen:[],add:[],playlist:[],other:[{kind:'some_new_kind'}]}};
    const host=document.getElementById('activitylist'); host.innerHTML=actCard(card); bindActivityCards(host);
    return document.querySelector('#activitylist .actday').textContent.replace(/\s+/g,' ').trim();
  });
  assert(!unknown.includes('some_new_kind'),'a raw internal kind is never printed: '+unknown);

  const order=await page.evaluate(()=>{
    const second={kind:'day',member_id:'m-bob',member:'Bob',day:'2026-11-08',ts:Date.now()/1000,
      counts:{listen:2},groups:{listen:[{title:'B1'},{title:'B2'}],add:[],playlist:[],other:[]}};
    const first={kind:'day',member_id:'m-ann',member:'Ann',day:'2026-11-07',ts:Date.now()/1000,
      counts:{listen:1},groups:{listen:[{title:'A1'}],add:[],playlist:[],other:[]}};
    const host=document.getElementById('activitylist');
    host.innerHTML=[first,second].map(actCard).join('');
    bindActivityCards(host);
    return [...document.querySelectorAll('.actday')].map(n=>n.querySelector('.actwho').textContent.trim());
  });
  assert.deepEqual(order,['Ann','Bob'],'server order preserved: newest first');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal overflow at '+width);
  await page.close();
 }
 console.log('Activity day card: one collapsed line per member-day, counts at a glance, 5 + Show more, name opens the profile, no overflow PASS');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
