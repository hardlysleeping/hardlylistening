"""Cue points use only scratch sessions/database/media from listening fixtures."""
from test_listening import ListeningHTTP


class CuesHTTP(ListeningHTTP):
    def cues(self, track, data=None, headers=None):
        return self.api(f'cues?track={track}', data, headers)

    def cue(self, track, slot, position, headers=None):
        return self.api('cues', {'track': track, 'slot': slot, 'position': position}, headers)

    def test_set_read_move_clear_per_member(self):
        track = self.ids[0]
        self.assertEqual(self.cues(track), (200, {'cues': []}))
        for slot, position in enumerate((12.5, 40, 61.25, 90)):
            code, body = self.cue(track, slot, position)
            self.assertEqual(code, 200)
        self.assertEqual([c['slot'] for c in self.cues(track)[1]['cues']], [0, 1, 2, 3])
        self.assertEqual([c['position'] for c in self.cues(track)[1]['cues']],
                         [12.5, 40, 61.25, 90])
        # Moving a pad overwrites its slot rather than adding a fifth.
        self.assertEqual(self.cue(track, 1, 44.5)[0], 200)
        self.assertEqual([c['position'] for c in self.cues(track)[1]['cues']],
                         [12.5, 44.5, 61.25, 90])
        self.assertEqual(self.cue(track, 2, None)[0], 200)
        self.assertEqual([c['slot'] for c in self.cues(track)[1]['cues']], [0, 1, 3])
        self.assertEqual(self.store.conn.execute(
            'SELECT count(*) FROM listening_cues').fetchone()[0], 3)
        # Cues are per member: another member starts empty on the same track.
        self.identity = dict(self.identity, id='second')
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role) VALUES('second','','member')")
            self.store.conn.commit()
        self.assertEqual(self.cues(track), (200, {'cues': []}))
        self.assertEqual(self.cue(track, 0, 3)[0], 200)
        self.identity = dict(self.identity, id='u1')
        self.assertEqual([c['position'] for c in self.cues(track)[1]['cues']],
                         [12.5, 44.5, 90])

    def test_validation_and_auth(self):
        track = self.ids[0]
        self.assertEqual(self.cue(track, 4, 1)[0], 400)
        self.assertEqual(self.cue(track, -1, 1)[0], 400)
        self.assertEqual(self.cue(track, 1, -2)[0], 400)
        self.assertEqual(self.cue(track, 1, 'later')[0], 400)
        self.assertEqual(self.cue('1', 1, 1)[0], 400)
        self.assertEqual(self.cue(True, 1, 1)[0], 400)
        self.assertEqual(self.cue(0, 1, 1)[0], 400)
        self.assertEqual(self.cues('bad')[0], 400)
        self.assertEqual(self.cue(track, 0, 1, headers={})[0], 401)
        self.assertEqual(self.cue(track, 0, 1,
                                  headers={'Authorization': 'Bearer fixture'})[0], 403)
        self.assertEqual(self.cues(track, headers={})[0], 401)
        # A cue write never touches the track row itself.
        self.assertEqual(self.store.conn.execute(
            "SELECT count(*) FROM tracks WHERE id=?", (track,)).fetchone()[0], 1)