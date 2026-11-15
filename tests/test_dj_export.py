# Export smoke tests: a DJ app must be able to open what we hand it.
import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
import test_security as security_tests
from library import dj_export


class DJExportHTTP(unittest.TestCase):
    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request
    token = security_tests.SecurityHTTP.token
    user = security_tests.SecurityHTTP.user

    def setUp(self):
        security_tests.SecurityHTTP.setUp(self)
        # Export is a library read: it needs the same approved membership as any
        # other API call, so the fixture signs in like a real member would.
        self.headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}
        self.identity = self.user()
        self.root = Path(self.tmp.name) / 'media'
        self.root.mkdir()
        self.store.set_settings({'root': str(self.root)})
        self.files = {}
        rows = [('Daft Punk', 'Discovery', 'One More Time', 320, 'a.mp3'),
                ('Daft Punk', 'Discovery', 'Digital Love', 300, 'b.mp3'),
                ('Ampersand & Co', 'Say "Hi" <now>', 'Café', 180, 'c d.mp3')]
        with self.store.lock:
            for artist, album, title, duration, name in rows:
                path = self.root / name
                path.write_bytes(b'\xff\xfb\x00' * 64)
                cursor = self.store.conn.execute(
                    'INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,'
                    'duration,file,added) VALUES(?,?,?,?,?,?,?,0)',
                    ('local', name, title, artist, album, duration, str(path)))
                self.files[title] = (path, cursor.lastrowid)
            gone = self.root / 'gone.mp3'
            self.store.conn.execute(
                'INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,'
                'duration,file,added) VALUES(?,?,?,?,?,?,?,0)',
                ('local', 'gone', 'Deleted', 'Nobody', 'Nowhere', 100, str(gone)))
            self.store.conn.execute("INSERT INTO playlists(title,added) VALUES('Mix',0)")
            self.store.conn.execute("INSERT INTO playlists(title,added) VALUES('Empty',1)")
            for position, title in enumerate(['One More Time', 'Café']):
                self.store.conn.execute(
                    'INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES(1,?,?)',
                    (self.files[title][1], position))
            self.store.conn.commit()

    def get(self, path):
        status, _, body = self.request(path, 'GET', self.headers)
        return status, body.decode('utf-8')

    def test_m3u8_lists_existing_files_only(self):
        status, body = self.get('/api/library/export?format=m3u8')
        self.assertEqual(status, 200)
        lines = body.splitlines()
        self.assertEqual(lines[0], '#EXTM3U')
        self.assertIn('#EXTINF:320,Daft Punk - One More Time', lines)
        self.assertTrue(any(line.endswith('a.mp3') for line in lines))
        self.assertNotIn('Deleted', body)
        self.assertEqual(sum(1 for line in lines if line.startswith('#EXTINF')), 3)

    def test_playlist_scope_and_unknown_playlist(self):
        status, body = self.get('/api/library/export?format=m3u8&playlist=1')
        self.assertEqual(status, 200)
        self.assertIn('#PLAYLIST:Mix', body)
        self.assertIn('Café', body)
        self.assertNotIn('Digital Love', body)
        self.assertEqual(self.request('/api/library/export?format=m3u8&playlist=999', 'GET', self.headers)[0], 400)
        self.assertEqual(self.request('/api/library/export?format=m3u8&playlist=abc', 'GET', self.headers)[0], 400)

    def test_rekordbox_xml_is_valid_and_escaped(self):
        status, body = self.get('/api/library/export?format=rekordbox')
        self.assertEqual(status, 200)
        # Parsing our own generated document in a test, never untrusted input.
        root = ET.fromstring(body)
        self.assertEqual(root.tag, 'DJ_PLAYLISTS')
        collection = root.find('COLLECTION')
        self.assertEqual(collection.get('Entries'), '3')
        names = {track.get('Name') for track in collection.findall('TRACK')}
        self.assertIn('Café', names)
        self.assertEqual({track.get('Artist') for track in collection.findall('TRACK')},
                         {'Daft Punk', 'Ampersand & Co'})
        locations = [track.get('Location') for track in collection.findall('TRACK')]
        self.assertTrue(all(location.startswith('file://localhost/') for location in locations))
        self.assertTrue(any('%20' in location for location in locations))
        node = root.find('PLAYLISTS/NODE/NODE')
        self.assertEqual(node.get('Name'), 'Mix')
        self.assertEqual(node.get('Entries'), '2')
        keys = [entry.get('Key') for entry in node.findall('TRACK')]
        collection_ids = {track.get('TrackID') for track in collection.findall('TRACK')}
        self.assertTrue(set(keys) <= collection_ids)
        # Empty playlists still appear, so a DJ app shows the folder structure.
        self.assertEqual(len(root.find('PLAYLISTS/NODE').findall('NODE')), 2)

    def test_bad_format_rejected(self):
        with self.assertRaises(ValueError):
            dj_export.build(self.store, 'csv')
        self.assertEqual(self.request('/api/library/export?format=csv', 'GET', self.headers)[0], 400)
        self.assertEqual(self.request('/api/library/export?format=csv')[0], 401)

    def test_export_is_not_served_as_audio(self):
        status, headers, _ = self.request('/api/library/export?format=m3u8', 'GET', self.headers)
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/plain'))
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertNotIn('audio/', headers['Content-Type'])


if __name__ == '__main__':
    unittest.main()
