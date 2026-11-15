"""A ZIP a DJ application can open, even when the library is stored as Opus.

The archive is a delivery copy: an Opus track is converted to MP3 on the way in,
the stored file is left exactly as it was.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

import zip_export


def _have_encoders() -> bool:
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        return False
    out = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'],
                         capture_output=True, text=True).stdout
    return 'libopus' in out and 'libmp3lame' in out


@unittest.skipUnless(_have_encoders(), 'ffmpeg with libopus and libmp3lame is required')
class DjSafeArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or '/tmp')
        self.root = Path(self.tmp.name)
        self.opus = self.root / 'song.opus'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=0.4', '-c:a', 'libopus', str(self.opus)],
                       check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, tracks):
        import listening
        store = SimpleNamespace(conn=None)
        original = listening.catalog
        listening.catalog = lambda store, uid: tracks
        try:
            return zip_export.build(store, uid=1)
        finally:
            listening.catalog = original

    def test_opus_is_delivered_as_mp3_and_the_stored_file_is_untouched(self):
        before = self.opus.read_bytes()
        track = {'id': 1, 'title': 'Song', 'artist_name': 'Artist', 'album': 'Album',
                 'track_number': 1, 'duration': 12, 'file': str(self.opus)}
        path, name, count = self.build([track])
        self.assertEqual(count, 1)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            body = archive.read(names[0])
            manifest = archive.read('library.m3u8').decode()
        self.assertTrue(names[0].endswith('.mp3'), names)
        self.assertNotIn('.opus', ' '.join(names))
        self.assertIn('.mp3', manifest)
        # It really is MP3, not an Opus file wearing a .mp3 name.
        check = Path(self.tmp.name) / 'check.mp3'
        check.write_bytes(body)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                '-show_entries', 'stream=codec_name', '-of', 'csv=p=0',
                                str(check)], capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), 'mp3')
        # The library copy keeps its original bytes: export must not transcode in place.
        self.assertEqual(self.opus.read_bytes(), before)

    def test_an_mp3_is_copied_byte_for_byte(self):
        mp3 = self.root / 'plain.mp3'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=0.4', '-c:a', 'libmp3lame', str(mp3)],
                       check=True)
        track = {'id': 2, 'title': 'Plain', 'artist_name': 'Artist', 'album': 'Album',
                 'track_number': 0, 'duration': 5, 'file': str(mp3)}
        path, _, _ = self.build([track])
        with zipfile.ZipFile(path) as archive:
            body = archive.read(archive.namelist()[0])
        self.assertEqual(body, mp3.read_bytes())


class RemoteLibrary(unittest.TestCase):
    """A track whose bytes are only in the bucket still exports: export is a delivery job.

    The failure this pins: the local file was gone, so the archive silently shipped one track
    fewer with a note that it was "unavailable". Nothing about that archive looked wrong.
    """

    def setUp(self):
        import db
        from library import storage
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or '/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'music'
        (self.root / 'Artist').mkdir(parents=True)
        patcher = mock.patch.object(db, 'DB_PATH', os.path.join(self.tmp.name, 'd.db'))
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': 'K',
                                           'HARDLYLISTENING_S3_SECRET_KEY': 'S'})
        env.start()
        self.addCleanup(env.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({'root': str(self.root)})
        self.workspace = Path(self.tmp.name) / 'cache' / 'materialize'
        anchor = mock.patch.object(storage.paths, 'MATERIALIZE_DIR', self.workspace)
        anchor.start()
        self.addCleanup(anchor.stop)

    def test_a_remote_only_track_is_in_the_archive_and_the_workspace_is_clean(self):
        import db  # noqa: F401 — the same module the fixture patched
        import listening
        from library import storage, uploads
        from test_storage import BucketServer

        audio = b'ID3' + bytes(range(64)) * 40
        path = self.root / 'Artist' / 'remote.mp3'
        path.write_bytes(audio)
        track_id = self.store.track('local', 'remote', 'Remote', artist_name='Artist',
                                    album='Album', duration=3)
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(path), track_id))
            self.store.conn.commit()
        track = dict(self.store.conn.execute('SELECT * FROM tracks WHERE id=?',
                                            (track_id,)).fetchone())
        with BucketServer() as server:
            self.store.set_settings({'storage_mode': 's3', 'storage_bucket': 'music',
                                     'storage_endpoint': server.endpoint,
                                     'storage_region': 'us-east-1'})
            uploads.migrate(self.store)
            uploads.tick(self.store)
            path.unlink()
            original = listening.catalog
            listening.catalog = lambda store, uid: [track]
            try:
                name, _, count = zip_export.build(self.store, uid=1)
            finally:
                listening.catalog = original
            with zipfile.ZipFile(name) as archive:
                body = archive.read(archive.namelist()[0])
                manifest = archive.read('library.m3u8').decode()
            self.assertEqual(count, 1, 'the remote-only track still ships')
            self.assertEqual(body, audio, 'the object bytes are exactly what went in')
            self.assertIn('Artist/Album/Remote.mp3', manifest)
        self.assertFalse(self.workspace.is_dir() and any(self.workspace.iterdir()),
                         'the workspace copy is released once the archive is written')
        self.assertFalse(path.exists(), 'export never writes into the library root')


class ArchivedName(unittest.TestCase):
    def test_dj_safe_formats_keep_their_name(self):
        for suffix in ('.mp3', '.m4a', '.aac', '.wav'):
            self.assertEqual(zip_export._archived_name(Path('a' + suffix)).suffix, suffix)

    def test_streaming_formats_become_mp3(self):
        for suffix in ('.opus', '.ogg', '.flac', '.webm'):
            self.assertEqual(zip_export._archived_name(Path('a' + suffix)).suffix, '.mp3')


if __name__ == '__main__':
    unittest.main()
