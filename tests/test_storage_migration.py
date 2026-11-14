"""HL-223: migrating a library into the bucket, restoring it, and caching what is played.

Three separate promises, tested separately:

* **Migration is asked for.** `uploads.migrate()` refuses local mode and refuses a bucket it
  cannot build *before* it writes a row; running it again skips what is already queued or stored;
  what it queues is drained by the standing worker, so a restart resumes rather than restarts.
* **Restore is verified.** A restored object has the byte count the bucket reports and the
  SHA-256 the queue recorded, and a mismatch leaves no file behind.
* **The cache is not the library.** Turning `storage_cache_bytes` on makes the first play of a
  remote track fill a local copy and later plays range-read that copy; eviction removes cache
  files and never a local original; a local delete leaves the bucket object, which is the whole
  point of mirror mode.

Everything uses the in-process stub bucket from `tests/test_storage.py`. No credential, bucket or
real library is involved, and every write lands under a temporary directory.
"""
import hashlib
import json
import os
import tempfile
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest import mock

import db
import server
from library import playback, storage, uploads
from security import Security
from test_storage import BucketServer


CREDENTIAL = {'HARDLYLISTENING_S3_ACCESS_KEY': 'K', 'HARDLYLISTENING_S3_SECRET_KEY': 'S'}
PAYLOAD = bytes(range(256)) * 20                 # 5 KiB, every offset distinct


class StorageCase(unittest.TestCase):
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
        # The cache lives under `paths.STORAGE_CACHE_DIR`, which follows `DATA_DIR`. Point it at
        # the temporary tree so nothing here can touch a real cache.
        self.cache = Path(self.dir.name) / 'cache' / 'storage'
        anchor = mock.patch.object(storage.paths, 'STORAGE_CACHE_DIR', self.cache)
        anchor.start()
        self.addCleanup(anchor.stop)

    def configure(self, server):
        self.store.set_settings({'storage_mode': 's3', 'storage_bucket': 'music',
                                 'storage_endpoint': server.endpoint,
                                 'storage_region': 'us-east-1'})

    def track(self, name='song.flac', data=PAYLOAD, provider_id=None):
        path = self.root / 'Artist' / name
        path.write_bytes(data)
        track_id = self.store.track('local', provider_id or name, name, artist_name='Artist',
                                    album='Album')
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?',
                                    (str(path), track_id))
            self.store.conn.commit()
        return track_id, path

    def object_key(self, path):
        return 'music/hardlylistening/' + storage.key_for(self.store, path)


class LocalMode(StorageCase):
    def test_migration_refuses_local_mode_and_queues_nothing(self):
        self.track()
        with self.assertRaises(ValueError) as caught:
            uploads.migrate(self.store, by='a1')
        self.assertIn('local', str(caught.exception))
        self.assertEqual(uploads.summary(self.store)['pending'], 0)

    def test_a_missing_credential_refuses_before_any_row_is_written(self):
        self.track()
        with BucketServer() as server:
            self.configure(server)
            with mock.patch.dict(os.environ,
                                 {'HARDLYLISTENING_S3_ACCESS_KEY': '',
                                  'HARDLYLISTENING_S3_SECRET_KEY': ''}):
                with self.assertRaises(storage.StorageError):
                    uploads.migrate(self.store, by='a1')
        self.assertEqual(uploads.summary(self.store)['pending'], 0, 'no row survives a refusal')
        self.assertEqual(uploads.migrations(self.store), [])


