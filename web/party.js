'use strict';
const $ = id => document.getElementById(id);
const icons={music:'♫',headphones:'🎧',star:'★',sun:'☀',moon:'☾'};
let inviteSecret=new URLSearchParams(location.hash.slice(1)).get('invite');
history.replaceState(null,'',location.pathname+location.search); // wipe bearer fragment before any request
let pid=new URLSearchParams(location.search).get('party'), member=false, hostToken='', current=null, playing=null, cursor=0, busy=false, timer, heartbeat;
const audio=$('audio');
let inviteEpoch=0, deckGainHeld=false;
// The page's job is "pick something and start it", so what a person can start from is fetched
// once per room and stays put while the queue churns every three seconds.
let library=null, tab='playlists', searching='', expandedQueue=false;
// What the browse list was last built from. A three-second poll must not rebuild rows a person
// is reading — an artist they opened would slam shut underneath them — so the list is only
// rebuilt when the tab, the aux state or the library itself actually changed.
let browseKey='';
const QUEUE_ROWS=3;
async function api(path,body){const r=await fetch('/api/party'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});let data=await r.json();if(!r.ok)throw Error(data.error||'Request failed');return data;}
function fail(e){$('error').textContent=e.message||String(e);}
function action(fn){return async e=>{if(e&&e.preventDefault)e.preventDefault();$('error').textContent='';try{await fn();}catch(e){fail(e);}};}
function button(text,fn){const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=action(fn);return b;}
const mmss=s=>{const n=Math.round(Number(s)||0);return Math.floor(n/60)+':'+String(n%60).padStart(2,'0');};
const initials=s=>{const w=String(s||'').trim();return w?w.replace(/^the\s+/i,'').split(/[\s\-_]+/).filter(Boolean).slice(0,2).map(x=>x[0]).join('').toUpperCase():'♫';};
// The aux host caches the next queued track so a skip or the end of a song starts
// instantly instead of waiting for the first bytes over the network.
const ahead=new Map(), aheadJobs=new Map();
function aheadKeepOnly(id){
  for(const [k,url] of [...ahead]) if(String(k)!==String(id)){URL.revokeObjectURL(url);ahead.delete(k);}
  for(const [k,c] of [...aheadJobs]) if(String(k)!==String(id)){c.abort();aheadJobs.delete(k);}
}
function prefetchParty(id){
  if(!id||ahead.has(id)||aheadJobs.has(id))return;
  if(navigator.connection&&navigator.connection.saveData)return;
  const c=new AbortController();aheadJobs.set(id,c);
  fetch('/api/track/'+id+'/audio',{signal:c.signal,credentials:'same-origin'})
    .then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.blob();})
    .then(b=>{if(b.size<16384)throw Error('too small');ahead.set(id,URL.createObjectURL(b));})
    .catch(()=>{})
    .then(()=>{aheadJobs.delete(id);});
}
function stopHost(){clearInterval(heartbeat);hostToken='';playing=null;audio.pause();audio.removeAttribute('src');audio.hidden=true;$('host').hidden=false;$('next').hidden=$('release').hidden=true;render();}
async function control(op,extra={}){return api('/'+pid+'/control',{op,host_token:hostToken,...extra});}
function clearInvite(){inviteEpoch++;$('link').value='';$('invitebox').hidden=true;}
async function openRoom(id){clearInvite();pid=id;cursor=0;library=null;searching='';tab='playlists';browseKey='';$('query').value='';$('clearq').hidden=true;setStatus('');$('messages').replaceChildren();history.replaceState(null,'','/party?party='+encodeURIComponent(pid));$('room').hidden=false;$('members').hidden=true;$('controls').hidden=!member;await refresh();await loadSets();schedule();}
// The deck is the room's one leased speaker. Everyone sees who holds it; members steer it.
function paintDeck(s){
  const d=s.deck||{},playing=!!d.playing,gain=d.gain==null?1:d.gain;
  $('deckstate').textContent=d.host_active
    ?(d.controlled_by||'A member')+"'s device — "+(playing?'playing':'paused')
    :(playing?'Deck connected':'Deck idle — no aux host');
  $('deckactions').hidden=!member;
  $('decktoggle').textContent=playing?'Pause':'Play';
  // A poll must not snap the fader back while somebody is dragging it.
  if(!deckGainHeld)$('deckgain').value=Math.round(gain*100);
  $('deckgain').setAttribute('aria-label','Room volume '+Math.round(gain*100)+'%');
}
function setStatus(text){$('setstatus').textContent=text||'';}
/* ── what you can start from ──────────────────────────────────────────────────────────────
   A set is a playlist or an artist. One press shuffles it into the waiting queue and skips
   what is already there, because a whole playlist is a question with a count for an answer,
   not a yes/no. */
