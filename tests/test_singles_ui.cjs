// A song with no release still opens a page; it just is not an album on the shelf.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('web/index.html', 'utf8');
const groups = html.slice(html.indexOf('function albumsOf'), html.indexOf('function sortAlbums'));
const openers = html.slice(html.indexOf('function openAlbumForTrack'),
  html.indexOf('function goneLabel')) +
  html.slice(html.indexOf('function openTrackAlbum'),
  html.indexOf('async function loadArtistProfile'));

const dom = new JSDOM('<div id="x"></div>', {runScripts: 'outside-only'});
const w = dom.window;
w.eval(`const esc = s => String(s ?? '');
  let artistArt = {};
  ${groups}
  let lib = [], libAll = [], currentRows = [], detail = null, renderedAlbums = [];
  const showToast = m => { window.__toast = m; };
  const renderLibrary = () => { window.__rendered = true; };
  const scrollTo = () => {};
  const focusSongRow = () => {};
  function openAlbumAt(i){ window.__opened = renderedAlbums[i].key; }
  const sortAlbums = list => list.slice();
  ${openers}
  window.albumsOf = albumsOf; window.openAlbumForTrack = openAlbumForTrack;
  window.openTrackAlbum = openTrackAlbum; window.state = () => ({detail, renderedAlbums, toast: window.__toast, opened: window.__opened});
  window.setRows = (all, vis) => { libAll = all; lib = vis || all; currentRows = vis || all; };
`);

const single = {id: 11, title: 'Solo', artist_name: 'Artist', album: '', album_id: null,
                duration: 200, playable: true, artwork: ''};
const album = [
  {id: 21, title: 'One', artist_name: 'Other', album: 'Real Album', album_id: 'a'.repeat(64), track_number: 1, playable: true},
  {id: 22, title: 'Two', artist_name: 'Other', album: 'Real Album', album_id: 'a'.repeat(64), track_number: 2, playable: true},
];
const oneSongAlbum = {id: 31, title: 'Only', artist_name: 'Third', album: 'Lonely LP',
                      album_id: 'b'.repeat(64), track_number: 1, playable: true};

const all = [single, ...album, oneSongAlbum];
const groupsOut = w.albumsOf(all);
const byKey = k => groupsOut.find(a => a.key === k);

// 1. the nameless song gets its own group, titled by the song
const solo = byKey('single\u000011');
assert.ok(solo, 'a single group exists');
assert.equal(solo.single, true);
assert.equal(solo.title, 'Solo');
assert.equal(solo.tracks.length, 1);

// 2. two nameless songs are not one album
w.setRows([single, {...single, id: 12, title: 'Solo 2'}]);
assert.equal(w.albumsOf([single, {...single, id: 12}]).filter(a => a.single).length, 2);

// 3. real releases stay albums, including a one-song one
assert.equal(byKey('a'.repeat(64)).single, false);
assert.equal(byKey('b'.repeat(64)).single, false);
assert.equal(byKey('b'.repeat(64)).title, 'Lonely LP');

// 4. the shelf keeps both single and one-song albums off the Album tab
w.setRows(all);
const shelf = w.albumsOf(all).filter(a => !a.single && a.tracks.length > 1);
assert.equal(JSON.stringify(shelf.map(a => a.title)), JSON.stringify(['Real Album']));

// 5. a row click reaches the single's page instead of toasting "no album page"
w.setRows(all);
w.openAlbumForTrack(11);
assert.equal(w.state().detail.key, 'single\u000011');
assert.equal(w.state().toast, undefined);

// 6. the cover click reaches it too (this used to return silently)
w.setRows(all);
w.openTrackAlbum(single);
assert.equal(w.state().opened, 'single\u000011');
assert.equal(w.state().detail.key, 'single\u000011');

// 7. a real album still opens by its own key
w.setRows(all);
w.openTrackAlbum(album[0]);
assert.equal(w.state().opened, 'a'.repeat(64));

console.log('Singles: own page, off the Album tab, both click paths PASS');
