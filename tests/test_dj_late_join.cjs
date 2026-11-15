const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(require('path').join(__dirname,'../web/dj.html'),'utf8');
let plays=0,loads=0;const nodes={};const $=s=>nodes[s]||(nodes[s]={hidden:true,textContent:''});
for(const side of ['A','B'])nodes['#audio'+side]={duration:NaN,currentTime:0,paused:true,playbackRate:1,play(){plays++;this.paused=false;return Promise.resolve();},pause(){this.paused=true;}};
const decks={},catalog=[];let now=100;
const c=vm.createContext({$,decks,Date:{now:()=>now*1000},Math,Object,Array,String,Number,autoOn:false,applyingRemote:false,myId:'',SIDES:['A','B'],trackById:id=>catalog.find(t=>t.id===id),loadTrack(s,t){loads++;decks[s]={track:t};nodes['#audio'+s].src='fake';},applyValues(){},claimsClear(){},mergeClaims(){},paintRoster(){},applyQueue(){},autoStop(){throw Error('unexpected');}});
const section=html.slice(html.indexOf('function roomNow('),html.indexOf('\nfunction applyQueue('));vm.runInContext(section,c);
vm.runInContext(html.slice(html.indexOf('function roomEvent('),html.indexOf('\nfunction claimsClear(')),c);
const run=s=>vm.runInContext(s,c);
const state={type:'state',you:'member',decks:{A:{track_id:7,playing:true,pos:20,at:98,rate:1.05}}};c.state=state;
run('roomEvent(state)');assert.equal(loads,0);assert.equal(plays,0);assert.equal(run('lastDecks.A.track_id'),7);
catalog.push({id:7,bpm:120});run('applyRemoteDeck("A",lastDecks.A)');assert.equal(loads,1);assert.equal(plays,0);assert.equal($('#joinaudio').hidden,false);
// Metadata arrived after the snapshot; seek uses the current anchor, not time at join.
now=105;nodes['#audioA'].duration=200;run('audioJoined=true; applyRemoteDeck("A",lastDecks.A)');assert.equal(plays,1);assert.equal(nodes['#audioA'].currentTime,27.35);assert.equal(nodes['#audioA'].playbackRate,1.05);assert.equal(run('localDeckClock.A'),false);
assert(html.includes('if (localDeckClock[side] && audio && audio.src && !audio.paused)'));
assert(html.includes('if (lastDecks[side] && !localDeckClock[side]) applyRemoteDeck(side, lastDecks[side]);'));
assert(html.includes('for (const side of SIDES) if (lastDecks[side]) applyRemoteDeck(side, lastDecks[side]);'));
console.log('PASS late join: snapshot decks retained before catalog, catalog retry, gesture gate, metadata/current-clock seek, rate, passive clock guard (fake audio only)');
