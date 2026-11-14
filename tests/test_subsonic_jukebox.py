"""HL-204: Subsonic `jukeboxControl` maps onto the party deck.

The room already had one leased player (`party.control` claim/heartbeat/next/release). This is the
protocol side: `jukeboxControl` steers that room — the playlist is the party queue in the order a
client asked for, votes still outrank it, and the status reports the one deck that is actually
playing. A member reaches only their own room; with no room the answer is error 30, never a new
party invented for the client.
"""
import json
import unittest
from urllib.parse import urlencode

import party
from test_listening import ListeningHTTP


class JukeboxControl(unittest.TestCase):
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
        party.schema(self.store)
        # A Subsonic member sees only what they saved, so the playlist has to be in their scope.
        for track_id in self.ids:
            self.save('track', track_id)
        self.member = {'id': self.identity['id'], 'role': 'admin'}

    def room(self):
        return party.create(self.store, self.member, 'Jukebox')['id']

    def control(self, **params):
        """The harness's `rest` does not repeat keys, and `id` is repeated in this protocol."""
        args = [('u', self.device['username']), ('p', self.device['password']),
                ('f', 'json'), ('v', '1.16.1'), ('c', 'fixture')]
        for key, value in params.items():
            for item in (value if isinstance(value, (list, tuple)) else [value]):
                args.append((key, str(item)))
        code, _, raw = self.request('/rest/jukeboxControl.view?' + urlencode(args))
        return json.loads(raw)['subsonic-response']

    def status(self):
        return self.control(action='status')['jukeboxStatus']

    def playlist(self):
        return self.control(action='get')['jukeboxPlaylist']

    def test_no_room_is_error_30(self):
        response = self.control(action='status')
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['code'], 30)

    def test_status_and_playlist_describe_the_deck(self):
        self.room()
        status = self.status()
        self.assertEqual(status, {'currentIndex': -1, 'playing': False, 'gain': 1.0, 'position': 0})
        playlist = self.playlist()
        self.assertEqual(playlist['entry'], [])
        self.assertEqual(playlist['currentIndex'], -1)

    def test_set_add_remove_and_clear_shape_the_playlist(self):
        self.room()
        self.control(action='set', id=[self.ids[2], self.ids[0]])
        entries = self.playlist()['entry']
        self.assertEqual([int(e['id']) for e in entries], [self.ids[2], self.ids[0]])
        self.assertTrue(entries[0]['title'])                    # full song objects, not bare ids

        self.control(action='add', id=[self.ids[1]])
        self.assertEqual([int(e['id']) for e in self.playlist()['entry']],
                         [self.ids[2], self.ids[0], self.ids[1]])

        self.control(action='remove', index=1)
        self.assertEqual([int(e['id']) for e in self.playlist()['entry']],
                         [self.ids[2], self.ids[1]])

        self.control(action='clear')
        self.assertEqual(self.playlist()['entry'], [])

    def test_the_playing_track_leads_the_playlist(self):
        pid = self.room()
        self.control(action='set', id=self.ids[:2])
        token = party.control(self.store, self.member, pid, 'claim')['host_token']
        party.control(self.store, self.member, pid, 'next', host_token=token, expected=None)
        playlist = self.playlist()
        self.assertEqual(playlist['currentIndex'], 0)
        self.assertEqual([int(e['id']) for e in playlist['entry']], self.ids[:2])

    def test_a_vote_still_reorders_the_playlist(self):
        pid = self.room()
        self.control(action='set', id=[self.ids[1], self.ids[2]])
        with self.store.lock:
            entries = [row[0] for row in self.store.conn.execute(
                "SELECT id FROM party_entries WHERE party=? AND status='waiting' ORDER BY position",
                (pid,))]
        # The client asked for 2 then 3; a vote for 3 puts it in front.
        party.vote(self.store, pid, 'm:' + self.member['id'], entries[1], 1)
        self.assertEqual([int(e['id']) for e in self.playlist()['entry']],
                         [self.ids[2], self.ids[1]])

    def test_start_requires_a_leased_deck_and_stop_does_not(self):
        pid = self.room()
        self.assertEqual(self.control(action='start')['error']['code'], 10)
        party.control(self.store, self.member, pid, 'claim')
        self.assertTrue(self.control(action='start')['jukeboxStatus']['playing'])
        self.assertFalse(self.control(action='stop')['jukeboxStatus']['playing'])

    def test_gain_is_bounded_and_shuffle_is_a_permutation(self):
        self.room()
        self.control(action='set', id=self.ids)
        self.assertEqual(self.control(action='setGain', gain='0.5')
                         ['jukeboxStatus']['gain'], 0.5)
        self.assertEqual(self.control(action='setGain', gain='loud')
                         ['error']['code'], 10)
        self.control(action='shuffle')
        self.assertEqual(sorted(int(e['id']) for e in self.playlist()['entry']), sorted(self.ids))

    def test_an_unknown_action_is_refused(self):
        self.room()
        self.assertEqual(self.control(action='rewind')['error']['code'], 10)


if __name__ == '__main__':
    unittest.main()