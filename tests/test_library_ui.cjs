// Pure DOM rendering smoke test; no live server, library or audio.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const html = fs.readFileSync('web/index.html', 'utf8');
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);

const makeEl = () => ({value: '', innerHTML: '', textContent: '', style: {},
  classList: {toggle(){}, add(){}, remove(){}}, querySelectorAll: () => []});
const elements = {};
const store = {};
const context = vm.createContext({
  $: id => elements[id] || (elements[id] = makeEl()),
  esc: s => String(s).replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
  localStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }},
  document: {documentElement: {classList: {toggle(){}}}, activeElement: null,
    querySelectorAll: () => []},
  scrollTo(){}, console
});
// The player object lives past the slice; the test supplies the one property
// renderLibrary reads so the library code stays testable without audio.
vm.runInContext('let playerAudio = {src: ""};', context);
vm.runInContext(html.slice(html.indexOf('// Personal listening state'), html.indexOf('const playerAudio =')), context);
const out = () => elements['#liblist'].innerHTML;

vm.runInContext(`
lib = [{id:1,title:'Done',artist_name:'Artist',album:'Album',album_id:'a'.repeat(64),duration:200,added:2,playable:true,artwork:'',playlists:[{id:7,title:'Set',position:1}]},
       {id:2,title:'Other',artist_name:'Artist',album:'Album',album_id:'a'.repeat(64),duration:100,added:2,playable:true,artwork:'',playlists:[{id:7,title:'Set',position:2}]}];
libAll = lib.concat([{id:3,title:'Pending <title>',artist_name:'Artist',album:'Album',album_id:'a'.repeat(64),duration:90,added:1,playable:false,status:'review',source_status:'gone',source_checked:1,playlists:[{id:7,title:'Set',position:3}]}]);
libPlaylists = [{id:7,title:'Set'}];
renderLibrary();
`, context);

// Default view is the album grid; only downloaded tracks are in the play queue.
assert(out().includes('class="acard'));
assert(out().includes('Album'));
assert(out().includes('3 songs'));   // a not-downloaded track still belongs to the album
assert(vm.runInContext('libQueue.length', context) === 2);
assert(vm.runInContext('renderedAlbums.length', context) === 1);

// Song rows carry a download link and a discussion entry point.
vm.runInContext('libView = "songs"; detail = null; renderLibrary()', context);
assert(out().includes('/api/track/1/download'));
assert(out().includes('openDiscussion'));
assert(out().includes('/api/track/1/audio') === false);   // src is set by the player, never by markup
assert(out().includes("vote('" ) === false);              // votes go through castVote, not inline strings

// Album detail: hero actions, group ZIP and album discussion.
vm.runInContext('libView = "albums"; detail = {kind:"album", key: renderedAlbums[0].key}; renderLibrary()', context);
assert(out().includes('class="hero"'));
assert(out().includes('Download ZIP'));
assert(out().includes('/api/library/download?ids=1,2,3'));
assert(out().includes('Download missing (1)'));       // the button that grabs the hole
assert(out().includes('class="bubbles"'));            // comment bubbles ride on the cover
assert(out().includes('1 not downloaded'));
assert(out().includes('class="social"'));
assert(out().includes('Play'));

// Playlist detail and filter keep membership behaviour.
vm.runInContext('detail = {kind:"playlist", key:"7"}; renderLibrary()', context);
assert(out().includes('/api/playlist/7/download'));
assert(out().includes('Pending &lt;title>'));
assert(out().includes('gone-badge'));
assert(out().includes('No longer in this playlist') || out().includes('Removed from the source'));
elements['#libplaylist'].value = '7';
vm.runInContext('detail = null; libView = "songs"; renderLibrary()', context);
assert(out().includes('Pending &lt;title>'));

// Search still filters and never renders raw markup.
vm.runInContext('detail = null; renderLibrary()', context);
elements['#libsearch'].value = 'pending';
vm.runInContext('renderLibrary()', context);
assert.equal(vm.runInContext('libQueue.length', context), 0);
assert(!out().includes('Pending <title>'));
elements['#libsearch'].value = '';
elements['#libplaylist'].value = '';
vm.runInContext('renderLibrary()', context);
assert.equal(vm.runInContext('libQueue.length', context), 2);

// Non-playable tracks stay visible in membership views, labelled by status.
vm.runInContext('detail = {kind:"playlist", key:"7"}; renderLibrary()', context);
assert(out().includes('review'));
assert(vm.runInContext('libQueue.every(t => t.playable)', context));   // only downloaded tracks are queueable
console.log('Library DOM smoke: album grid, playlist filter, song rows, album/playlist detail, download links, escaping, playable-only queue and full script syntax PASS');
