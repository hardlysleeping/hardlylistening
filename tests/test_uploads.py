"""The bucket mirror: a persistent queue of finished files, drained with retries.

The rules under test are the ones that make this safe to leave running:

* local mode queues nothing at all — a server with no bucket is untouched;
* a file is only offered *after* it exists locally, and the local original is still there after a
  successful upload (mirror, not move);
* the queue is SQLite, so a restart resumes rather than losing the work;
* an upload is only "stored" when the bucket verified it, and a bucket that will not verify is a
  retry, not a success;
* a failure that will never succeed stops after a bounded number of attempts with the reason kept;
* a manifest, an adapter or a bucket that is misconfigured does not burn every row's attempts.

Everything talks to the in-process stub bucket from `tests/test_storage.py`. No credential, bucket
or real file is involved.
"""
import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import db
from library import storage, uploads
from test_storage import BucketServer


CREDENTIAL = {'HARDLYLISTENING_S3_ACCESS_KEY': 'K', 'HARDLYLISTENING_S3_SECRET_KEY': 'S'}


class Mirror(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / 'music'
        (self.root / 'Artist').mkdir(parents=True)
        patcher = mock.patch.object(db, 'DB_PATH', os.path.join(self.dir.name, 'd.db'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = mock.patch.dict(os.environ, CREDENTIAL)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({'root': str(self.root)})

    def configure(self, server):
        self.store.set_settings({'storage_mode': 's3', 'storage_bucket': 'music',
                                 'storage_endpoint': server.endpoint,
                                 'storage_region': 'us-east-1'})

    def file(self, name='song.flac', data=b'flac-bytes' * 100):
        path = self.root / 'Artist' / name
        path.write_bytes(data)
        return path

    # -- local mode ----------------------------------------------------------
    def test_local_mode_queues_nothing(self):
        path = self.file()
        self.assertFalse(uploads.offer(self.store, path))
        self.assertEqual(uploads.summary(self.store),
                         {'pending': 0, 'stored': 0, 'failed': 0, 'missing': 0})
        # And a pass is a no-op even if rows somehow exist.
        self.store.set_settings({})
        self.assertEqual(uploads.tick(self.store)['stored'], 0)

    # -- the happy path ------------------------------------------------------
    def test_a_finished_file_is_mirrored_and_the_original_stays(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            self.assertTrue(uploads.offer(self.store, path))
            self.assertEqual(uploads.summary(self.store)['pending'], 1)

            result = uploads.tick(self.store)
            self.assertEqual(result['stored'], 1)
            self.assertEqual(uploads.summary(self.store)['stored'], 1)

            key = 'music/hardlylistening/Artist/song.flac'
            self.assertIn(key, server.bucket.objects)
            self.assertEqual(server.bucket.objects[key], path.read_bytes())
            # Mirror, not move.
            self.assertTrue(path.is_file(), 'the local original is never deleted')
            self.assertEqual(path.read_bytes(), server.bucket.objects[key])

    def test_offering_the_same_file_twice_does_not_upload_it_twice(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            uploads.offer(self.store, path)
            uploads.tick(self.store)
            requests = len(server.bucket.requests)
            self.assertFalse(uploads.offer(self.store, path),
                             'a stored row is not queued again')
            uploads.tick(self.store)
            self.assertEqual(len(server.bucket.requests), requests)

    def test_a_changed_file_is_queued_again_after_a_failed_row(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            uploads.offer(self.store, path)
            with mock.patch.object(storage.S3Storage, 'put',
                                   side_effect=storage.StorageError('bucket said no')):
                uploads.tick(self.store)
            self.assertEqual(uploads.summary(self.store)['pending'], 1)
            # A fresh offer clears the backoff, so a person who fixed the bucket does not wait.
            self.assertTrue(uploads.offer(self.store, path))
            self.assertEqual(uploads.tick(self.store)['stored'], 1)

    # -- failure behaviour ---------------------------------------------------
    def test_a_failed_upload_retries_with_backoff_and_keeps_the_reason(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            uploads.offer(self.store, path)
            now = time.time()
            with mock.patch.object(storage.S3Storage, 'put',
                                   side_effect=storage.StorageError('503 from bucket')):
                result = uploads.tick(self.store, now=now)
            self.assertEqual(result['retried'], 1)
            row = uploads.due(self.store, now=now + 1)
            self.assertEqual(row, [], 'a failed row is not due again immediately')
            with self.store.lock:
                stored = self.store.conn.execute(
                    'SELECT attempts,next_try,last_error FROM storage_uploads').fetchone()
            self.assertEqual(stored['attempts'], 1)
            self.assertGreater(stored['next_try'], now)
            self.assertIn('503', stored['last_error'])

    def test_it_gives_up_after_a_bounded_number_of_attempts(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            uploads.offer(self.store, path)
            now = time.time()
            with mock.patch.object(storage.S3Storage, 'put',
                                   side_effect=storage.StorageError('nope')):
                for attempt in range(uploads.MAX_TRIES):
                    uploads.tick(self.store, now=now + attempt * 10000)
            self.assertEqual(uploads.summary(self.store)['failed'], 1)
            self.assertEqual(uploads.summary(self.store)['pending'], 0)
            self.assertEqual(uploads.due(self.store, now=now + 10 ** 9), [],
                             'a failed row stops being retried')
            with self.store.lock:
                error = self.store.conn.execute(
                    'SELECT last_error FROM storage_uploads').fetchone()['last_error']
            self.assertIn('nope', error)

    def test_a_bucket_that_does_not_verify_is_not_stored(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            uploads.offer(self.store, path)
            with mock.patch.object(storage.S3Storage, 'verify', return_value=False):
                result = uploads.tick(self.store, now=time.time())
            self.assertEqual((result['stored'], result['retried']), (0, 1))
            self.assertEqual(uploads.summary(self.store)['stored'], 0)

    def test_a_local_file_that_vanished_is_marked_missing_not_retried_forever(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            uploads.offer(self.store, path)
            path.unlink()
            result = uploads.tick(self.store)
            self.assertEqual(result['missing'], 1)
            self.assertEqual(uploads.summary(self.store)['missing'], 1)
            self.assertEqual(server.bucket.requests, [], 'nothing is asked of the bucket')

    def test_a_misconfigured_bucket_does_not_burn_every_row(self):
        self.store.set_settings({'storage_mode': 's3', 'storage_bucket': 'music',
                                 'storage_endpoint': 'https://s3.example'})
        path = self.file()
        with mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': '',
                                          'HARDLYLISTENING_S3_SECRET_KEY': ''}):
            self.store.set_settings({'storage_mode': 's3'})     # offers need the mode only
            uploads.offer(self.store, path)
            uploads.offer(self.store, self.file('other.flac'))
            result = uploads.tick(self.store)
            self.assertIn('HARDLYLISTENING_S3_ACCESS_KEY', result['skipped'])
            self.assertEqual(uploads.summary(self.store)['pending'], 2,
                             'a configuration error is not a per-file failure')

    # -- persistence ---------------------------------------------------------
    def test_the_queue_survives_a_restart(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            uploads.offer(self.store, path)
            # A new process would open the same database and see the row waiting.
            reopened = db.Store(Path(self.dir.name) / 'd.db')
            self.addCleanup(lambda: reopened.conn.close())
            self.assertEqual(uploads.summary(reopened)['pending'], 1)

            with mock.patch.dict(os.environ, CREDENTIAL):
                self.assertEqual(uploads.tick(reopened)['stored'], 1)
            self.assertIn('music/hardlylistening/Artist/song.flac', server.bucket.objects)

    def test_the_key_is_the_stored_path_and_never_leaves_the_prefix(self):
        with BucketServer() as server:
            self.configure(server)
            path = self.file()
            uploads.offer(self.store, path)
            uploads.tick(self.store)
            (method, address, _), = [c for c in server.bucket.requests if c[0] == 'PUT']
            self.assertTrue(address.startswith('music/hardlylistening/Artist/'), address)

    def test_an_offer_never_raises_into_the_download_path(self):
        self.store.set_settings({'storage_mode': 's3', 'storage_bucket': 'music',
                                 'storage_endpoint': 'not a url'})
        self.assertFalse(uploads.offer(self.store, Path(self.dir.name) / 'gone.flac'))


class TheDownloadPath(unittest.TestCase):
    """The queue is only useful if a finished download actually reaches it.

    The hook sits in `Queue._run`, past a real yt-dlp call, so it is driven here with the
    download stubbed: the file appears, the row is offered, and local mode offers nothing.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / 'music'
        self.root.mkdir()
        patcher = mock.patch.object(db, 'DB_PATH', os.path.join(self.dir.name, 'q.db'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({'root': str(self.root), 'pacing': '0'})

    def run_one(self, mode, server=None):
        from engine import queue as queue_module
        settings = {'storage_mode': mode}
        if server is not None:
            settings.update({'storage_bucket': 'music', 'storage_endpoint': server.endpoint,
                             'storage_region': 'us-east-1'})
        self.store.set_settings(settings)
        provider = 'soundcloud'
        tid = self.store.track(provider, 'Hooked', 'Hooked', artist_name='Artist',
                               duration=100, source_url='https://example.test/one')
        job_id = self.store.enqueue(tid, provider, 'https://example.test/one')
        job = self.store.job(job_id) if hasattr(self.store, 'job') else \
            self.store.conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        job = dict(job)
        written = self.root / 'Artist' / 'Hooked.mp3'
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_bytes(b'ID3' + b'\0' * 2048)
        queue = queue_module.Queue(self.store)
        with mock.patch.object(queue_module.ytdlp, 'build_command', return_value=['true']), \
             mock.patch.object(queue_module.ytdlp, 'run', return_value=(0, '')), \
             mock.patch.object(queue_module.ytdlp, 'find_output', return_value=written), \
             mock.patch.object(queue_module.ytdlp, 'verify_audio', return_value=(True, '')), \
             mock.patch.object(queue, '_resolve_source', return_value='https://example.test/x'), \
             mock.patch.object(queue, '_remember_artist_art'), \
             mock.patch.object(queue, '_start_reacquire'):
            queue._run(job)
        return written

    def test_a_completed_download_is_mirrored(self):
        with BucketServer() as server, mock.patch.dict(os.environ, CREDENTIAL):
            written = self.run_one('s3', server)
            self.assertEqual(uploads.summary(self.store)['pending'], 1,
                             'the finished file has to reach the queue')
            self.assertEqual(uploads.tick(self.store)['stored'], 1)
            self.assertIn('music/hardlylistening/Artist/Hooked.mp3', server.bucket.objects)
            self.assertTrue(written.is_file())

    def test_local_mode_queues_nothing_from_a_download(self):
        self.run_one('local')
        self.assertEqual(uploads.summary(self.store)['pending'], 0)


if __name__ == '__main__':
    unittest.main()