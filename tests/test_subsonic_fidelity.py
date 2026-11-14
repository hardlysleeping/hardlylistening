"""Fidelity against another implementation: Navidrome's recorded answers (HL-231).

Every other Subsonic test in this tree has our server answering our client's questions. That
proves the surface is self-consistent, not that it is *compatible*: a third-party server arranges
the same protocol differently, and a strict client refuses to guess. `tests/fixtures/subsonic/
navidrome-0.64.2/` holds real responses recorded from a public Navidrome 0.64.2 instance
(`tools/record-subsonic-fixtures.py`, `manifest.json` says when and from where), and this module
drives both directions over them:

* `RecordedThirdParty` — our SDK client (`sdk/subsonic.py`) against Navidrome's JSON, with no
  network and no server of ours in the loop.
* `OurServerHoldsTheSameContract` — our own server's live answers, cell by cell, where the
  recorded response is the reference for what a client is allowed to assume.

`docs/subsonic-compatibility.md` is the matrix; each test below is one cell of it.
"""
import json
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from sdk import subsonic as client
from test_listening import ListeningHTTP

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'subsonic' / 'navidrome-0.64.2'


def recorded(label):
    """One recorded envelope, by fixture name."""
    return json.loads((FIXTURES / f'{label}.json').read_text())['subsonic-response']


class Reply:
    """The small part of an `http.client` response the SDK reads."""

    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Recorder:
    """Serves the recorded bodies, routing each request the way a server decides it.

    Two recordings are refusals, so they are filed under a label of their own
    (`getArtist_missing_id`, `getCoverArt_unknown`); the router picks those from the request's
    parameters instead of from the endpoint name.
    """

    def __init__(self, responses):
        self.responses = responses
        self.asked = []

    def __call__(self, request, timeout=None):
        parsed = urllib.parse.urlparse(request.full_url)
        endpoint = parsed.path.split('/rest/')[1].split('.view')[0]
        query = urllib.parse.parse_qs(parsed.query)
        self.asked.append(endpoint)
        label = endpoint
        if endpoint == 'getArtist' and not (query.get('id') or [''])[0]:
            label = 'getArtist_missing_id'
        elif endpoint == 'getCoverArt':
            label = 'getCoverArt_unknown'
        if label not in self.responses:
            raise AssertionError(f'no recorded response for {endpoint}')
        return Reply(self.responses[label])


def recorded_client():
    responses = {path.stem: path.read_bytes()
                 for path in sorted(FIXTURES.glob('*.json')) if path.stem != 'manifest'}
    opener = Recorder(responses)
    credentials = client.Credentials.from_password('demo', 'demo')
    return client.SubsonicClient('https://demo.navidrome.org', credentials, opener=opener), opener


class RecordedThirdParty(unittest.TestCase):
    """The SDK client against a server that is not ours."""

    def test_cell_2_open_subsonic_extensions_are_read_from_a_third_party_server(self):
        api, _ = recorded_client()
        self.assertTrue(api.ping())
        names = {extension.name for extension in api.extensions()}
        self.assertIn('songLyrics', names)
        self.assertIn('formPost', names)

    def test_cell_9_nested_lists_keep_their_singular_key(self):
        """`artists.index[].artist` — the shape a client walks, not a flat array."""
        api, _ = recorded_client()
        artists = api.artists()
        row = recorded('getArtists')['artists']['index'][0]['artist'][0]
        self.assertEqual(artists[0].id, row['id'])
        self.assertEqual(artists[0].name, row['name'])
        self.assertEqual(len(artists), sum(len(index['artist'])
                                           for index in recorded('getArtists')['artists']['index']))

    def test_cell_8_child_objects_carry_what_a_player_needs(self):
        """A song from another implementation parses into the same value we use for ours."""
        api, _ = recorded_client()
        played = api.song(recorded('getSong')['song']['id'])
        reference = recorded('getSong')['song']
        self.assertEqual(played.title, reference['title'])
        self.assertEqual(played.album, reference['album'])
        self.assertEqual(played.artist, reference['artist'])
        self.assertIsInstance(played.duration, int)
        self.assertIsInstance(played.size, int)
        self.assertEqual(played.raw['isDir'], False)
        self.assertTrue(played.raw['created'].endswith('Z'))

    def test_cell_10_an_empty_answer_is_an_empty_list_not_an_error(self):
        """`getLyricsBySongId` on a song with no lyrics: status ok, no lines, not a refusal.

        The recorded server omits `structuredLyrics` entirely when it has nothing; ours sends
        an empty array. A client has to accept both, which is why this cell asserts "no lines"
        rather than pinning one of the two spellings.
        """
        envelope = recorded('getLyricsBySongId')
        self.assertEqual(envelope['status'], 'ok')
        self.assertEqual(envelope['lyricsList'].get('structuredLyrics') or [], [])

    def test_cell_5_a_missing_parameter_is_a_protocol_error_not_an_http_failure(self):
        api, _ = recorded_client()
        with self.assertRaises(client.NotFound):
            api.artist('')                      # recorded: error 70, "Artist not found"
        envelope = recorded('getArtist_missing_id')
        self.assertEqual(envelope['status'], 'failed')
        self.assertEqual(envelope['error']['code'], 70)

    def test_cell_6_an_unknown_object_is_a_protocol_error(self):
        api, _ = recorded_client()
        with self.assertRaises(client.NotFound) as caught:
            api.request('getCoverArt', id='nonexistent-cover-art')
        self.assertEqual(caught.exception.code, 70)

    def test_cell_11_a_declared_extension_is_answered_by_a_third_party_server(self):
        """`songLyrics` is declared in `getOpenSubsonicExtensions`, so asking is legitimate."""
        api, opener = recorded_client()
        self.assertIn('songLyrics', {extension.name for extension in api.extensions()})
        api.request('getLyricsBySongId', id=recorded('getSong')['song']['id'])
        self.assertIn('getLyricsBySongId', opener.asked)


