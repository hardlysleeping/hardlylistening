"""The client SDK, driven against a stub server on 127.0.0.1.

Nothing here reaches a real HardlyListening, and nothing here is mocked at the Python level: the
stub answers the canned envelopes and capability documents the cards ask for, so the thing
under test is the wire — which query parameters were sent, which header carried the range, and
which exception a failure became. The stub also reproduces the two refusals this server really
makes: `Unsupported endpoint` for the playlist writes it does not implement, and a 500 that is
not an envelope at all.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sdk import subsonic
from sdk.subsonic import ClientError, Credentials, NotSupported, SubsonicClient, WrongCredentials

STREAM = bytes(range(256)) * 12          # 3072 bytes: every offset is distinguishable


def song_json(track='7'):
    return {'id': str(track), 'title': f'Track {track}', 'artist': 'Artist', 'album': 'Album',
            'albumId': 'al_1', 'artistId': 'ar_1', 'duration': 200, 'track': 1,
            'suffix': 'mp3', 'size': 3000}


def envelope(payload=None, error=None):
    data = {'status': 'failed' if error else 'ok', 'version': '1.16.1',
            'type': 'HardlyListening', 'serverVersion': 'scoped-1', 'openSubsonic': True}
    data.update({'error': {'code': error[0], 'message': error[1]}} if error else (payload or {}))
    return json.dumps({'subsonic-response': data}).encode()


class Call:
    """One request the stub received, kept so a test can read the wire instead of a mock."""

    def __init__(self, path, query, headers):
        self.path, self.query, self.headers = path, query, headers

    @property
    def endpoint(self):
        return self.path.rsplit('/', 1)[-1].removesuffix('.view')

    def params(self):
        return {key: values[0] for key, values in self.query.items()}


class Stub(BaseHTTPRequestHandler):
    """A canned server. Each test points `answer` at its own dispatcher."""

    requests = []
    answer = None

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        headers = {name.lower(): value for name, value in self.headers.items()}
        Stub.requests.append(Call(parsed.path, query, headers))
        status, extra, body = Stub.answer(parsed.path, query, headers)
        # A test may claim more bytes than it writes, which is what a dropped transfer looks
        # like on the wire; the client has to notice and ask again from where it stopped.
        claimed = int(extra.pop('X-Claim-Length', len(body)))
        self.send_response(status)
        for name, value in extra.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(claimed))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        if len(body) < claimed:
            self.close_connection = True

    def log_message(self, *args):
        pass


class StubServer(unittest.TestCase):
    """A stub in a thread, and the base URL a client is pointed at."""

    def setUp(self):
        Stub.requests = []
        Stub.answer = self.answer
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        self.thread = threading.Thread(target=self.http.serve_forever, args=(0.02,), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = 'http://127.0.0.1:%d' % self.http.server_port

    def stop(self):
        Stub.answer = None
        self.http.shutdown()
        self.thread.join(timeout=10)
        self.http.server_close()

    def answer(self, path, query, headers):
        return 404, {'Content-Type': 'text/plain'}, b'no such endpoint'

    def calls(self, endpoint=None):
        return [call for call in Stub.requests if endpoint is None or call.endpoint == endpoint]


class Wire(StubServer):
    """The envelope of a working server, plus the switches a failure test needs."""

    def setUp(self):
        super().setUp()
        self.starred = set()
        self.bookmarks = {}
        self.broken = None            # an envelope error tuple, when a test wants one
        self.http_status = None       # a bare HTTP failure, when a test wants one
        self.raw = None               # a body that is not an envelope
        self.truncate = False         # drop the first stream transfer halfway through
        self.client = SubsonicClient(self.base, Credentials.from_password('hl_device', 'secret'))

    def json(self, body):
        return 200, {'Content-Type': 'application/json'}, body

    def serve_stream(self, body, headers):
        """Slice a byte range the way `library/playback.py` does: 200, 206 or 416."""
        header = headers.get('range', '')
        if not header:
            return 200, {'Content-Type': 'audio/mpeg'}, body
        start_text, _, end_text = header.partition('=')[2].partition('-')
        start = int(start_text or 0)
        end = int(end_text) if end_text else len(body) - 1
        if start >= len(body):
            return 416, {'Content-Range': f'bytes */{len(body)}'}, b''
        end = min(end, len(body) - 1)
        extra = {'Content-Type': 'audio/mpeg',
                 'Content-Range': f'bytes {start}-{end}/{len(body)}'}
        if self.truncate:
            self.truncate = False
            remaining = len(body) - start
            extra['X-Claim-Length'] = remaining
            return 206, extra, body[start:start + remaining // 2]
        return 206, extra, body[start:end + 1]

    def answer(self, path, query, headers):
        endpoint = path.rsplit('/', 1)[-1].removesuffix('.view')
        params = {key: values[0] for key, values in query.items()}
        if self.raw is not None:
            return self.json(self.raw)
        if self.http_status:
            return self.http_status, {'Content-Type': 'text/html'}, b'<h1>boom</h1>'
        if self.broken:
            return self.json(envelope(error=self.broken))
        if endpoint == 'stream':
            return self.serve_stream(STREAM, headers)
        if endpoint == 'ping':
            return self.json(envelope())
        if endpoint == 'getOpenSubsonicExtensions':
            return self.json(envelope({'openSubsonicExtensions': [
                {'name': 'transcoding', 'versions': [1]}]}))
        if endpoint == 'getArtists':
            return self.json(envelope({'artists': {'ignoredArticles': '', 'index': [
                {'name': 'Saved', 'artist': [{'id': 'ar_1', 'name': 'Artist', 'albumCount': 1}]}]}}))
        if endpoint == 'getArtist':
            return self.json(envelope({'artist': {
                'id': params['id'], 'name': 'Artist', 'albumCount': 1,
                'album': [{'id': 'al_1', 'name': 'Album', 'artist': 'Artist'}]}}))
        if endpoint == 'getAlbum':
            return self.json(envelope({'album': {
                'id': params['id'], 'name': 'Album', 'artist': 'Artist', 'artistId': 'ar_1',
                'songCount': 2, 'duration': 400, 'song': [song_json('7'), song_json('8')]}}))
        if endpoint == 'search3':
            return self.json(envelope({'searchResult3': {
                'song': [song_json('7')], 'album': [{'id': 'al_1', 'name': 'Album'}],
                'artist': [{'id': 'ar_1', 'name': 'Artist'}]}}))
        if endpoint == 'getPlaylist':
            return self.json(envelope({'playlist': {
                'id': params['id'], 'name': 'Mix', 'songCount': 1, 'duration': 200,
                'entry': [song_json('7')]}}))
        if endpoint in ('star', 'unstar'):
            for track in query.get('id', ()):
                (self.starred.add if endpoint == 'star' else self.starred.discard)(track)
            for album in query.get('albumId', ()):
                (self.starred.add if endpoint == 'star' else self.starred.discard)(album)
            return self.json(envelope())
        if endpoint == 'getBookmarks':
            rows = [{'entry': song_json(track), 'position': position, 'comment': comment,
                     'created': '2026-11-01T00:00:00.000Z',
                     'changed': '2026-11-02T00:00:00.000Z'}
                    for track, (position, comment) in sorted(self.bookmarks.items())]
            return self.json(envelope({'bookmarks': {'bookmark': rows}}))
        if endpoint == 'createBookmark':
            self.bookmarks[params['id']] = (int(params['position']), params.get('comment', ''))
            return self.json(envelope())
        if endpoint == 'deleteBookmark':
            self.bookmarks.pop(params['id'], None)
            return self.json(envelope())
        if endpoint == 'createPlaylist':
            # Exactly what `listening.rest()` sends for an endpoint `subsonic.py` lacks: a 404
            # flattened to code 70, with the message naming the reason.
            return self.json(envelope(error=(70, 'Unsupported endpoint')))
        return super().answer(path, query, headers)


class CredentialsOnTheWire(Wire):

    def test_password_credentials_are_sent_as_u_and_p(self):
        self.assertTrue(self.client.ping())
        params = self.calls('ping')[0].params()
        self.assertEqual((params['u'], params['p']), ('hl_device', 'secret'))
        self.assertNotIn('t', params)
        self.assertEqual(params['c'], 'hardlylistening-sdk')
        self.assertEqual((params['v'], params['f']), ('1.16.1', 'json'))

    def test_token_credentials_send_t_and_s_and_no_password(self):
        token = 'a' * 32
        client = SubsonicClient(self.base, Credentials.from_token('hl_device', token, 'salt'))
        client.ping()
        params = self.calls('ping')[0].params()
        self.assertEqual((params['t'], params['s']), (token, 'salt'))
        self.assertNotIn('p', params)

    def test_the_device_body_is_usable_credentials(self):
        """`POST /api/listening/devices` prints this shape once; it is a device's whole login."""
        credentials = Credentials.from_device(
            {'username': 'hl_device', 'password': 'pw', 'token_auth': True})
        self.assertEqual(credentials.params(), {'u': 'hl_device', 'p': 'pw'})

    def test_credentials_without_a_secret_say_so(self):
        with self.assertRaises(ValueError):
            Credentials(username='hl_device').params()


