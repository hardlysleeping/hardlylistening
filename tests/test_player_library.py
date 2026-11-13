"""PLAYER-003: the player's library is the app's own library, answered from Subsonic.

The player serves `web/` with no Python behind it, so the app's `/api/*` calls have no one to
answer them. Two shell files are that answer, and this test keeps them honest in two ways:

  * **Provenance.** The envelopes the node test feeds the bridge are not invented: every one is
    built here from this server's own code (`subsonic.song()` for a track row, `listening.key()`
    for an album or artist id, `subsonic.PROTOCOL_VERSION`/`SERVER_TYPE`/`SERVER_VERSION` for the
    envelope's identity, and the album row is the literal `listening.rest()` builds). They are
    written to `tests/fixtures/player/envelopes.json` and this test fails if the fixture and the
    server have drifted apart, so the bridge is always exercised against what a Subsonic server
    actually says rather than against a shape someone remembered.

  * **Statement.** The route table is explicit and finite: the paths it names are the paths the
    docs name, the host's own routes are refused with a reason rather than answered with an
    empty list, the password never reaches a URL the page renders, and the boot hides only the
    controls it can name. Behaviour — the mapping itself, error codes, token URLs, refusals —
    is `tests/test_player_library.cjs`; this file checks what can be checked by reading.

Run the node half too (both are needed; neither alone is the claim):

    python -m unittest tests.test_player_library
    node tests/test_player_library.cjs

`python -m tests.test_player_library --write` regenerates the fixture from the server.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import listening
import subsonic

APP_DIR = Path(__file__).resolve().parents[1]
PLAYER = APP_DIR / 'player'
SHELL = PLAYER / 'shell'
BRIDGE = SHELL / 'api-bridge.js'
BOOT = SHELL / 'library-boot.js'
DOCS = APP_DIR / 'docs' / 'player' / 'library.md'
FIXTURE = APP_DIR / 'tests' / 'fixtures' / 'player' / 'envelopes.json'
NODE_TEST = APP_DIR / 'tests' / 'test_player_library.cjs'

#: The routes PLAYER-003 maps, in the order the bridge lists them. `PLAYER-003` is a fixed
#: subset on purpose: a route that is not here is refused by name, and the docs have to say so.
ROUTES = [
    ('GET', '/api/library'),
    ('GET', '/api/playlists'),
    ('POST', '/api/playlists'),
    ('GET', '/api/playlists/:id'),
    ('POST', '/api/playlists/:id'),
    ('GET', '/api/listening/saves'),
    ('POST', '/api/listening/saves'),
    ('GET', '/api/listening/folders'),
    ('GET', '/api/listening/now'),
    ('POST', '/api/listening/now'),
    ('GET', '/api/track/:id/audio'),
    ('GET', '/api/track/:id/download'),
    ('POST', '/api/activity/listen'),
    ('GET', '/api/bookmarks'),
    ('GET', '/api/search'),
    ('POST', '/api/search'),
]

#: Two albums by two artists, so a grouping mistake cannot pass as a pass.
TRACKS = [
    {'id': 11, 'title': 'Night Drive', 'artist_name': 'Ana Vale', 'album': 'After Hours',
     'duration': 214, 'track_number': 1, 'albumId': '', 'artistId': '',
     'file': 'music/ana-vale/after-hours/01-night-drive.mp3', 'size': 8_600_000,
     'added': 1760000000, 'artwork': 'https://art.example/after-hours.jpg'},
    {'id': 12, 'title': 'Tail Lights', 'artist_name': 'Ana Vale', 'album': 'After Hours',
     'duration': 187, 'track_number': 2, 'albumId': '', 'artistId': '',
     'file': 'music/ana-vale/after-hours/02-tail-lights.mp3', 'size': 7_500_000,
     'added': 1760000060, 'artwork': ''},
    {'id': 21, 'title': 'Field Notes', 'artist_name': 'Bo Ito', 'album': 'Field Notes',
     'duration': 341, 'track_number': 1, 'albumId': '', 'artistId': '',
     'file': 'music/bo-ito/field-notes/01-field-notes.flac', 'size': 24_000_000,
     'added': 1760500000, 'artwork': ''},
]

#: The one track this member starred; `getStarred2` and `song(starred=…)` both read it.
STARRED = {21}
#: The playlist the server holds, and the tracks in it.
PLAYLIST_ID = '7'
PLAYLIST_TITLE = 'Road trip'
PLAYLIST_TRACKS = [11, 21]


def envelope(payload=None, error=None):
    """`listening.envelope()`'s JSON body, without the HTTP handler around it."""
    data = {'status': 'failed' if error else 'ok',
            'version': subsonic.PROTOCOL_VERSION,
            'type': subsonic.SERVER_TYPE,
            'serverVersion': subsonic.SERVER_VERSION,
            'openSubsonic': True}
    data.update({'error': {'code': error[0], 'message': error[1]}} if error else (payload or {}))
    return {'subsonic-response': data}


