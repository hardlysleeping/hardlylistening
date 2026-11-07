"""Silent scratch-only HTTP playlist contracts, ownership and rollback."""
import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import test_security as harness
import user_playlists


class UserPlaylistsHTTP(unittest.TestCase):
    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request
    token = harness.SecurityHTTP.token
    user = harness.SecurityHTTP.user

    def setUp(self):
        harness.SecurityHTTP.setUp(self)
        self.store.set_settings({'root': str(Path(self.tmp.name) / 'music')})
        self.identity = self.user()
        self.headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}
        self.ids = [self.store.track('local', str(i), 'Song '+str(i)) for i in range(3)]

    def api(self, path='', body=None, headers=None):
        code, _, raw = self.request('/api/playlists'+path, 'GET' if body is None else 'POST',
                                    self.headers if headers is None else headers,
                                    None if body is None else json.dumps(body))
        return code, json.loads(raw)

    def create(self, tracks=None):
        code, data = self.api(body={'title': ' My mix ', 'track_ids': self.ids if tracks is None else tracks})
        self.assertEqual(code, 200, data)
        return data['playlist']

    def test_create_add_remove_reorder_and_rename(self):
        row = self.create([])
        self.assertEqual(row['title'], 'My mix')
        self.assertEqual(row['owner_id'], 'u1')
        self.assertTrue(row['can_edit'])
        path = '/'+str(row['id'])
        for previous, following in [([], self.ids[:2]), (self.ids[:2], self.ids[::-1]), (self.ids[::-1], [self.ids[1]]), ([self.ids[1]], [])]:
            code, data = self.api(path, {'track_ids': following, 'expected_track_ids': previous})
            self.assertEqual(code, 200, data)
            self.assertEqual(self.api(path)[1]['playlist']['track_ids'], following)
            positions = list(self.store.conn.execute('SELECT position FROM playlist_tracks WHERE playlist_id=? ORDER BY position', (row['id'],)))
            self.assertEqual([r[0] for r in positions], list(range(len(following))))
        self.assertEqual(self.api(path, {'title': 'Renamed'})[1]['playlist']['title'], 'Renamed')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM tracks').fetchone()[0], 3)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM download_jobs').fetchone()[0], 0)

    def test_validation_and_conflict_leave_everything_unchanged(self):
        row = self.create()
        path = '/'+str(row['id'])
        for bad in ([True], ['1'], [-1], [2**64], self.ids[:1]*2, None):
            self.assertEqual(self.api(path, {'track_ids': bad, 'expected_track_ids': self.ids})[0], 400)
        self.assertEqual(self.api(path, {'track_ids': [], 'expected_track_ids': []})[0], 409)
        self.assertEqual(self.api(path, {'track_ids': []})[0], 400)
        self.assertEqual(self.api(path, {'title': 'Changed', 'track_ids': [999999], 'expected_track_ids': self.ids})[0], 404)
        self.assertEqual(self.api(path)[1]['playlist'], row)
        count = self.store.conn.execute('SELECT count(*) FROM playlists').fetchone()[0]
        self.assertEqual(self.api(body={'title': 'Invalid', 'track_ids': [999999]})[0], 404)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM playlists').fetchone()[0], count)
        self.assertEqual(self.api('/999999')[0], 404)
        for body in ({'title': ''}, {'title': 'x'*201}, {'title': 'X', 'owner_id': 'evil'}):
            self.assertEqual(self.api(body=body)[0], 400)

    def test_other_members_and_admin_read_but_cannot_edit(self):
        row = self.create()
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role) VALUES('other','','member')")
            self.store.conn.commit()
        self.identity = {'id': 'other'}
        path = '/'+str(row['id'])
        self.assertFalse(self.api(path)[1]['playlist']['can_edit'])
        self.assertEqual(self.api(path, {'title': 'Stolen'})[0], 403)
        with self.store.lock:
            self.store.conn.execute("UPDATE members SET role='admin' WHERE id='other'")
            self.store.conn.commit()
        self.assertEqual(self.api(path, {'track_ids': [], 'expected_track_ids': self.ids})[0], 403)
        self.assertEqual(self.api(path)[1]['playlist']['track_ids'], self.ids)
        self.assertEqual(len(self.api()[1]['playlists']), 1)

    def test_auth_csrf_blocking_and_disabled_mode(self):
        for mode in ('1', '0'):
            with patch.dict(os.environ, {'HARDLYLISTENING_AUTH_REQUIRED': mode}):
                self.assertEqual(self.api(headers={})[0], 401)
                self.assertEqual(self.api(body={'title': 'No'}, headers={'Authorization': 'Bearer fixture'})[0], 403)
        for role in ('pending', 'blocked'):
            with self.store.lock:
                self.store.conn.execute('UPDATE members SET role=? WHERE id=?', (role, 'u1'))
                self.store.conn.commit()
            self.assertEqual(self.api()[0], 403)

    def test_snapshot_and_existing_source_membership_preserved(self):
        import listening
        row = self.create()
        listening.schema(self.store, self.sec)
        with self.store.lock:
            self.store.conn.execute('INSERT INTO listening_playlists VALUES(?,?)', ('u1', row['id']))
            self.store.conn.execute('INSERT INTO listening_playlist_tracks SELECT ?,playlist_id,track_id,position FROM playlist_tracks WHERE playlist_id=?', ('u1', row['id']))
            self.store.conn.commit()
        source = self.store.playlist('Source', 'soundcloud', 'https://example.com/source')
        self.store.add_to_playlist(source, self.ids[0], 7)
        self.assertEqual(self.api('/'+str(source), {'title': 'Hijack'})[0], 403)
        self.assertEqual(self.api('/'+str(row['id']), {'track_ids': [], 'expected_track_ids': self.ids})[0], 200)
        self.assertEqual([r[0] for r in self.store.conn.execute('SELECT track_id FROM listening_playlist_tracks ORDER BY position')], self.ids)
        self.assertEqual(self.api('/'+str(source))[1]['playlist']['track_ids'], self.ids[:1])

    def test_mid_insert_failure_rolls_back_membership_and_title(self):
        row = self.create()
        with self.store.lock:
            self.store.conn.execute('''CREATE TRIGGER reject_membership BEFORE INSERT ON playlist_tracks
                WHEN NEW.position=1 BEGIN SELECT RAISE(ABORT, 'fixture failure'); END''')
            self.store.conn.commit()
        handler = Mock(command='POST', path='/api/playlists/'+str(row['id']))
        handler.body.return_value = {'title': 'Changed', 'track_ids': self.ids[::-1], 'expected_track_ids': self.ids}
        with patch.object(self.sec, 'authenticate', return_value={'id': 'u1'}):
            with self.assertRaises(sqlite3.IntegrityError):
                user_playlists.dispatch(handler, self.store, self.sec)
        self.assertEqual(self.api('/'+str(row['id']))[1]['playlist'], row)
        self.assertFalse(self.store.conn.in_transaction)


if __name__ == '__main__':
    unittest.main()
