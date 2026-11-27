"""Starting the room from a set: what a person can choose, and what shuffling one in does.

Same isolation as `test_party.PartyHTTP`: a throwaway SQLite file and a real HTTP server on
a free port. Nothing here plays audio; it only queues.
"""
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import party
import server
from security import Security


class PartySetsHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.root = Path(self.tmp.name)
        conn = sqlite3.connect(self.root/'isolated.sqlite', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE tracks(id INTEGER PRIMARY KEY,title TEXT,artist_name TEXT,'
                     'album TEXT,duration REAL,file TEXT,source_url TEXT,deleted INTEGER DEFAULT 0)')
        self.files = {}
        for i in range(1, 9):
            f = self.root/f'{i}.mp3'
            f.write_bytes(b'fixture never played')
            self.files[i] = str(f)
            conn.execute('INSERT INTO tracks(id,title,artist_name,album,duration,file,deleted)'
                         ' VALUES(?,?,?,?,?,?,0)',
                         (i, f'Track {i}', 'Shuffler' if i <= 5 else 'Other', 'Album', 120, str(f)))
        # A deleted row and a row whose file is gone: both are unplayable, and a set must say so
        # rather than queue a song that cannot make a sound.
        conn.execute('INSERT INTO tracks(id,title,artist_name,album,duration,file,deleted)'
                     " VALUES(9,'Gone','Shuffler','Album',120,'/missing',0)")
        conn.execute('INSERT INTO tracks(id,title,artist_name,album,duration,file,deleted)'
                     " VALUES(10,'Deleted','Shuffler','Album',120,? || '',1)", (self.files[1],))
        conn.execute('CREATE TABLE playlists(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL)')
        conn.execute('CREATE TABLE playlist_tracks(playlist_id INTEGER NOT NULL,'
                     ' track_id INTEGER NOT NULL, position INTEGER NOT NULL DEFAULT 0,'
                     ' PRIMARY KEY (playlist_id, track_id))')
        conn.execute('CREATE TABLE listening_playlists(member_id TEXT NOT NULL,'
                     ' playlist_id INTEGER NOT NULL, PRIMARY KEY(member_id,playlist_id))')
        conn.execute("INSERT INTO playlists(id,title) VALUES(1,'Shuffle'),(2,'Small'),(3,'Broken')")
        for i, order in enumerate([1, 2, 3, 4, 5, 9, 10]):
            conn.execute('INSERT INTO playlist_tracks VALUES(1,?,?)', (order, i))
        conn.execute('INSERT INTO playlist_tracks VALUES(2,1,0)')
        conn.execute('INSERT INTO playlist_tracks VALUES(2,6,1)')
        conn.execute('INSERT INTO playlist_tracks VALUES(3,9,0)')
        conn.commit()
        self.store = SimpleNamespace(conn=conn, lock=threading.RLock(), setting=lambda key: str(self.root))
        self.sec = Security()
        self.env = patch.dict(os.environ, {'HARDLYLISTENING_AUTH_REQUIRED': '1',
                                          'HARDLYLISTENING_PUBLIC_ORIGIN': 'https://music.example'})
        self.env.start()
        self.sec.schema(self.store)
        party.schema(self.store)
        with self.store.lock:
            for uid, role in [('m', 'member'), ('o', 'member')]:
                conn.execute('INSERT INTO members(id,role,display_name,profile_icon) VALUES(?,?,?,?)',
                             (uid, role, uid.upper(), 'moon'))
            conn.commit()
        self.patches = [patch.object(server, 'boot', return_value=(self.store, None)),
                        patch.object(server, 'SECURITY', self.sec)]
        for p in self.patches:
            p.start()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.thread.start()
        self.member = self.session('m')
        self.other = self.session('o')
        self.pid = self.req('/api/party', 'POST', {'title': 'Sets'}, self.member)[2]['id']

    def tearDown(self):
        self.http.shutdown()
        self.thread.join()
        self.http.server_close()
        for p in reversed(self.patches):
            p.stop()
        self.env.stop()
        self.store.conn.close()
        self.tmp.cleanup()

    def session(self, uid):
        sid = secrets.token_urlsafe(32)
        with self.store.lock:
            self.store.conn.execute('INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)',
                                    (self.sec.digest(sid), uid, time.time() + 3600))
            self.store.conn.commit()
        return '__Host-hl_session=' + sid

    def req(self, path, method='GET', body=None, cookie='', origin='https://music.example'):
        c = HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        headers = {'Cookie': cookie, 'Origin': origin}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = c.getresponse()
        data = r.read()
        status = r.status
        c.close()
        try:
            data = json.loads(data)
        except (ValueError, UnicodeError):
            pass
        return status, dict(r.getheaders()), data

    def path(self, action=''):
        return '/api/party/' + self.pid + ('/' + action if action else '')

    def join(self, name='Guest'):
        code, _, inv = self.req(self.path('invite'), 'POST', {}, self.member)
        self.assertEqual(code, 200)
        token = inv['link'].split('invite=')[1]
        code, headers, _ = self.req('/api/party/exchange', 'POST',
                                    {'invite': token, 'display_name': name, 'profile_icon': 'moon'})
        self.assertEqual(code, 200)
        return headers['Set-Cookie'].split(';')[0]

    def queue_set(self, kind, ident, cookie=None):
        return self.req(self.path('queue-set'), 'POST', {'kind': kind, 'id': ident},
                        self.member if cookie is None else cookie)

    def waiting(self, cookie=None):
        return self.req(self.path(), cookie=self.member if cookie is None else cookie)[2]['queue']

    # ── what can be started ────────────────────────────────────────────────────────────────
    def test_sets_count_songs_by_sql_alone(self):
        code, _, s = self.req(self.path('sets'), cookie=self.member)
        self.assertEqual(code, 200)
        by_name = {p['name']: p for p in s['playlists']}
        # A listing cannot stat every file in every playlist, so it counts what SQL can see: not
        # deleted, with a file path. The one track whose file vanished still counts here and is
        # reported as unavailable the moment a set is actually queued.
        self.assertEqual(by_name['Shuffle']['count'], 6)
        self.assertEqual(by_name['Small']['count'], 2)
        # "Broken" holds one song whose file is gone: counted here, reported unavailable when
        # somebody actually tries to start it.
        self.assertEqual(by_name['Broken']['count'], 1)
        self.assertEqual([p['name'] for p in s['playlists']][0], 'Shuffle')  # biggest first
        self.assertFalse(any(p['saved'] for p in s['playlists']))
        self.assertEqual(s['artists'], [{'name': 'Shuffler', 'count': 6},
                                        {'name': 'Other', 'count': 3}])

    def test_saved_playlists_are_marked_for_the_member_who_saved_them(self):
        with self.store.lock:
            self.store.conn.execute("INSERT INTO listening_playlists VALUES('m',2)")
            self.store.conn.commit()
        mine = {p['name']: p['saved'] for p in self.req(self.path('sets'), cookie=self.member)[2]['playlists']}
        theirs = {p['name']: p['saved'] for p in self.req(self.path('sets'), cookie=self.other)[2]['playlists']}
        self.assertEqual(mine, {'Shuffle': False, 'Small': True, 'Broken': False})
        self.assertEqual(theirs, {'Shuffle': False, 'Small': False, 'Broken': False})

    def test_a_library_that_never_saved_a_playlist_still_lists_sets(self):
        with self.store.lock:
            self.store.conn.execute('DROP TABLE listening_playlists')
            self.store.conn.commit()
        code, _, s = self.req(self.path('sets'), cookie=self.member)
        self.assertEqual(code, 200)
        self.assertEqual(len(s['playlists']), 3)

    def test_a_guest_cannot_list_sets_but_can_still_add_a_song(self):
        guest = self.join()
        code, _, s = self.req(self.path('sets'), cookie=guest)
        self.assertEqual(code, 200)
        self.assertFalse(any(p['saved'] for p in s['playlists']))
        code, _, body = self.queue_set('playlist', 1, guest)
        self.assertEqual(code, 403)
        self.assertIn('Members start sets', body['error'])
        self.assertEqual(self.req(self.path('queue'), 'POST', {'track_id': 1}, guest)[0], 200)

    # ── shuffling one in ─────────────────────────────────────────────────────────────────
    def test_a_playlist_lands_as_its_playable_songs_and_reports_the_rest(self):
        code, _, r = self.queue_set('playlist', 1)
        self.assertEqual(code, 200)
        self.assertEqual(r, {'name': 'Shuffle', 'added': 5, 'already': 0, 'unavailable': 2,
                             'full': False, 'limit': party.WAITING_LIMIT, 'size': 7})
        self.assertEqual(sorted(t['title'] for t in self.waiting()),
                         ['Track 1', 'Track 2', 'Track 3', 'Track 4', 'Track 5'])

    def test_shuffling_the_same_set_again_skips_what_is_already_there(self):
        self.queue_set('playlist', 1)
        code, _, r = self.queue_set('playlist', 1)
        self.assertEqual(code, 200)
        self.assertEqual((r['added'], r['already'], r['unavailable']), (0, 5, 2))
        self.assertEqual(len(self.waiting()), 5)

    def test_only_the_missing_songs_are_added_on_a_second_pass(self):
        self.queue_set('playlist', 2)
        code, _, r = self.queue_set('playlist', 1)
        self.assertEqual((r['added'], r['already']), (4, 1))
        self.assertEqual(len(self.waiting()), 6)

    def test_an_artist_is_a_set_of_their_playable_songs(self):
        code, _, r = self.queue_set('artist', 'Shuffler')
        self.assertEqual(code, 200)
        # The deleted song is already gone from the artist's own query; the one whose file
        # vanished is only discovered when it is really looked at.
        self.assertEqual((r['name'], r['added'], r['unavailable'], r['size']), ('Shuffler', 5, 1, 6))
        self.assertEqual({t['artist_name'] for t in self.waiting()}, {'Shuffler'})

    def test_a_set_never_exceeds_the_waiting_limit_and_says_where_it_stopped(self):
        with self.store.lock:
            self.store.conn.execute("INSERT INTO tracks(id,title,artist_name,album,duration,file,deleted)"
                                    " SELECT 100+n,'Filler '||n,'Filler','Album',60,?,0 FROM ("
                                    "  WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c WHERE n<210)"
                                    "  SELECT n FROM c)", (self.files[1],))
            self.store.conn.commit()
        code, _, r = self.queue_set('artist', 'Filler')
        self.assertEqual(code, 200)
        self.assertEqual((r['added'], r['full']), (party.WAITING_LIMIT, True))
        self.assertEqual(len(self.waiting()), party.WAITING_LIMIT)

    def test_an_unknown_set_is_a_404_and_a_wrong_kind_is_a_400(self):
        self.assertEqual(self.queue_set('playlist', 999)[0], 404)
        self.assertEqual(self.queue_set('artist', 'Nobody')[0], 404)
        self.assertEqual(self.queue_set('album', 'Album')[0], 400)

    def test_shuffling_is_actually_shuffled(self):
        # Five songs in library order would be one arrangement; over a few rooms they are not.
        orders = set()
        for _ in range(6):
            with self.store.lock:
                self.store.conn.execute('DELETE FROM party_entries')
                self.store.conn.commit()
            self.queue_set('playlist', 1)
            orders.add(tuple(t['title'] for t in self.waiting()))
        self.assertGreater(len(orders), 1)

    # ── searching ────────────────────────────────────────────────────────────────────────
    def test_search_answers_with_sets_as_well_as_songs(self):
        code, _, s = self.req(self.path('search') + '?q=shuf', cookie=self.member)
        self.assertEqual(code, 200)
        self.assertEqual(s['query'], 'shuf')
        self.assertEqual([p['name'] for p in s['playlists']], ['Shuffle'])
        self.assertEqual([a['name'] for a in s['artists']], ['Shuffler'])
        self.assertEqual(len(s['tracks']), 6)
        # The track shape is the same one the old single-song search returned, unchanged.
        for t in s['tracks']:
            self.assertEqual(set(t), {'id', 'title', 'artist_name', 'album', 'duration'})

    def test_search_can_match_a_playlist_with_no_matching_songs(self):
        code, _, s = self.req(self.path('search') + '?q=small', cookie=self.member)
        self.assertEqual([p['name'] for p in s['playlists']], ['Small'])
        self.assertEqual(s['tracks'], [])

    def test_search_still_refuses_an_empty_or_oversized_term(self):
        self.assertEqual(self.req(self.path('search') + '?q=', cookie=self.member)[0], 400)
        self.assertEqual(self.req(self.path('search') + '?q=' + 'x' * 101, cookie=self.member)[0], 400)

    def test_search_wildcards_are_literals(self):
        code, _, s = self.req(self.path('search') + '?q=%25', cookie=self.member)
        self.assertEqual(code, 200)
        self.assertEqual(s['tracks'], [])
        self.assertEqual(s['playlists'], [])


if __name__ == '__main__':
    unittest.main()