def catalogue():
    """The catalogue exactly as `listening.rest()` builds it before `subsonic.handle` sees it.

    Same two lines of id derivation, same `subsonic.song()` call, same album/artist rows: the
    fixture is this function's output, so a change to the server's row shape shows up here as
    a fixture the node test no longer recognises, not as a bridge that quietly reads nothing.
    """
    tracks = []
    for source in TRACKS:
        track = dict(source)
        track['albumId'] = listening.key('al_', track['artist_name'], track['album'])
        track['artistId'] = listening.key('ar_', track['artist_name'])
        tracks.append(track)
    songs = [subsonic.song(track, STARRED) for track in tracks]
    albums = {}
    artists = {}
    for song in songs:
        album = albums.setdefault(song['albumId'], {
            'id': song['albumId'], 'name': song['album'] or 'Unknown album',
            'artist': song['artist'], 'artistId': song['artistId'],
            'songCount': 0, 'duration': 0, 'coverArt': song['albumId']})
        album['songCount'] += 1
        album['duration'] += song['duration']
        artists.setdefault(song['artistId'], {'id': song['artistId'], 'name': song['artist'],
                                              'albumCount': 0, 'coverArt': song['artistId']})
    for album in albums.values():
        artists[album['artistId']]['albumCount'] += 1
    return tracks, songs, albums, artists


def build_fixture():
    tracks, songs, albums, artists = catalogue()
    by_id = {song['id']: song for song in songs}
    album_list = sorted(albums.values(), key=lambda row: (row['artist'], row['name']))
    artist_list = sorted(artists.values(), key=lambda row: row['name'])
    entries = [by_id[str(track_id)] for track_id in PLAYLIST_TRACKS]
    playlist = {'id': PLAYLIST_ID, 'name': PLAYLIST_TITLE, 'public': False,
                'songCount': len(entries), 'duration': sum(e['duration'] for e in entries)}
    starred = [song for song in songs if int(song['id']) in STARRED]
    return {
        'server': {'type': subsonic.SERVER_TYPE, 'version': subsonic.PROTOCOL_VERSION,
                   'serverVersion': subsonic.SERVER_VERSION},
        'songs': songs,
        'albums': album_list,
        'artists': artist_list,
        'playlists': [playlist],
        'envelopes': {
            'ping': envelope(),
            'getAlbumList2': envelope({'albumList2': {'album': album_list}}),
            # A page past the end is how the bridge learns it has read the library.
            'getAlbumList2:empty': envelope({'albumList2': {'album': []}}),
            'getAlbumList2:code30': envelope(
                error=(30, 'This server does not implement getAlbumList2')),
            'getAlbumList2:code40': envelope(error=(40, 'Invalid device credentials')),
            'getArtists': envelope({'artists': {
                'ignoredArticles': '',
                'index': [{'name': 'Saved', 'artist': artist_list}]}}),
            **{f'getArtist:{album["artistId"]}': envelope({'artist': {
                **next(a for a in artist_list if a['id'] == album['artistId']),
                'album': [album]}}) for album in album_list},
            **{f'getAlbum:{album["id"]}': envelope({'album': {
                **album,
                'song': [song for song in songs if song['albumId'] == album['id']]}})
               for album in album_list},
            'getPlaylists': envelope({'playlists': {'playlist': [playlist]}}),
            f'getPlaylist:{PLAYLIST_ID}': envelope({'playlist': {**playlist, 'entry': entries}}),
            'getStarred2': envelope({'getStarred2': {
                'song': starred,
                'album': [a for a in album_list if a['id'] in {s['albumId'] for s in starred}],
                'artist': [a for a in artist_list if a['id'] in {s['artistId'] for s in starred}]}}),
            'getPlayQueue': envelope({'playQueue': {
                'current': songs[0]['id'], 'position': 42000, 'username': 'bazzite',
                'changed': subsonic.iso(1760000100), 'entry': [songs[0]]}}),
            'search3': envelope({'searchResult3': {
                'song': [songs[0]], 'album': [], 'artist': []}}),
            'getBookmarks': envelope({'bookmarks': {'bookmark': [{
                'entry': songs[0], 'position': 60000, 'username': 'bazzite', 'comment': '',
                'created': subsonic.iso(1760000200), 'changed': subsonic.iso(1760000300)}]}}),
            # Writes: the bridge only has to prove it asked; the server answers with `{}`.
            'createPlaylist': envelope(),
            'updatePlaylist': envelope(),
            'star': envelope(),
            'unstar': envelope(),
            'scrobble': envelope(),
            'savePlayQueue': envelope(),
            'getCoverArt': envelope(),
        },
        # Named so a reader can see what the fixture is for without running the router.
        'expectations': {
            'tracks': len(songs),
            'albums': len(album_list),
            'artists': len(artist_list),
            'playlist_id': int(PLAYLIST_ID),
            'playlist_tracks': [int(track_id) for track_id in PLAYLIST_TRACKS],
            'starred': [int(song['id']) for song in starred],
        },
    }