class OurServerHoldsTheSameContract(unittest.TestCase):
    """The same cells, asked of us, judged by what the recording says a client may assume."""

    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    token = ListeningHTTP.token
    user = ListeningHTTP.user
    api = ListeningHTTP.api
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save

    def setUp(self):
        ListeningHTTP.setUp(self)
        # The fixture's admin sees nothing until the member's scope is granted; the Subsonic
        # surface has no admin bypass, which is itself one of the recorded assumptions.
        for track_id in self.ids:
            self.save('track', track_id)
        self.save('playlist', self.pid)
        # `lyrics.get` reaches LRCLIB for a song with no sidecar; a test must not. The stub is
        # at the one function that owns the socket, so the cache and the payload shaping stay
        # the real code under test.
        self.enterContext(mock.patch('lyrics.lookup', lambda track: {
            'status': 'not_found', 'source': 'stub', 'lines': [], 'text': '', 'word_timed': False}))

    def test_cell_1_the_envelope_names_the_server_and_the_protocol(self):
        ours = self.response('ping')
        theirs = recorded('ping')
        for key in ('status', 'version', 'type', 'serverVersion'):
            self.assertIn(key, ours, key)
            self.assertIn(key, theirs, key)
        self.assertEqual(ours['status'], 'ok')
        self.assertGreaterEqual(tuple(int(part) for part in ours['version'].split('.')),
                                (1, 16, 1))

    def test_cell_2_open_subsonic_is_declared_with_versions(self):
        ours = self.response('ping')
        self.assertIs(ours['openSubsonic'], True)
        extensions = self.response('getOpenSubsonicExtensions')['openSubsonicExtensions']
        theirs = recorded('getOpenSubsonicExtensions')['openSubsonicExtensions']
        self.assertTrue(extensions)
        for extension in extensions + theirs:
            self.assertIsInstance(extension['name'], str)
            self.assertTrue(all(isinstance(v, int) for v in extension['versions']))
        # We must not declare something we refuse to answer (the classic lie).
        self.assertIn('songLyrics', {e['name'] for e in extensions})
        self.assertEqual(self.response('getLyricsBySongId', id=str(self.ids[0]))['status'], 'ok')
        self.assertIn('formPost', {e['name'] for e in extensions})

    def test_cell_3_json_and_xml_are_the_same_answer(self):
        code, headers, raw = self.rest('getArtists')
        self.assertEqual(code, 200)
        self.assertIn('application/json', headers.get('Content-Type', ''))
        as_json = json.loads(raw)['subsonic-response']
        query = urlencode({'u': self.device['username'], 'p': self.device['password'],
                           'v': '1.16.1', 'c': 'fixture', 'f': 'xml'})
        code, headers, raw = self.request('/rest/getArtists.view?' + query)
        self.assertEqual(code, 200)
        self.assertIn('xml', headers.get('Content-Type', ''))
        root = ET.fromstring(raw)
        self.assertEqual(root.tag.rsplit('}', 1)[-1], 'subsonic-response')
        self.assertEqual(root.get('status'), as_json['status'])
        self.assertEqual(root.get('version'), as_json['version'])
        listed = [element for element in root.iter()
                  if element.tag.rsplit('}', 1)[-1] == 'artist']
        self.assertEqual(len(listed), len(as_json['artists']['index'][0]['artist']))
        self.assertEqual([element.get('name') for element in listed],
                         [row['name'] for row in as_json['artists']['index'][0]['artist']])

    def test_cell_4_form_post_is_real_because_it_is_declared(self):
        """The extension is announced, so credentials in a form body must work, not 501."""
        body = urlencode({'u': self.device['username'], 'p': self.device['password'],
                          'v': '1.16.1', 'c': 'fixture', 'f': 'json'}).encode()
        code, headers, raw = self.request('/rest/ping.view', 'POST', {
            'Content-Type': 'application/x-www-form-urlencoded'}, body.decode())
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)['subsonic-response']['status'], 'ok')

    def test_cell_5_an_error_is_a_200_envelope_with_a_numeric_code(self):
        """Both servers refuse inside a 200 envelope; neither uses an HTTP status to say it."""
        args = urlencode({'u': 'nobody', 'p': 'wrong', 'v': '1.16.1', 'c': 'fixture', 'f': 'json'})
        code, headers, raw = self.request('/rest/ping.view?' + args)
        self.assertEqual(code, 200)
        envelope = json.loads(raw)['subsonic-response']
        self.assertEqual(envelope['status'], 'failed')
        self.assertEqual(envelope['error']['code'], 40)      # wrong credentials
        theirs = recorded('getArtist_missing_id')
        self.assertEqual(theirs['status'], 'failed')
        self.assertEqual(theirs['error']['code'], 70)        # the recorded refusal

    def test_cell_6_an_unknown_cover_art_id_is_error_70(self):
        envelope = self.response('getCoverArt', id='not-in-this-library')
        self.assertEqual(envelope['status'], 'failed')
        self.assertEqual(envelope['error']['code'], 70)

    def test_cell_7_pagination_parameters_are_honoured_not_ignored(self):
        """A client pages; a server that ignores `size`/`offset` shows the library twice."""
        everything = self.response('getAlbumList2', type='alphabeticalByName')['albumList2']['album']
        self.assertEqual(len(everything), 3)
        page = self.response('getAlbumList2', type='alphabeticalByName',
                             size='2', offset='1')['albumList2']['album']
        self.assertEqual([row['name'] for row in page],
                         [row['name'] for row in everything[1:3]])
        beyond = self.response('getAlbumList2', type='alphabeticalByName',
                               size='5', offset='99')['albumList2']
        self.assertEqual(beyond.get('album'), [])
        theirs = recorded('getAlbumList2')['albumList2']['album']
        self.assertLessEqual(len(theirs), 5)          # the recorded server honoured `size=5`

    def test_cell_8_our_child_objects_carry_the_same_fields(self):
        ours = self.response('getSong', id=str(self.ids[0]))['song']
        theirs = recorded('getSong')['song']
        for key in ('id', 'title', 'album', 'duration', 'size', 'isDir', 'created', 'contentType',
                    'suffix', 'parent'):
            self.assertIn(key, ours, key)
            self.assertIn(key, theirs, key)
        self.assertIs(ours['isDir'], False)
        self.assertIsInstance(ours['duration'], int)
        self.assertTrue(ours['created'].endswith('Z'))

    def test_cell_9_lists_use_the_singular_key_in_array_form(self):
        ours = self.response('getPlaylists')['playlists']['playlist']
        theirs = recorded('getPlaylists')['playlists']['playlist']
        self.assertIsInstance(ours, list)
        self.assertIsInstance(theirs, list)
        self.assertTrue(all(isinstance(row['id'], str) for row in ours))

    def test_cell_10_an_empty_search_is_an_empty_array(self):
        answer = self.response('search3', query='nothing matches this')
        self.assertEqual(answer['searchResult3']['song'], [])
        self.assertEqual(answer['searchResult3']['album'], [])
        self.assertEqual(answer['searchResult3']['artist'], [])

    def test_cell_11_a_declared_lyrics_extension_answers_even_with_no_lyrics(self):
        answer = self.response('getLyricsBySongId', id=str(self.ids[0]))
        self.assertEqual(answer['status'], 'ok')
        self.assertIn('lyricsList', answer)
        self.assertIsInstance(answer['lyricsList']['structuredLyrics'], list)

    def test_cell_12_an_unimplemented_endpoint_is_error_30_not_a_fake_empty_answer(self):
        for endpoint in ('getSonicSimilarTracks', 'findSonicPath', 'getVideos'):
            envelope = self.response(endpoint, id=str(self.ids[0]))
            self.assertEqual(envelope['status'], 'failed', endpoint)
            self.assertEqual(envelope['error']['code'], 30, endpoint)


class Provenance(unittest.TestCase):
    """The recordings are the claim; this pins that they are what they say they are."""

    def test_the_fixture_set_is_manifested(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        self.assertEqual(manifest['server'], 'navidrome-0.64.2')
        self.assertTrue(manifest['recorded'].startswith('20'))
        self.assertIn('demo.navidrome.org', manifest['base_url'])
        for label, info in manifest['responses'].items():
            self.assertTrue((FIXTURES / f'{label}.json').is_file(), label)
            envelope = recorded(label)
            self.assertEqual(envelope['type'], 'navidrome', label)
            self.assertEqual(envelope['version'], '1.16.1', label)
        self.assertGreaterEqual(len(manifest['responses']), 13)


if __name__ == '__main__':
    unittest.main()