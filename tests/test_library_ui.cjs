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
libAll = lib.concat([{id:3,title:'Pending <title>',artist_name:'Artist',album:'Album',album_id:'a'.repeat(64),duration:90,added:1,playable:false,status:'review',playlists:[{id:7,title:'Set',position:3}]},
  {id:4,title:'Gone',artist_name:'Artist',album:'Album',album_id:'a'.repeat(64),duration:80,added:1,playable:true,source_status:'gone',source_checked:1,playlists:[{id:7,title:'Set',position:4}]}]);
libPlaylists = [{id:7,title:'Set'}];
renderLibrary();
`, context);

// Default view is the album grid; only downloaded tracks are in the play queue.
assert(out().includes('class="acard'));
assert(out().includes('Album'));
assert(out().includes('3 songs'));   // the album counts the tracks you can play
assert(!out().includes('Pending'));   // a queued track is not browsable at all
assert(out().includes('1 track still downloading'), 'the queue says what is still coming, once');
assert(vm.runInContext('libQueue.length', context) === 3);   // one entry per playable track
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
assert(out().includes('/api/library/download?ids=' + vm.runInContext('renderedAlbums[0].tracks.map(t=>t.id).join(",")', context)));
assert(!out().includes('Download missing'), 'no holes are listed, so nothing to grab here');
assert(out().includes('class="bubbles"'));            // comment bubbles ride on the cover
assert(!out().includes('not downloaded'));
assert(out().includes('class="social"'));
assert(out().includes('Play'));

// Playlist detail and filter keep membership behaviour.
vm.runInContext('detail = {kind:"playlist", key:"7"}; renderLibrary()', context);
assert(out().includes('/api/playlist/7/download'));
assert(!out().includes('Pending &lt;title>'), 'a not-yet-downloaded track must not be listed');
assert(out().includes('1 still downloading'), 'the playlist says how many are on their way');
assert(out().includes('Download missing (1)'), 'and still offers to grab them');
assert(out().includes('gone-badge'));
assert(out().includes('No longer in this playlist') || out().includes('Removed from the source'));
elements['#libplaylist'].value = '7';
vm.runInContext('detail = null; libView = "songs"; renderLibrary()', context);
assert(!out().includes('Pending &lt;title>'), 'the playlist filter lists ready tracks only');
assert(out().includes('Gone'), 'a ready track with a dead source stays visible');

// Search still filters and never renders raw markup.
vm.runInContext('detail = null; renderLibrary()', context);
elements['#libsearch'].value = 'pending';
vm.runInContext('renderLibrary()', context);
assert.equal(vm.runInContext('libQueue.length', context), 0);
assert(!out().includes('Pending <title>'));
elements['#libsearch'].value = '';
elements['#libplaylist'].value = '';
vm.runInContext('renderLibrary()', context);
assert.equal(vm.runInContext('libQueue.length', context), 3);

// A playing track is marked on the tile itself, in both layouts.
vm.runInContext('detail = null; libView = "songs"; setSongLayout("grid"); playingId = 2; renderLibrary()', context);
assert(out().includes('nowbars'), 'grid card carries a playing badge on its artwork');
vm.runInContext('setSongLayout("list"); renderLibrary()', context);
assert(/<div class="idx playing">/.test(out()), 'list row swaps the index for playing bars');
vm.runInContext('libView = "albums"; detail = null; renderLibrary()', context);
assert(out().includes('acard playing') && out().includes('nowbars'), 'album cover of the playing track is marked');
vm.runInContext('playingId = null; libView = "songs"; setSongLayout("list"); renderLibrary()', context);
assert(!out().includes('nowbars'), 'nothing is marked while nothing plays');

// Sorting: newest and oldest added are distinct, and the grid honours them.
vm.runInContext('detail = null; libView = "songs"; libSort = "recent"; libSortDir = "desc"; renderLibrary()', context);
assert(out().indexOf('Gone') > out().indexOf('Done'), 'newest first puts the oldest track last');
vm.runInContext('libSort = "oldest"; libSortDir = "asc"; renderLibrary()', context);
assert(out().indexOf('Gone') < out().indexOf('Done'), 'oldest first puts it first');
vm.runInContext('libSort = "recent"; libSortDir = "desc"; renderLibrary()', context);

// Non-playable tracks are never rendered as rows, whatever their status.
vm.runInContext('detail = {kind:"playlist", key:"7"}; renderLibrary()', context);
assert(!out().includes('review'), 'a queued row has no place in the library');
assert(vm.runInContext('libQueue.every(t => t.playable)', context));   // only downloaded tracks are queueable
console.log('Library DOM smoke: album grid, playlist filter, song rows, album/playlist detail, ready-only browsing with a still-downloading note, escaping, playable-only queue and full script syntax PASS');
