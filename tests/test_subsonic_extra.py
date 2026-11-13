"""Phase 0 of the Subsonic surface: the endpoints that map onto existing app data.

Runs over the same isolated HTTP fixture as `test_listening` — real requests, generated WAV
bytes, a temporary database, no live boot and no audible playback.
"""
import json
import unittest

from test_listening import ListeningHTTP


class SubsonicSurface(unittest.TestCase):
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
        for track_id in self.ids:
            self.save('track', track_id)

    def test_song_payload_is_complete_and_never_leaks_paths(self):
        song = self.response('getSong', id=self.ids[0])['song']
        for field in ('suffix', 'contentType', 'size', 'created', 'coverArt', 'albumArtist', 'genre', 'bpm'):
            self.assertIn(field, song)
        self.assertEqual(song['suffix'], 'wav')
        self.assertEqual(song['contentType'], 'audio/wav')
        raw = self.rest('getSong', id=self.ids[0])[2]
        self.assertNotIn(b'secret.example', raw)
        self.assertNotIn(str(self.media and 'fixture.wav').encode(), raw)

    def test_cover_art_redirects_once_known_and_misses_unknown_ids(self):
        code, headers, raw = self.rest('getCoverArt', id=self.ids[0])
        self.assertEqual(self.response('getCoverArt', id='http://127.0.0.1')['error']['code'], 70)
        # A known track with no artwork still answers an image, never an error.
        code, headers, raw = self.rest('getCoverArt', id=self.ids[0])
        self.assertEqual(code, 200)
        self.assertTrue(headers.get('Content-Type', '').startswith('image/'))

    def test_indexes_and_music_directory(self):
        artists = self.response('getIndexes')['indexes']['index'][0]['artist']
        self.assertTrue(artists)
        album_id = artists[0]['id']
        albums = self.response('getMusicDirectory', id=album_id)['directory']['child']
        self.assertTrue(albums)
        songs = self.response('getMusicDirectory', id=albums[0]['id'])['directory']['child']
        self.assertTrue(songs)
        self.assertEqual(songs[0]['isDir'], False)

    def test_star_rating_and_starred_lists(self):
        self.assertEqual(self.response('star', id=self.ids[0])['status'], 'ok')
        starred = self.response('getStarred2')['getStarred2']
        self.assertIn(str(self.ids[0]), [s['id'] for s in starred['song']])
        self.assertEqual(self.response('setRating', id=self.ids[0], rating=4)['status'], 'ok')
        self.assertEqual(self.response('getSong', id=self.ids[0])['song']['userRating'], 4)
        self.response('unstar', id=self.ids[0])
        starred = self.response('getStarred2')['getStarred2']
        self.assertNotIn(str(self.ids[0]), [s['id'] for s in starred['song']])

    def test_scrobble_counts_and_top_songs(self):
        for _ in range(3):
            self.assertEqual(self.response('scrobble', id=self.ids[0])['status'], 'ok')
        top = self.response('getTopSongs', artist='Artist 0')['topSongs']['song']
        self.assertEqual(top[0]['id'], str(self.ids[0]))

    def test_play_queue_round_trip(self):
        query = '/rest/savePlayQueue.view?' + '&'.join([
            'u=%s' % self.device['username'], 'p=%s' % self.device['password'], 'f=json',
            'id=%d' % self.ids[0], 'id=%d' % self.ids[1], 'current=%d' % self.ids[1], 'position=1500'])
        code, _, raw = self.request(query)
        self.assertEqual(json.loads(raw)['subsonic-response']['status'], 'ok')
        queue = self.response('getPlayQueue')['playQueue']
        self.assertEqual([entry['id'] for entry in queue['entry']], [str(self.ids[0]), str(self.ids[1])])
        self.assertEqual(queue['current'], str(self.ids[1]))
        self.assertEqual(queue['position'], 1500)

    def test_legacy_search_and_album_lists(self):
        for endpoint, key in (('search2', 'searchResult2'), ('search', 'searchResult')):
            payload = self.response(endpoint, query='Song 1')[key]
            self.assertIn('song', payload)
        newest = self.response('getAlbumList2', type='newest')['albumList2']['album']
        self.assertTrue(newest)
        random_rows = self.response('getAlbumList2', type='random')['albumList2']['album']
        self.assertEqual(len(random_rows), len(newest))

    def test_plumbing_endpoints_answer(self):
        self.assertTrue(self.response('getOpenSubsonicExtensions')['openSubsonicExtensions'])
        self.assertFalse(self.response('getScanStatus')['scanStatus']['scanning'])
        self.assertEqual(self.response('getUser')['user']['username'], self.device['username'])
        self.assertEqual(self.response('getLyrics', title='Song 0', artist='Artist 0')['status'], 'ok')

    def test_unimplemented_endpoints_say_so(self):
        # `downloadPodcastEpisode` left this list with HL-209: it stores the episode under
        # the member's cap instead of refusing, so it is covered by its own suite.
        for endpoint in ('jukeboxControl', 'addChatMessage'):
            response = self.response(endpoint)
            self.assertEqual(response['status'], 'failed')
            self.assertEqual(response['error']['code'], 30)


if __name__ == '__main__':
    unittest.main()