async function loadSets(){
  try{library=await api('/'+pid+'/sets');}catch(e){library={playlists:[],artists:[]};fail(e);}
  browseKey='';
  render();
}
async function queueSet(kind,id,name,btn){
  const was=btn?btn.textContent:'';
  if(btn){btn.disabled=true;btn.textContent='Adding…';}
  try{
    const r=await api('/'+pid+'/queue-set',{kind,id});
    let said;
    if(r.added>0)said=r.added+' of '+r.size+' from “'+r.name+'” queued';
    else if(r.full)said='The queue is full at '+r.limit+' songs';
    else if(r.already===r.size)said='Everything in “'+r.name+'” is already queued';
    else said='“'+r.name+'” has nothing playable on this disk';
    const extra=[];
    if(r.added>0&&r.already)extra.push(r.already+' already queued');
    if(r.unavailable)extra.push(r.unavailable+' not on this disk');
    if(r.full&&r.added>0)extra.push('stopped at the '+r.limit+'-song limit');
    // Only the aux holder can make sound, so only they are promised a start.
    let started=false;
    if(r.added>0&&hostToken&&!current){
      try{await control('next',{expected:null});started=true;}catch(e){}
    }
    if(started)extra.push('playing now');
    else if(r.added>0&&!hostToken)extra.push('the aux host starts it');
    setStatus(said+(extra.length?' · '+extra.join(' · '):''));
    await refresh();
  }finally{
    if(btn){btn.disabled=false;btn.textContent=was;}
  }
}
function setRow(kind,id,name,sub){
  const r=document.createElement('div');r.className='row';
  const sw=document.createElement('span');sw.className='sw';sw.setAttribute('aria-hidden','true');sw.textContent=initials(name);
  const t=document.createElement('div');t.className='t';
  const b=document.createElement('b');b.textContent=name;
  const s=document.createElement('span');s.textContent=sub;
  t.append(b,s);
  const go=document.createElement('button');go.type='button';go.className='go';
  go.textContent=(hostToken&&!current)?'Shuffle in ▶':'Shuffle in';
  go.setAttribute('aria-label','Shuffle '+name+' into the queue');
  go.onclick=action(()=>queueSet(kind,id,name,go));
  r.append(sw,t,go);
  return r;
}
function trackRow(t){
  const r=document.createElement('div');r.className='row';
  const sw=document.createElement('span');sw.className='sw';sw.setAttribute('aria-hidden','true');sw.textContent=initials(t.title);
  const box=document.createElement('div');box.className='t';
  const b=document.createElement('b');b.textContent=t.title;
  const s=document.createElement('span');s.textContent=(t.artist_name||'Unknown artist')+' · '+mmss(t.duration);
  box.append(b,s);
  const add=document.createElement('button');add.type='button';add.className='plain';add.textContent='Add';
  add.setAttribute('aria-label','Add '+t.title+' to the queue');
  add.onclick=action(async()=>{
    add.disabled=true;
    try{await api('/'+pid+'/queue',{track_id:t.id});setStatus('Added “'+t.title+'”.');await refresh();}
    finally{add.disabled=false;}
  });
  r.append(sw,box,add);
  return r;
}
// An artist is a set you might want to look inside before shuffling, so the row opens.
function artistRow(a,{expand=true}={}){
  const r=document.createElement('div');r.className='row';
  const sw=document.createElement('span');sw.className='sw two';sw.setAttribute('aria-hidden','true');sw.textContent=initials(a.name);
  const t=document.createElement('div');t.className='t';
  const b=document.createElement('b');b.textContent=a.name;
  const s=document.createElement('span');s.textContent=a.count+(a.count===1?' song':' songs');
  t.append(b,s);
  const open=button('Songs',async()=>{
    const wrap=r.nextElementSibling;
    if(wrap&&wrap.classList.contains('opened')){wrap.remove();open.textContent='Songs';return;}
    if(wrap&&wrap.classList.contains('songs')){wrap.classList.add('opened');wrap.hidden=false;open.textContent='Hide';return;}
    const box=document.createElement('div');box.className='songs opened';
    box.textContent='Loading…';
    r.after(box);
    open.textContent='Hide';
    const found=await api('/'+pid+'/search?q='+encodeURIComponent(a.name));
    box.replaceChildren();
    const mine=found.tracks.filter(x=>x.artist_name===a.name);
    if(!mine.length){box.textContent='No playable songs.';return;}
    for(const tr of mine)box.append(trackRow(tr));
  });
  open.className='plain';
  if(expand)r.append(open);
  const go=document.createElement('button');go.type='button';go.className='go';
  go.textContent=(hostToken&&!current)?'Shuffle all ▶':'Shuffle all';
  go.setAttribute('aria-label','Shuffle '+a.name+' into the queue');
  go.onclick=action(()=>queueSet('artist',a.name,a.name,go));
  r.append(sw,t,go);
  return r;
}
const group=(title,count)=>{
  const g=document.createElement('div');g.className='group';
  const h=document.createElement('div');h.className='ghead';
  h.innerHTML=title+' <b>'+count+'</b>';
  g.append(h);return g;
};
const empty=text=>{const p=document.createElement('p');p.className='empty';p.textContent=text;return p;};
function render(){
  const box=$('results');
  if(!box)return;
  const hostLabel=hostToken&&!current;
  for(const b of box.querySelectorAll('.go'))b.textContent=(hostLabel?'Shuffle in ▶':'Shuffle in');
  $('qcount').textContent=String(waitingCount);
  const tabs=document.querySelector('.tabs');
  if(tabs)tabs.hidden=!!searching;
  if(searching)return;                       // search paints its own three groups
  if(!library){box.replaceChildren(empty('Loading what you can play…'));return;}
  if(!pid)return;
  const key=[tab,hostToken&&!current,library.playlists.length,library.artists.length].join('|');
  if(key===browseKey)return;
  browseKey=key;
  if(tab==='playlists'){
    const list=library.playlists;
    if(!list.length){box.replaceChildren(empty('No playlists in the library yet.'));return;}
    const frag=document.createDocumentFragment();
    for(const p of list)frag.append(setRow('playlist',p.id,p.name,p.count+' songs'+(p.saved?' · saved':'')));
    box.replaceChildren(frag);return;
  }
  if(tab==='artists'){
    const list=library.artists;
    if(!list.length){box.replaceChildren(empty('No artists with playable songs yet.'));return;}
    const frag=document.createDocumentFragment();
    for(const a of list)frag.append(artistRow(a));
    box.replaceChildren(frag);return;
  }
  box.replaceChildren(empty('Type a song, artist or playlist above.'));
}
// Searching asks the server, which is the only thing that can see the whole library. Filtering
// the browse lists here would hide an artist who simply was not in the top two dozen.
let searchSeq=0;
async function runSearch(term){
  const seq=++searchSeq, box=$('results');
  box.replaceChildren(empty('Searching…'));
  let s;
  try{s=await api('/'+pid+'/search?q='+encodeURIComponent(term));}catch(e){fail(e);return;}
  if(seq!==searchSeq||searching!==term)return;
  const found=s.playlists.length+s.artists.length+s.tracks.length;
  if(!found){box.replaceChildren(empty('Nothing matches “'+s.query+'”.'));return;}
  const frag=document.createDocumentFragment();
  if(s.playlists.length){
    const g=group('Playlists',s.playlists.length);
    for(const p of s.playlists)g.append(setRow('playlist',p.id,p.name,p.count+' songs'));
    frag.append(g);
  }
  if(s.artists.length){
    const g=group('Artists',s.artists.length);
    // A search already lists the songs below, so an artist row there does not need to unfold
    // into the same three rows a second time.
    for(const a of s.artists)g.append(artistRow(a,{expand:false}));
    frag.append(g);
  }
  const songs=group('Songs',s.tracks.length);
  for(const t of s.tracks)songs.append(trackRow(t));
  frag.append(songs);
  box.replaceChildren(frag);
}
/* ── waiting queue ─────────────────────────────────────────────────────────────────────── */
let waitingCount=0, lastQueue=[];
function queueRow(t,i){
  const r=document.createElement('div');r.className='qrow';
  const pos=document.createElement('span');pos.className='pos';pos.textContent=i+1;
  const box=document.createElement('div');box.className='t';
  const b=document.createElement('b');b.textContent=t.title;
  const s=document.createElement('span');s.textContent=t.artist_name||'Unknown artist';
  box.append(b,s);
  const votes=document.createElement('div');votes.className='v';
  for(const [v,label] of [[1,'▲'],[-1,'▼']]){
    const btn=button(label,async()=>{await api('/'+pid+'/vote',{entry_id:t.entry_id,value:t.vote===v?0:v});await refresh();});
    btn.setAttribute('aria-label',(v===1?'Upvote ':'Downvote ')+t.title);
    btn.setAttribute('aria-pressed',String(t.vote===v));
    votes.append(btn);
    if(v===1){const score=document.createElement('span');score.className='s';score.textContent=t.score;votes.append(score);}
  }
  r.append(pos,box,votes);
  if(t.voters&&t.voters.length){
    const who=document.createElement('span');who.className='by';
    who.textContent=t.voters.map(v=>v.name).join(', ');
    who.title='Voted: '+t.voters.map(v=>v.name+(v.value>0?' ▲':' ▼')).join(' · ');
    r.append(who);
  }
  if(member){
    const drop=document.createElement('button');
    drop.type='button';drop.className='plain drop';drop.textContent='×';
    drop.setAttribute('aria-label','Remove '+t.title+' from the queue');
    drop.title='Remove from queue';
    drop.onclick=action(async()=>{await api('/'+pid+'/jukebox',{action:'remove',index:i});await refresh();});
    r.append(drop);
  }
  return r;
}
// Who is in the room, and — for a member — the way to remove a guest from it.
function paintPeople(roster){
  const box=$('people');
  if(!box)return;
  $('pcount').textContent=String(roster.length);
  const frag=document.createDocumentFragment();
  for(const p of roster){
    const row=document.createElement('div');row.className='prow';
    const face=document.createElement('span');face.className='pf';face.setAttribute('aria-hidden','true');
    face.textContent=(icons[p.icon]||'♫');
    const name=document.createElement('b');name.textContent=p.name;
    row.append(face,name);
    if(p.you){const tag=document.createElement('span');tag.className='ptag';tag.textContent='You';row.append(tag);}
    if(p.host){const tag=document.createElement('span');tag.className='ptag';tag.textContent='Aux host';row.append(tag);}
    if(!p.you){const tag=document.createElement('span');tag.className='ptag quiet';tag.textContent=p.kind==='guest'?'Guest':'Member';row.append(tag);}
    if(member&&p.kind==='guest'&&!p.you){
      const out=document.createElement('button');out.type='button';out.className='plain drop';
      out.textContent='Remove';out.title='Remove this guest and their queued songs';
      out.setAttribute('aria-label','Remove guest '+p.name);
      out.onclick=action(async()=>{
        if(!confirm('Remove '+p.name+' from the party? Their queued songs and votes go too.'))return;
        await api('/'+pid+'/control',{op:'revoke',guest_id:p.guest_id});
        await refresh();
      });
      row.append(out);
    }
    frag.append(row);
  }
  if(!roster.length){const p=document.createElement('p');p.className='empty';p.style.padding='12px 13px';p.textContent='Nobody else is here yet.';frag.append(p);}
  box.replaceChildren(frag);
}
function paintQueue(queue){
  lastQueue=queue;
  waitingCount=queue.length;
  const frag=document.createDocumentFragment();
  const shown=expandedQueue?queue:queue.slice(0,QUEUE_ROWS);
  shown.forEach((t,i)=>frag.append(queueRow(t,i)));
  if(!queue.length){const p=document.createElement('p');p.className='empty';p.style.padding='14px 13px';p.textContent='Nothing waiting.';frag.append(p);}
  $('queue').replaceChildren(frag);
  const more=$('more');
  more.hidden=queue.length<=QUEUE_ROWS;
  more.textContent=expandedQueue?'Show fewer':Math.max(0,queue.length-QUEUE_ROWS)+' more waiting';
  more.setAttribute('aria-expanded',String(expandedQueue));
}
async function refresh(){if(busy||!pid)return;busy=true;try{const s=await api('/'+pid);current=s.current;$('roomtitle').textContent=s.title;$('identity').textContent=(icons[s.viewer.profile_icon]||'♫')+' '+s.viewer.display_name;$('expiry').textContent='Expires '+new Date(s.expires*1000).toLocaleString();$('current').textContent=current?current.title+' — '+current.artist_name:'Nothing yet';
paintDeck(s);
if(hostToken&&s.deck&&s.deck.gain!=null)audio.volume=Math.min(1,Math.max(0,Number(s.deck.gain)));
const skp=s.skip||{count:0,needed:1,mine:false};
$('skip').disabled=!current||!!skp.mine;
$('skip').textContent=skp.mine?'Skip requested':'Skip';
$('skipcount').textContent=current?(skp.mine?'Your skip is in — ':'')+skp.count+' of '+(s.participants??1)+' want to skip · '+skp.needed+' skips needed':'';
$('nextup').textContent=s.next?'Next up: '+s.next.title+' — '+s.next.artist_name:'Next up: nothing queued';
paintQueue(s.queue||[]);
paintPeople(s.people||[]);
$('queuemods').hidden=!member;
render();
if(hostToken&&!current&&playing!==null){audio.pause();audio.removeAttribute('src');playing=null;}
if(hostToken&&current&&playing!==current.entry_id){playing=current.entry_id;aheadKeepOnly(current.id);const cached=ahead.get(current.id);audio.src=cached||('/api/track/'+current.id+'/audio');try{await audio.play();}catch(e){fail(Error('Press Play on the aux player to enable audio.'));}}
// The host obeys the deck, so a remote stop stops this speaker and a remote start resumes it.
if(hostToken&&s.deck&&s.deck.playing===false&&!audio.paused)audio.pause();
if(hostToken&&s.deck&&s.deck.playing&&audio.paused&&!audio.ended&&current&&playing===current.entry_id){try{await audio.play();}catch(e){}}
if(hostToken&&s.next)prefetchParty(s.next.id);
const c=await api('/'+pid+'/chat?after='+cursor),log=$('messages'),atEnd=log.scrollHeight-log.scrollTop-log.clientHeight<40;for(const m of c.messages){const p=document.createElement('p'),name=document.createElement('strong');name.textContent=(icons[m.profile_icon]||'♫')+' '+m.display_name+': ';p.append(name,document.createTextNode(m.body));log.append(p);}cursor=c.cursor;while(log.children.length>500)log.firstElementChild.remove();if(atEnd)log.scrollTop=log.scrollHeight;
}catch(e){if(hostToken)stopHost();fail(e);}finally{busy=false;}}
$('qshuffle').onclick=action(async()=>{await api('/'+pid+'/jukebox',{action:'shuffle'});await refresh();});
$('qclear').onclick=action(async()=>{
  if(!confirm('Clear every waiting song? The one playing keeps playing.'))return;
  await api('/'+pid+'/jukebox',{action:'clear'});await refresh();
});
function schedule(){clearTimeout(timer);if(!document.hidden&&pid)timer=setTimeout(async()=>{await refresh();schedule();},3000);}
$('joinform').onsubmit=action(async()=>{const s=await api('/exchange',{invite:inviteSecret,display_name:$('name').value,profile_icon:$('icon').value});inviteSecret=null;$('join').hidden=true;await openRoom(s.party_id);});
$('create').onsubmit=action(async()=>{const s=await api('',{title:$('title').value,hours:24});await openRoom(s.id);});
$('find').onsubmit=action(async()=>{
  const q=$('query').value.trim();
  if(!q){clearSearch();return;}
  searching=q;$('clearq').hidden=false;
  document.querySelector('.tabs').hidden=true;
  setStatus('');
  await runSearch(q);
});
function clearSearch(){$('query').value='';searching='';$('clearq').hidden=true;setStatus('');browseKey='';render();}
$('clearq').onclick=clearSearch;
for(const b of document.querySelectorAll('[data-tab]'))b.onclick=()=>{
  tab=b.dataset.tab;
  for(const o of document.querySelectorAll('[data-tab]'))o.setAttribute('aria-selected',String(o===b));
  searching='';$('query').value='';$('clearq').hidden=true;setStatus('');browseKey='';render();
};
$('more').onclick=()=>{expandedQueue=!expandedQueue;paintQueue(lastQueue);};
$('invite').onclick=action(async()=>{const id=pid,epoch=++inviteEpoch,s=await api('/'+pid+'/invite',{});if(pid!==id||epoch!==inviteEpoch)return;$('link').value=location.origin+s.link;$('invitebox').hidden=false;});
$('dismiss').onclick=clearInvite;$('copy').onclick=action(async()=>navigator.clipboard.writeText($('link').value));
$('host').onclick=action(async()=>{const s=await control('claim');hostToken=s.host_token;audio.hidden=false;$('host').hidden=true;$('next').hidden=$('release').hidden=false;await api('/'+pid+'/jukebox',{action:'start'});heartbeat=setInterval(async()=>{try{await control('heartbeat',{position:audio.currentTime||0,playing:!audio.paused});}catch(e){stopHost();fail(e);}},10000);if(!current)await control('next',{expected:null});await refresh();});
$('next').onclick=action(async()=>{await control('next',{expected:current?current.entry_id:null});await refresh();});
$('skip').onclick=action(async()=>{const expected=current?current.entry_id:null;const r=await api('/'+pid+'/skip',{expected});if(r.skipped&&hostToken){audio.pause();audio.removeAttribute('src');playing=null;}await refresh();});
$('release').onclick=action(async()=>{await control('release');stopHost();});
// The deck bar steers the room's one speaker; a member's phone is not a second one.
$('decktoggle').onclick=action(async()=>{const wasPlaying=$('decktoggle').textContent==='Pause';await api('/'+pid+'/jukebox',{action:wasPlaying?'stop':'start'});if(hostToken){if(wasPlaying)audio.pause();else if(current){try{await audio.play();}catch(e){fail(Error('Press Play on the aux player to enable audio.'));}}}await refresh();});
$('deckgain').addEventListener('pointerdown',()=>{deckGainHeld=true;});
$('deckgain').addEventListener('pointerup',()=>{deckGainHeld=false;});
$('deckgain').addEventListener('blur',()=>{deckGainHeld=false;});
$('deckgain').oninput=()=>{const gain=Number($('deckgain').value)/100;$('deckgain').setAttribute('aria-label','Room volume '+Math.round(gain*100)+'%');if(hostToken)audio.volume=gain;};
$('deckgain').onchange=action(async()=>{await api('/'+pid+'/jukebox',{action:'setGain',gain:Number($('deckgain').value)/100});await refresh();});
$('close').onclick=action(async()=>{if(!confirm('End this party and expire all its guests?'))return;await control('close');stopHost();location.assign('/party');});
audio.onended=action(async()=>{if(hostToken){await control('next',{expected:current?current.entry_id:null});await refresh();}});
// A local play/pause is the same fact as a remote one; report it at once so the poll agrees.
audio.onplay=audio.onpause=()=>{if(hostToken)control('heartbeat',{position:audio.currentTime||0,playing:!audio.paused}).catch(()=>{});};
$('chat').onsubmit=action(async()=>{const draft=$('message').value;$('send').disabled=true;try{await api('/'+pid+'/chat',{body:draft});if($('message').value===draft)$('message').value='';await refresh();}finally{$('send').disabled=false;}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();schedule();});
window.addEventListener('pagehide',()=>{clearInvite();inviteSecret=null;clearTimeout(timer);stopHost();for(const c of aheadJobs.values())c.abort();for(const url of ahead.values())URL.revokeObjectURL(url);ahead.clear();aheadJobs.clear();});
(async()=>{if(inviteSecret){$('join').hidden=false;return;}try{const s=await api('');member=true;if(pid){await openRoom(pid);return;}$('members').hidden=false;for(const p of s.parties)$('parties').append(button(p.title,()=>openRoom(p.id)));}catch(e){if(pid){await openRoom(pid);}else{fail(Error('Open your private guest link, or sign in as a member to create a party.'));}}})();