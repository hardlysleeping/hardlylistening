// Isolated real renderer + mocked two-user saves API. Never boots audio/server.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('web/index.html','utf8'), elements={};
const el=id=>elements[id] ||= {value:'',innerHTML:'',textContent:'',hidden:false,style:{},classList:{toggle(){}},querySelectorAll:()=>[]};
const users={alice:{tracks:[1],playlists:[7],playlist_tracks:[{playlist_id:7,track_id:2,position:1}],folder_tracks:[],folders:[{id:11,parent_id:null,name:'Personal mixes',position:0},{id:12,parent_id:11,name:'Warmup',position:0}]},bob:{tracks:[3],playlists:[],playlist_tracks:[],folder_tracks:[],folders:[{id:21,parent_id:null,name:'Private',position:0}]},admin:{tracks:[],playlists:[],playlist_tracks:[],folder_tracks:[],folders:[]}};
let user='alice';
const ctx=vm.createContext({$:el,esc:String,localStorage:{getItem(){},setItem(){}},document:{activeElement:null,querySelectorAll:()=>[]},scrollTo(){},alert:assert.fail,prompt:()=>null,confirm:()=>true,showToast:()=>{},fetch:async(path)=>({ok:true,json:async()=>path.endsWith('/folders')?{folders:structuredClone(users[user].folders)}:structuredClone(users[user])}),api:async(path,b)=>{const s=users[user];if(path.endsWith('/folders')){if(b.action==='add_track')s.folder_tracks.push({folder_id:b.folder_id,track_id:b.track_id,position:0});if(b.action==='remove_track')s.folder_tracks=s.folder_tracks.filter(t=>!(t.folder_id===b.folder_id&&t.track_id===b.track_id));return {ok:true};}const key=b.kind==='track'?'tracks':'playlists';s[key]=b.saved?[...s[key],b.id]:s[key].filter(id=>id!==b.id);if(b.kind==='playlist'){s.playlist_tracks=b.saved?[{playlist_id:b.id,track_id:2,position:1}]:[];}return {ok:true};}});
vm.runInContext('let playerAudio={src:""};',ctx);
vm.runInContext(html.slice(html.indexOf('// Personal listening state'),html.indexOf('const playerAudio =')),ctx);
ctx.showToast=()=>{};
const run=s=>vm.runInContext(s,ctx), out=()=>el('#liblist').innerHTML;
run(`libAll=[1,2,3].map(id=>({id,title:'Song'+id,artist_name:'Artist'+id,album:'Album'+id,album_id:'a'+id,playable:true,playlists:[{id:7,title:'Collection',position:id}]}));lib=libAll;libPlaylists=[{id:7,title:'Collection'},{id:8,title:'Empty set'}];libView='songs';`);
(async()=>{
 assert(html.includes('>Repository</button>')&&html.includes('>Library</button>'));
 await ctx.loadListeningSaves();ctx.setLibraryScope('mine');
 assert(out().includes('Song1')&&out().includes('Song2')&&!out().includes('Song3'));
 ctx.setView('folders');assert(out().includes('foldername">Personal mixes</span>')&&!out().includes('foldername">Private</span>'));
 ctx.openPersonalFolder(11);assert(out().includes('Warmup'));
 ctx.openPersonalFolder(12);assert(out().includes('This folder is empty'));
 ctx.leavePersonalFolder();ctx.leavePersonalFolder();assert(out().includes('Personal mixes'));
 ctx.setView('songs');
 assert(el('#libcount').textContent.includes('2 songs · 1 song saves · 1 saved playlists'));
 assert(ctx.listeningSaveControl('track',2).includes('Add to library')); // playlist entry is not an explicit save
 for(const [view,term] of [['songs','song2'],['albums','album2'],['artists','artist2'],['playlists','collection']]){
  run(`libView='${view}';`);el('#libsearch').value=term;ctx.renderLibrary();assert(!out().includes('Nothing matches'));
 }
 el('#libsearch').value='';run("libView='playlists';renderLibrary();");assert(out().includes('Collection')&&!out().includes('Empty set'));assert(out().includes('ids=2'));
 ctx.openPlaylist(7);assert(out().includes('Song2')&&!out().includes('Song1')&&!out().includes('Song3'));
 await ctx.toggleListeningSave('track',1);assert(!out().includes('Song1'));assert(el('#libcount').textContent.startsWith('1 songs'));
 await ctx.toggleListeningSave('track',2);await ctx.toggleListeningSave('track',2);assert(out().includes('Song2')); // playlist keeps song accessible
 await ctx.toggleListeningSave('playlist',7);assert(!out().includes('Song2'));
 run("detail=null;libView='songs';renderLibrary();");assert(out().includes('Open Repository'));
 user='bob';await ctx.loadListeningSaves();ctx.renderLibrary();assert(out().includes('Song3')&&!out().includes('Song1')&&!out().includes('Song2'));
 user='admin';await ctx.loadListeningSaves();ctx.renderLibrary();assert(out().includes('Open Repository')&&!out().includes('Song3'));
 ctx.setLibraryScope('shared');assert(out().includes('Song1')&&out().includes('Song2')&&out().includes('Song3'));
 await ctx.toggleListeningSave('track',1);assert(ctx.listeningSaveControl('track',1).includes('Remove from library'));ctx.setLibraryScope('mine');assert(out().includes('Song1'));assert(el('#libcount').textContent.startsWith('1 songs'));
 ctx.setLibraryScope('shared');run("libView='playlists';renderLibrary();");assert(out().includes('Empty set'));
 user='alice';await ctx.loadListeningSaves();ctx.setLibraryScope('mine');ctx.setView('folders');ctx.openPersonalFolder(12);
 await ctx.addTrackToPersonalFolder(2,12);assert(out().includes('Song2'));
 await ctx.removeTrackFromPersonalFolder(2);assert(out().includes('This folder is empty'));
 user='bob';await ctx.loadListeningSaves();ctx.renderLibrary();assert(out().includes('Private')&&!out().includes('Personal mixes')&&!out().includes('Song2'));
 console.log('Personal Library: member-scoped nested folders, canonical song references, two-user isolation, folder add/remove controls, existing saves and Repository scope PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
