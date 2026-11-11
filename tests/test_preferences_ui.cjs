// Isolated DOM rendering and capture clicks; no audio/server or real records.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync('web/index.html','utf8');
const dom=new JSDOM('<div id="view-library"><div id="liblist"></div></div><input id="libsearch"><select id="libplaylist"></select><div id="libscopes"></div><div id="libtitle"></div><div id="libscopehelp"></div><div id="libcount"></div>',{runScripts:'outside-only',url:'https://music.example'});
const w=dom.window,ctx=dom.getInternalVMContext();
const likers=name=>[{id:'u1',name:'Ana',icon:'music'},{id:'u2',name:'Ben',icon:'star'},{id:'u3',name:'Cara',icon:'sun'},
  {id:'u4',name:'Dee',icon:'moon'},{id:'u5',name:'Eli',icon:'music'},{id:'u6',name:'Fay',icon:'star'}].slice(0,name);
let data={favorites_id:'favorites',favorites:[1],pins:[{kind:'track',target:'1'},{kind:'album',target:'a'.repeat(64)},{kind:'playlist',target:'7'}],
  public_favorites:{'1':likers(6),'2':likers(1)}},calls=[];
w.$=s=>w.document.querySelector(s) || {value:'',innerHTML:'',textContent:'',classList:{toggle(){}},style:{},querySelectorAll:()=>[]};
w.esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');w.scrollTo=()=>{};w.showToast=e=>{throw Error(e)};
w.fetch=async path=>({ok:true,json:async()=>structuredClone(path.endsWith('preferences')?data:{tracks:[1,2],playlists:[7],playlist_tracks:[{playlist_id:7,track_id:1,position:0}]})});
w.api=async(path,body)=>{calls.push(body);if(body.action==='favorite')data.favorites=body.enabled?[body.id]:[];else data.pins=data.pins.filter(p=>!(p.kind===body.kind&&p.target===String(body.id)));return structuredClone(data)};
vm.runInContext('let playerAudio={src:"",paused:true};',ctx);
vm.runInContext(html.slice(html.indexOf('// Personal listening state'),html.indexOf('const playerAudio =')),ctx);
vm.runInContext(`libAll=[{id:1,title:'Zulu <script>',artist_name:'Artist',album:'Pinned',album_id:'a'.repeat(64),added:1,playable:true,playlists:[{id:7,title:'Set',position:0}]},{id:2,title:'Alpha',artist_name:'Other',album:'Other',album_id:'b'.repeat(64),added:2,playable:true,playlists:[]}];lib=libAll;libPlaylists=[{id:7,title:'Set'}];libraryScope='mine';libSort='title';playedIds=[];toggleSongPlayback=id=>playedIds.push(id);`,ctx);
vm.runInContext(fs.readFileSync('web/preferences.js','utf8'),ctx);
(async()=>{
 await w.loadListeningSaves();w.renderLibrary();
 assert.equal(w.document.querySelectorAll('.mixed-pinned').length,1);
 assert.equal(w.document.querySelectorAll('.pinned-item').length,3);
 assert.equal(w.document.querySelectorAll('.mixed-pinned script').length,0);
 assert.equal(vm.runInContext('renderedAlbums[0].key',ctx),'a'.repeat(64));

 vm.runInContext("libView='playlists';renderLibrary()",ctx);
 // Favorites is a dynamic playlist card in the playlists grid, not a panel under Pinned.
 assert.equal(w.document.querySelectorAll('.favorites-playlist').length,0,'the old favorites panel is gone');
 const dyn=w.document.querySelectorAll('.plcard.dyn');
 assert.equal(dyn.length,1,'My Library holds your own Favorites only');
 assert.equal(w.document.querySelector('[data-open-favorites] .pt').textContent,'Favorites');
 assert(w.document.querySelector('[data-open-favorites] .ps').textContent.includes('1 song · yours'));
 assert.equal(w.document.querySelector('[data-open-public]'),null,"Everyone's Favorites is not in My Library");
 // Shared scope keeps the public playlist and drops the personal one.
 vm.runInContext("libraryScope='shared';renderLibrary()",ctx);
 assert.equal(w.document.querySelectorAll('.plcard.dyn').length,1);
 const pub=w.document.querySelector('[data-open-public]');
 assert.equal(pub.querySelector('.pt').textContent,"Everyone's Favorites");
 assert(pub.querySelector('.ps').textContent.includes('2 songs · 6 members'),pub.querySelector('.ps').textContent);
 assert.equal(w.document.querySelector('[data-open-favorites]'),null,'no personal favorites in the Repository');
 assert.equal(w.document.querySelectorAll('.mixed-pinned').length,0);

 // Opening it lists every liked song with the members who liked it.
 pub.click();
 assert(w.document.querySelector('#liblist').textContent.includes("Everyone's Favorites"));
 assert(w.document.querySelector('#liblist').textContent.includes('2 songs liked by 6 members'));
 const rows=w.document.querySelectorAll('.favrow');
 assert.equal(rows.length,2);
 assert.equal(rows[0].dataset.favId,'1','most liked first');
 assert.equal(rows[0].querySelectorAll('.liker').length,5,'four chips plus a +2 badge');
 assert.equal(rows[0].querySelector('.liker.more').textContent,'+2');
 assert.equal(rows[1].querySelectorAll('.liker').length,1);
 assert(rows[0].getAttribute('aria-label').includes('liked by Ana, Ben'));
 assert.equal(rows[0].querySelector('.liker').textContent,'♫','liker chips carry the profile symbol');
 rows[0].click();
 assert.deepEqual(vm.runInContext('playedIds',ctx),[1],'clicking a row plays that song');
 // The personal detail still uses the normal song list.
 w.document.querySelector('[data-close-favorites]').click();
 vm.runInContext("libraryScope='mine';renderLibrary()",ctx);
 w.document.querySelector('[data-open-favorites]').click();
 assert(w.document.querySelector('#liblist').textContent.includes('1 song · liked by you'));

 w.document.querySelector('[data-close-favorites]').click();
 vm.runInContext("libView='songs';renderLibrary()",ctx);
 assert.equal(w.document.querySelector('.songrow').dataset.id,'1');
 let audioClicks=0;w.document.querySelector('[data-pref-action="favorite"]').closest('.songrow').addEventListener('click',()=>audioClicks++);
 w.document.querySelector('[data-pref-action="favorite"]').click();
 await new Promise(r=>setTimeout(r,10));
 assert.equal(audioClicks,0);assert.equal(calls.length,1);assert.equal(calls[0].enabled,false);
 assert(!('member_id' in calls[0]));
 // The pin control is an icon: same box in both states, no word label.
 const pinBtn=w.document.querySelector('[data-pref-action="pin"]');
 assert(pinBtn.querySelector('svg'),'pin control has no icon');
 assert.equal(pinBtn.textContent.trim(),'','pin control still prints a label');
 assert.equal(pinBtn.getAttribute('aria-label'),'Unpin track');
 w.renderLibrary();w.renderLibrary();
 assert.equal(w.document.querySelectorAll('.mixed-pinned').length,1);
 // Pinned songs rank first inside your own library, and not at all in the shared one.
 assert.equal(w.PreferenceUI.rank('track',2,1),1,'a pinned song ranks above an unpinned one');
 vm.runInContext("libraryScope='shared';renderLibrary()",ctx);
 assert.equal(w.PreferenceUI.rank('track',2,1),0);
 console.log('Preferences DOM PASS: mixed pins, favorites + everyone\'s favorites as dynamic playlists, liker chips, play from a liked row, escaping, self payload, no playback/bubbling, repeat render');
 dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
