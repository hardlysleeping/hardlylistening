import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from db import Store
from library.playlist_files import folder, MARKER
from library.import_files import move_library


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='playlist-test-', dir=os.environ.get('TMPDIR'))
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / 'test.db')
        self.root = self.base / 'music'
        self.store.set_settings({'root': str(self.root)})
        self.p1 = self.store.playlist('Café\n/ mix', 'test', '')
        self.p2 = self.store.playlist('Café\n/ mix', 'test', '')
        self.t = self.store.track('test', 'one', 'Chanson\n#EXTINF:injected', artist_name='Björk')
        self.store.add_to_playlist(self.p1, self.t, 1)
        self.store.add_to_playlist(self.p2, self.t, 1)
        self.job = self.store.enqueue(self.t, 'test')

    def tearDown(self):
        with self.store.lock:
            self.store._projection_timer.cancel()
        self.store._projection_timer.join()
        self.store.conn.close()
        self.tmp.cleanup()

    def directory(self, pid):
        return self.root / 'Playlists' / folder('Café\n/ mix', pid)

    def sync(self):
        self.store.sync_playlists()
        self.assertEqual(self.store.projection_error, '')

    def audio(self):
        path = self.root / 'Björk' / 'Album' / '01 - Chanson.wav'
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.1', str(path)], check=True)
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(path), self.t))
            self.store.conn.commit()
        self.store.update_job(self.job, status='completed')
        return path

    def test_pending_states_manifest_and_no_fake_audio(self):
        for status in ('queued', 'downloading', 'review', 'failed', 'cancelled'):
            self.store.update_job(self.job, status=status)
            self.sync()
            self.assertIn(f'[{status}]', (self.directory(self.p1) / 'playlist.txt').read_text())
            self.assertEqual((self.directory(self.p1) / 'playlist.m3u8').read_text(), '#EXTM3U\n')
            self.assertFalse(self.store.library(include_pending=True)[0]['playable'])
        self.assertEqual(self.store.library(), [])
        self.assertEqual(list(self.root.rglob('*.wav')), [])

    def test_one_recording_two_playlists_relative_and_unicode(self):
        audio = self.audio()
        self.sync()
        self.assertNotEqual(self.directory(self.p1), self.directory(self.p2))
        for pid in (self.p1, self.p2):
            text = (self.directory(pid) / 'playlist.m3u8').read_text()
            self.assertIn('Björk', text)
            self.assertEqual(text.count('#EXTINF:'), 2)  # injected text is inline, never a directive
            entries = [x for x in text.splitlines() if not x.startswith('#')]
            self.assertEqual(len(entries), 1)
            self.assertFalse(Path(entries[0]).is_absolute())
            self.assertEqual((self.directory(pid) / entries[0]).resolve(), audio.resolve())
        self.assertEqual(len(list(self.root.rglob('*.wav'))), 1)
        self.assertEqual(len(self.store.library()[0]['playlists']), 2)
        before = (self.directory(self.p1) / 'playlist.txt').stat().st_mtime_ns
        self.sync()
        self.assertEqual(before, (self.directory(self.p1) / 'playlist.txt').stat().st_mtime_ns)

    def test_root_move_and_safe_removal(self):
        self.audio()
        self.sync()
        old = self.directory(self.p1)
        (old / 'keep.txt').write_text('user data')
        target = self.base / 'new-root'
        result = move_library(self.store, str(target))
        self.assertEqual(result['moved'], 1)
        self.root = target
        self.assertTrue((old / 'keep.txt').exists())
        self.assertFalse((old / 'playlist.m3u8').exists())
        entry = (self.directory(self.p1) / 'playlist.m3u8').read_text().splitlines()[-1]
        self.assertTrue((self.directory(self.p1) / entry).is_file())
        unmanaged = target / 'Playlists' / 'mine'
        unmanaged.mkdir()
        (unmanaged / 'playlist.txt').write_text('mine')
        directory = self.directory(self.p1)
        (directory / 'keep.txt').write_text('keep')
        with self.store.lock:
            self.store.conn.execute('DELETE FROM playlists WHERE id=?', (self.p1,))
            self.store.conn.commit()
        self.sync()
        self.assertFalse((directory / 'playlist.m3u8').exists())
        self.assertTrue((directory / 'keep.txt').exists())
        self.assertTrue((unmanaged / 'playlist.txt').exists())
        self.assertEqual(len(list(target.rglob('*.wav'))), 1)

    def test_automatic_status_membership_sync_and_progress_is_clean(self):
        time.sleep(.4)
        self.assertTrue((self.directory(self.p1) / 'playlist.txt').exists())
        revision = self.store._projection_revision
        self.store.update_job(self.job, progress=50)
        self.assertEqual(self.store.conn.execute('SELECT n FROM projection_revision').fetchone()[0], revision)
        self.store.update_job(self.job, status='review')
        time.sleep(.4)
        self.assertIn('[review]', (self.directory(self.p1) / 'playlist.txt').read_text())
        with self.store.lock:
            self.store.conn.execute('DELETE FROM playlist_tracks WHERE playlist_id=?', (self.p1,))
            self.store.conn.commit()
        time.sleep(.4)
        self.assertNotIn('Chanson', (self.directory(self.p1) / 'playlist.txt').read_text())

    def test_missing_file_and_newline_path_not_exported(self):
        audio = self.audio()
        strange = audio.with_name('bad\nname.wav')
        audio.rename(strange)
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(strange), self.t))
            self.store.conn.commit()
        self.sync()
        self.assertEqual((self.directory(self.p1) / 'playlist.m3u8').read_text(), '#EXTM3U\n')
        strange.unlink()
        self.sync()
        self.assertIn('[missing]', (self.directory(self.p1) / 'playlist.txt').read_text())


if __name__ == '__main__':
    unittest.main()
