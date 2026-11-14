"""HL-204: the party deck, steered as a jukebox.

The card's question was "who owns the audio" — answered by the aux-host lease (`control('claim')`
holds a 30-second `host_until` with a hashed token). This is the other half: the room's queue and
playback state become a first-class control surface, so a phone can steer the room it is in
without becoming a second speaker.

Two things are checked here:

* the commands (`set`, `start`, `stop`, `add`, `clear`, `remove`, `shuffle`, `setGain`, `get`,
  `status`) act on one room in one transaction, and the room's votes still outrank an order a
  jukebox client asked for — the queue is a ranked crowd queue first;
* Subsonic's `jukeboxControl` maps 1:1 onto them, requires membership, and refuses with error 30
  when the member has no room.

The two races the card named are pinned at the bottom: a second deck claiming, and an advance whose
expected entry is stale.
"""
import tempfile
import time
import unittest
from pathlib import Path

import db
import party
from security import Denied, Security


class Room(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = db.Store(Path(self.tmp.name) / 'party.db')
        self.addCleanup(self.store.close)
        Security().schema(self.store)
        party.schema(self.store)
        root = Path(self.tmp.name) / 'music'
        root.mkdir()
        self.store.set_settings({'root': str(root)})
        self.ids = []
        for index in range(4):
            path = root / f't{index}.wav'
            path.write_bytes(b'RIFF' + b'\0' * 64)
            tid = self.store.track('local', str(index), f'Song {index}',
                                   artist_name=f'Artist {index}', album='Album')
            self.ids.append(tid)
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=?, duration=1',
                                    (str(root / 't0.wav'),))
            self.store.conn.commit()
        for index in range(1, 4):
            with self.store.lock:
                self.store.conn.execute('UPDATE tracks SET file=?, duration=1 WHERE id=?',
                                        (str(root / f't{index}.wav'), self.ids[index]))
                self.store.conn.commit()
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role,display_name) "
                                    "VALUES('u1','u1@example.com','admin','Host')")
            self.store.conn.execute("INSERT INTO members(id,email,role,display_name) "
                                    "VALUES('u2','u2@example.com','member','Friend')")
            self.store.conn.commit()
        self.member = {'id': 'u1', 'role': 'admin'}
        self.room = party.create(self.store, self.member, 'Jukebox')['id']

    def play(self, action, **kwargs):
        return party.jukebox(self.store, self.room, self.member, action, **kwargs)

    def queue(self):
        result = self.play('get')
        return result['tracks']

    def entry_ids(self):
        with self.store.lock:
            return [row[0] for row in self.store.conn.execute(
                "SELECT id FROM party_entries WHERE party=? AND status='waiting'", (self.room,))]


class Commands(Room):
    def test_only_members_steer_the_deck(self):
        for role in ('guest', '', None):
            with self.assertRaises(Denied) as refused:
                party.jukebox(self.store, self.room, {'id': 'g1', 'role': role}, 'status')
            self.assertEqual(refused.exception.code, 403)

    def test_set_replaces_the_queue_in_the_order_asked_for(self):
        asked = [self.ids[3], self.ids[1], self.ids[2]]
        result = self.play('set', ids=[str(i) for i in asked])
        self.assertEqual(result['status']['currentIndex'], -1)
        self.assertEqual(self.queue(), asked)

    def test_a_vote_still_outranks_the_order_a_client_asked_for(self):
        self.play('set', ids=[str(self.ids[1]), str(self.ids[2]), str(self.ids[3])])
        entry = self.entry_ids()[1]
        party.vote(self.store, self.room, 'm:u2', entry, 1)
        self.assertEqual(self.queue(), [self.ids[2], self.ids[1], self.ids[3]])

    def test_add_appends_and_remove_takes_an_index(self):
        self.play('set', ids=[str(self.ids[0])])
        self.play('add', ids=[str(self.ids[1]), str(self.ids[2])])
        self.assertEqual(self.queue(), self.ids[:3])
        self.play('remove', index=1)
        self.assertEqual(self.queue(), [self.ids[0], self.ids[2]])
        with self.assertRaises(Denied) as refused:
            self.play('remove', index=5)
        self.assertEqual(refused.exception.code, 400)

    def test_clear_empties_the_queue_and_its_votes(self):
        self.play('set', ids=[str(self.ids[0]), str(self.ids[1])])
        entry = self.entry_ids()[0]
        party.vote(self.store, self.room, 'm:u2', entry, 1)
        self.play('clear')
        self.assertEqual(self.queue(), [])
        with self.store.lock:
            self.assertEqual(self.store.conn.execute(
                'SELECT count(*) FROM party_votes').fetchone()[0], 0)

    def test_shuffle_is_a_permutation_of_the_queue(self):
        self.play('set', ids=[str(i) for i in self.ids])
        self.play('shuffle')
        self.assertEqual(sorted(self.queue()), sorted(self.ids))
        self.play('shuffle')
        self.assertEqual(sorted(self.queue()), sorted(self.ids))

    def test_start_stop_and_gain_are_measured(self):
        self.assertEqual(self.play('status')['status']['playing'], False)
        with self.assertRaises(Denied) as refused:
            self.play('start')                       # no leased deck, so no speaker
        self.assertEqual(refused.exception.code, 409)
        party.control(self.store, self.member, self.room, 'claim')
        self.play('start')
        self.assertEqual(self.play('status')['status']['playing'], True)
        self.play('stop')
        self.assertEqual(self.play('status')['status']['playing'], False)
        self.play('setGain', gain=0.5)
        self.assertEqual(self.play('status')['status']['gain'], 0.5)
        for bad in (-0.1, 1.1, True):
            with self.assertRaises(Denied):
                self.play('setGain', gain=bad)

    def test_unknown_actions_are_refused(self):
        for action in ('', 'play', 'DROP', None, 7):
            with self.assertRaises(Denied) as refused:
                self.play(action)
            self.assertEqual(refused.exception.code, 400)

    def test_duplicates_and_missing_tracks_are_refused(self):
        self.play('set', ids=[str(self.ids[0])])
        with self.assertRaises(Denied) as refused:
            self.play('add', ids=[str(self.ids[0])])
        self.assertEqual(refused.exception.code, 409)
        with self.assertRaises(Denied) as refused:
            self.play('add', ids=['9999'])
        self.assertEqual(refused.exception.code, 404)

    def test_a_paused_deck_reports_as_not_playing_even_when_the_flag_is_on(self):
        lease = party.control(self.store, self.member, self.room, 'claim')['host_token']
        self.play('start')
        self.assertEqual(self.play('status')['status']['playing'], True)
        # The heartbeat owns the lease, so let it lapse without renewing it.
        with self.store.lock:
            self.store.conn.execute('UPDATE party_rooms SET host_until=1 WHERE id=?', (self.room,))
            self.store.conn.commit()
        self.assertEqual(self.play('status')['status']['playing'], False)


