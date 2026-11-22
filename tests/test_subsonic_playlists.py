"""Playlists written from a client: the same rows the web editor writes, saved into the caller's
own listening scope. Scratch database and media only (the `ListeningHTTP` fixtures).

What these pin, in the client's own terms:

* `createPlaylist` makes one playlist, not a second private list: it is in `getPlaylists`, it is
  the canonical row the library editor reads, and it belongs to the member who asked.
* Membership edits (`songIdToAdd`, `songIndexToRemove`) and a rename are visible on the very next
  read — the member's snapshot follows the edit instead of waiting to be saved again.
* Refusals are named, not silent: a duplicate add, a position that does not exist, a comment
  this server cannot store, another member's playlist, the Favorites set (computed, not a row),
  and an edit that changes nothing.
* Removing a song from a playlist never touches the song.
"""
import json
from urllib.parse import urlencode

from test_listening import ListeningHTTP


class PlaylistWritesHTTP(ListeningHTTP):
    def call(self, endpoint, **params):
        """One REST call, with repeatable parameters encoded properly. Returns the envelope."""
        args = dict(u=self.device['username'], p=self.device['password'], f='json',
                    v='1.16.1', c='fixture')
        args.update(params)
        code, _, raw = self.request('/rest/' + endpoint + '.view?' + urlencode(args, doseq=True))
        self.assertEqual(code, 200)
        return json.loads(raw)['subsonic-response']

    def owner(self):
        """The member this test's device belongs to."""
        return self.store.conn.execute('SELECT member_id FROM listening_devices WHERE id=?',
                                       (self.device['username'],)).fetchone()[0]

    def created(self, **params):
        response = self.call('createPlaylist', **params)
        self.assertNotIn('error', response)
        return response['playlist']

    def entries(self, playlist_id):
        return [entry['id'] for entry in self.call('getPlaylist', id=playlist_id)['playlist']['entry']]

    def test_create_is_one_playlist_the_web_editor_can_see(self):
        before = {row[0] for row in self.store.conn.execute('SELECT id FROM playlists')}
        row = self.created(name='Phone list', songId=[self.ids[0], self.ids[2]])
        self.assertEqual(row['name'], 'Phone list')
        self.assertEqual(row['songCount'], 2)
        pid = int(row['id'])

        # It is in the member's own list, with the order it was given.
        listed = {p['id']: p for p in self.call('getPlaylists')['playlists']['playlist']}
        self.assertIn(str(pid), listed)
        self.assertEqual(listed[str(pid)]['songCount'], 2)
        self.assertEqual(self.entries(pid), [str(self.ids[0]), str(self.ids[2])])

        # And it is the canonical row: the library editor's own listing sees it, owned here.
        from library import playlists as library_playlists
        editor = {r['id']: r for r in library_playlists.listing(self.store, self.owner())}
        self.assertIn(pid, editor)
        self.assertEqual(editor[pid]['title'], 'Phone list')
        self.assertTrue(editor[pid]['can_edit'])
        self.assertEqual(set(row) & {'kind'}, set())
        self.assertEqual({r[0] for r in self.store.conn.execute('SELECT id FROM playlists')} - before, {pid})

    def test_membership_and_name_follow_the_edit_immediately(self):
        pid = self.created(name='Grows', songId=[self.ids[0]])['id']
        self.assertEqual(self.call('updatePlaylist', playlistId=pid, songIdToAdd=self.ids[2])['status'], 'ok')
        self.assertEqual(self.entries(pid), [str(self.ids[0]), str(self.ids[2])])
        # A second add lands at the end, and the repeatable form works.
        self.assertEqual(self.call('updatePlaylist', playlistId=pid,
                                   songIdToAdd=self.ids[1])['status'], 'ok')
        self.assertEqual(self.entries(pid), [str(self.ids[0]), str(self.ids[2]), str(self.ids[1])])

        # Removing by position is the position the read returned.
        self.call('updatePlaylist', playlistId=pid, songIndexToRemove='1')
        self.assertEqual(self.entries(pid), [str(self.ids[0]), str(self.ids[1])])

        # Removing the last song leaves an empty playlist, not a vanished one.
        self.call('updatePlaylist', playlistId=pid, songIndexToRemove=['0', '1'])
        self.assertEqual(self.entries(pid), [])
        self.assertEqual(self.call('getPlaylist', id=pid)['playlist']['songCount'], 0)

        # A rename is the same call, and it is what `getPlaylists` lists.
        self.call('updatePlaylist', playlistId=pid, name='  Renamed  ')
        listed = {p['id']: p for p in self.call('getPlaylists')['playlists']['playlist']}
        self.assertEqual(listed[str(pid)]['name'], 'Renamed')

        # The song rows are untouched by any of it.
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM tracks').fetchone()[0], len(self.ids))

    def test_refusals_are_named(self):
        pid = self.created(name='Guarded', songId=[self.ids[0]])['id']
        duplicate = self.call('updatePlaylist', playlistId=pid, songIdToAdd=self.ids[0])['error']
        self.assertEqual((duplicate['code'], duplicate['message']),
                         (10, 'That song is already in the playlist'))
        self.assertEqual(self.call('updatePlaylist', playlistId=pid,
                                   songIndexToRemove='9')['error']['code'], 10)
        self.assertEqual(self.call('updatePlaylist', playlistId=pid,
                                   comment='great')['error']['code'], 10)
        self.assertEqual(self.call('updatePlaylist', playlistId=pid)['error']['code'], 10)
        # Favorites is computed from stars, so it is refused as not editable rather than
        # as a bad id.
        computed = self.call('updatePlaylist', playlistId='favorites', name='Mine')['error']
        self.assertEqual((computed['code'], computed['message']),
                         (40, 'That playlist is computed and cannot be edited'))
        self.assertEqual(self.call('updatePlaylist', playlistId='999999',
                                   name='Mine')['error']['code'], 70)
        self.assertEqual(self.call('updatePlaylist', songIdToAdd=self.ids[1])['error']['code'], 10)
        self.assertEqual(self.call('createPlaylist', name='  ')['error']['code'], 10)
        self.assertEqual(self.call('createPlaylist', name='Bad id',
                                   songId='not-a-song')['error']['code'], 70)
        # Nothing above changed anything.
        self.assertEqual(self.entries(pid), [str(self.ids[0])])

    def test_another_member_cannot_edit_it(self):
        pid = self.created(name='Mine', songId=[self.ids[0]])['id']
        self.identity = dict(self.identity, id='second')
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role) VALUES('second','','member')")
            self.store.conn.commit()
        other = self.api('devices', {'label': 'Other'})[1]
        args = dict(u=other['username'], p=other['password'], f='json', v='1.16.1', c='fixture',
                    playlistId=pid, songIdToAdd=self.ids[1])
        code, _, raw = self.request('/rest/updatePlaylist.view?' + urlencode(args))
        self.assertEqual(json.loads(raw)['subsonic-response']['error']['code'], 40)
        # The owner's membership is exactly what it was, and it is still their playlist.
        self.assertEqual(self.entries(pid), [str(self.ids[0])])
        from library import playlists as library_playlists
        mine = {r['id']: r for r in library_playlists.listing(self.store, self.owner())}
        self.assertEqual(mine[int(pid)]['track_ids'], [self.ids[0]])