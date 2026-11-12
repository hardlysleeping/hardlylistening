"""`getNowPlaying` reports the member's own last playback state, honestly.

The position is written by the web player into `listening_now` (one row per member), so this
surface only reports it: `minutesAgo` is the real age of that write, the player fields name
the device the request authenticated as, and a track the member no longer has in scope is
not described at all.
"""
import json
import time
import unittest
from urllib.parse import urlencode

from test_listening import ListeningHTTP


class SubsonicNowPlaying(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    token = ListeningHTTP.token
    user = ListeningHTTP.user
    api = ListeningHTTP.api
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save

    def play(self, track_id=None, position=12.5, headers=None):
        track_id = track_id if track_id is not None else self.ids[0]
        code, body = self.api('now', {'id': track_id, 'queue': [track_id], 'time': position}, headers)
        self.assertEqual(code, 200, body)
        return body

    def entries(self, **params):
        body = self.response('getNowPlaying', **params)
        self.assertEqual(body['status'], 'ok')
        return body['nowPlaying']['entry']

    def test_nothing_reported_before_anything_plays(self):
        self.assertEqual(self.entries(), [])

    def test_a_played_song_is_reported_with_its_song_fields(self):
        self.save('track', self.ids[0])
        self.play(self.ids[0])
        entry, = self.entries()
        self.assertEqual(entry['id'], str(self.ids[0]))
        self.assertEqual(entry['title'], 'Song 0')
        self.assertEqual(entry['artist'], 'Artist 0')
        self.assertEqual(entry['suffix'], 'wav')
        self.assertEqual(entry['type'], 'music')
        # The player fields name this device, which is this app's Subsonic user.
        self.assertEqual(entry['username'], self.device['username'])
        self.assertEqual(entry['playerId'], self.device['username'])
        self.assertEqual(entry['playerName'], 'Phone')
        self.assertEqual(entry['minutesAgo'], 0)
        # A now-playing entry carries no position; that belongs to getPlayQueue.
        self.assertNotIn('position', entry)

    def test_minutes_ago_is_the_age_of_the_last_write(self):
        self.save('track', self.ids[0])
        self.play(self.ids[0])
        self.store.conn.execute('UPDATE listening_now SET updated=? WHERE member_id=?',
                                (time.time() - 5 * 60 - 30, 'u1'))
        self.store.conn.commit()
        self.assertEqual(self.entries()[0]['minutesAgo'], 5)

    def test_a_track_outside_the_members_scope_is_not_reported(self):
        # The state survives unsaving, but describing a track the scope refuses would be a lie.
        self.save('track', self.ids[0])
        self.play(self.ids[0])
        self.save('track', self.ids[0], False)
        self.assertEqual(self.entries(), [])

    def test_another_members_state_is_invisible(self):
        self.store.conn.execute("INSERT OR IGNORE INTO members(id,email,role) VALUES('u2','two@example.com','member')")
        self.store.conn.commit()
        other_headers = {'Cookie': self.token('u2'), 'Origin': 'https://music.example'}
        other = self.api('devices', {'label': 'Other phone'}, other_headers)[1]
        self.save('track', self.ids[0])
        self.play(self.ids[0])
        # The other member owns a different song and their own position.
        self.assertEqual(self.api('saves', {'kind': 'track', 'id': self.ids[1], 'saved': True},
                                  other_headers)[0], 200)
        self.play(self.ids[1], headers=other_headers)
        self.assertEqual([e['id'] for e in self.entries()], [str(self.ids[0])])
        self.assertEqual([e['id'] for e in self.entries(device=other)], [str(self.ids[1])])

    def test_the_xml_form_carries_the_same_entry(self):
        self.save('track', self.ids[2])
        self.play(self.ids[2])
        query = urlencode(dict(u=self.device['username'], p=self.device['password'],
                               f='xml', v='1.16.1', c='fixture'))
        _, _, raw = self.request('/rest/getNowPlaying.view?' + query)
        self.assertIn(b'<nowPlaying>', raw)
        self.assertIn(b'id="%d"' % self.ids[2], raw)
        self.assertIn(b'playerName="Phone"', raw)
        self.assertIn(b'username="%s"' % self.device['username'].encode(), raw)
        self.assertIn(b'minutesAgo="0"', raw)
        self.assertIn(b'<entry', raw)

    def test_the_empty_state_is_an_empty_now_playing_not_an_error(self):
        query = urlencode(dict(u=self.device['username'], p=self.device['password'],
                               f='json', v='1.16.1', c='fixture'))
        _, _, raw = self.request('/rest/getNowPlaying.view?' + query)
        body = json.loads(raw)['subsonic-response']
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['nowPlaying']['entry'], [])
        query = urlencode(dict(u=self.device['username'], p=self.device['password'],
                               f='xml', v='1.16.1', c='fixture'))
        _, _, raw = self.request('/rest/getNowPlaying.view?' + query)
        self.assertIn(b'<nowPlaying', raw)
        self.assertNotIn(b'<entry', raw)
        self.assertNotIn(b'code=', raw)


if __name__ == '__main__':
    unittest.main()