"""The client SDK's Repo module, driven against a stub server on 127.0.0.1.

The same harness `tests/test_sdk.py` and `tests/test_sdk_social.py` use: a
`ThreadingHTTPServer` answering canned bodies, and every request kept so a test reads the wire —
which method, which path, which query — instead of a mock.

Two kinds of capability document are used, and the difference is the card:

  * `document()` is what `server.py` publishes today — `discovery` (`search`, `sonic_analysis`)
    and no `repo` domain. Every entry point must refuse against it, before the wire.
  * `document(repo=...)` is a server that does advertise a `repo` domain; only then are the five
    reads parsed. Nothing in the client invents that section, and nothing here tests a flag
    inside it, because the server writes none.

The route shapes are `api/v1.py`'s: `repo/service.py` for the rows, `store.state()` and the
`watches`, `artist_releases` and `artist_tracks` columns for their fields.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sdk import capabilities, repo, social

COOKIE = 'session-value-that-must-never-be-logged'


def document(**repo_flags):
    """The capability document `server.py` publishes, with this test's Repo domain if any.

    No flags means the domain is absent, which is how this server answers: it names `discovery`
    (`search`, `sonic_analysis`) and never mentions acquisition.
    """
    build = {
        'server': {'name': 'HardlyListening', 'version': '0.16.6', 'api': 'v1',
                   'requires_members': True},
        'subsonic': {'enabled': True, 'version': '1.16.1', 'server_version': 'scoped-1',
                     'open_subsonic': True},
        'features': {
            'accounts': {'password': True},
            'library': {'scope': 'member'},
            'playback': {'range': True},
            'party': {'enabled': True},
            'social': {'comments': True, 'votes': True, 'likes': True, 'activity': True},
            'discovery': {'search': True, 'sonic_analysis': False},
        },
    }
    if repo_flags:
        build['features']['repo'] = dict(repo_flags)
    return build


def envelope(resource, data):
    body = {'api': 'v1', 'resource': resource, 'data': data}
    if isinstance(data, list):
        body['count'] = len(data)
    return body


SUBSCRIBED = {
    'id': 3, 'provider': 'soundcloud', 'artist_id': '12345', 'artist_name': 'Ada',
    'artist_url': 'https://soundcloud.com/ada', 'member_id': 'm_1', 'interval': 21600.0,
    'last_checked': 1700000000.0, 'watermark': 1699000000.0, 'enabled': 1,
    'added': 1690000000.0, 'new_items': 2, 'mine': True,
}
PAUSED = {
    'id': 4, 'provider': 'apple', 'artist_id': '67890', 'artist_name': 'Grace',
    'artist_url': 'https://music.apple.com/artist/67890', 'member_id': '',
    'interval': 21600.0, 'last_checked': 0.0, 'watermark': 0.0, 'enabled': 0,
    'added': 1695000000.0, 'new_items': 0, 'mine': False,
}
SUBSCRIPTIONS = [SUBSCRIBED, PAUSED]

SNAPSHOT = {
    'playlists': [], 'logs': [], 'settings': {},
    'stats': {'tracks': 12, 'running': 1, 'total': 12},
    'current': {'track_id': 7, 'title': 'Track 7', 'status': 'downloading'},
    'subscriptions': SUBSCRIPTIONS, 'feed_new': 4,
}

RELEASE = {
    'watch_id': 3, 'provider': 'soundcloud', 'release_id': 'r1', 'title': 'First',
    'release_type': 'album', 'track_count': 3, 'release_date': '2026-01-02',
    'artwork': 'https://img.example/1',
    'source_url': 'https://soundcloud.com/ada/sets/first', 'fetched': 1700000000.0,
}
TRACK = {
    'watch_id': 3, 'provider': 'soundcloud', 'track_id': 't9', 'release_id': 'r1',
    'title': 'Song', 'artist': 'Ada', 'album': 'First', 'track_no': 2, 'duration': 181.5,
    'artwork': 'https://img.example/1', 'source_url': 'https://soundcloud.com/ada/song',
    'fetched': 1700000000.0,
}
RELEASE_PAGE = {'artist': 'Ada', 'provider': 'soundcloud', 'releases': [RELEASE],
                'tracks': [TRACK], 'track_total': 11, 'offset': 5, 'page': 2,
                'fetched': 1700000000.0}
ARTIST = {
    'name': 'Ada',
    'providers': [{'provider': 'soundcloud', 'artist_id': '12345',
                   'url': 'https://soundcloud.com/ada',
                   'rss': 'https://feeds.soundcloud.com/users/soundcloud:users:12345/sounds.rss'}],
    'subscribed': True, 'watches': [SUBSCRIBED],
    'feed': [{'id': 5, 'title': 'First', 'artist': 'Ada', 'release_type': 'album',
              'release_date': '2026-01-02',
              'source_url': 'https://soundcloud.com/ada/sets/first',
              'artwork': 'https://img.example/1', 'first_seen': 1699999999.0,
              'decision': 'new'}],
    'tracks': 12, 'downloaded': 9,
}


class Call:
    """One request the stub received, kept so a test can read the wire instead of a mock."""

    def __init__(self, method, path, query, headers, body):
        self.method, self.path, self.query = method, path, query
        self.headers, self.body = headers, body

    def params(self):
        return {key: values[0] for key, values in self.query.items()}


class Stub(BaseHTTPRequestHandler):
    """A canned server. Each test points `answer` at its own dispatcher."""

    requests = []
    answer = None

    def _record(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        headers = {name.lower(): value for name, value in self.headers.items()}
        length = int(headers.get('content-length') or 0)
        body = self.rfile.read(length) if length else b''
        Stub.requests.append(Call(method, parsed.path, query, headers, body))
        status, extra, payload = Stub.answer(method, parsed.path, query, headers, body)
        self.send_response(status)
        for name, value in extra.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def do_GET(self):
        self._record('GET')

    def do_POST(self):
        self._record('POST')

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
        self.refusal = None       # one (status, document) every request answers with
        self.raw = None           # one (status, headers, body) a test wants verbatim

    def stop(self):
        Stub.answer = None
        self.http.shutdown()
        self.thread.join(timeout=10)
        self.http.server_close()

    def answer(self, method, path, query, headers, body):
        return 404, {'Content-Type': 'application/json'}, b'{"error": "no such endpoint"}'

    def calls(self, method=None, path=None):
        return [call for call in Stub.requests
                if (method is None or call.method == method)
                and (path is None or call.path == path)]

    def json(self, document_, status=200):
        return status, {'Content-Type': 'application/json'}, json.dumps(document_).encode()

    def client(self, **repo_flags):
        return repo.RepoClient(self.base,
                               capabilities.Capabilities.from_document(document(**repo_flags)),
                               social.Session.from_cookie(COOKIE))


class Wire(StubServer):
    """A server that advertises the Repo domain: the five versioned reads, nothing else."""

    def setUp(self):
        super().setUp()
        self.subject = self.client(repo=True)

    def answer(self, method, path, query, headers, body):
        if self.raw is not None:
            return self.raw
        if self.refusal is not None:
            return self.json(self.refusal[1], self.refusal[0])
        if method == 'GET' and path == repo.SNAPSHOT_PATH:
            return self.json(envelope('repo.snapshot', SNAPSHOT))
        if method == 'GET' and path == repo.RELEASES_PATH:
            return self.json(envelope('repo.releases', RELEASE_PAGE))
        if method == 'GET' and path == repo.TRACKS_PATH:
            return self.json(envelope('repo.tracks', [TRACK]))
        if method == 'GET' and path == repo.ARTIST_PATH:
            return self.json(envelope('repo.artist', ARTIST))
        if method == 'GET' and path == repo.SUBSCRIPTIONS_PATH:
            return self.json(envelope('repo.subscriptions', SUBSCRIPTIONS))
        return super().answer(method, path, query, headers, body)


class Refusals(StubServer):
    """What a server that names no `repo` domain must hear: a name and a reason, not a shrug."""

    def entry_points(self, client):
        return {
            'snapshot': lambda: client.snapshot(),
            'releases': lambda: client.releases(3),
            'tracks': lambda: client.tracks('https://soundcloud.com/ada/sets/first'),
            'artist': lambda: client.artist('Ada'),
            'subscriptions': lambda: client.subscriptions(),
        }

    def test_every_entry_point_refuses_by_name_when_repo_is_absent(self):
        client = self.client()                 # the document `server.py` publishes today
        for name, call in self.entry_points(client).items():
            with self.subTest(entry=name):
                with self.assertRaises(repo.Unavailable) as caught:
                    call()
                self.assertEqual(caught.exception.needed, 'repo')
                self.assertIn('unavailable', str(caught.exception))
                self.assertIn('repo', str(caught.exception))
        self.assertEqual(self.calls(), [])     # the refusal never reached the wire

    def test_the_reason_quotes_what_the_document_does_name(self):
        # The server advertises `discovery` (search, sonic_analysis) and no repo domain, so the
        # refusal says exactly that rather than pretending acquisition was there.
        with self.assertRaises(repo.Unavailable) as caught:
            self.client().snapshot()
        message = str(caught.exception)
        self.assertIn('discovery', message)
        self.assertIn('search', message)
        self.assertIn('sonic_analysis', message)
        self.assertIn('not advertise repo', message)
        self.assertEqual(self.calls(), [])

    def test_a_refusal_is_not_an_empty_answer(self):
        client = self.client()
        with self.assertRaises(repo.Unavailable):
            client.subscriptions()
        self.assertEqual(self.calls(), [])

    def test_a_degraded_document_refuses_and_keeps_the_probe_reason(self):
        client = repo.RepoClient(self.base, capabilities.Capabilities.assume_core('HTTP 500'),
                                 social.Session.from_cookie(COOKIE))
        with self.assertRaises(repo.Unavailable) as caught:
            client.artist('Ada')
        self.assertIn('HTTP 500', str(caught.exception))
        self.assertEqual(self.calls(), [])

    def test_a_raw_capability_document_is_accepted(self):
        client = repo.RepoClient(self.base, {'features': {'repo': {'snapshot': True}}})
        self.assertTrue(client.capabilities.feature('repo').available)

    def test_an_advertised_domain_is_what_lets_a_call_out(self):
        client = self.client(repo=True)
        with self.assertRaises(repo.NotFound):      # the stub has no route: the call went out
            client.snapshot()
        self.assertEqual(len(self.calls('GET', repo.SNAPSHOT_PATH)), 1)


class Reads(Wire):

    def test_the_snapshot_carries_follows_queue_numbers_and_the_feed_counter(self):
        found = self.subject.snapshot()
        self.assertEqual([row.id for row in found.subscriptions], [3, 4])
        self.assertEqual(found.subscriptions[0].artist_name, 'Ada')
        self.assertTrue(found.subscriptions[0].mine)
        self.assertTrue(found.subscriptions[0].enabled)
        self.assertFalse(found.subscriptions[1].enabled)
        self.assertFalse(found.subscriptions[1].mine)
        self.assertEqual(found.subscriptions[1].new_items, 0)
        self.assertEqual(found.stats['running'], 1)
        self.assertEqual(found.current['status'], 'downloading')
        self.assertEqual(found.feed_new, 4)
        call = self.calls('GET', repo.SNAPSHOT_PATH)[0]
        self.assertEqual(call.query, {})
        self.assertEqual(call.method, 'GET')

    def test_a_release_page_carries_releases_a_song_page_and_the_counts(self):
        page = self.subject.releases(3, offset=5, limit=2)
        self.assertEqual((page.artist, page.provider), ('Ada', 'soundcloud'))
        self.assertEqual(page.track_total, 11)
        self.assertEqual((page.offset, page.page), (5, 2))
        self.assertEqual(page.fetched, 1700000000.0)
        release = page.releases[0]
        self.assertEqual((release.release_id, release.title), ('r1', 'First'))
        self.assertEqual(release.track_count, 3)
        self.assertEqual(release.release_date, '2026-01-02')
        self.assertEqual(release.source_url, 'https://soundcloud.com/ada/sets/first')
        track = page.tracks[0]
        self.assertEqual((track.track_id, track.album, track.track_no), ('t9', 'First', 2))
        self.assertEqual(track.duration, 181.5)
        params = self.calls('GET', repo.RELEASES_PATH)[0].params()
        self.assertEqual((params['watch'], params['offset'], params['limit']), ('3', '5', '2'))

    def test_the_songs_of_one_album_are_typed_rows(self):
        rows = self.subject.tracks('https://soundcloud.com/ada/sets/first')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, 'Song')
        self.assertEqual(rows[0].artist, 'Ada')
        self.assertEqual(rows[0].release_id, 'r1')
        params = self.calls('GET', repo.TRACKS_PATH)[0].params()
        self.assertEqual(params['url'], 'https://soundcloud.com/ada/sets/first')

    def test_an_artist_page_carries_providers_follows_feed_and_counts(self):
        page = self.subject.artist('Ada')
        self.assertEqual(page.name, 'Ada')
        self.assertTrue(page.subscribed)
        self.assertEqual((page.tracks, page.downloaded), (12, 9))
        self.assertEqual(page.providers[0].provider, 'soundcloud')
        self.assertEqual(page.providers[0].artist_id, '12345')
        self.assertIn('soundcloud:users:12345', page.providers[0].rss)
        self.assertEqual(page.watches[0].artist_url, 'https://soundcloud.com/ada')
        self.assertEqual(page.feed[0].decision, 'new')
        self.assertEqual(page.feed[0].first_seen, 1699999999.0)
        self.assertEqual(self.calls('GET', repo.ARTIST_PATH)[0].params()['name'], 'Ada')

    def test_subscriptions_carry_the_servers_ownership_flag(self):
        rows = self.subject.subscriptions()
        self.assertEqual([row.artist_name for row in rows], ['Ada', 'Grace'])
        self.assertEqual([row.mine for row in rows], [True, False])
        self.assertEqual(rows[1].provider, 'apple')
        self.assertEqual(rows[0].last_checked, 1700000000.0)


class Errors(Wire):

    def test_an_error_envelope_becomes_its_typed_refusal(self):
        self.refusal = (404, {'error': 'no such subscription'})
        with self.assertRaises(repo.NotFound) as caught:
            self.subject.releases(9)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.message, 'no such subscription')
        self.assertIsInstance(caught.exception, repo.Denied)
        self.assertIn('404', str(caught.exception))

    def test_a_missing_session_is_named(self):
        self.refusal = (401, {'error': 'Sign in required'})
        with self.assertRaises(repo.SignInRequired):
            self.subject.snapshot()

    def test_a_spent_bucket_carries_its_retry_after(self):
        self.refusal = (429, {'error': 'Rate limit exceeded', 'retry_after': 42})
        with self.assertRaises(repo.RateLimited) as caught:
            self.subject.artist('Ada')
        self.assertEqual(caught.exception.retry_after, 42)

    def test_a_callers_mistake_keeps_the_servers_sentence(self):
        # `api/v1.py` answers an unknown follow with a 400 and its own words.
        self.refusal = (400, {'error': 'no such follow'})
        with self.assertRaises(repo.Denied) as caught:
            self.subject.releases(9)
        self.assertNotIsInstance(caught.exception, (repo.NotFound, repo.SignInRequired))
        self.assertEqual(caught.exception.message, 'no such follow')

    def test_a_status_without_an_envelope_is_a_client_error(self):
        self.raw = (500, {'Content-Type': 'text/html'}, b'<h1>boom</h1>')
        with self.assertRaises(repo.ClientError) as caught:
            self.subject.snapshot()
        self.assertIn('500', str(caught.exception))

    def test_an_answer_that_is_not_a_v1_envelope_is_a_client_error(self):
        self.refusal = (200, {'subscriptions': SUBSCRIPTIONS})
        with self.assertRaises(repo.ClientError) as caught:
            self.subject.subscriptions()
        self.assertIn('v1 envelope', str(caught.exception))

    def test_a_non_json_answer_is_a_client_error(self):
        self.raw = (200, {'Content-Type': 'text/html'}, b'<html>proxy</html>')
        with self.assertRaises(repo.ClientError):
            self.subject.artist('Ada')


class CredentialsStayOutOfMessages(Wire):

    def test_a_refusal_never_carries_the_session_cookie(self):
        self.refusal = (403, {'error': 'Sign in required'})
        with self.assertRaises(repo.Denied) as caught:
            self.subject.snapshot()
        self.assertNotIn(COOKIE, str(caught.exception))
        self.assertNotIn(COOKIE, repr(caught.exception))
        self.assertNotIn(COOKIE, repr(self.subject))

    def test_a_wire_failure_never_carries_the_session_cookie(self):
        dead = repo.RepoClient('http://127.0.0.1:1',
                               capabilities.Capabilities.from_document(document(repo=True)),
                               social.Session.from_cookie(COOKIE))
        with self.assertRaises(repo.ClientError) as caught:
            dead.snapshot()
        self.assertNotIn(COOKIE, str(caught.exception))
        self.assertNotIn(COOKIE, repr(caught.exception))

    def test_the_session_travels_in_a_header_and_never_in_the_url(self):
        self.subject.snapshot()
        call = self.calls('GET', repo.SNAPSHOT_PATH)[0]
        self.assertEqual(call.headers['cookie'],
                         f'{social.SESSION_COOKIE}={COOKIE}')
        self.assertNotIn(COOKIE, call.path)
        self.assertNotIn(COOKIE, json.dumps(call.query))


if __name__ == '__main__':
    unittest.main()