class Envelopes(Wire):

    def test_the_envelope_is_parsed(self):
        parsed = self.client.request('ping')
        self.assertEqual(parsed['status'], 'ok')
        self.assertEqual(parsed['serverVersion'], 'scoped-1')
        self.assertTrue(parsed['openSubsonic'])
        self.assertEqual(self.client.extensions(), (subsonic.Extension('transcoding', (1,)),))

    def test_a_failed_envelope_raises_its_code(self):
        self.broken = (40, 'Invalid device credentials')
        with self.assertRaises(WrongCredentials) as caught:
            self.client.ping()
        self.assertEqual(caught.exception.code, 40)
        self.assertEqual(caught.exception.message, 'Invalid device credentials')

    def test_an_unimplemented_endpoint_is_not_supported(self):
        with self.assertRaises(NotSupported) as caught:
            self.client.create_playlist('Mix', ['7'])
        self.assertIn('Unsupported endpoint', caught.exception.message)
        self.assertEqual(self.calls('createPlaylist')[0].params()['songId'], '7')

    def test_a_server_that_says_it_does_not_implement_is_not_supported(self):
        self.broken = (30, 'This server does not implement hls')
        with self.assertRaises(NotSupported):
            self.client.request('hls')

    def test_an_http_error_is_a_clean_client_error(self):
        self.http_status = 500
        with self.assertRaises(ClientError) as caught:
            self.client.ping()
        self.assertIn('500', str(caught.exception))

    def test_an_answer_that_is_not_an_envelope_is_a_client_error(self):
        self.raw = b'<html>a proxy, probably</html>'
        with self.assertRaises(ClientError):
            self.client.ping()