class Migration(StorageCase):
    def test_it_queues_every_file_once_and_records_who_asked(self):
        first, first_path = self.track('one.flac')
        second, second_path = self.track('two.flac', data=b'two' * 100)
        # A row whose path is not a file inside the root has no key to give the bucket.
        outside = self.store.track('local', 'outside', 'Outside')
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?',
                                    ('/etc/passwd', outside))
            self.store.conn.commit()
        with BucketServer() as server:
            self.configure(server)
            result = uploads.migrate(self.store, by='a1')
        self.assertEqual((result['queued'], result['skipped'], result['missing']), (2, 0, 1))
        self.assertEqual(result['summary']['pending'], 2)
        run = uploads.migrations(self.store)[0]
        self.assertEqual((run['member_id'], run['queued'], run['skipped'], run['missing']),
                         ('a1', 2, 0, 1))
        self.assertGreater(run['finished'], 0)

    def test_running_it_again_skips_what_is_already_queued_or_stored(self):
        self.track()
        with BucketServer() as server:
            self.configure(server)
            self.assertEqual(uploads.migrate(self.store)['queued'], 1)
            again = uploads.migrate(self.store)
            self.assertEqual((again['queued'], again['skipped']), (0, 1))
            uploads.tick(self.store)
            third = uploads.migrate(self.store)
            self.assertEqual((third['queued'], third['skipped']), (0, 1))

    def test_a_failed_row_is_put_back_to_pending_when_asked_again(self):
        _, path = self.track()
        with BucketServer() as server:
            self.configure(server)
            uploads.migrate(self.store)
            with self.store.lock:
                self.store.conn.execute("UPDATE storage_uploads SET status='failed',"
                                        " attempts=5")
                self.store.conn.commit()
            self.assertEqual(uploads.migrate(self.store)['queued'], 1)
        with self.store.lock:
            row = self.store.conn.execute('SELECT status, attempts FROM storage_uploads'
                                          ' WHERE key=?', (storage.key_for(self.store, path),)
                                  ).fetchone()
        self.assertEqual((row['status'], row['attempts']), ('pending', 0))

    def test_a_limit_walks_a_library_in_batches(self):
        for n in range(3):
            self.track('song-%d.flac' % n, data=b'x%d' % n)
        with BucketServer() as server:
            self.configure(server)
            self.assertEqual(uploads.migrate(self.store, limit=2)['queued'], 2)
            self.assertEqual(uploads.summary(self.store)['pending'], 2)
            self.assertEqual(uploads.migrate(self.store, limit=2)['queued'], 1)
            self.assertEqual(uploads.summary(self.store)['pending'], 3)

    def test_the_worker_drains_what_migration_queued_and_keeps_the_original(self):
        _, path = self.track()
        with BucketServer() as server:
            self.configure(server)
            uploads.migrate(self.store, by='a1')
            result = uploads.tick(self.store)
            self.assertEqual(result['stored'], 1)
            self.assertIn(self.object_key(path).split('hardlylistening/')[1],
                          {key.split('hardlylistening/')[1] for key in server.bucket.objects})
        self.assertTrue(path.is_file(), 'mirror mode never removes the local original')
        with self.store.lock:
            row = self.store.conn.execute('SELECT status, sha256 FROM storage_uploads').fetchone()
        self.assertEqual(row['status'], 'stored')
        self.assertEqual(row['sha256'], hashlib.sha256(PAYLOAD).hexdigest())

    def test_the_queue_survives_a_restart_so_migration_resumes(self):
        self.track()
        with BucketServer() as server:
            self.configure(server)
            uploads.migrate(self.store, by='a1')
        self.store.conn.close()
        self.store = db.Store()
        with BucketServer() as server:
            self.configure(server)
            again = uploads.migrate(self.store)
            self.assertEqual((again['queued'], again['skipped']), (0, 1))
            self.assertEqual(uploads.tick(self.store)['stored'], 1)


