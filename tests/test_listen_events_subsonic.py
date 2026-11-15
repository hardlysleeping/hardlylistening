"""Subsonic scrobbles in the dated history, over the same HTTP fixture as the rest.

Kept apart from `test_subsonic_extra.py` only because that suite is the general endpoint
contract; this is the stats' write path.
"""
import unittest

from test_listening import ListeningHTTP


class SubsonicDatedPlays(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save
    user = ListeningHTTP.user
    api = ListeningHTTP.api
    token = ListeningHTTP.token

    def setUp(self):
        ListeningHTTP.setUp(self)
        for track_id in self.ids:
            self.save('track', track_id)

    def dated(self):
        with self.store.lock:
            return [dict(r) for r in self.store.conn.execute(
                'SELECT member_id,track_id,source FROM listen_events ORDER BY id')]

    def test_a_finished_play_is_dated(self):
        self.assertEqual(self.response('scrobble', id=self.ids[0])['status'], 'ok')
        rows = self.dated()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['track_id'], rows[0]['source']), (self.ids[0], 'subsonic'))

    def test_a_now_playing_note_is_not_a_play(self):
        # submission=false is the client saying "this started", not "this played"; it still
        # counts in the lifetime totals, but it must not fill the history with half-plays.
        self.response('scrobble', id=self.ids[0], submission='false')
        self.assertEqual(self.dated(), [])
        self.response('scrobble', id=self.ids[1])
        self.assertEqual([r['track_id'] for r in self.dated()], [self.ids[1]])

    def test_a_double_report_from_one_client_is_one_play(self):
        self.response('scrobble', id=self.ids[0])
        self.response('scrobble', id=self.ids[0])
        self.assertEqual(len(self.dated()), 1)