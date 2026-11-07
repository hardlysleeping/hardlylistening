// Real Chromium coordinate clicks; isolated HTML, canned GETs, no app boot/media/DB.
const fs=require('fs'),assert=require('assert');
const {chromium}=require('playwright');
const html=fs.readFileSync('web/index.html','utf8');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
 for(const width of [1280,390]){
 const page=await browser.newPage({viewport:{width,height:900}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://fixture.test/**',route=>route.fulfill({contentType:'text/html',body:html.slice(0,html.indexOf('<script>')).replace(/<link[^>]*>/g,'').replace(/<script[\s\S]*?<\/script>/g,'')}));
 await page.goto('http://fixture.test/');
 await page.addStyleTag({content:fs.readFileSync('web/polish.css','utf8')});
 await page.evaluate(()=>{
 window.$=s=>document.querySelector(s);window.esc=s=>String(s??'');
 window.playerAudio={src:''};window.localStorage={getItem:()=>null};
 window.fetch=async url=>{window.requests.push(url);return {ok:!window.fail,status:403,json:async()=>window.fail?{error:'Membership required'}:{comments:1,score:0,vote:0,up:0,down:0,items:[{id:1,member_id:'fixture',body:'Fixture comment',created_at:'now'}]}}};
 window.requests=[];window.api=async()=>{throw Error('Fixture write denied')};
 });
 await page.addScriptTag({content:html.slice(html.indexOf('// Personal listening state'),html.indexOf('const playerAudio ='))});
 await page.evaluate(()=>{
 document.body.insertAdjacentHTML('beforeend','<main id="fixture"></main>');
 window.cardClicks=0;
 document.querySelector('#fixture').innerHTML='<div class="songtable">'+songRow({id:1,title:'Song',artist_name:'Artist',album:'Album',playable:true,playlists:[]},0)+'</div><div class="albumgrid"><div class="acard" onclick="cardClicks++"><div class="artbox"><div class="acover">Art</div><div class="bubbles"><div class="bubble">Bubble</div></div></div>'+voteRow('album','a'.repeat(64))+'</div><div class="acard" onclick="cardClicks++">Playlist'+voteRow('playlist',1)+'</div></div>';
 document.querySelector('#fixture').style.cssText='position:absolute;top:0;left:0;width:100%;background:#222;z-index:100';
 });
 await page.addScriptTag({content:html.slice(html.indexOf('function toggleSongPlayback'),html.indexOf('function playTrackNow'))});
 await page.evaluate(()=>{
 window.playCalls=[];window.playTrackNow=id=>{playCalls.push(id);playingId=id;playerAudio.src='mock';playerAudio.paused=false};
 playerAudio.paused=true;playerAudio.play=async()=>{playerAudio.paused=false};playerAudio.pause=()=>{playerAudio.paused=true};
 const fixture=document.querySelector('#fixture');fixture.addEventListener('dblclick',activateSongRow);fixture.addEventListener('pointerup',touchSongRow);
 });
 const rowTitle=page.locator('#fixture .songrow .st b');
 await rowTitle.click();assert.equal(await page.evaluate(()=>playCalls.length),0);
 await rowTitle.dblclick();assert.equal(await page.evaluate(()=>playCalls.length),1);
 await page.locator('#fixture .splay').click();assert(await page.evaluate(()=>playerAudio.paused));
 await page.locator('#fixture .splay').click();assert(!await page.evaluate(()=>playerAudio.paused));
 await page.evaluate(()=>{playCalls.length=0});
 for(const kind of ['track','album','playlist']){
 const button=page.locator(`#fixture .ccount[onclick*="'${kind}'"]`);
 if(!await button.isVisible()){console.log(width,kind,'HIDDEN');continue;}
 for(const fraction of [.15,.5,.85]){
 await button.scrollIntoViewIfNeeded();const r=await button.boundingBox();const x=r.x+r.width*fraction,y=r.y+r.height/2;
 const hit=await page.evaluate(({x,y})=>document.elementFromPoint(x,y)?.outerHTML,{x,y});
 console.log(width,kind,fraction,hit);
 await page.mouse.click(x,y);
 await page.waitForTimeout(30);
 assert(await page.locator('#dlg-social').evaluate(n=>n.open),`Dialog did not open: ${width} ${kind} ${fraction}`);
 assert(await page.locator('#dlg-social textarea').isVisible());
 assert((await page.locator('#dlg-social').innerText()).includes('Fixture comment'));
 await page.evaluate(()=>document.querySelector('#dlg-social').close());
 }
 }
 assert.equal(await page.evaluate(()=>cardClicks),0);
 assert.equal(await page.evaluate(()=>playCalls.length),0);
 await page.evaluate(()=>{
   const row=document.querySelector('#fixture .st b');
   for(const control of document.querySelectorAll('#fixture .songrow button,#fixture .songrow a')){
     control.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
     control.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerType:'touch'}));
   }
   if(playCalls.length) throw Error('Control triggered row playback');
   row.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerType:'touch'}));
   if(playCalls.length) throw Error('Single tap played');
   row.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerType:'touch'}));
   if(playCalls.length!==1) throw Error('Double tap did not play');
   row.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
   if(playCalls.length!==1) throw Error('Touch compatibility dblclick played twice');
 });
 await page.evaluate(()=>{window.fail=true;openDiscussion('track',1)});
 await page.waitForTimeout(30);
 assert((await page.locator('#dlg-social').innerText()).includes('Membership required'));
 assert.deepEqual(errors,[]);
 await page.close();
 }
 console.log('Social coordinate clicks PASS');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