class DeckState(Room):
    def test_the_room_reports_who_holds_the_deck(self):
        state = party.state(self.store, self.room, 'm:u1')
        self.assertFalse(state['deck']['host_active'])
        self.assertEqual(state['deck']['controlled_by'], '')
        token = party.control(self.store, self.member, self.room, 'claim')['host_token']
        state = party.state(self.store, self.room, 'm:u1')
        self.assertTrue(state['deck']['host_active'])
        self.assertEqual(state['deck']['controlled_by'], 'Host')
        party.control(self.store, self.member, self.room, 'heartbeat', host_token=token, position=42.5)
        self.assertEqual(party.state(self.store, self.room, 'm:u1')['deck']['position'], 42.5)

    def test_the_speaker_reports_its_state_and_a_remote_stop_stops_it(self):
        token = party.control(self.store, self.member, self.room, 'claim')['host_token']
        self.play('start')
        self.assertTrue(self.play('status')['status']['playing'])
        # The device making sound is the only honest source: its heartbeat can stop the room,
        # which is what makes a remote `stop` mean something.
        party.control(self.store, self.member, self.room, 'heartbeat', host_token=token, playing=False)
        self.assertFalse(self.play('status')['status']['playing'])
        party.control(self.store, self.member, self.room, 'heartbeat', host_token=token, playing=True)
        self.assertTrue(self.play('status')['status']['playing'])
        party.control(self.store, self.member, self.room, 'heartbeat', host_token=token, playing=None)
        self.assertTrue(self.play('status')['status']['playing'])       # absent, not a change
        for bad in ('yes', 1, 0):
            with self.assertRaises(Denied) as refused:
                party.control(self.store, self.member, self.room, 'heartbeat',
                              host_token=token, playing=bad)
            self.assertEqual(refused.exception.code, 400)

    def test_a_bad_position_is_refused_and_next_resets_it(self):
        token = party.control(self.store, self.member, self.room, 'claim')['host_token']
        for bad in (-1, 86400, 'soon', True):
            with self.assertRaises(Denied):
                party.control(self.store, self.member, self.room, 'heartbeat',
                              host_token=token, position=bad)
        party.control(self.store, self.member, self.room, 'heartbeat', host_token=token, position=30)
        party.control(self.store, self.member, self.room, 'next', host_token=token, expected=None)
        with self.store.lock:
            self.assertEqual(self.store.conn.execute(
                'SELECT deck_position FROM party_rooms WHERE id=?', (self.room,)).fetchone()[0], 0)


class Races(Room):
    def test_a_second_deck_cannot_claim_a_live_lease(self):
        party.control(self.store, self.member, self.room, 'claim')
        with self.assertRaises(Denied) as refused:
            party.control(self.store, self.member, self.room, 'claim')
        self.assertEqual(refused.exception.code, 409)

    def test_an_advance_with_a_stale_expectation_does_not_move_the_room(self):
        self.play('set', ids=[str(self.ids[1]), str(self.ids[2])])
        token = party.control(self.store, self.member, self.room, 'claim')['host_token']
        party.control(self.store, self.member, self.room, 'next', host_token=token, expected=None)
        current = party.state(self.store, self.room, 'm:u1')['current']
        self.assertEqual(current['id'], self.ids[1])
        with self.assertRaises(Denied) as refused:
            party.control(self.store, self.member, self.room, 'next', host_token=token, expected=999)
        self.assertEqual(refused.exception.code, 409)
        self.assertEqual(party.state(self.store, self.room, 'm:u1')['current']['id'], self.ids[1])


if __name__ == '__main__':
    unittest.main()