// Apple restore controls on the playlist page: one backup file, one shared shortcut link.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('web/index.html','utf8');
const elements={},storage={};
const el=id=>elements[id] ||= {value:'',innerHTML:'',textContent:'',style:{},dataset:{},classList:{toggle(){},add(){},remove(){},contains:()=>false},querySelectorAll:()=>[],addEventListener(){},setAttribute(){},appendChild(){}};
const ctx=vm.createContext({$:el,esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),localStorage:{getItem:k=>storage[k],setItem:(k,v)=>storage[k]=v},document:{activeElement:null,querySelectorAll:()=>[]},scrollTo(){}});
vm.runInContext('let playerAudio={src:"",paused:true};',ctx);
vm.runInContext(html.slice(html.indexOf('// Personal listening state'),html.indexOf('const playerAudio =')),ctx);
ctx.showToast=()=>{};
const run=s=>vm.runInContext(s,ctx), out=()=>el('#liblist').innerHTML;
const playlist=(id,title,provider)=>({id,title,provider,source_url:provider==='apple'?'https://music.apple.com/us/playlist/x/pl.u-abc':''});
function scene(provider, shortcut){
  run(`libAll=[1,2].map(id=>({id,title:'Song '+id,artist_name:'Artist',album:'Album',album_id:'a',playable:true,status:'ok',duration:180,playlists:[{id:7,title:'Driving',position:id}],provider:'${provider}'}));
    lib=libAll.slice();libPlaylists=[${JSON.stringify(playlist(7,'Driving',provider))}];detail={kind:'playlist',key:'7'};
    last={stats:{apple_restore_shortcut:${JSON.stringify(shortcut||'')}}};libView='playlists';renderLibrary();`);
  return out();
}
assert(html.includes('function appleBackupControls'),'the control helper is gone from index.html');
assert(html.includes('href="/api/playlist/${Number(playlist.id)}/apple-backup"') ||
       html.includes('/apple-backup'),'the backup route is not linked');
// Apple playlist with the shared shortcut configured: both controls, right targets.
let page=scene('apple','https://www.icloud.com/shortcuts/0123456789abcdef');
assert(page.includes('Apple Music Backup'),'backup button missing');
assert(page.includes('href="/api/playlist/7/apple-backup"'),'backup button points at the wrong route');
assert(page.includes('Add Restore Shortcut'),'shortcut button missing when configured');
assert(page.includes('href="https://www.icloud.com/shortcuts/0123456789abcdef"'),'shortcut link is wrong');
assert(page.includes('target="_blank"')&&page.includes('rel="noopener"'),'shortcut link must open safely');
assert(page.includes('Download ZIP'),'the existing download control must survive');
// No shortcut configured: the backup file is still offered, no dead shortcut button.
page=scene('apple','');
assert(page.includes('Apple Music Backup'),'backup button must not depend on the shortcut');
assert(!page.includes('Add Restore Shortcut'),'no shortcut button without a configured URL');
// A non-Apple playlist has no Apple restore path.
page=scene('soundcloud','https://www.icloud.com/shortcuts/0123456789abcdef');
assert(!page.includes('Apple Music Backup')&&!page.includes('Add Restore Shortcut'),'soundcloud playlist must not offer an Apple restore');
// Only an https URL is accepted, and it is escaped on the way into the markup.
page=scene('apple','javascript:alert(1)');
assert(!page.includes('Add Restore Shortcut'),'a non-https value must not become a link');
page=scene('apple','https://www.icloud.com/shortcuts/abc?x=1&y=2');
assert(page.includes('x=1&amp;y=2')&&!page.includes('x=1&y=2"'),'shortcut URL must be escaped');
// The guide: a small round "?" beside the pills, opening its own dialog.
page=scene('apple','');
assert(page.includes('openAppleBackupHelp()'),'the ? button is missing');
assert(/class="ghost abhelp"/.test(page),'the ? button lost its compact round style');
assert(html.includes('id="dlg-apple-backup"'),'the guide dialog is missing from the page');
assert(html.includes('Add Restore Shortcut')&&html.includes('Apple Music Backup'),'the guide must name both buttons');
assert(!html.includes('apple-music.json'),'the restore file must not ride in the playlist ZIP any more');
el('#dlg-apple-backup').showModal=function(){ this.open=true; };
ctx.openAppleBackupHelp();
assert(el('#dlg-apple-backup').open===true,'openAppleBackupHelp must open the dialog');
ctx.openAppleBackupHelp();
assert(el('#dlg-apple-backup').open===true,'opening twice must not double-show a modal');
console.log('Apple restore UI: backup link, shortcut, provider gate, URL escaping and the ? guide PASS');
