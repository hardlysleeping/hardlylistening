// The song table: one header definition, one grid for header and rows, and the
// same table on the list page and the detail pages.
//
// The bug this pins: the Votes column used to be sized `max-content`, so each row
// widened it differently and the header drifted off its own column; detail pages
// (album, playlist) had no header at all.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('web/index.html', 'utf8');

// ---- CSS -----------------------------------------------------------------
const gridRule = html.match(/\.songhead,\.songrow\{([^}]*)\}/);
assert(gridRule, 'header and rows must share one grid declaration');
const cols = /grid-template-columns:([^;]+);/.exec(gridRule[1])[1].trim();
assert(!/max-content|min-content|auto/.test(cols.replace(/minmax\(0,[\d.]+fr\)/g, '')),
  `columns must be fixed or fr, never content-sized: ${cols}`);
assert(/96px/.test(cols), 'the Votes column needs a fixed width');
// no second, conflicting grid for rows at desktop width (media queries may
// legitimately restate it)
assert(!/^\s{0,1}\.songrow\{grid-template-columns/.test(html),
  'a second desktop grid rule would desync the header');
assert(/\.songhead\{[^}]*height:34px/.test(html), 'header needs a fixed height');
assert(/\.songrow\{min-height:46px/.test(html), 'rows need one height');
assert(/\.songhead \.h-time\{justify-content:flex-end\}/.test(html));
assert(/\.songhead \.h-votes\{justify-content:center\}/.test(html));
assert(/\.songtable\{[^}]*border:1px solid/.test(html), 'the table is one bounded section');
assert(/@media \(max-width:760px\)\{[^@]*\.songhead\{display:none\}/.test(html),
  'on a phone the artist/album columns are hidden, so the header must go too');

// ---- markup ---------------------------------------------------------------
const start = html.indexOf('function headCell(');
const end = html.indexOf('function renderPlaylistsView(');
assert(start > 0 && end > start, 'songTable block not found');

const el = () => ({ innerHTML: '', textContent: '' });
const ctx = vm.createContext({
  esc: s => String(s ?? ''),
  fmtClock: s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`,
  chips: () => '', voteRow: () => '<div class="votes"></div>',
  listeningSaveControl: () => '', goneLabel: () => '',
  SVG: { play: () => '<svg></svg>', pause: () => '<svg></svg>' },
  playerAudio: { paused: true }, playingId: null,
  libSort: 'recent', libSortDir: 'desc',
  localStorage: { getItem: () => null, setItem() {} },
  console,
});
vm.runInContext('let playingId = null;', ctx);
vm.runInContext(html.slice(start, end), ctx);
const run = s => vm.runInContext(s, ctx);

// header: labels in order, on the right cells
const head = run('songHead()');
// Cells are buttons now (they sort), plus the plain spans around them.
const flat = head.replace(/<i class="harrow">[^<]*<\/i>/g, '');
const labels = [...flat.matchAll(/<(?:span|button)[^>]*>([^<]*)</g)].map(m => m[1].trim());
assert.deepStrictEqual(JSON.parse(JSON.stringify(labels)),
  ['#', '', 'Title', 'Artist', 'Album', 'Votes', 'Time', ''],
  'header must name the columns in the order the rows use them');
assert(/class="hcell h-votes/.test(head) && /class="hcell h-time/.test(head));

const track = { id: 1, title: 'Song', artist_name: 'Artist', album: 'Album',
                duration: 200, playable: true, artwork: '' };
const table = run(`songTable([${JSON.stringify(track)}])`);
assert(/class="songhead"/.test(table) && /class="songrow/.test(table),
  'a table is header plus rows');
assert(table.indexOf('songhead') < table.indexOf('songrow'), 'header comes first');

// the same table, empty
const empty = run('songTable([], "Nothing downloaded yet.")');
assert(empty.includes('Nothing downloaded yet.') && /class="songhead"/.test(empty),
  'an empty detail page keeps the header so the columns still read');

// detail pages reuse it instead of hand-rolling rows
for (const marker of ['albumDetail', 'detailPlaylist']) {
  const startIdx = html.indexOf(`function ${marker}(`);
  if (startIdx < 0) continue;
  const body = html.slice(startIdx, html.indexOf('\nfunction ', startIdx + 10));
  assert(body.includes('songTable('), `${marker} must use songTable`);
}
assert(!/detaillist tracklist">\$\{album\.tracks\.map/.test(html),
  'album detail must not build rows without a header');
assert(!/detaillist tracklist">\s*\n?\s*\$\{tracks\.length \? tracks\.map/.test(html),
  'playlist detail must not build rows without a header');
assert(/SONG_HEAD/.test(html.split('function renderSongs(')[1] || '') === false,
  'renderSongs must not repeat the header markup');

// ---- add/remove from library is one square icon, plus then check ---------
assert(/\.ghost\.lsave\{[^}]*width:32px/.test(html), 'the save control is a fixed square');
assert(/@media \(max-width:760px\)\{ \.ghost\.lsave\{width:34px/.test(html),
  'and a touch-sized square on a phone');
const control = html.slice(html.indexOf('function listeningSaveControl('),
                           html.indexOf('async function toggleListeningSave('));
assert(control.includes('class="ghost lsave'), 'the control must carry the .lsave sizing');
assert(control.includes('aria-label="${label}"') && control.includes("saved ? SVG.check(14) : SVG.plus(14)"),
  'a plus when it can be added, a check once it is in — never a minus');
assert(control.includes("'Remove from library'") && control.includes("'Add to library'"),
  'both labels come from the same control, as its accessible name');
assert(!control.includes('−'), 'no minus glyph in the control');

console.log('Song table: one header definition, fixed columns, matching grid, detail pages covered, phone hides it PASS');
