// Real renderer, isolated saves and browser DOM. No HTTP, database or media.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {chromium}=require('playwright');
const html=fs.readFileSync('web/index.html','utf8'),css=fs.readFileSync('web/polish.css','utf8');
const elements={},storage={};
const el=id=>elements[id] ||= {value:'',innerHTML:'',textContent:'',classList:{toggle(){}},querySelectorAll:()=>[]};
const ctx=vm.createContext({$:el,esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),localStorage:{getItem:k=>storage[k],setItem:(k,v)=>storage[k]=v},document:{activeElement:null,querySelectorAll:()=>[]},scrollTo(){}});
vm.runInContext('let playerAudio={src:"",paused:true};',ctx);
vm.runInContext(html.slice(html.indexOf('// Personal listening state'),html.indexOf('const playerAudio =')),ctx);
const run=s=>vm.runInContext(s,ctx),out=()=>el('#liblist').innerHTML;
vm.runInContext(html.slice(html.indexOf('function queueSong('),html.indexOf('function playTrackNow(')),ctx);
ctx.showToast=()=>{};
run(`libAll=[1,2,3].map(id=>({id,title:'Song '+id,artist_name:'Artist',album:'Album',album_id:'a',playable:id!==3,status:'queued',duration:180,playlists:[{id:7,title:'Set',position:id}]}));lib=libAll.filter(t=>t.playable);libPlaylists=[{id:7,title:'Set'}];listeningSaves={tracks:[1],playlists:[],playlist_tracks:[]};libView='songs';`);
ctx.renderLibrary();assert(out().includes('songtable'));ctx.setSongLayout('grid');assert.equal(storage['hl.songLayout'],'grid');assert(out().includes('songgrid'));
ctx.setLibraryScope('mine');assert(out().includes('Song 1')&&!out().includes('Song 2'));
ctx.setLibraryScope('shared');el('#libsearch').value='Song 2';ctx.renderLibrary();assert(out().includes('Song 2')&&!out().includes('Song 1'));
el('#libsearch').value='';run("libSort='title';libSortDir='asc';renderLibrary()");assert(out().indexOf('Song 1')<out().indexOf('Song 2'));assert(out().includes('queued'));assert(out().includes('Remove from library')&&out().includes('Add to library'));
ctx.queueSong(2);ctx.renderLibrary();assert.equal(run('libQueue.map(t=>t.id).join()'),'2');ctx.queueSong(1,true);assert.equal(run('libQueue.map(t=>t.id).join()'),'1,2');ctx.queueSong(3);assert.equal(run('libQueue.length'),2);assert.equal(run('playerAudio.src'),'');
run("playingId=1;libIndex=0;playerAudio.src='fixture';");ctx.queueSong(2,true);assert.equal(run('libIndex'),0);assert.equal(run('libQueue[1].id'),2);
const fixture=out();ctx.setSongLayout('list');assert(out().includes('songtable'));ctx.setSongLayout('invalid');assert.equal(storage['hl.songLayout'],'list');
const contextScript='// Song context actions:' + html.split('// Song context actions:')[1].split('</script>')[0];
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{for(const width of [1280,390]){
 const page=await browser.newPage({viewport:{width,height:900}});
 await page.route('**/*',r=>r.abort());
 await page.setContent(`<style>${html.match(/<style>([\s\S]*?)<\/style>/)[1]}${css}</style><main>${fixture}</main>`);
 await page.evaluate(()=>{window.calls=[];window.playTrackNow=id=>calls.push(['now',id]);window.queueSong=(id,next=false)=>calls.push(['queue',id,next]);window.toggleSongPlayback=id=>calls.push(['play',id]);window.toggleListeningSave=(kind,id)=>calls.push(['save',id]);window.castVote=()=>calls.push(['vote']);window.openDiscussion=()=>calls.push(['comment']);window.listeningSaves={folders:[{id:9,parent_id:null,name:'Practice'}],folder_tracks:[]};window.libraryScope='mine';window.activeFolderId=null;window.folderPath=id=>'Practice';window.addTrackToPersonalFolder=(id,folder)=>calls.push(['folder',id,folder]);window.boostTrack=id=>calls.push(['boost',id]);HTMLMediaElement.prototype.play=()=>{throw Error('Media forbidden')};});
 await page.addScriptTag({content:contextScript});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const row=page.locator('.songcard').first();assert.equal(await row.locator('.sdl').count(),1);
 await row.locator('.st b').click();assert.deepEqual(await page.evaluate(()=>calls),[]);
 await row.locator('.st b').click({button:'right'});assert.equal(await page.locator('[role=menu]').count(),1);
 assert.equal(await page.getByRole('menuitem',{name:'Add to Practice',exact:true}).count(),1);
 const boostItem=page.getByRole('menuitem',{name:'Boost to top of Feed (24h)',exact:true});assert.equal(await boostItem.count(),1);await boostItem.click();
 assert.deepEqual(await page.evaluate(()=>calls),[['boost',1]]);await page.evaluate(()=>calls=[]);
 await row.locator('.st b').click({button:'right'});assert.equal(await page.locator('[role=menu]').count(),1);
 await page.keyboard.press('Escape');assert.equal(await page.locator('[role=menu]').count(),0);
 await row.focus();await page.keyboard.press('Shift+F10');await page.getByRole('menuitem',{name:'Play',exact:true}).click();assert.deepEqual(await page.evaluate(()=>calls),[['play',1]]);
 await row.locator('.st b').dispatchEvent('pointerdown',{pointerType:'touch',isPrimary:true,clientX:50,clientY:50});await page.waitForTimeout(600);assert.equal(await page.locator('[role=menu]').count(),1);
 const box=await page.locator('[role=menu]').boundingBox();assert(box.x>=0&&box.x+box.width<=width);await page.keyboard.press('Escape');
 for(const cancel of ['pointercancel','pointermove','scroll']){
 await row.dispatchEvent('pointerdown',{pointerType:'touch',isPrimary:true,clientX:50,clientY:50});
 if(cancel==='scroll')await page.evaluate(()=>window.dispatchEvent(new Event('scroll')));else await row.dispatchEvent(cancel,{clientX:100,clientY:100});
 await page.waitForTimeout(600);assert.equal(await page.locator('[role=menu]').count(),0,cancel);
 }
 const native=await row.locator('.lsave').evaluate(e=>{const event=new MouseEvent('contextmenu',{bubbles:true,cancelable:true});e.dispatchEvent(event);return event.defaultPrevented;});assert.equal(native,false);
 for(const name of ['Play now','Play next','Add to queue']){await row.focus();await page.keyboard.press('Shift+F10');await page.getByRole('menuitem',{name,exact:true}).click();}
 assert.deepEqual((await page.evaluate(()=>calls)).slice(1),[['now',1],['queue',1,true],['queue',1,false]]);
 await page.screenshot({path:`/tmp/hardlylistening-songs-${width}.png`,fullPage:true});await page.close();
 }
 console.log('Songs grid: persistence, shared/personal scope, search, sort, statuses, controls; Chromium 1280/390 no overflow, inert card click, context keyboard/right-click/touch/cancellation/native controls PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