def serialise(fixture):
    return json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def route_labels(source):
    """Every `route("METHOD", "/api/…")` in the bridge, as (method, path)."""
    return re.findall(r'route\("(GET|POST)",\s*"(/api/[^"]+)"', source)


def refusal_patterns(source):
    """Every regex literal in the refusal table, as source text Python can compile."""
    return re.findall(r'^\s*\[/(\^[^\n]+?\$)/,', source, re.M)


def code_only(source):
    """The file with comments and string literals removed.

    A sentence about importing is not an import statement, so the build-step check has to read
    the code rather than the prose. This walks the file once, tracking whether it is inside a
    string, a line comment or a block comment, rather than guessing with regexes (a trailing
    `// import …` comment and an apostrophe in `host's` both survive a regex).
    """
    out = []
    index, size = 0, len(source)
    while index < size:
        char = source[index]
        pair = source[index + 1] if index + 1 < size else ''
        if char == '/' and pair == '*':
            end = source.find('*/', index + 2)
            if end < 0:
                break
            index = end + 2
            out.append(' ')
        elif char == '/' and pair == '/':
            end = source.find('\n', index)
            if end < 0:
                break
            index = end
        elif char in '"\'':
            quote = char
            index += 1
            while index < size:
                if source[index] == '\\':
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            out.append(quote * 2)
        elif char == '`':
            index += 1
            while index < size:
                if source[index] == '\\':
                    index += 2
                    continue
                if source[index] == '`':
                    index += 1
                    break
                index += 1
            out.append('``')
        else:
            out.append(char)
            index += 1
    return ''.join(out)


class Fixture(unittest.TestCase):
    """The envelopes the bridge is tested against are this server's own output."""

    def test_the_fixture_is_the_servers_current_shape(self):
        self.assertTrue(FIXTURE.is_file(),
                        'no fixture — run: python -m tests.test_player_library --write')
        self.assertEqual(json.loads(FIXTURE.read_text()), build_fixture(),
                         'the server\'s row shape changed; regenerate with --write and re-read '
                         'the bridge where it reads a field that moved')

    def test_a_song_row_carries_every_field_the_bridge_reads(self):
        fixture = build_fixture()
        # The bridge maps exactly these; if `subsonic.song()` renames one, this fails here
        # instead of showing up as a blank column in the window.
        for field in ('id', 'title', 'artist', 'album', 'duration', 'track', 'coverArt',
                      'albumId', 'created'):
            self.assertIn(field, fixture['songs'][0], field)

    def test_the_envelope_carries_the_protocol_identity(self):
        top = build_fixture()['envelopes']['ping']['subsonic-response']
        self.assertEqual(top['status'], 'ok')
        for field in ('version', 'type', 'serverVersion', 'openSubsonic'):
            self.assertIn(field, top)


