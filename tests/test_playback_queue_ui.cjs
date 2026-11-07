// Isolated real queue/editor script, mocked media and playlist contract.
const fs=require('fs'),assert=require('assert'),{chromium}=require('playwright');
const html=fs.readFileSync('web/index.html','utf8'),css=fs.readFileSync('web/polish.css','utf8');
const markup=html.slice(html.indexOf('<aside id="playback-panel"'),html.indexOf('// Playback queue and owner playlist editor')).split('<script>')[0];
const script=html.split('// Playback queue and owner playlist editor (no media or network on initialization).')[1].split('</script>')[0];
const queueSong=html.slice(html.indexOf('function queueSong('),html.indexOf('function playTrackNow('));
(async()=>{const browser=await chromium.launch({headless:true});try{for(const width of [1280,390]){
 const page=await browser.newPage({viewport:{width,height:1100}});let network=0;await page.route('**/*',r=>{network++;r.abort()});
 await page.goto('about:blank');await page.setContent(`<style>${html.match(/<style>([\s\S]*?)<\/style>/)[1]}${css}</style>${markup}`);
 await page.addScriptTag({content:`
 const memory={};const localStorage={getItem:k=>memory[k]??null,setItem:(k,v)=>memory[k]=v};
 let libAll=[1,2,3,4].map(id=>({id,title:'Song '+id,artist_name:'Artist',playable:id!==4})),currentRows=libAll,libQueue=[],playingId=null,libIndex=-1,playbackQueueEdited=false;
 let playerAudio={src:'',play(){throw Error('Media forbidden')}},calls=[],posts=[],conflict=false;
 function esc(s){return String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')}
 function showToast(){} function playTrackNow(id){calls.push(['now',id])}
 let fixture={id:8,title:'Mine',can_edit:true,track_ids:[1,2],owner_id:'me'};
 async function fetch(url){return {ok:true,json:async()=>url==='/api/playlists'?{playlists:[fixture,{id:9,title:'Other',can_edit:false,track_ids:[3]}]}:{playlist:fixture}}}
 async function api(url,body){posts.push([url,JSON.parse(JSON.stringify(body))]);if(conflict)throw Error('Playlist changed; reload before editing');fixture={...fixture,title:body.title||fixture.title,track_ids:body.track_ids};return {playlist:fixture}}
 ${queueSong}
 // Playback queue and owner playlist editor${script}`});
 const run=s=>page.evaluate(s);
 // The panel is a drawer now: open it the way the player button does before clicking rows.
 await run(`document.querySelector('#playback-panel').classList.add('open')`);
 await run(`queueSong(1);queueSong(2);queueSong(3,true)`);assert.equal(await run('libQueue.map(t=>t.id).join()'),'3,1,2');assert.equal(await run('playerAudio.src'),'');
 assert.equal(await page.locator('#playback-count').textContent(),'3','the badge counts what is up next');
 // Compact queue: no arrow buttons at all, keyboard moves with Alt+arrows.
 assert.equal(await page.locator('#playback-items [data-action="top"], #playback-items [data-action="up"], #playback-items [data-action="down"], #playback-items [data-action="bottom"]').count(),0);
 await page.locator('#playback-items [data-id="2"]').focus();await page.keyboard.press('Alt+Home');assert.equal(await run('libQueue.map(t=>t.id).join()'),'2,3,1');
 await run(`queueMove(2,'down');queueMove(2,'bottom');queueMove(2,'up')`);assert.equal(await run('libQueue.map(t=>t.id).join()'),'3,2,1');
 // Drag by pressing the row itself (no grip in the queue); real mouse coordinates.
 let row3=await page.locator('[data-id="3"]').boundingBox(),row1=await page.locator('[data-id="1"]').boundingBox();
 await page.mouse.move(row3.x+60,row3.y+row3.height/2);await page.mouse.down();await page.mouse.move(row1.x+60,row1.y+row1.height-2,{steps:8});await page.mouse.up();
 assert.equal(await run('libQueue.map(t=>t.id).join()'),'2,1,3');
 // Touch needs a hold to lift the row; cancel restores, release commits.
 const hold=cancel=>page.evaluate(async cancel=>{const host=document.querySelector('#playback-items'),row=host.querySelector('[data-id="2"]'),target=host.querySelector('[data-id="3"]'),box=target.getBoundingClientRect(),own=row.getBoundingClientRect();row.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:17,pointerType:'touch',isPrimary:true,clientX:own.x+40,clientY:own.y+own.height/2}));await new Promise(r=>setTimeout(r,330));row.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:17,pointerType:'touch',clientX:box.x+20,clientY:box.y+box.height-2}));row.dispatchEvent(new PointerEvent(cancel?'pointercancel':'pointerup',{bubbles:true,pointerId:17,pointerType:'touch'}));await new Promise(r=>setTimeout(r,40));},cancel);
 await hold(true);assert.equal(await run('libQueue.map(t=>t.id).join()'),'2,1,3');
 await hold(false);assert.equal(await run('libQueue.map(t=>t.id).join()'),'1,3,2');
 await run(`playingId=3;queueMove(3,'remove')`);assert.equal(await run('playingId'),3);assert.equal(await run('libIndex'),-1);
 await run(`memory['hl.playbackQueue.v1']='[3,4,999,1,1]';libQueue=[];queueRestorePending=true;syncPlaybackQueue()`);assert.equal(await run('libQueue.map(t=>t.id).join()'),'3,1');assert.equal(await run('playerAudio.src'),'');
 await page.locator('#playback-items .pq-row').first().click();assert.deepEqual(await run('calls'),[['now',3]],'tapping a row plays it');
 assert(await run('document.documentElement.scrollWidth<=innerWidth'));
 await page.getByRole('button',{name:'Create / edit playlists'}).click();await page.locator('#playlist-choice').selectOption('8');
 await page.locator('#playlist-members [data-id="2"] [data-action="top"]').click();await page.waitForTimeout(30);
 assert.deepEqual(await run('posts.at(-1)[1]'),{track_ids:[2,1],expected_track_ids:[1,2]});assert.equal(await page.locator('#playlist-members .pq-row').first().getAttribute('data-id'),'2');
 await page.locator('#playlist-candidates .pq-row').filter({hasText:'Song 4'}).getByRole('button').click();await page.waitForTimeout(30);assert.deepEqual(await run('fixture.track_ids'),[2,1,4]);
 await page.evaluate(async()=>{const host=document.querySelector('#playlist-members'),grip=host.querySelector('[data-id="4"] .pq-grip'),row=host.querySelector('[data-id="2"]'),box=row.getBoundingClientRect();grip.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:22,pointerType:'touch'}));grip.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:22,pointerType:'touch',clientX:box.x+20,clientY:box.y+2}));grip.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:22,pointerType:'touch'}));});await page.waitForTimeout(30);assert.deepEqual(await run('fixture.track_ids'),[4,2,1]);
 await run('conflict=true');await page.locator('#playlist-members [data-id="1"] [data-action="remove"]').click();await page.waitForTimeout(30);assert((await page.locator('#playlist-message').textContent()).includes('reload'));assert.deepEqual(await run('fixture.track_ids'),[4,2,1]);
 await page.locator('#playlist-choice').selectOption('9');assert.equal(await page.locator('#playlist-members button').count(),0);
 await run('conflict=false');await page.locator('#playlist-name').fill('New list');await page.getByRole('button',{name:'Create playlist',exact:true}).click();await page.waitForTimeout(30);assert.deepEqual(await run('posts.at(-1)'),['/api/playlists',{title:'New list',track_ids:[]}]);
 assert(await run('document.documentElement.scrollWidth<=innerWidth'));await page.getByRole('button',{name:'Close playlist editor'}).click();await page.getByRole('button',{name:'Clear queue',exact:true}).click();assert.deepEqual(await run('libQueue'),[]);assert.equal(await run("memory['hl.playbackQueue.v1']"),'[]');assert.equal(network,0);
 await page.close();console.log(`Playback queue/editor ${width}: pointer/touch/cancel, keyboard moves, persistence/no autoplay, owner contract, create/pending membership/conflict/readback and overflow PASS`);
}}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
