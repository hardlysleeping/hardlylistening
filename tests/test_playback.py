"""AutoDJ's per-member flag and the loudness curve use only listening fixtures."""
from test_listening import ListeningHTTP


class PlaybackHTTP(ListeningHTTP):
    def test_autodj_flag_follows_the_member(self):
        self.assertEqual(self.api('playback'), (200, {'playback': {'autodj': False}}))
        self.assertEqual(self.api('playback', {'autodj': True}),
                         (200, {'playback': {'autodj': True}}))
        self.assertEqual(self.api('playback')[1]['playback']['autodj'], True)
        # The preferences snapshot is what a fresh device boots from.
        self.assertEqual(self.api('preferences')[1]['playback'], {'autodj': True})
        # Per member: a second member keeps their own setting.
        self.identity = dict(self.identity, id='second')
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO members(id,email,role) VALUES('second','','member')")
            self.store.conn.commit()
        self.assertEqual(self.api('playback')[1]['playback']['autodj'], False)
        self.assertEqual(self.api('playback', {'autodj': True})[0], 200)
        self.identity = dict(self.identity, id='u1')
        self.assertEqual(self.api('playback')[1]['playback']['autodj'], True)
        self.assertEqual(self.store.conn.execute(
            'SELECT count(*) FROM listening_playback').fetchone()[0], 2)

    def test_playback_rejects_bad_bodies_and_auth(self):
        self.assertEqual(self.api('playback', {})[0], 400)
        self.assertEqual(self.api('playback', {'autodj': 'yes'})[0], 400)
        self.assertEqual(self.api('playback', {'autodj': 1})[0], 400)
        self.assertEqual(self.api('playback', [], headers={})[0], 401)
        self.assertEqual(self.api('playback', {'autodj': True}, headers={})[0], 401)
        self.assertEqual(self.api('playback', {'autodj': True},
                                  headers={'Authorization': 'Bearer fixture'})[0], 403)
        self.assertEqual(self.api('playback')[1]['playback']['autodj'], False)


class LoudnessHTTP(ListeningHTTP):
    def eq(self, data=None, headers=None):
        return self.api('eq', data, headers)

    def test_loudness_persists_alongside_the_bands(self):
        self.assertEqual(self.eq()[1]['eq'],
                         {'enabled': False, 'gains': [0, 0, 0, 0, 0, 0],
                          'loudness': False})
        code, body = self.eq({'enabled': True, 'gains': [3, 0, 0, -2, 0, 1],
                              'loudness': True})
        self.assertEqual(code, 200)
        self.assertEqual(body['eq']['loudness'], True)
        self.assertEqual(self.eq()[1]['eq']['loudness'], True)
        self.assertEqual(self.api('preferences')[1]['eq']['loudness'], True)
        # The server stores what the client sends: all-zero gains do not secretly switch the
        # equalizer off. The Flat button sends `enabled:false` itself, so a bypass left on
        # (which is what a guessed coercion broke) survives a visit to the flat curve.
        code, body = self.eq({'enabled': True, 'gains': [0, 0, 0, 0, 0, 0],
                              'loudness': True})
        self.assertEqual((code, body['eq']['enabled'], body['eq']['loudness']),
                         (200, True, True))
        self.assertEqual(self.eq({'enabled': False, 'gains': [0, 0, 0, 0, 0, 0],
                                  'loudness': True})[1]['eq']['enabled'], False)
        # Turning it off is remembered just as plainly.
        self.assertEqual(self.eq({'enabled': False, 'gains': [0, 0, 0, 0, 0, 0],
                                  'loudness': False})[1]['eq']['loudness'], False)
        self.assertEqual(self.eq()[1]['eq']['loudness'], False)

    def test_loudness_is_validated_and_per_member(self):
        self.assertEqual(self.eq({'enabled': True, 'gains': [0, 0, 0, 0, 0, 0],
                                  'loudness': 'on'})[0], 400)
        self.assertEqual(self.eq({'enabled': 'yes', 'gains': [0, 0, 0, 0, 0, 0],
                                  'loudness': True})[0], 400)
        self.assertEqual(self.eq({'enabled': True, 'gains': [0, 0], 'loudness': True})[0], 400)
        self.assertEqual(self.eq({'enabled': True, 'gains': [0, 0, 0, 0, 0, 13],
                                  'loudness': True})[0], 400)
        self.assertEqual(self.eq({'loudness': True})[1]['eq']['loudness'], True)
        self.identity = dict(self.identity, id='second')
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO members(id,email,role) VALUES('second','','member')")
            self.store.conn.commit()
        self.assertEqual(self.eq()[1]['eq']['loudness'], False)
        self.identity = dict(self.identity, id='u1')
        self.assertEqual(self.eq()[1]['eq']['loudness'], True)