class RouteTable(unittest.TestCase):
    """The table is the whole statement of what this player answers."""

    @classmethod
    def setUpClass(cls):
        cls.source = BRIDGE.read_text()
        # The regex literals escape their slashes; this is the same text a person reads.
        cls.flat = cls.source.replace('\\/', '/')
        cls.labels = route_labels(cls.source)

    def test_the_table_is_exactly_the_documented_subset(self):
        self.assertEqual(self.labels, ROUTES)

    def test_no_route_is_a_catch_all(self):
        for method, path in self.labels:
            self.assertNotIn('*', path, f'{method} {path} is a wildcard')
            self.assertNotIn('.*', path, f'{method} {path} is a regular expression')
            self.assertTrue(path.startswith('/api/'), path)

    def test_an_unmapped_path_is_refused_with_a_sentence(self):
        # The dispatcher's last resort must name the path and say why it is not empty.
        self.assertIn('The player has no route for', self.source)
        self.assertIn('an empty answer looks like an empty library', self.source)
        self.assertIn('function refusal(', self.source)
        self.assertIn('refusal(404', self.source)
        self.assertIn('refusal(405', self.source)
        # One `answer(200` per routed handler and no place that answers a miss with data.
        dispatch = re.search(r'async function dispatch\((.+?)\n  \}', self.source, re.S).group(1)
        self.assertEqual(dispatch.count('answer(200'), 1)
        self.assertEqual(dispatch.count('refusal('), 2)

    def test_the_hosts_own_routes_are_refused_by_name(self):
        patterns = refusal_patterns(self.source)
        self.assertGreaterEqual(len(patterns), 15, 'the refusal table lost entries')
        # Sample paths, one per family the app still asks for. Each must match a refusal, so a
        # control that hits it says something true instead of quietly doing nothing.
        for route in ('/api/state', '/api/add', '/api/library/download', '/api/import',
                      '/api/artist', '/api/members', '/api/queue', '/api/listening/devices',
                      '/api/health', '/api/boost'):
            self.assertTrue(any(re.search(pattern, route) for pattern in patterns),
                            f'no refusal explains {route}')
        for phrase in ('never acquires music', 'streams from your server',
                       'importing files is a host feature'):
            self.assertTrue(phrase in self.source, phrase)

    def test_the_table_and_the_docs_cannot_drift(self):
        self.assertTrue(DOCS.is_file(), 'docs/player/library.md is a deliverable')
        rows = re.findall(r'^\|\s*(GET|POST)\s*\|\s*`(/api/[^`]+)`\s*\|', DOCS.read_text(), re.M)
        self.assertEqual(rows, ROUTES, 'the documented table and the route table differ')


class Secrets(unittest.TestCase):
    """A URL the page renders, and the log, are read by people and by other software."""

    @classmethod
    def setUpClass(cls):
        cls.source = BRIDGE.read_text()

    def test_stream_and_download_urls_use_the_protocols_token_form(self):
        self.assertIn('function mediaUrl(', self.source)
        media = re.search(r'function mediaUrl\(endpoint, appId\)\s*\{(.+?)\n  \}', self.source,
                          re.S).group(1)
        self.assertIn('restUrl(state, "token"', media)
        self.assertNotIn('"plain"', media)

    def test_the_password_is_only_ever_appended_for_a_fetch(self):
        # One place writes `p`; everything else asks for `token`. If a second one appears,
        # this test is the place that notices.
        self.assertEqual(self.source.count('search.append("p", state.password)'), 1)
        block = re.search(r'if \(auth === "token"\) \{(.+?)\} else \{(.+?)\}', self.source, re.S)
        self.assertIsNotNone(block, 'the two auth forms are no longer distinguishable')
        self.assertIn('md5(state.password + value)', block.group(1))
        self.assertIn('search.append("p"', block.group(2))

    def test_the_log_keeps_endpoints_and_statuses_only(self):
        trace = re.search(r'state\.trace\.push\(([^;]+)\);', self.source, re.S).group(1)
        self.assertIn('endpoint', trace)
        self.assertIn('status', trace)
        for leak in ('url', 'password', 'salt', 'query'):
            self.assertNotIn(leak, trace, f'the trace records {leak}')

    def test_covers_are_handed_over_as_object_urls(self):
        # An <img src> cannot send a header and must not carry `u`/`p`, so the bridge fetches.
        self.assertIn('fetchCover', self.source)
        self.assertIn('objectUrl(', self.source)
        self.assertIn('getCoverArt', self.source)