class Restore(StorageCase):
    def test_an_object_is_written_back_verified(self):
        _, path = self.track()
        with BucketServer() as server:
            self.configure(server)
            uploads.migrate(self.store)
            uploads.tick(self.store)
            path.unlink()
            restored = uploads.restore(self.store, storage.key_for(self.store, path))
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(restored['size'], len(PAYLOAD))
        self.assertEqual(restored['sha256'], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(list(path.parent.glob('*.part')), [])

    def test_a_checksum_mismatch_leaves_no_file_and_no_partial(self):
        _, path = self.track()
        key = storage.key_for(self.store, path)
        with BucketServer() as server:
            self.configure(server)
            server.bucket.objects[self.object_key(path)] = PAYLOAD
            uploads.schema(self.store)
            with self.store.lock:
                self.store.conn.execute(
                    'INSERT INTO storage_uploads(key,size,status,sha256) VALUES(?,?,?,?)',
                    (key, len(PAYLOAD), 'stored', 'f' * 64))
                self.store.conn.commit()
            path.unlink()
            with self.assertRaises(storage.StorageError) as caught:
                uploads.restore(self.store, key)
        self.assertIn('checksum', str(caught.exception))
        self.assertFalse(path.exists())
        self.assertEqual(list(path.parent.glob('*.part')), [])

    def test_a_key_outside_the_root_is_refused(self):
        with BucketServer() as server:
            self.configure(server)
            with self.assertRaises(storage.StorageError):
                uploads.restore(self.store, '../outside.flac')

    def test_restore_refuses_local_mode(self):
        with self.assertRaises(storage.StorageError) as caught:
            uploads.restore(self.store, 'Artist/song.flac')
        self.assertIn('local', str(caught.exception))


class Verification(StorageCase):
    """A second migration run checks what the queue says is stored, instead of trusting it."""

    def stored(self, name='song.flac'):
        track_id, path = self.track(name)
        with BucketServer() as server:
            self.configure(server)
            uploads.migrate(self.store)
            uploads.tick(self.store)
            return server, track_id, path, storage.key_for(self.store, path)

    def status_of(self, key):
        with self.store.lock:
            return self.store.conn.execute('SELECT status, verified FROM storage_uploads'
                                           ' WHERE key=?', (key,)).fetchone()

    def test_a_vanished_object_is_requeued_and_uploaded_again(self):
        with BucketServer() as server:
            self.configure(server)
            track_id, path = self.track()
            uploads.migrate(self.store)
            uploads.tick(self.store)
            key = storage.key_for(self.store, path)
            del server.bucket.objects[self.object_key(path)]
            result = uploads.migrate(self.store)
            self.assertEqual((result['verified'], result['repaired']), (0, 1))
            self.assertEqual(self.status_of(key)['status'], 'pending')
            self.assertEqual(uploads.tick(self.store)['stored'], 1)
            self.assertIn(self.object_key(path), server.bucket.objects,
                          'the worker puts the object back')
        self.assertEqual(self.status_of(key)['status'], 'stored')

    def test_an_intact_object_is_verified_and_left_alone(self):
        with BucketServer() as server:
            self.configure(server)
            track_id, path = self.track()
            uploads.migrate(self.store)
            uploads.tick(self.store)
            key = storage.key_for(self.store, path)
            result = uploads.migrate(self.store)
            self.assertEqual((result['verified'], result['repaired']), (1, 0))
        row = self.status_of(key)
        self.assertEqual(row['status'], 'stored')
        self.assertGreater(row['verified'], 0)

    def test_an_object_of_the_wrong_size_is_requeued(self):
        with BucketServer() as server:
            self.configure(server)
            track_id, path = self.track()
            uploads.migrate(self.store)
            uploads.tick(self.store)
            key = storage.key_for(self.store, path)
            server.bucket.objects[self.object_key(path)] = b'truncated'
            result = uploads.migrate(self.store)
            self.assertEqual(result['repaired'], 1)
        self.assertEqual(self.status_of(key)['status'], 'pending')

    def test_a_bucket_refusal_aborts_without_requeueing_everything(self):
        with BucketServer() as server:
            self.configure(server)
            track_id, path = self.track()
            uploads.migrate(self.store)
            uploads.tick(self.store)
            key = storage.key_for(self.store, path)
            with mock.patch.object(storage.S3Storage, 'head',
                                   side_effect=storage.StorageError('denied', status=403)):
                with self.assertRaises(storage.StorageError):
                    uploads.migrate(self.store)
        self.assertEqual(self.status_of(key)['status'], 'stored',
                         'a refusal is not a lost object')

    def test_verification_walks_the_least_recently_checked_first(self):
        with BucketServer() as server:
            self.configure(server)
            self.track('one.flac')
            self.track('two.flac', data=b'two' * 100)
            uploads.migrate(self.store)
            uploads.tick(self.store)
            first = uploads.migrate(self.store, limit=1)
            self.assertEqual(first['verified'], 1)
            second = uploads.migrate(self.store, limit=1)
            self.assertEqual(second['verified'], 1)
            third = uploads.migrate(self.store, limit=1)
            self.assertEqual(third['verified'], 1)
        with self.store.lock:
            rows = self.store.conn.execute(
                'SELECT key, verified FROM storage_uploads ORDER BY key').fetchall()
        self.assertTrue(all(row['verified'] > 0 for row in rows),
                        'two runs must cover both rows, not the same row twice')


class DeletionScope(StorageCase):
    def test_deleting_the_local_original_leaves_the_object_and_it_still_plays(self):
        track_id, path = self.track()
        with BucketServer() as server:
            self.configure(server)
            uploads.migrate(self.store)
            uploads.tick(self.store)
            path.unlink()
            self.assertIn(self.object_key(path), server.bucket.objects,
                          'a local delete is not an object delete')
            stream = playback.plan(self.store, track_id)
            self.assertEqual(b''.join(stream.chunks()), PAYLOAD)
            deleted = [row for row in server.bucket.requests if row[0] == 'DELETE']
        self.assertEqual(deleted, [], 'nothing in the mirror path deletes an object')


class Cache(StorageCase):
    def enable(self, server, limit):
        self.configure(server)
        self.store.set_settings({'storage_cache_bytes': str(limit)})

    def test_with_no_budget_playback_streams_and_writes_no_cache(self):
        track_id, path = self.track()
        with BucketServer() as server:
            self.configure(server)
            server.bucket.objects[self.object_key(path)] = PAYLOAD
            stream = playback.plan(self.store, track_id)
            self.assertEqual(b''.join(stream.chunks()), PAYLOAD)
        self.assertFalse(storage.cache_report(self.store)['enabled'])
        self.assertFalse(self.cache.exists())

    def test_the_first_play_fills_the_cache_and_the_next_one_reads_it(self):
        track_id, path = self.track()
        path.unlink()          # remote-with-cache: the bytes are only in the bucket
        with BucketServer() as server:
            self.enable(server, 64 * 1024)
            server.bucket.objects[self.object_key(path)] = PAYLOAD
            first = playback.plan(self.store, track_id, 'bytes=100-199')
            self.assertEqual((first.status, b''.join(first.chunks())),
                             (206, PAYLOAD[100:200]))
            gets = [row for row in server.bucket.requests if row[0] == 'GET']
            self.assertEqual(len(gets), 1, 'the first request fetches the object once')
            after_first = len(server.bucket.requests)

            second = playback.plan(self.store, track_id, 'bytes=100-199')
            self.assertEqual(b''.join(second.chunks()), PAYLOAD[100:200])
            self.assertEqual(len(server.bucket.requests), after_first,
                             'a cache hit costs no request at all, not even a HEAD')
        report = storage.cache_report(self.store)
        self.assertEqual((report['files'], report['bytes'], report['tracked']),
                         (1, len(PAYLOAD), 1))

    def test_after_the_window_one_head_refreshes_the_row_without_a_refetch(self):
        track_id, path = self.track()
        path.unlink()
        with BucketServer() as server:
            self.enable(server, 64 * 1024)
            server.bucket.objects[self.object_key(path)] = PAYLOAD
            playback.plan(self.store, track_id)
            before = len(server.bucket.requests)
            # Age the row past the revalidation window.
            with self.store.lock:
                self.store.conn.execute('UPDATE storage_cache SET used=?',
                                        (time.time() - storage.REVALIDATE - 60,))
                self.store.conn.commit()
            stream = playback.plan(self.store, track_id)
            self.assertEqual(b''.join(stream.chunks()), PAYLOAD)
            new = server.bucket.requests[before:]
            self.assertEqual([row[0] for row in new], ['HEAD'],
                             'an expired row is revalidated with one HEAD, no body fetch')
        with self.store.lock:
            row = self.store.conn.execute('SELECT used FROM storage_cache').fetchone()
        self.assertGreater(row['used'], time.time() - 60, 'the row was refreshed')

    def test_a_cache_row_whose_file_vanished_is_dropped_and_refilled(self):
        track_id, path = self.track()
        path.unlink()
        with BucketServer() as server:
            self.enable(server, 64 * 1024)
            server.bucket.objects[self.object_key(path)] = PAYLOAD
            playback.plan(self.store, track_id)
            cached = self.cache / storage.key_for(self.store, path)
            cached.unlink()
            stream = playback.plan(self.store, track_id)
            self.assertEqual(b''.join(stream.chunks()), PAYLOAD)
            self.assertTrue(cached.is_file(), 'the object is fetched again')
        self.assertEqual(storage.cache_report(self.store)['tracked'], 1)

    def test_an_object_larger_than_the_budget_is_streamed_not_cached(self):
        track_id, path = self.track()
        path.unlink()
        with BucketServer() as server:
            self.enable(server, 1024)          # the object is 5 KiB
            server.bucket.objects[self.object_key(path)] = PAYLOAD
            stream = playback.plan(self.store, track_id)
            self.assertEqual(b''.join(stream.chunks()), PAYLOAD)
        self.assertEqual(storage.cache_report(self.store)['files'], 0)

    def test_eviction_removes_the_oldest_cache_file_and_never_the_library(self):
        one, one_path = self.track('one.flac')
        two, two_path = self.track('two.flac', data=b'two' * (len(PAYLOAD) // 3))
        one_path.unlink()
        two_path.unlink()
        keep = self.root / 'Artist' / 'original.flac'
        keep.write_bytes(b'local original')
        with BucketServer() as server:
            self.enable(server, len(PAYLOAD) + 10)
            server.bucket.objects[self.object_key(one_path)] = PAYLOAD
            server.bucket.objects[self.object_key(two_path)] = b'two' * (len(PAYLOAD) // 3)
            playback.plan(self.store, one)
            cached_one = self.cache / storage.key_for(self.store, one_path)
            old = time.time() - 3600
            os.utime(cached_one, (old, old))
            playback.plan(self.store, two)
        self.assertFalse(cached_one.exists(), 'the older copy is the one that goes')
        self.assertTrue(self.cache.joinpath(
            storage.key_for(self.store, two_path)).is_file())
        self.assertEqual(keep.read_bytes(), b'local original',
                         'eviction only ever deletes inside the cache directory')
        self.assertIsNone(storage.cached_copy(self.store,
                                              storage.key_for(self.store, one_path)),
                          'the metadata row goes with the file')
        self.assertEqual(storage.cache_report(self.store)['tracked'], 1)

    def test_a_new_upload_of_the_same_key_drops_the_cached_copy(self):
        track_id, path = self.track()
        path.unlink()
        with BucketServer() as server:
            self.enable(server, 64 * 1024)
            server.bucket.objects[self.object_key(path)] = PAYLOAD
            playback.plan(self.store, track_id)
            key = storage.key_for(self.store, path)
            self.assertIsNotNone(storage.cached_copy(self.store, key))
            path.write_bytes(b'new bytes' * 50)     # the download path offers the new file
            self.assertTrue(uploads.offer(self.store, path))
            uploads.tick(self.store)
        self.assertIsNone(storage.cached_copy(self.store, key),
                          'stale bytes at the same key must not be served as the new object')
        self.assertFalse((self.cache / key).exists())

    def test_a_cache_hit_keeps_the_entry_recent_for_eviction(self):
        one, one_path = self.track('one.flac')
        two, two_path = self.track('two.flac')
        three, three_path = self.track('three.flac')
        for path in (one_path, two_path, three_path):
            path.unlink()
        with BucketServer() as server:
            self.enable(server, len(PAYLOAD) * 2 + 10)
            for path in (one_path, two_path, three_path):
                server.bucket.objects[self.object_key(path)] = PAYLOAD
            playback.plan(self.store, one)
            playback.plan(self.store, two)
            cached_one = self.cache / storage.key_for(self.store, one_path)
            old = time.time() - 3600
            os.utime(cached_one, (old, old))     # as if it had been filled an hour ago
            playback.plan(self.store, one)       # a hit must move it to the front
            playback.plan(self.store, three)     # needs room: the *other* old file goes
        self.assertTrue(cached_one.is_file(), 'a hit protects the entry from eviction')
        self.assertFalse(self.cache.joinpath(
            storage.key_for(self.store, two_path)).exists(), 'the untouched old entry goes')

    def test_available_answers_without_downloading_a_track(self):
        track_id, path = self.track()
        path.unlink()
        with BucketServer() as server:
            self.enable(server, 64 * 1024)
            server.bucket.objects[self.object_key(path)] = PAYLOAD
            self.assertTrue(playback.available(self.store, track_id))
            self.assertEqual([row for row in server.bucket.requests if row[0] == 'GET'], [])
        self.assertEqual(storage.cache_report(self.store)['files'], 0)


class Routes(StorageCase):
    """The administrator surface: read the status, ask for a migration, restore one object."""

    def setUp(self):
        super().setUp()
        self.sec = Security()
        self.sec.schema(self.store)
        env = mock.patch.dict(os.environ, {
            'HARDLYLISTENING_AUTH_REQUIRED': '1',
            'HARDLYLISTENING_SUPABASE_URL': 'https://example.supabase.co',
            'HARDLYLISTENING_SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_fixture',
            'HARDLYLISTENING_PUBLIC_ORIGIN': 'https://music.example',
            'HARDLYLISTENING_BOOTSTRAP_ADMIN_EMAIL': 'owner@example.com'})
        env.start()
        self.addCleanup(env.stop)
        for patcher in (mock.patch.object(server, 'SECURITY', self.sec),
                        mock.patch.object(server, 'boot', return_value=(self.store, None))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def member(self, mid, role):
        with self.store.lock:
            self.store.conn.execute(
                'INSERT OR REPLACE INTO members(id,email,role,display_name) VALUES(?,?,?,?)',
                (mid, mid + '@example.test', role, mid))
            self.store.conn.commit()

    def token(self, mid):
        import secrets
        sid = secrets.token_urlsafe(32)
        self.sec.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                'INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)',
                (self.sec.digest(sid), mid, time.time() + 3600))
            self.store.conn.commit()
        return {'Cookie': '__Host-hl_session=' + sid, 'Origin': 'https://music.example'}

    def request(self, path, method='GET', headers=None, body=None):
        connection = HTTPConnection('127.0.0.1', self.http.server_port, timeout=30)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = (response.status, response.read())
        connection.close()
        return result

    def test_a_member_cannot_read_or_act(self):
        self.member('a1', 'admin')
        self.member('m1', 'member')
        headers = self.token('m1')
        self.assertEqual(self.request('/api/storage', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/storage/migrate', 'POST', headers)[0], 403)
        self.assertEqual(self.request('/api/storage/restore', 'POST', headers,
                                      json.dumps({'key': 'Artist/song.flac'}))[0], 403)

    def test_the_status_reports_the_queue_the_runs_and_the_cache(self):
        self.member('a1', 'admin')
        headers = self.token('a1')
        status, raw = self.request('/api/storage', headers=headers)
        self.assertEqual(status, 200, raw)
        view = json.loads(raw)
        for key in ('uploads', 'migrations', 'cache', 'ready'):
            self.assertIn(key, view)
        self.assertNotIn('access_key', raw.decode())
        self.assertNotIn('secret_key', raw.decode())

    def test_migration_in_local_mode_is_answered_with_the_reason(self):
        self.member('a1', 'admin')
        status, raw = self.request('/api/storage/migrate', 'POST', self.token('a1'))
        self.assertEqual(status, 400, raw)
        self.assertIn(b'local', raw)

    def test_restore_of_an_unknown_object_is_a_request_error(self):
        self.member('a1', 'admin')
        with BucketServer() as server:
            self.configure(server)
            status, raw = self.request('/api/storage/restore', 'POST', self.token('a1'),
                                       json.dumps({'key': 'Artist/missing.flac'}))
        self.assertEqual(status, 400, raw)

    def test_a_track_id_that_is_not_a_number_is_a_request_error(self):
        self.member('a1', 'admin')
        status, raw = self.request('/api/storage/restore', 'POST', self.token('a1'),
                                   json.dumps({'track_id': 'forty-one'}))
        self.assertEqual(status, 400, raw)
        self.assertIn(b'track_id', raw)


if __name__ == '__main__':
    unittest.main()