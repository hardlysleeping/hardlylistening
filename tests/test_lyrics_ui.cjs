// Real isolated jsdom; no app boot, media playback or network.
const fs=require('fs'),assert=require('assert'),{JSDOM}=require('jsdom');
const html=fs.readFileSync('web/index.html','utf8');
const dom=new JSDOM(html,{runScripts:'outside-only',url:'https://music.example'});
const w=dom.window,d=w.document,a=d.getElementById('player-audio'),dialog=d.getElementById('dlg-lyrics');
dialog.showModal=()=>{dialog.open=true};dialog.close=()=>{dialog.open=false;dialog.dispatchEvent(new w.Event('close'))};
w.HTMLElement.prototype.scrollIntoView=function(){};
let listeners=new Set(), fetches=0, aborted=0;
const add=a.addEventListener.bind(a),remove=a.removeEventListener.bind(a);
a.addEventListener=(event,fn)=>{listeners.add(event);add(event,fn)};
a.removeEventListener=(event,fn)=>{listeners.delete(event);remove(event,fn)};
let result={status:'synced',source:'mock',lines:[{time:1,text:'<img src=x>'},{time:3,text:'Next'},{time:5,text:'Last'}]};
w.fetch=async(url,options)=>{fetches++;options.signal.addEventListener('abort',()=>aborted++);return {ok:true,json:async()=>result}};
a.play=()=>{throw Error('Must not play audio')};
a.setAttribute('src','/api/track/1/audio');
w.eval(fs.readFileSync('web/lyrics.js','utf8'));
const click=id=>d.getElementById(id).click(),tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 click('pllyrics');await tick();assert.equal(fetches,1);assert.equal(listeners.size,6);
 a.currentTime=1.5;a.dispatchEvent(new w.Event('timeupdate'));
 assert.equal(d.querySelector('#lyrics-body .current').textContent,'<img src=x>');assert(!d.querySelector('#lyrics-body img'));
 click('lyrics-karaoke');assert(dialog.classList.contains('karaoke'));
 a.currentTime=4;a.dispatchEvent(new w.Event('seeking'));assert.equal(d.querySelector('#lyrics-body .current').textContent,'Next');
 a.currentTime=.2;a.dispatchEvent(new w.Event('ratechange'));assert(!d.querySelector('#lyrics-body .current'));
 assert.equal(d.querySelector('#lyrics-body .next').textContent,'<img src=x>');
 click('lyrics-close');assert.equal(listeners.size,0);assert(aborted>0);assert.equal(d.getElementById('lyrics-body').children.length,0);
 result={status:'plain',source:'mock',text:'Plain <script>unsafe</script>'};click('pllyrics');await tick();
 assert(d.getElementById('lyrics-karaoke').disabled);assert(d.getElementById('lyrics-status').textContent.includes('timing unavailable'));
 assert.equal(d.querySelector('#lyrics-body pre').textContent,result.text);
 result={status:'instrumental',lines:[]};a.setAttribute('src','/api/track/2/audio');a.dispatchEvent(new w.Event('loadstart'));await tick();
 assert(d.getElementById('lyrics-status').textContent.includes('instrumental'));assert.equal(d.getElementById('lyrics-body').children.length,0);
 // Late response after close cannot repaint or leak listeners.
 let resolve;w.fetch=()=>new Promise(r=>resolve=r);a.dispatchEvent(new w.Event('loadstart'));click('lyrics-close');
 resolve({ok:true,json:async()=>({status:'plain',text:'late'})});await tick();assert.equal(listeners.size,0);assert.equal(d.getElementById('lyrics-body').children.length,0);
 dom.window.close();console.log('lyrics DOM: synchronized seek/rate updates, plain fallback, escaping, track changes and listener/request cleanup passed');
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
