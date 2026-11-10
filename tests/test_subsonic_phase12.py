"""Phase 1 (bookmarks) and Phase 2 (public shares) of the Subsonic surface.

Same isolated HTTP fixture as `test_listening`/`test_subsonic_extra`: real
requests, generated WAV bytes, temporary database, no live boot.
"""
import json
import time
import unittest
from urllib.parse import urlparse

from test_listening import ListeningHTTP


class BookmarksAndShares(unittest.TestCase):
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
        self.save('playlist', self.pid)

    def share(self, kind, target, **extra):
        body = {'kind': kind, 'id': target, **extra}
        code, _, raw = self.request('/api/shares', 'POST', self.headers, json.dumps(body))
        self.assertEqual(code, 200, raw)
        return json.loads(raw)

    def path_of(self, url):
        parsed = urlparse(url)
        return parsed.path + (('?' + parsed.query) if parsed.query else '')

    # -- bookmarks ----------------------------------------------------------
    def test_bookmark_create_get_update_delete_round_trip(self):
        first = self.response('createBookmark', id=self.ids[0], position=12500, comment='verse two')
        self.assertEqual(first['status'], 'ok')
        self.assertEqual(self.response('getSong', id=self.ids[0])['song']['bookmarkPosition'], 12500)
        bookmarks = self.response('getBookmarks')['bookmarks']['bookmark']
        self.assertEqual(len(bookmarks), 1)
        self.assertEqual(bookmarks[0]['position'], 12500)
        self.assertEqual(bookmarks[0]['comment'], 'verse two')
        self.assertEqual(bookmarks[0]['entry']['id'], str(self.ids[0]))
        self.assertTrue(bookmarks[0]['created'].endswith('Z'))

        created = self.store.conn.execute(
            'SELECT created FROM listening_bookmarks WHERE member_id=? AND track_id=?',
            ('u1', self.ids[0])).fetchone()[0]
        time.sleep(0.01)
        self.response('createBookmark', id=self.ids[0], position=9000, comment='chorus')
        bookmarks = self.response('getBookmarks')['bookmarks']['bookmark']
        self.assertEqual(len(bookmarks), 1)                 # upsert, never a duplicate
        self.assertEqual(bookmarks[0]['position'], 9000)
        self.assertEqual(bookmarks[0]['comment'], 'chorus')
        kept = self.store.conn.execute(
            'SELECT created FROM listening_bookmarks WHERE member_id=? AND track_id=?',
            ('u1', self.ids[0])).fetchone()[0]
        self.assertEqual(kept, created)

        self.assertEqual(self.response('deleteBookmark', id=self.ids[0])['status'], 'ok')
        self.assertEqual(self.response('getBookmarks')['bookmarks']['bookmark'], [])
        self.assertNotIn('bookmarkPosition', self.response('getSong', id=self.ids[0])['song'])

    def test_bookmarks_are_member_scoped(self):
        self.response('createBookmark', id=self.ids[0], position=4000, comment='mine')
        self.store.conn.execute("INSERT INTO members(id,email,role,display_name) "
                                "VALUES('u2','other@example.com','member','Other')")
        self.store.conn.commit()
        self.identity = {'id': 'u2', 'email': 'other@example.com'}
        second = self.api('devices', {'label': 'Other phone'})[1]
        self.save('track', self.ids[0])
        self.assertEqual(json.loads(self.rest('getBookmarks', device=second)[2])
                         ['subsonic-response']['bookmarks']['bookmark'], [])
        song = json.loads(self.rest('getSong', device=second, id=self.ids[0])[2])['subsonic-response']['song']
        self.assertNotIn('bookmarkPosition', song)

    # -- shares over the Subsonic API --------------------------------------
    def test_subsonic_share_round_trip_with_expiry(self):
        album_id = self.response('getSong', id=self.ids[0])['song']['albumId']
        self.assertEqual(self.response('createShare', id=self.ids[0])['status'], 'ok')
        self.assertEqual(self.response('createShare', id=album_id)['status'], 'ok')
        self.assertEqual(self.response('createShare', id=self.pid, kind='playlist')['status'], 'ok')
        with self.store.lock:
            rows = {r['kind']: dict(r) for r in self.store.conn.execute(
                'SELECT * FROM listening_shares ORDER BY id')}
        self.assertEqual(set(rows), {'track', 'album', 'playlist'})
        shares = self.response('getShares')['shares']['share']
        self.assertEqual(len(shares), 3)
        by_id = {row['id']: row for row in shares}
        self.assertEqual(set(by_id), {str(r['id']) for r in rows.values()})
        for row in shares:
            self.assertTrue(row['expires'].endswith('Z'))
            self.assertEqual(row['visitCount'], 0)
            self.assertTrue(row['entry'])
        self.assertEqual(by_id[str(rows['track']['id'])]['entry'][0]['id'], str(self.ids[0]))
        self.assertEqual(len(by_id[str(rows['playlist']['id'])]['entry']), 1)
        # Track shares get a fresh 24h expiry by default.
        self.assertAlmostEqual(rows['track']['expires'] - rows['track']['created'], 86400, delta=2)

        share_id = str(rows['track']['id'])
        later = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + 3600))
        updated = self.response('updateShare', id=share_id, description='new note',
                                expires=later)
        self.assertEqual(updated['status'], 'ok')
        again = next(r for r in self.response('getShares')['shares']['share']
                     if r['id'] == share_id)
        self.assertEqual(again['description'], 'new note')
        self.assertEqual(again['expires'][:13], later[:13])
        self.assertEqual(self.response('deleteShare', id=share_id)['status'], 'ok')
        self.assertEqual(len(self.response('getShares')['shares']['share']), 2)

    def test_subsonic_share_rejects_unowned_targets(self):
        self.assertEqual(self.response('createShare', id='nope')['error']['code'], 70)
        self.assertEqual(self.response('createShare', id='al_deadbeef')['error']['code'], 70)
        self.assertEqual(self.response('deleteShare', id='999999')['error']['code'], 70)

    # -- public share pages -------------------------------------------------
    def test_public_share_page_stream_and_cover(self):
        made = self.share('track', self.ids[0], note='for you')
        path = self.path_of(made['url'])
        self.assertTrue(path.startswith('/s/'))
        code, headers, raw = self.request(path)
        self.assertEqual(code, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        self.assertIn(b'Song 0', raw)
        self.assertIn(b'for you', raw)
        self.assertNotIn(b'Friend', raw)                  # never the owner's name
        self.assertNotIn(self.tmp.name.encode(), raw)     # never a filesystem path
        self.assertNotIn(b'secret.example', raw)

        code, headers, raw = self.request(path + '/stream?track=%d' % self.ids[0],
                                          headers={'Range': 'bytes=4-19'})
        self.assertEqual(code, 206)
        self.assertEqual(raw, self.media[4:20])
        self.assertIn('Content-Range', headers)

        code, headers, raw = self.request(path + '/cover')
        self.assertEqual(code, 200)
        self.assertTrue(headers['Content-Type'].startswith('image/'))

        hits = self.store.conn.execute('SELECT hits FROM listening_shares WHERE id=?',
                                       (made['id'],)).fetchone()[0]
        self.assertEqual(hits, 1)
        self.request(path)
        self.assertEqual(self.store.conn.execute('SELECT hits FROM listening_shares WHERE id=?',
                                                 (made['id'],)).fetchone()[0], 2)

    def test_album_and_playlist_shares_expose_only_their_tracks(self):
        # Give the fixture a real two-track album so the page can be checked.
        self.store.conn.execute("UPDATE tracks SET artist_name='Band', album='Shared' "
                                "WHERE id IN (?,?)", (self.ids[0], self.ids[1]))
        self.store.conn.commit()
        album_id = self.response('getSong', id=self.ids[0])['song']['albumId']
        album = self.share('album', album_id)
        page = self.request(self.path_of(album['url']))[2]
        self.assertIn(b'Song 0', page)
        self.assertIn(b'Song 1', page)
        self.assertNotIn(b'Song 2', page)
        self.assertIn(b'Shared', page)
        # A track outside the album cannot be streamed through this capability.
        self.assertEqual(self.request(self.path_of(album['url']) + '/stream?track=99999')[0], 404)

        playlist = self.share('playlist', self.pid)
        page = self.request(self.path_of(playlist['url']))[2]
        self.assertIn(b'Song 1', page)
        self.assertNotIn(b'secret.example', page)
        code, _, raw = self.request(self.path_of(playlist['url']) + '/stream?track=%d' % self.ids[1],
                                    headers={'Range': 'bytes=0-7'})
        self.assertEqual(code, 206)
        self.assertEqual(raw, self.media[:8])

    def test_shares_expire_and_can_be_deleted(self):
        made = self.share('track', self.ids[0])
        path = self.path_of(made['url'])
        self.store.conn.execute('UPDATE listening_shares SET expires=? WHERE id=?',
                                (time.time() - 1, made['id']))
        self.store.conn.commit()
        self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request(path + '/stream')[0], 404)

        fresh = self.share('track', self.ids[1])
        code, _, raw = self.request('/api/shares/' + str(fresh['id']), 'DELETE', self.headers)
        self.assertEqual(code, 200, raw)
        self.assertEqual(self.request(self.path_of(fresh['url']))[0], 404)

    def test_share_expiry_must_be_future_and_guests_are_refused(self):
        past = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 60))
        code, _, raw = self.request('/api/shares', 'POST', self.headers,
                                    json.dumps({'kind': 'track', 'id': self.ids[0], 'expires': past}))
        self.assertEqual(code, 400, raw)
        code, _, raw = self.request('/api/shares', 'POST',
                                    {'Origin': 'https://music.example',
                                     'Cookie': '__Host-hl_party=guest-session'},
                                    json.dumps({'kind': 'track', 'id': self.ids[0]}))
        self.assertEqual(code, 403, raw)
        self.assertEqual(self.request('/api/shares', 'POST', {'Origin': 'https://music.example'},
                                      json.dumps({'kind': 'track', 'id': self.ids[0]}))[0], 401)

    def test_only_the_owner_or_admin_may_delete_a_share(self):
        made = self.share('track', self.ids[0])
        self.store.conn.execute("INSERT INTO members(id,email,role,display_name) "
                                "VALUES('u2','other@example.com','member','Other')")
        self.store.conn.commit()
        self.identity = {'id': 'u2', 'email': 'other@example.com'}
        self.api('devices', {'label': 'Other phone'})
        code, _, _ = self.request('/api/shares/' + str(made['id']), 'DELETE', self.headers)
        self.assertEqual(code, 403)
        self.identity = {'id': 'u1', 'email': 'friend@example.com', 'email_confirmed_at': 'yes'}
        self.store.conn.execute("UPDATE members SET role='admin' WHERE id='u1'")
        self.store.conn.commit()
        code, _, _ = self.request('/api/shares/' + str(made['id']), 'DELETE', self.headers)
        self.assertEqual(code, 200)

    def test_only_hash_is_stored_and_phase_two_does_not_claim_more(self):
        made = self.share('track', self.ids[0])
        stored = self.store.conn.execute('SELECT token_hash FROM listening_shares WHERE id=?',
                                         (made['id'],)).fetchone()[0]
        self.assertNotEqual(stored, made['token'])
        self.assertEqual(len(stored), 64)
        self.assertNotIn(made['token'], stored)
        for endpoint in ('jukeboxControl', 'addChatMessage',
                         'getSonicSimilarTracks', 'hls'):
            response = self.response(endpoint)
            self.assertEqual(response['status'], 'failed', endpoint)
            self.assertEqual(response['error']['code'], 30, endpoint)


if __name__ == '__main__':
    unittest.main()
