"""The per-playlist restore file: contract, zip copy, HTTP route."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import urlopen

from db import Store
from library import playlist_backup
from library.browser_downloads import archive, selection
import server

APPLE_URL = "https://music.apple.com/us/playlist/driving-night/pl.u-WabZoqauWJDdV3"


class BackupContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='playlist-backup-', dir=os.environ.get('TMPDIR'))
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'fixture.db')
        self.store.set_settings({'root': str(self.base / 'music')})
        self.playlist = self.store.playlist('Driving', 'apple', APPLE_URL)
        self.first = self.store.track('apple', '1440833084', 'One', artist_name='Artist A', album='Album', duration=213.0)
        self.second = self.store.track('apple', '1440857781', 'Two', artist_name='Artist B', duration=None)
        # Deliberately added out of order: position, not insertion, is the order.
        self.store.add_to_playlist(self.playlist, self.second, 1)
        self.store.add_to_playlist(self.playlist, self.first, 0)

    def tearDown(self):
        with self.store.lock:
            self.store._projection_timer.cancel()
        self.store._projection_timer.join()
        self.store.conn.close()
        self.tmp.cleanup()

    def test_contract_shape(self):
        doc = playlist_backup.payload(self.store, self.playlist)
        self.assertEqual(doc['format'], 'hardlylistening-playlist')
        self.assertEqual(doc['version'], 1)
        self.assertEqual(set(doc), {'format', 'version', 'backup', 'playlist', 'tracks'})
        self.assertEqual(set(doc['backup']), {'id', 'created_at', 'app_version'})
        self.assertTrue(doc['backup']['created_at'].endswith('Z'))
        self.assertEqual(set(doc['playlist']),
                         {'title', 'provider', 'provider_playlist_id', 'source_url', 'storefront'})
        self.assertEqual(doc['playlist']['title'], 'Driving')
        self.assertEqual(doc['playlist']['provider'], 'apple_music')      # db says "apple"
        self.assertEqual(doc['playlist']['storefront'], 'us')
        self.assertEqual(doc['playlist']['provider_playlist_id'], 'pl.u-WabZoqauWJDdV3')
        self.assertEqual(doc['playlist']['source_url'], APPLE_URL)

    def test_tracks_are_ordered_by_position_and_ids_are_strings(self):
        tracks = playlist_backup.payload(self.store, self.playlist)['tracks']
        self.assertEqual([t['position'] for t in tracks], [0, 1])
        self.assertEqual([t['provider_track_id'] for t in tracks], ['1440833084', '1440857781'])
        self.assertTrue(all(isinstance(t['provider_track_id'], str) for t in tracks))
        self.assertEqual(tracks[0]['provider'], 'apple_music')
        self.assertEqual(tracks[0]['duration_ms'], 213000)
        self.assertIsNone(tracks[1]['duration_ms'])
        self.assertIsNone(tracks[1]['album'])
        self.assertEqual(set(tracks[0]), {'position', 'provider', 'provider_track_id', 'title', 'artist', 'album', 'duration_ms'})

    def test_duplicate_positions_are_rejected(self):
        with self.store.lock:
            self.store.conn.execute('UPDATE playlist_tracks SET position=0')
            self.store.conn.commit()
        with self.assertRaises(ValueError):
            playlist_backup.payload(self.store, self.playlist)

    def test_file_name_and_bytes(self):
        self.assertEqual(playlist_backup.filename(self.store, self.playlist), 'Driving.hardlyplaylist.json')
        body = playlist_backup.dumps(self.store, self.playlist)
        self.assertTrue(body.endswith(b'\n'))
        self.assertEqual(json.loads(body)['playlist']['title'], 'Driving')
        # Anything but the generated id and timestamp is stable between two exports.
        first, second = json.loads(playlist_backup.dumps(self.store, self.playlist)), json.loads(body)
        first['backup'].pop('id'), first['backup'].pop('created_at')
        second['backup'].pop('id'), second['backup'].pop('created_at')
        self.assertEqual(first, second)

    def test_unknown_playlist_is_an_error(self):
        with self.assertRaises(ValueError):
            playlist_backup.payload(self.store, self.playlist + 999)

    def test_other_providers_keep_their_own_name(self):
        pid = self.store.playlist('Set', 'soundcloud', 'https://soundcloud.com/x/sets/y')
        tid = self.store.track('soundcloud', 'abc123', 'Track', artist_name='A')
        self.store.add_to_playlist(pid, tid, 0)
        doc = playlist_backup.payload(self.store, pid)
        self.assertEqual(doc['playlist']['provider'], 'soundcloud')
        self.assertIsNone(doc['tracks'][0]['duration_ms'])


class ZipCarriesAudioAndPlaylist(unittest.TestCase):
    def test_archive_has_no_restore_sidecar(self):
        with tempfile.TemporaryDirectory(prefix='backup-zip-', dir=os.environ.get('TMPDIR')) as tmp:
            store = Store(Path(tmp) / 'fixture.db')
            store.set_settings({'root': str(Path(tmp) / 'music')})
            pid = store.playlist('Driving', 'apple', APPLE_URL)
            tid = store.track('apple', '1440833084', 'One', artist_name='A')
            store.add_to_playlist(pid, tid, 0)
            music = Path(tmp) / 'music'
            music.mkdir()
            media = music / 'one.mp3'
            media.write_bytes(b'audio')
            with store.lock:
                store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(media), tid))
                store.conn.commit()
            _title, tracks = selection(store, 'playlist', pid)
            with archive(store, tracks, _title) as handle:
                payload = handle.read()
            archive_zip = zipfile.ZipFile(io.BytesIO(payload))
            names = archive_zip.namelist()
            # The restore file is its own button; the ZIP is audio, the manifest and the
            # playlist that indexes the audio.
            self.assertNotIn('apple-music.json', names)
            self.assertIn('MANIFEST.txt', names)
            self.assertIn('Driving.m3u8', names)
            playlist = archive_zip.read('Driving.m3u8').decode().splitlines()
            self.assertEqual(playlist[0], '#EXTM3U')
            self.assertEqual(playlist[1], '#PLAYLIST:Driving')
            self.assertEqual(playlist[2], '#EXTINF:-1,A - One')
            audio = next(name for name in names if name.endswith('.mp3'))
            # The entry is the archive's own path, so the folder plays where it is unpacked.
            self.assertEqual(playlist[3], audio)
            with store.lock:
                store._projection_timer.cancel()
            store._projection_timer.join()
            store.conn.close()


class BackupRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='backup-http-', dir=os.environ.get('TMPDIR'))
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'fixture.db')
        self.store.set_settings({'root': str(self.base / 'music')})
        (self.base / 'music').mkdir()
        self.pid = self.store.playlist('Driving', 'apple', APPLE_URL)
        for i, (tid, title) in enumerate((('1440833084', 'One'), ('1440857781', 'Two'))):
            self.store.add_to_playlist(self.pid, self.store.track('apple', tid, title, artist_name='A'), i)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.env = patch.dict(os.environ, {'HARDLYLISTENING_AUTH_REQUIRED': '0'})
        self.boot = patch.object(server, 'boot', return_value=(self.store, None))

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        with self.store.lock:
            self.store._projection_timer.cancel()
        self.store._projection_timer.join()
        self.store.conn.close()
        self.tmp.cleanup()

    def get(self, path):
        with urlopen(f'http://127.0.0.1:{self.http.server_port}{path}') as response:
            return response, response.read()

    def test_route_serves_the_contract_as_a_download(self):
        self.env.start(), self.boot.start(), self.thread.start()
        try:
            response, body = self.get(f'/api/playlist/{self.pid}/apple-backup')
            self.assertEqual(response.status, 200)
            self.assertIn('application/json', response.headers['Content-Type'])
            self.assertIn('Driving.hardlyplaylist.json', response.headers['Content-Disposition'])
            doc = json.loads(body)
            self.assertEqual(doc['format'], 'hardlylistening-playlist')
            self.assertEqual([t['provider_track_id'] for t in doc['tracks']], ['1440833084', '1440857781'])
        finally:
            self.env.stop(), self.boot.stop()

    def test_missing_playlist_is_404(self):
        self.env.start(), self.boot.start(), self.thread.start()
        try:
            with self.assertRaises(Exception) as caught:
                self.get('/api/playlist/999999/apple-backup')
            self.assertIn('404', str(caught.exception))
        finally:
            self.env.stop(), self.boot.stop()


if __name__ == '__main__':
    unittest.main()
