const assert=require('assert');
const fs=require('fs');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync('web/party.html','utf8');
const code=fs.readFileSync('web/party.js','utf8');
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const dom=new JSDOM(html,{url:'https://music.example/party?party=abcdefghijklmnop',runScripts:'outside-only'}),w=dom.window;
 const requests=[];let chats=0,plays=0;
 w.setTimeout=()=>1;w.clearTimeout=()=>{};w.setInterval=()=>2;w.clearInterval=()=>{};
 w.HTMLMediaElement.prototype.play=function(){plays++;return Promise.resolve();};w.HTMLMediaElement.prototype.pause=function(){};
 let roomState={title:'Party',expires:2000000000,viewer:{display_name:'Member',profile_icon:'star'},current:null,queue:[{entry_id:1,id:1,title:'<unsafe>',artist_name:'Artist',vote:0,score:2}]};
 w.fetch=async(url,opts={})=>{requests.push([url,opts]);let data;
 if(url==='/api/party')data={parties:[]};
 else if(url.includes('/chat?')){chats++;data={messages:chats===1?[{id:1,display_name:'<img src=x onerror=alert(1)>',profile_icon:'moon',body:'<script>alert(1)</script>'}]:chats===2?[{id:2,display_name:'Guest',profile_icon:'headphones',body:'second'}]:[],cursor:chats===1?1:2};}
 else if(url.endsWith('/chat'))data={id:3};
 else if(url.endsWith('/skip'))data={skipped:false,skips:1,people:3,current:9};
 else data=roomState;
 return {ok:true,json:async()=>data};};
 w.eval(code+'\nwindow.testPartyRefresh=refresh;');await tick();await tick();await tick();
 const $=id=>w.document.getElementById(id);
 assert.equal(plays,0,'boot must never play audio');
 assert.equal($('messages').querySelectorAll('img,script').length,0,'chat is text only');
 assert($('messages').textContent.includes('<script>'));
 assert($('messages').textContent.includes('☾'));
 assert.equal($('queue').querySelectorAll('unsafe').length,0);
 const draft=$('message');draft.value='unsent draft';draft.focus();
 await w.testPartyRefresh();
 assert.equal($('message'),draft);assert.equal(draft.value,'unsent draft');assert.equal(w.document.activeElement,draft);
 assert.equal($('messages').children.length,2);assert($('messages').textContent.includes('🎧 Guest'));
 assert(requests.some(([url])=>url.endsWith('/chat?after=1')),'incremental cursor');
 assert(!code.includes('.innerHTML'),'no untrusted HTML sink');
 assert(!code.includes('localStorage'),'no credential browser storage');
 // Submission uses only body and preserves edits made during a request.
 $('chat').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();await tick();
 const sent=requests.find(([url,opts])=>url.endsWith('/chat')&&opts.method==='POST');
 assert.deepEqual(JSON.parse(sent[1].body),{body:'unsent draft'});assert.equal(draft.value,'');
 // Skip: counts, "next up" and the POST body all come from the room state.
 roomState={...roomState,
   current:{entry_id:9,id:1,title:'Now playing',artist_name:'Artist'},
   next:{entry_id:11,id:2,title:'Up next',artist_name:'Other'},
   participants:3,skip:{count:1,mine:false,needed:2}};
 await w.testPartyRefresh();
 assert.equal($('skip').disabled,false);
 assert.equal($('skip').textContent,'Skip');
 assert($('skipcount').textContent.includes('1 of 3 want to skip')&&$('skipcount').textContent.includes('2 skips needed'),$('skipcount').textContent);
 assert.equal($('nextup').textContent,'Next up: Up next — Other');
 $('skip').dispatchEvent(new w.Event('click'));await tick();await tick();
 const skipped=requests.find(([url,opts])=>url.endsWith('/skip')&&opts.method==='POST');
 assert.deepEqual(JSON.parse(skipped[1].body),{expected:9});
 roomState={...roomState,skip:{count:2,mine:true,needed:2}};
 await w.testPartyRefresh();
 assert.equal($('skip').disabled,true,'a second skip by the same person is refused');
 assert.equal($('skip').textContent,'Skip requested');
 assert($('skipcount').textContent.startsWith('Your skip is in'));
 roomState={...roomState,current:null,next:null,participants:0,skip:{count:0,mine:false,needed:1}};
 await w.testPartyRefresh();
 assert.equal($('skip').disabled,true,'nothing playing means nothing to skip');
 assert.equal($('nextup').textContent,'Next up: nothing queued');
 // Deck bar: who holds the room's one speaker, and a member's play/pause and gain.
 roomState={...roomState,deck:{playing:true,gain:0.5,host_active:true,controlled_by:'Host'}};
 await w.testPartyRefresh();
 assert($('deckstate').textContent.includes("Host's device")&&$('deckstate').textContent.includes('playing'),$('deckstate').textContent);
 assert.equal($('decktoggle').textContent,'Pause');
 assert.equal($('deckgain').value,'50');
 $('decktoggle').dispatchEvent(new w.Event('click'));await tick();await tick();
 const deckStop=requests.find(([url,opts])=>url.endsWith('/jukebox')&&opts.method==='POST');
 assert.deepEqual(JSON.parse(deckStop[1].body),{action:'stop'});
 $('deckgain').value='30';$('deckgain').dispatchEvent(new w.Event('change'));await tick();await tick();
 const deckGain=requests.filter(([url,opts])=>url.endsWith('/jukebox')&&opts.method==='POST').pop();
 assert.deepEqual(JSON.parse(deckGain[1].body),{action:'setGain',gain:0.3});
 // A room state from before HL-204 carries no deck field and still paints.
 roomState={...roomState,deck:undefined};
 await w.testPartyRefresh();
 assert($('deckstate').textContent.includes('Deck idle'),$('deckstate').textContent);
 dom.window.close();
 const guest=new JSDOM(html,{url:'https://music.example/party#invite=SECRET',runScripts:'outside-only'}),g=guest.window;
 g.HTMLMediaElement.prototype.pause=function(){};g.fetch=()=>{throw Error('No fetch before fragment wipe and user join');};g.eval(code);
 assert.equal(g.location.hash,'');assert.equal(g.document.getElementById('join').hidden,false);assert.equal(g.document.getElementById('controls').hidden,true);
 guest.window.close();
 console.log('PASS Party safe chat rendering, guest/member icons, incremental polling, preserved draft/focus, body-only send, fragment wipe, no autoplay');
})().catch(e=>{console.error(e);process.exitCode=1;});
