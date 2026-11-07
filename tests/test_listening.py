"""Isolated HTTP fixtures; generated WAV bytes, never audible playback/live boot."""
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import wave
import xml.etree.ElementTree as ET
import test_security as harness


class ListeningHTTP(unittest.TestCase):
    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request
    token = harness.SecurityHTTP.token
    user = harness.SecurityHTTP.user

    def setUp(self):
        harness.SecurityHTTP.setUp(self)
        root = Path(self.tmp.name) / 'music'
        root.mkdir()
        self.store.set_settings({'root': str(root)})
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as w:
            w.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            w.writeframes(b'\x00\x00' * 8000)
        self.media = buffer.getvalue()
        path = root / 'fixture.wav'
        path.write_bytes(self.media)
        self.ids = [self.store.track('local', str(i), 'Song '+str(i), artist_name='Artist '+str(i), album='Album', source_url='https://secret.example/acquire') for i in range(3)]
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=?,duration=1', (str(path),))
            self.store.conn.commit()
        self.pid = self.store.playlist('Set', 'local', 'https://secret.example/set')
        self.store.add_to_playlist(self.pid, self.ids[1], 0)
        self.identity = self.user('admin')
        self.headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}

        self.device = self.api('devices', {'label': 'Phone'})[1]

    def api(self, path, data=None, headers=None):
        code, _, raw = self.request('/api/listening/'+path, 'GET' if data is None else 'POST', self.headers if headers is None else headers, None if data is None else json.dumps(data))
        return code, json.loads(raw)

    def rest(self, endpoint, device=None, **params):
        device = device or self.device
        args = dict(u=device['username'], p=device['password'], f='json', v='1.16.1', c='fixture', **params)
        code, headers, raw = self.request('/rest/'+endpoint+'.view?'+urlencode(args))
        return code, headers, raw

    def response(self, endpoint, **params):
        return json.loads(self.rest(endpoint, **params)[2])['subsonic-response']

    def save(self, kind, identity, saved=True):
        self.assertEqual(self.api('saves', {'kind': kind, 'id': identity, 'saved': saved})[0], 200)

    def test_scope_snapshot_guessed_ids_and_no_admin_bypass(self):
        self.assertEqual(self.response('search3')['searchResult3']['song'], [])
        self.save('track', self.ids[0])
        self.save('playlist', self.pid)
        self.store.add_to_playlist(self.pid, self.ids[2], 1)
        saved = self.api('saves')[1]
        self.assertEqual(saved['tracks'], [self.ids[0]])
        self.assertEqual(saved['playlists'], [self.pid])
        self.assertEqual(saved['playlist_tracks'], [{'playlist_id': self.pid, 'track_id': self.ids[1], 'position': 0}])
        songs = self.response('search3')['searchResult3']['song']
        self.assertEqual({s['id'] for s in songs}, set(map(str, self.ids[:2])))
        for endpoint in ('getSong', 'stream', 'download'):
            self.assertEqual(self.response(endpoint, id=self.ids[2])['error']['code'], 70)
        self.assertEqual(self.response('getPlaylist', id=self.pid)['playlist']['songCount'], 1)
        raw = self.rest('search3')[2]
        for secret in (self.tmp.name, 'source_url', 'https://secret', 'logs', 'file'):
            self.assertNotIn(secret.encode(), raw)
        self.save('playlist', self.pid, False)
        self.assertEqual(self.response('getSong', id=self.ids[1])['status'], 'failed')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM playlist_tracks').fetchone()[0], 2)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM tracks').fetchone()[0], 3)

    def test_personal_folders_are_member_scoped_and_link_canonical_tracks(self):
        code, root = self.api('folders', {'action': 'create', 'name': 'Mood sets'})
        self.assertEqual(code, 200, root)
        root_id = root['id']
        code, child = self.api('folders', {'action': 'create', 'name': 'Late night', 'parent_id': root_id})
        self.assertEqual(code, 200, child)
        child_id = child['id']
        self.assertEqual(self.api('folders', {'action': 'create', 'name': 'mood sets'})[0], 409)
        self.assertEqual(self.api('folders', {'action': 'add_track', 'folder_id': child_id, 'track_id': self.ids[0]})[0], 200)
        listing = self.api('folders')[1]['folders']
        self.assertEqual([(f['id'], f['parent_id'], f['name']) for f in listing],
                         [(root_id, None, 'Mood sets'), (child_id, root_id, 'Late night')])
        self.assertEqual(self.api('folders', {'action': 'rename', 'folder_id': root_id, 'name': 'Listening moods'})[0], 200)
        saves = self.api('saves')[1]
        self.assertEqual(saves['folder_tracks'], [{'folder_id': child_id, 'track_id': self.ids[0], 'position': 0}])
        import listening
        self.assertEqual([r['id'] for r in listening.catalog(self.store, 'u1')], [self.ids[0]])
        self.assertEqual(self.api('folders', {'action': 'remove_track', 'folder_id': child_id, 'track_id': self.ids[0]})[0], 200)
        self.assertEqual(listening.catalog(self.store, 'u1'), [])
        self.assertEqual(self.api('folders', {'action': 'add_track', 'folder_id': child_id, 'track_id': self.ids[0]})[0], 200)

        self.store.conn.execute("INSERT INTO members(id,email,role,display_name) VALUES('u2','other@example.com','member','Other')")
        self.store.conn.commit()
        self.identity = {'id': 'u2', 'email': 'other@example.com'}
        self.assertEqual(self.api('folders')[1]['folders'], [])
        self.assertEqual(self.api('folders', {'action': 'rename', 'folder_id': root_id, 'name': 'stolen'})[0], 404)
        self.assertEqual(self.api('folders', {'action': 'add_track', 'folder_id': child_id, 'track_id': self.ids[0]})[0], 404)
        self.assertEqual(listening.catalog(self.store, 'u2'), [])

        self.identity = {'id': 'u1', 'email': 'friend@example.com'}
        self.assertEqual(self.api('folders', {'action': 'delete', 'folder_id': root_id})[0], 200)
        self.assertEqual(self.api('folders')[1]['folders'], [])
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM listening_folder_tracks WHERE member_id=\'u1\'').fetchone()[0], 0)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM tracks WHERE id=?', (self.ids[0],)).fetchone()[0], 1)
        self.assertTrue(Path(self.store.conn.execute('SELECT file FROM tracks WHERE id=?', (self.ids[0],)).fetchone()[0]).is_file())

    def test_json_xml_core_and_range(self):
        self.save('track', self.ids[0])
        self.save('playlist', self.pid)
        for endpoint in ('ping', 'getLicense', 'getMusicFolders', 'getArtists', 'getPlaylists', 'search3', 'getAlbumList2'):
            self.assertEqual(self.response(endpoint)['status'], 'ok', endpoint)
        s = self.response('getSong', id=self.ids[0])['song']
        self.assertEqual(self.response('getArtist', id=s['artistId'])['artist']['albumCount'], 1)
        self.assertEqual(self.response('getAlbum', id=s['albumId'])['album']['songCount'], 1)
        q = urlencode(dict(u=self.device['username'], p=self.device['password'], id=self.ids[0]))
        code, headers, raw = self.request('/rest/getSong.view?'+q)
        root = ET.fromstring(raw)
        self.assertEqual(root.attrib['status'], 'ok')
        self.assertEqual(root.find('{http://subsonic.org/restapi}song').attrib['id'], str(self.ids[0]))
        for endpoint in ('stream', 'download'):
            code, headers, raw = self.request('/rest/'+endpoint+'.view?'+q, headers={'Range': 'bytes=4-19'})
            self.assertEqual(code, 206)
            self.assertEqual(raw, self.media[4:20])
            self.assertEqual(headers['Content-Range'], f'bytes 4-19/{len(self.media)}')
        self.assertEqual(self.rest('download', id=self.ids[0])[2], self.media)
        self.assertEqual(self.response('getAlbumList2', size='bad')['status'], 'failed')
        self.assertEqual(self.response('getCoverArt', id='http://127.0.0.1')['error']['code'], 70)

    def test_two_users_revocation_and_membership_rechecked(self):
        self.save('track', self.ids[0])
        first = self.device
        self.identity = dict(self.identity, id='second', email='second@example.com')

        self.sec.schema(self.store)
        self.store.conn.execute("INSERT INTO members(id,email,role) VALUES('second','','pending')")
        self.store.conn.commit()
        with self.assertRaises(harness.Denied):
            self.sec.member(self.store, self.identity)
        self.store.conn.execute("UPDATE members SET role='member' WHERE id='second'")
        self.store.conn.commit()
        second = self.api('devices', {'label': 'Other'})[1]
        self.assertEqual(self.api('saves')[1], {'tracks': [], 'playlists': [], 'playlist_tracks': [], 'folder_tracks': []})
        self.assertEqual(json.loads(self.rest('search3', device=second)[2])['subsonic-response']['searchResult3']['song'], [])
        self.api('devices/revoke', {'id': first['username']})
        self.assertEqual(self.response('ping')['status'], 'ok')
        self.api('devices/revoke', {'id': second['username']})
        self.assertEqual(json.loads(self.rest('ping', device=second)[2])['subsonic-response']['error']['code'], 40)
        for role in ('pending', 'blocked'):
            self.store.conn.execute('UPDATE members SET role=? WHERE id=?', (role, 'u1'))
            self.store.conn.commit()
            self.assertEqual(self.response('ping')['error']['code'], 40)
        digest = self.store.conn.execute('SELECT digest FROM listening_devices WHERE id=?', (first['username'],)).fetchone()[0]
        self.assertEqual(digest, hashlib.sha256(first['password'].encode()).hexdigest())
        self.assertNotIn('password', self.api('devices')[1])

    def test_unavailable_media_filters_and_credentials_never_logged(self):
        from contextlib import redirect_stdout
        self.save('track', self.ids[0])
        self.save('playlist', self.pid)
        full = self.response('getSong', id=self.ids[0])['song']
        other = self.response('getSong', id=self.ids[1])['song']
        self.save('playlist', self.pid, False)
        for endpoint, identity in [('getArtist', other['artistId']), ('getAlbum', other['albumId']), ('getPlaylist', self.pid)]:
            self.assertEqual(self.response(endpoint, id=identity)['error']['code'], 70)
        output = io.StringIO()
        with redirect_stdout(output):
            self.response('ping')
            self.rest('ping', device={'username': self.device['username'], 'password': 'wrong-secret'})
        self.assertNotIn(self.device['password'], output.getvalue())
        self.assertNotIn('wrong-secret', output.getvalue())
        self.assertEqual(self.response('search3', songCount=0)['searchResult3']['song'], [])
        for filename in ('', str(Path(self.tmp.name)/'outside.wav')):
            Path(self.tmp.name, 'outside.wav').write_bytes(self.media)
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (filename, self.ids[0]))
            self.store.conn.commit()
            self.assertEqual(self.response('stream', id=self.ids[0])['error']['code'], 70)
        # Device secrets are not accepted as email sessions; listing omits digest.
        listing = self.api('devices')[1]
        self.assertNotIn('digest', str(listing))
        self.assertNotIn(self.device['password'], str(listing))
        self.assertEqual(self.response('ping', device=dict(self.device, password='enc:'+self.device['password'].encode().hex()))['status'], 'ok')

    def test_browser_auth_csrf_local_mode_device_is_not_browser_session(self):
        for mode in ('0', '1'):
            os.environ['HARDLYLISTENING_AUTH_REQUIRED'] = mode
            self.assertEqual(self.api('devices', headers={})[0], 401)
            self.assertEqual(self.api('saves', {'kind': 'track', 'id': self.ids[0], 'saved': True}, headers={'Authorization': 'Bearer fixture'})[0], 403)
            self.assertEqual(self.response('ping')['status'], 'ok')
        with patch.object(self.sec, 'authenticate', side_effect=harness.Denied(401, 'Invalid session')):
            self.assertEqual(self.request('/api/listening/devices', headers={'Authorization': 'Bearer '+self.device['password']})[0], 401)
        self.assertEqual(self.request('/rest/ping.view?f=json')[0], 200)
        self.assertEqual(json.loads(self.request('/rest/ping.view?f=json')[2])['subsonic-response']['error']['code'], 40)
        q = urlencode(dict(u=self.device['username'], p=self.device['password'], f='json'))
        code, _, raw = self.request('/rest/ping.view', 'POST', {'Content-Type': 'application/x-www-form-urlencoded'}, q)
        self.assertEqual(json.loads(raw)['subsonic-response']['status'], 'ok')