class Library(Wire):

    def test_artists_are_flattened_out_of_the_index(self):
        self.assertEqual([artist.id for artist in self.client.artists()], ['ar_1'])

    def test_an_artist_carries_their_albums(self):
        artist = self.client.artist('ar_1')
        self.assertEqual(artist.name, 'Artist')
        self.assertEqual([album.id for album in artist.albums], ['al_1'])

    def test_an_album_carries_its_songs_typed(self):
        album = self.client.album('al_1')
        self.assertEqual([song.id for song in album.songs], ['7', '8'])
        self.assertEqual(album.songs[0].title, 'Track 7')
        self.assertEqual(album.songs[0].duration, 200)
        self.assertFalse(album.songs[0].starred)

    def test_search3_splits_the_three_kinds(self):
        result = self.client.search('Track')
        self.assertEqual([song.id for song in result.songs], ['7'])
        self.assertEqual([album.id for album in result.albums], ['al_1'])
        self.assertEqual([artist.name for artist in result.artists], ['Artist'])
        params = self.calls('search3')[0].params()
        self.assertEqual((params['query'], params['songCount']), ('Track', '20'))

    def test_a_playlist_returns_its_entries(self):
        playlist = self.client.playlist('9')
        self.assertEqual((playlist.name, playlist.song_count), ('Mix', 1))
        self.assertEqual([song.id for song in playlist.songs], ['7'])


class StarsAndBookmarks(Wire):

    def test_star_and_unstar_round_trip(self):
        self.client.star(['5', '6'], ['al_1'])
        call = self.calls('star')[0]
        self.assertEqual(call.query['id'], ['5', '6'])
        self.assertEqual(call.query['albumId'], ['al_1'])
        self.assertEqual(self.starred, {'5', '6', 'al_1'})
        self.client.unstar(['5', '6'], ['al_1'])
        self.assertEqual(self.starred, set())
        with self.assertRaises(ValueError):
            self.client.star()

    def test_bookmark_round_trip(self):
        self.client.create_bookmark('7', 42000, 'chorus')
        bookmarks = self.client.bookmarks()
        self.assertEqual(len(bookmarks), 1)
        self.assertEqual((bookmarks[0].song.id, bookmarks[0].position, bookmarks[0].comment),
                         ('7', 42000, 'chorus'))
        self.client.delete_bookmark('7')
        self.assertEqual(self.client.bookmarks(), ())


class Streams(Wire):

    def test_a_stream_url_carries_the_stream_parameters(self):
        target = urlparse(self.client.stream_url('7', max_bitrate=128, format='opus'))
        params = {key: values[0] for key, values in parse_qs(target.query).items()}
        self.assertEqual(target.path, '/rest/stream.view')
        self.assertEqual(params['id'], '7')
        self.assertEqual((params['maxBitRate'], params['format']), ('128', 'opus'))
        self.assertEqual((params['u'], params['p'], params['f']), ('hl_device', 'secret', 'json'))
        bare = parse_qs(urlparse(self.client.stream_url('7')).query)
        self.assertNotIn('maxBitRate', bare)
        self.assertNotIn('format', bare)

    def test_one_range_header_per_request(self):
        data = self.client.read_range('7', offset=100, length=50)
        self.assertEqual(data, STREAM[100:150])
        calls = self.calls('stream')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].headers.get('range'), 'bytes=100-149')
        self.assertEqual([name for name in calls[0].headers if name == 'range'], ['range'])

    def test_a_dropped_stream_resumes_from_the_byte_it_stopped_at(self):
        self.truncate = True
        self.assertEqual(b''.join(self.client.iter_range('7')), STREAM)
        self.assertEqual([call.headers['range'] for call in self.calls('stream')],
                         ['bytes=0-', f'bytes={len(STREAM) // 2}-'])

    def test_a_range_past_the_end_is_a_client_error(self):
        with self.assertRaises(ClientError) as caught:
            self.client.read_range('7', offset=len(STREAM) + 1)
        self.assertIn('416', str(caught.exception))


if __name__ == '__main__':
    unittest.main()