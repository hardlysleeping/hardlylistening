"""`/api/v1` is a contract, not a second implementation.

Two things can go wrong with a versioned surface and nothing else will notice. It can stop
delegating — a route grows its own query, and the day the service changes the version contract
quietly answers something else. Or it can stop being the stable thing it promises: an unknown
path falls through to the static handler, a wrong method answers 404, or auth stops matching
the unversioned routes it sits beside.

So each route is pinned twice: it answers the documented envelope, and the service function it
names was the one that ran. The unversioned surface is not tested here — it has its own suites
and this module must not disturb it.
"""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from library import service
import test_security as harness


class ApiV1(unittest.TestCase):
    # The HTTP fixture is a test case in its own right: bind the plumbing without inheriting
    # (and re-running) its tests.
    setUp = harness.SecurityHTTP.setUp
    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request
    token = harness.SecurityHTTP.token
    user = harness.SecurityHTTP.user

    def setUp(self):
        harness.SecurityHTTP.setUp(self)
        self.identity = self.user('admin')
        self.headers = {'Cookie': self.token(), 'Origin': 'https://music.example'}

    def get(self, path):
        code, _, raw = self.request(path, headers=self.headers)
        return code, json.loads(raw)

    def post(self, path, body):
        code, _, raw = self.request(path, 'POST', self.headers, json.dumps(body))
        return code, json.loads(raw)

    def assertEnvelope(self, body, resource, data):
        self.assertEqual(body['api'], 'v1')
        self.assertEqual(body['resource'], resource)
        self.assertEqual(body['data'], data)


    def test_repo_routes_call_the_repo_service(self):
        from repo import service as repo_service
        asked = []

        def record(name, answer):
            def wrapper(*args, **kwargs):
                asked.append(name)
                return answer
            return wrapper

        answers = {
            'snapshot': {'current': None},
            'subscriptions': [{'id': 4, 'mine': True}],
            'releases': {'artist': 'A', 'releases': [], 'tracks': [], 'track_total': 0},
            'tracks': [{'id': 7}],
            'artist': {'name': 'A', 'releases': []},
        }
        patches = [patch.object(repo_service, name, record(name, answer))
                   for name, answer in answers.items()]
        for p in patches:
            p.start()
        try:
            code, body = self.get('/api/v1/repo/snapshot')
            self.assertEqual(code, 200)
            self.assertEqual(body['resource'], 'repo.snapshot')
            # The snapshot is decorated with the same ownership rule /api/state applies.
            self.assertEqual(body['data']['subscriptions'], answers['subscriptions'])

            code, body = self.get('/api/v1/repo/subscriptions')
            self.assertEnvelope(body, 'repo.subscriptions', answers['subscriptions'])
            self.assertEqual(body['count'], 1)

            code, body = self.get('/api/v1/repo/releases?watch=4&offset=0&limit=20')
            self.assertEnvelope(body, 'repo.releases', answers['releases'])

            code, body = self.get('/api/v1/repo/tracks?url=https://example.test/album')
            self.assertEnvelope(body, 'repo.tracks', answers['tracks'])
            self.assertEqual(body['count'], 1)

            code, body = self.get('/api/v1/repo/artist?name=A')
            self.assertEnvelope(body, 'repo.artist', answers['artist'])
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(sorted(set(asked)), ['artist', 'releases', 'snapshot', 'subscriptions',
                                              'tracks'])

    def test_repo_arguments_are_validated_before_the_service_is_asked(self):
        from repo import service as repo_service
        with patch.object(repo_service, 'releases') as releases:
            code, body = self.get('/api/v1/repo/releases?watch=0')
            self.assertEqual(code, 400)
            code, body = self.get('/api/v1/repo/releases?watch=4&limit=500')
            self.assertEqual(code, 400)
            releases.assert_not_called()
        code, body = self.get('/api/v1/repo/artist')
        self.assertEqual(code, 400)
        code, body = self.get('/api/v1/repo/tracks')
        self.assertEqual(code, 400)

    # -- Library ---------------------------------------------------------------------------------

    def test_library_routes_call_the_library_service(self):
        asked = []

        def record(name, answer):
            def wrapper(*args, **kwargs):
                asked.append(name)
                return answer
            return wrapper

        answers = {
            'catalog': [{'id': 7}],
            'albums': [{'artist': 'A', 'album': 'B', 'song_count': 1}],
            'artists': [{'name': 'A', 'album_count': 1, 'song_count': 1}],
            'playlists': [{'id': 3, 'title': 'Mix'}],
            'playlist': {'id': 3, 'title': 'Mix', 'tracks': [{'id': 7}]},
            'favorites': [{'id': 7}],
            'bookmark_rows': [{'track_id': 7, 'position_ms': 1000}],
        }
        routes = {
            '/api/v1/library/songs': ('catalog', 'library.songs'),
            '/api/v1/library/albums': ('albums', 'library.albums'),
            '/api/v1/library/artists': ('artists', 'library.artists'),
            '/api/v1/library/playlists': ('playlists', 'library.playlists'),
            '/api/v1/library/favorites': ('favorites', 'library.favorites'),
            '/api/v1/library/bookmarks': ('bookmark_rows', 'library.bookmarks'),
        }
        patches = [patch.object(service, name, record(name, answer))
                   for name, answer in answers.items()]
        for p in patches:
            p.start()
        try:
            for path, (name, resource) in routes.items():
                code, body = self.get(path)
                self.assertEqual(code, 200, path)
                self.assertEnvelope(body, resource, answers[name])
                self.assertEqual(body['count'], len(answers[name]))
            code, body = self.get('/api/v1/library/playlist/3')
            self.assertEnvelope(body, 'library.playlist', answers['playlist'])
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(sorted(set(asked)), sorted(answers))

    def test_a_playlist_the_member_never_saved_is_a_404(self):
        with patch.object(service, 'playlist', return_value=None):
            code, body = self.get('/api/v1/library/playlist/9')
        self.assertEqual(code, 404)
        self.assertEqual(body, {'error': 'No such saved playlist'})

    def test_the_library_answers_the_members_own_rows(self):
        """Not a mock: the new albums/artists/playlist projections read the real scope."""
        tid = self.store.track('local', 'one', 'Song', artist_name='A', album='B')
        service.star(self.store, 'u1', tid, True)
        service.save_bookmark(self.store, 'u1', tid, 1234, 'intro')
        playlist_id = self.store.playlist('Set', 'local', '')
        self.store.add_to_playlist(playlist_id, tid, 0)
        import listening, preferences
        listening.schema(self.store, self.sec)
        preferences.save(self.store, 'u1', 'playlist', playlist_id)

        code, body = self.get('/api/v1/library/songs')
        self.assertEqual([t['id'] for t in body['data']], [tid])
        code, body = self.get('/api/v1/library/albums')
        self.assertEqual([(a['artist'], a['album'], a['song_count']) for a in body['data']],
                         [('A', 'B', 1)])
        code, body = self.get('/api/v1/library/artists')
        self.assertEqual([(a['name'], a['album_count']) for a in body['data']], [('A', 1)])
        code, body = self.get('/api/v1/library/favorites')
        self.assertEqual([t['id'] for t in body['data']], [tid])
        code, body = self.get('/api/v1/library/bookmarks')
        self.assertEqual([(b['track_id'], b['position_ms']) for b in body['data']], [(tid, 1234)])
        code, body = self.get('/api/v1/library/playlist/%d' % playlist_id)
        self.assertEqual((body['data']['title'], [t['id'] for t in body['data']['tracks']]),
                         ('Set', [tid]))

    # -- Playback --------------------------------------------------------------------------------

    def test_playback_routes_call_the_playback_helpers(self):
        import nowplaying
        from library import playback
        state = {'id': 7, 'queue': [7, 8], 'time': 12.5, 'index': 0, 'updated': 100.0}
        asked = []

        def record(name, answer):
            def wrapper(*args, **kwargs):
                asked.append(name)
                return answer
            return wrapper

        with patch.object(nowplaying, 'get', record('get', state)), \
                patch.object(nowplaying, 'put', record('put', state)), \
                patch.object(playback, 'available', record('available', True)):
            code, body = self.get('/api/v1/playback/state')
            self.assertEnvelope(body, 'playback.state', state)
            code, body = self.get('/api/v1/playback/queue')
            self.assertEqual(body['data']['track_ids'], [7, 8])
            self.assertEqual((body['data']['current'], body['data']['index']), (7, 0))
            code, body = self.get('/api/v1/playback/now')
            self.assertEqual(body['data']['track_id'], 7)
            self.assertEqual(body['data']['position'], 12.5)
            code, body = self.get('/api/v1/playback/stream/7')
            self.assertEqual(code, 200)
            self.assertEqual(body['data'], {'track_id': 7, 'url': '/api/track/7/audio',
                                            'available': True})
            code, body = self.post('/api/v1/playback/state', state)
            self.assertEnvelope(body, 'playback.state', state)
        self.assertEqual(sorted(set(asked)), ['available', 'get', 'put'])

    def test_saving_playback_state_needs_the_same_origin_a_post_always_needs(self):
        import nowplaying
        with patch.object(nowplaying, 'put') as put:
            code, _, raw = self.request('/api/v1/playback/state', 'POST',
                                        {'Cookie': self.headers['Cookie'],
                                         'Origin': 'https://evil.example'},
                                        json.dumps({'queue': []}))
        self.assertEqual(code, 403)
        put.assert_not_called()

    # -- Social ----------------------------------------------------------------------------------

    def test_an_unknown_v1_path_is_a_clean_404(self):
        code, body = self.get('/api/v1/nope')
        self.assertEqual(code, 404)
        self.assertEqual(body, {'error': 'Unknown /api/v1 endpoint'})

    def test_local_mode_serves_shared_reads_without_a_session(self):
        """Auth off means no member, not an invented one: the shared pool stays readable."""
        from repo import service as repo_service
        with patch.dict(os.environ, {'HARDLYLISTENING_AUTH_REQUIRED': '0'}), \
                patch.object(repo_service, 'subscriptions', return_value=[]):
            code, _, raw = self.request('/api/v1/repo/subscriptions')
        self.assertEqual(code, 200)
        body = json.loads(raw)
        self.assertEqual((body['data'], body['count']), ([], 0))

    def test_a_wrong_method_says_what_is_allowed(self):
        code, body = self.post('/api/v1/library/songs', {})
        self.assertEqual(code, 405)
        self.assertIn('GET', body['error'])

    def test_a_session_is_required_when_accounts_are_on(self):
        for path in ('/api/v1/repo/subscriptions', '/api/v1/library/songs',
                     '/api/v1/playback/state'):
            code, _, _ = self.request(path)
            self.assertEqual(code, 401, path)


if __name__ == '__main__':
    unittest.main()