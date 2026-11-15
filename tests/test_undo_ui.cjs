// Undo pick: the button only appears for a source chosen by hand, it asks first,
// and it tells the server to drop the downloaded file.
const fs = require('fs');
const assert = require('assert');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const slice = (start, end) => {
  const a = html.indexOf(start);
  const b = html.indexOf(end);
  assert(a > -1 && b > a, `slice ${start} -> ${end}`);
  return html.slice(a, b);
};

// -- the button -------------------------------------------------------------
const row = slice('function trackRow(', 'function playlistCard(');
assert(/if \(t\.pinned && !\["downloading","resolving"\]\.includes\(t\.status\)\)/.test(row),
  'only a hand-picked source offers undo, and never mid-download');
assert(/undoPick\(\$\{t\.id\},\$\{t\.file \? 1 : 0\}\)/.test(row),
  'the row tells undo whether there is a file to drop');

// -- the action -------------------------------------------------------------
const undo = slice('async function undoPick(', 'function useCandidate(');
assert(/confirm\(msg\)/.test(undo), 'undo must confirm before it does anything');
assert(/\/api\/track\/\$\{trackId\}\/unpick/.test(undo), 'undo posts to the unpick route');
assert(/delete_file: !!hasFile/.test(undo), 'and reports the file decision');

// -- the routes -------------------------------------------------------------
const server = fs.readFileSync(path.join(__dirname, '..', 'server.py'), 'utf8');
assert(/\^\/api\/track\/\(\\d\+\)\/unpick\$/.test(server), 'the route is registered');
assert(/def do_track_unpick/.test(server), 'and has a handler');
assert(/"error": "stop the active job first"/.test(server), 'a running job cannot be undone');
assert(/ranking\.forget\(/.test(server), 'undo stops the pick training the ranking');

// -- the pool depth ---------------------------------------------------------
const yt = fs.readFileSync(path.join(__dirname, '..', 'downloaders', 'ytdlp.py'), 'utf8');
assert(/scsearch10/.test(yt) && /ytsearch10/.test(yt),
  'three results per engine could not contain the right upload');

console.log('Undo pick: PASS');