class Boot(unittest.TestCase):
    """The half that waits for the session and puts the library in the window."""

    @classmethod
    def setUpClass(cls):
        cls.source = BOOT.read_text()

    def test_it_waits_for_the_connect_screens_session(self):
        self.assertIn('PlayerConnect', self.source)
        self.assertIn('current()', self.source)
        self.assertIn('PlayerApi', self.source)
        self.assertIn('install(', self.source)

    def test_it_writes_the_page_into_the_same_realm(self):
        # A navigation would drop the in-memory session; that is the whole reason for this.
        for token in ('document.open()', 'document.write(', 'document.close()', './index.html'):
            self.assertIn(token, self.source, token)
        self.assertNotIn('location.replace', self.source)
        self.assertNotIn('location.href', self.source)

    def test_it_hides_the_controls_the_player_cannot_answer(self):
        for selector in ('#qtab-search', '#url', '#btn-add', '#addstatus', '#exportbtn',
                         '#view-library .libtools'):
            self.assertIn(selector, self.source, selector)
        for page in ('/party', '/dj', '/swipe'):
            self.assertIn(f'href="{page}"', self.source, page)

    def test_it_defaults_to_the_library_and_keeps_a_choice_already_made(self):
        defaults = re.search(r'const DEFAULTS = \[(.+?)\];', self.source, re.S).group(1)
        self.assertIn('"hl.view", "library"', defaults)
        self.assertIn('"hl.scope", "shared"', defaults)
        self.assertIn('if (current) continue', self.source)

    def test_it_writes_no_credential_to_storage(self):
        # `defaults()` is the only writer, and its keys are view state.
        self.assertEqual(self.source.count('setItem('), 1)
        self.assertNotIn('sessionStorage', self.source)
        self.assertNotIn('indexedDB', self.source)
        self.assertNotIn('document.cookie', self.source)

    def test_it_says_why_when_the_library_cannot_open(self):
        self.assertIn('could not open', self.source)
        self.assertIn('connect-msg', self.source)

    def test_no_shell_file_needs_a_build_step(self):
        for path in (BRIDGE, BOOT):
            code = code_only(path.read_text())
            self.assertNotIn('import ', code, path.name)
            self.assertNotIn('require(', code, path.name)
            self.assertNotIn('export ', code, path.name)


class Wiring(unittest.TestCase):
    """The exact two lines connect.html needs, recorded where the next card can find them."""

    def test_the_docs_name_the_script_tags_in_load_order(self):
        docs = DOCS.read_text()
        bridge = docs.find('<script src="./api-bridge.js"></script>')
        boot = docs.find('<script src="./library-boot.js"></script>')
        connect = docs.find('<script src="./connect.js"></script>')
        self.assertGreaterEqual(connect, 0, 'the anchor script is not named')
        self.assertGreater(bridge, connect, 'api-bridge.js must load after connect.js')
        self.assertGreater(boot, bridge, 'library-boot.js must load after api-bridge.js')

    def test_the_node_half_exists_and_names_the_fixture(self):
        self.assertTrue(NODE_TEST.is_file())
        self.assertIn('envelopes.json', NODE_TEST.read_text())


if __name__ == '__main__':
    import sys

    if '--write' in sys.argv:
        sys.argv.remove('--write')
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(serialise(build_fixture()))
        print(f'wrote {FIXTURE.relative_to(APP_DIR)}')
    unittest.main()