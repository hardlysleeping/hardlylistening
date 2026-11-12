"""Playback facade tests: whole files, HTTP byte ranges, and missing media."""
import tempfile
import unittest
from pathlib import Path

from db import Store
from library import playback


class PlaybackPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / 'music'
        root.mkdir()
        self.store = Store(Path(self.tmp.name) / 'test.db')
        self.store.set_settings({'root': str(root)})
        self.data = b'0123456789abcdef'
        path = root / 'fixture.bin'
        path.write_bytes(self.data)
        self.track_id = self.store.track('local', 'fixture', 'Fixture', artist_name='Test', album='Test', source_url='')
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(path), self.track_id))
            self.store.conn.commit()

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_whole_file_is_200(self):
        stream = playback.plan(self.store, self.track_id)
        self.assertEqual(stream.status, 200)
        self.assertEqual(stream.length, len(self.data))
        self.assertEqual(b''.join(stream.chunks()), self.data)

    def test_open_ended_and_suffix_ranges_are_206(self):
        open_ended = playback.plan(self.store, self.track_id, 'bytes=4-')
        self.assertEqual((open_ended.status, open_ended.content_range), (206, 'bytes 4-15/16'))
        self.assertEqual(b''.join(open_ended.chunks()), self.data[4:])
        suffix = playback.plan(self.store, self.track_id, 'bytes=-5')
        self.assertEqual((suffix.status, suffix.content_range), (206, 'bytes 11-15/16'))
        self.assertEqual(b''.join(suffix.chunks()), self.data[-5:])

    def test_range_past_end_is_416(self):
        stream = playback.plan(self.store, self.track_id, 'bytes=16-')
        self.assertEqual(stream.status, 416)
        self.assertEqual(stream.content_range, 'bytes */16')
        self.assertEqual(stream.length, 0)
        self.assertEqual(list(stream.chunks()), [])

    def test_missing_track_or_file_is_none(self):
        self.assertIsNone(playback.plan(self.store, 999999))
        track = self.store.track_by_id(self.track_id)
        Path(track['file']).unlink()
        self.assertIsNone(playback.plan(self.store, self.track_id))


if __name__ == '__main__':
    unittest.main()
