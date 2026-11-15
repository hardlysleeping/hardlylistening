"""Downloading from the Feed must end in the library — but only once the file is
really there. A queued row would list a song that cannot play yet; a failed one
would list a song that never will."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('TMPDIR', '/tmp')


class FeedDownloadSavesToLibrary(unittest.TestCase):
    def setUp(self):
        import db
        import listening
        self.db, self.listening = db, listening
        self.dir = Path(tempfile.mkdtemp(dir='/tmp'))
        (self.dir / 'media').mkdir()
        self.store = db.Store(self.dir / 'lib.db')
        self.store.set_settings({'root': str(self.dir / 'media')})
        listening.schema(self.store, __import__('security').SECURITY)
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role) VALUES('m1','','member')")
            self.store.conn.commit()
        self.playlist = self.store.playlist('Release', 'apple', 'https://x/release')
        self.on_disk = self.track('on-disk', 'Have It')
        self.pending = self.track('pending', 'Coming')
        self.dead = self.track('dead', 'Never')

    def track(self, track_id, title):
        written = self.dir / 'media' / f'{track_id}.m4a'
        if track_id == 'on-disk':
            written.write_bytes(b'audio')
        row = self.store.track('apple', track_id, title, artist_name='Someone',
                               artist_id=None, album='Release',
                               source_url=f'https://x/{track_id}')
        path = str(written) if track_id == 'on-disk' else ''
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?", (path, row))
            self.store.conn.commit()
        return row

    def saves(self):
        with self.store.lock:
            return [r[0] for r in self.store.conn.execute(
                'SELECT track_id FROM listening_tracks WHERE member_id=? ORDER BY track_id',
                ('m1',))]

    def intents(self):
        with self.store.lock:
            return [r[0] for r in self.store.conn.execute(
                'SELECT track_id FROM library_intents ORDER BY track_id')]

    def test_download_claims_the_release(self):
        from library.browser_downloads import local_path
        for position, track_id in enumerate((self.on_disk, self.pending, self.dead), start=1):
            self.store.add_to_playlist(self.playlist, track_id, position)

        self.listening.want_playlist(self.store, 'm1', self.playlist)
        # What the repository already holds is saved at once; the rest waits.
        self.assertEqual(self.saves(), [self.on_disk])
        self.assertEqual(self.intents(), [self.pending, self.dead])

        # The worker wrote the file, so it joins the library now.
        (self.dir / 'media' / 'pending.m4a').write_bytes(b'audio')
        (self.dir / 'media' / 'dead.m4a').write_bytes(b'audio')
        with self.store.lock:
            self.store.conn.execute(
                "UPDATE tracks SET file=? WHERE id=?",
                (str(self.dir / 'media' / 'pending.m4a'), self.pending))
            self.store.conn.execute(
                "UPDATE tracks SET file=? WHERE id=?",
                (str(self.dir / 'media' / 'dead.m4a'), self.dead))
            self.store.conn.commit()
        self.assertIsNotNone(local_path(self.store, self.store.track_by_id(self.pending)))

        self.assertEqual(self.listening.deliver_track(self.store, self.pending), 1)
        self.assertEqual(self.saves(), [self.on_disk, self.pending])
        self.assertEqual(self.intents(), [self.dead])   # a promise is not repeated

        # A download that dies leaves nothing behind in the library.
        self.listening.forget_track(self.store, self.dead)
        self.assertEqual(self.saves(), [self.on_disk, self.pending])
        self.assertEqual(self.intents(), [])

    def test_two_people_wanting_the_same_song_both_get_it(self):
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role) VALUES('m2','','member')")
            self.store.conn.commit()
        self.store.add_to_playlist(self.playlist, self.pending, 1)
        self.listening.want_playlist(self.store, 'm1', self.playlist)
        self.listening.want_playlist(self.store, 'm2', self.playlist)
        self.listening.deliver_track(self.store, self.pending)
        for member in ('m1', 'm2'):
            with self.store.lock:
                row = self.store.conn.execute(
                    'SELECT 1 FROM listening_tracks WHERE member_id=? AND track_id=?',
                    (member, self.pending)).fetchone()
            self.assertTrue(row, member)

    def test_anonymous_click_saves_nothing(self):
        self.store.add_to_playlist(self.playlist, self.pending, 1)
        self.assertEqual(self.listening.want_playlist(self.store, '', self.playlist), 0)
        self.assertEqual(self.intents(), [])


if __name__ == '__main__':
    unittest.main()