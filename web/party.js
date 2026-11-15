'use strict';
const $ = id => document.getElementById(id);
const icons={music:'♫',headphones:'🎧',star:'★',sun:'☀',moon:'☾'};
let inviteSecret=new URLSearchParams(location.hash.slice(1)).get('invite');
history.replaceState(null,'',location.pathname+location.search); // wipe bearer fragment before any request
let pid=new URLSearchParams(location.search).get('party'), member=false, hostToken='', current=null, playing=null, cursor=0, busy=false, timer, heartbeat;
const audio=$('audio');
let inviteEpoch=0, deckGainHeld=false;
async function api(path,body){const r=await fetch('/api/party'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});let data=await r.json();if(!r.ok)throw Error(data.error||'Request failed');return data;}
function fail(e){$('error').textContent=e.message||String(e);}
function action(fn){return async e=>{if(e)e.preventDefault();$('error').textContent='';try{await fn();}catch(e){fail(e);}};}
function button(text,fn){const b=document.createElement('button');b.textContent=text;b.onclick=action(fn);return b;}
function row(text){const r=document.createElement('div');r.className='row';const s=document.createElement('span');s.textContent=text;r.append(s);return r;}
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
function stopHost(){clearInterval(heartbeat);hostToken='';playing=null;audio.pause();audio.removeAttribute('src');$('host').hidden=false;$('next').hidden=$('release').hidden=true;}
async function control(op,extra={}){return api('/'+pid+'/control',{op,host_token:hostToken,...extra});}
function clearInvite(){inviteEpoch++;$('link').value='';$('invitebox').hidden=true;}
async function openRoom(id){clearInvite();pid=id;cursor=0;$('messages').replaceChildren();history.replaceState(null,'','/party?party='+encodeURIComponent(pid));$('room').hidden=false;$('members').hidden=true;$('controls').hidden=!member;await refresh();schedule();}
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
async function refresh(){if(busy||!pid)return;busy=true;try{const s=await api('/'+pid);current=s.current;$('roomtitle').textContent=s.title;$('identity').textContent=(icons[s.viewer.profile_icon]||'♫')+' '+s.viewer.display_name;$('expiry').textContent='Expires '+new Date(s.expires*1000).toLocaleString();$('current').textContent=current?current.title+' — '+current.artist_name:'Nothing yet';
paintDeck(s);
if(hostToken&&s.deck&&s.deck.gain!=null)audio.volume=Math.min(1,Math.max(0,Number(s.deck.gain)));
const skp=s.skip||{count:0,needed:1,mine:false};
$('skip').disabled=!current||!!skp.mine;
$('skip').textContent=skp.mine?'Skip requested':'Skip';
$('skipcount').textContent=current?(skp.mine?'Your skip is in — ':'')+skp.count+' of '+(s.participants??1)+' want to skip · '+skp.needed+' skips needed':'';
$('nextup').textContent=s.next?'Next up: '+s.next.title+' — '+s.next.artist_name:'Next up: nothing queued';const frag=document.createDocumentFragment();for(const t of s.queue){const r=row(t.title+' — '+t.artist_name),score=document.createElement('strong');score.textContent=t.score;for(const [v,label] of [[1,'▲'],[-1,'▼']]){const b=button(label,async()=>{await api('/'+pid+'/vote',{entry_id:t.entry_id,value:t.vote===v?0:v});await refresh();});b.setAttribute('aria-label',(v===1?'Upvote ':'Downvote ')+t.title);b.setAttribute('aria-pressed',String(t.vote===v));r.append(b);if(v===1)r.append(score);}if(t.voters&&t.voters.length){const w=document.createElement('small');w.className='pvoters';w.textContent='Voted: '+t.voters.map(v=>v.name+(v.value>0?' ▲':' ▼')).join(' · ');r.append(w);}frag.append(r);}$('queue').replaceChildren(frag);
if(hostToken&&!current&&playing!==null){audio.pause();audio.removeAttribute('src');playing=null;}
if(hostToken&&current&&playing!==current.entry_id){playing=current.entry_id;aheadKeepOnly(current.id);const cached=ahead.get(current.id);audio.src=cached||('/api/track/'+current.id+'/audio');try{await audio.play();}catch(e){fail(Error('Press Play on the aux player to enable audio.'));}}
// The host obeys the deck, so a remote stop stops this speaker and a remote start resumes it.
if(hostToken&&s.deck&&s.deck.playing===false&&!audio.paused)audio.pause();
if(hostToken&&s.deck&&s.deck.playing&&audio.paused&&!audio.ended&&current&&playing===current.entry_id){try{await audio.play();}catch(e){}}
if(hostToken&&s.next)prefetchParty(s.next.id);
const c=await api('/'+pid+'/chat?after='+cursor),log=$('messages'),atEnd=log.scrollHeight-log.scrollTop-log.clientHeight<40;for(const m of c.messages){const p=document.createElement('p'),name=document.createElement('strong');name.textContent=(icons[m.profile_icon]||'♫')+' '+m.display_name+': ';p.append(name,document.createTextNode(m.body));log.append(p);}cursor=c.cursor;while(log.children.length>500)log.firstElementChild.remove();if(atEnd)log.scrollTop=log.scrollHeight;
}catch(e){if(hostToken)stopHost();fail(e);}finally{busy=false;}}
function schedule(){clearTimeout(timer);if(!document.hidden&&pid)timer=setTimeout(async()=>{await refresh();schedule();},3000);}
$('joinform').onsubmit=action(async()=>{const s=await api('/exchange',{invite:inviteSecret,display_name:$('name').value,profile_icon:$('icon').value});inviteSecret=null;$('join').hidden=true;await openRoom(s.party_id);});
$('create').onsubmit=action(async()=>{const s=await api('',{title:$('title').value,hours:24});await openRoom(s.id);});
$('find').onsubmit=action(async()=>{const s=await api('/'+pid+'/search?q='+encodeURIComponent($('query').value));$('results').replaceChildren();for(const t of s.tracks){const r=row(t.title+' — '+t.artist_name);r.append(button('Add',async()=>{await api('/'+pid+'/queue',{track_id:t.id});await refresh();}));$('results').append(r);}if(!s.tracks.length)$('results').textContent='No playable matches.';});
$('invite').onclick=action(async()=>{const id=pid,epoch=++inviteEpoch,s=await api('/'+pid+'/invite',{});if(pid!==id||epoch!==inviteEpoch)return;$('link').value=location.origin+s.link;$('invitebox').hidden=false;});
$('dismiss').onclick=clearInvite;$('copy').onclick=action(async()=>navigator.clipboard.writeText($('link').value));
$('host').onclick=action(async()=>{const s=await control('claim');hostToken=s.host_token;$('host').hidden=true;$('next').hidden=$('release').hidden=false;await api('/'+pid+'/jukebox',{action:'start'});heartbeat=setInterval(async()=>{try{await control('heartbeat',{position:audio.currentTime||0,playing:!audio.paused});}catch(e){stopHost();fail(e);}},10000);if(!current)await control('next',{expected:null});await refresh();});
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
