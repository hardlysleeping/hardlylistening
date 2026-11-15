"""Storage adapters: the signing is checked against AWS's own vector, the rest against a bucket.

Two kinds of proof here, because they fail differently:

* `sign_v4` is compared with the published "Example: GET Object" request from the S3 SigV4
  documentation — the canonical request hash, the string to sign and the signature. A change to
  the signer that still agrees with itself would pass a round-trip test against our own stub and
  be wrong against the real service; this one would not.
* `S3Storage` and `LocalStorage` are driven against a stub bucket: one PUT, HEAD, ranged GET and
  DELETE, path-style under the app's own prefix. The stub keeps the bytes, so a range read is
  compared with the file it came from rather than with a length.

No bucket, credential or real file is involved. Nothing here writes into the live library.
"""
import base64
import hashlib
import http.server
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import db
from library import storage

# ---- the published vector --------------------------------------------------------------
# https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
# "Example: GET Object" — GET /test.txt from examplebucket, Range: bytes=0-9.
AWS = dict(
    method='GET',
    host='examplebucket.s3.amazonaws.com',
    canonical_uri='/test.txt',
    query='',
    headers={
        'host': 'examplebucket.s3.amazonaws.com',
        'range': 'bytes=0-9',
        'x-amz-content-sha256':
            'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        'x-amz-date': '20130524T000000Z',
    },
    payload_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    access_key='AKIAIOSFODNN7EXAMPLE',
    secret_key='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    region='us-east-1',
)
AWS_CANONICAL_HASH = '7344ae5b7ee6c3e7e6b0fe0640412a37625d1fbfff95c48bbb2dc43964946972'
AWS_SIGNATURE = 'f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41'


class Signing(unittest.TestCase):
    def test_the_published_get_object_vector(self):
        authorization, signed = storage.sign_v4(**AWS)
        self.assertEqual(signed, 'host;range;x-amz-content-sha256;x-amz-date')
        self.assertIn('Signature=' + AWS_SIGNATURE, authorization)
        self.assertIn('Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request',
                      authorization)

    def test_the_canonical_request_is_the_documented_byte_sequence(self):
        # Built by hand here, so a formatting change in sign_v4 (a blank line, a stray space)
        # fails against the document rather than against a second call to the same function.
        canonical = '\n'.join([
            'GET', '/test.txt', '',
            'host:examplebucket.s3.amazonaws.com\n'
            'range:bytes=0-9\n'
            'x-amz-content-sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n'
            'x-amz-date:20130524T000000Z\n',
            'host;range;x-amz-content-sha256;x-amz-date',
            'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        ])
        self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), AWS_CANONICAL_HASH)


# ---- a bucket that answers -------------------------------------------------------------

class Bucket:
    """The four operations the adapter uses, in memory, path-style."""

    def __init__(self, prefix='hardlylistening'):
        self.prefix = prefix
        self.objects: dict[str, bytes] = {}
        self.requests: list[tuple[str, str, dict]] = []
        self.fail_once = 0
        self.lock = threading.Lock()

    def handle(self, request, body):
        path = request.path.lstrip('/')
        self.requests.append((request.command, path, dict(request.headers)))
        with self.lock:
            if self.fail_once:
                self.fail_once -= 1
                return 503, {}, b'slow down'
            if request.command == 'PUT':
                self.objects[path] = body
                return 200, {'ETag': '"%s"' % hashlib.md5(body).hexdigest()}, b''
            if request.command == 'DELETE':
                self.objects.pop(path, None)
                return 204, {}, b''
            if path not in self.objects:
                return 404, {}, b'missing'
            data = self.objects[path]
            span = request.headers.get('Range')
            if request.command == 'HEAD':
                return 200, {'Content-Length': str(len(data)),
                             'ETag': '"%s"' % hashlib.md5(data).hexdigest()}, b''
            if span:
                first, last = span.split('=')[1].split('-')
                start = int(first or 0)
                end = min(int(last), len(data) - 1) if last else len(data) - 1
                return 206, {'Content-Length': str(end - start + 1),
                             'Content-Range': 'bytes %d-%d/%d' % (start, end, len(data))}, \
                    data[start:end + 1]
            return 200, {'Content-Length': str(len(data))}, data


class BucketServer:
    def __init__(self):
        self.bucket = Bucket()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def _answer(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                status, headers, payload = outer.bucket.handle(self, body)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(payload)

            do_GET = do_PUT = do_HEAD = do_DELETE = _answer

        self.http = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.endpoint = 'http://127.0.0.1:%d' % self.http.server_port
        return self

    def __exit__(self, *exc):
        self.http.shutdown()
        self.http.server_close()


class BucketStorage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.payload = bytes(range(256)) * 40        # 10 KiB, distinguishable by offset
        self.file = Path(self.dir.name) / 'track.flac'
        self.file.write_bytes(self.payload)

    def adapter(self, server, **extra):
        return storage.S3Storage(server.endpoint, 'music', 'us-east-1', 'hardlylistening',
                                 'KEYID', 'SECRET', **extra)

    def test_put_head_range_and_delete(self):
        with BucketServer() as server:
            store = self.adapter(server)
            written = store.put('a/b/track.flac', self.file)

            self.assertEqual(written['size'], len(self.payload))
            self.assertEqual(written['sha256'], hashlib.sha256(self.payload).hexdigest())
            # The object is under the app's prefix, inside the configured bucket, path-style.
            self.assertIn('music/hardlylistening/a/b/track.flac', server.bucket.objects)

            self.assertEqual(store.size('a/b/track.flac'), len(self.payload))
            self.assertTrue(store.exists('a/b/track.flac'))
            self.assertEqual(store.read_range('a/b/track.flac', 10, 19), self.payload[10:20])
            self.assertEqual(b''.join(store.iter_range('a/b/track.flac', 0, None, 1024)),
                             self.payload)

            store.delete('a/b/track.flac')
            self.assertFalse(store.exists('a/b/track.flac'))

    def test_every_request_is_signed_and_says_which_key_it_used(self):
        with BucketServer() as server:
            store = self.adapter(server)
            store.put('x.bin', self.file)
            store.head('x.bin')
            for method, path, headers in server.bucket.requests:
                # Header names arrive title-cased from urllib; compare by name, not by spelling.
                lower = {name.lower(): value for name, value in headers.items()}
                authorization = headers.get('Authorization', '')
                self.assertTrue(authorization.startswith('AWS4-HMAC-SHA256 Credential=KEYID/'),
                                method + ' ' + authorization)
                signed = authorization.split('SignedHeaders=', 1)[1].split(',', 1)[0].split(';')
                # Every header we sign is named, sorted, and the three that make the request
                # authenticated are always among them.
                self.assertEqual(signed, sorted(signed))
                self.assertTrue({'host', 'x-amz-content-sha256', 'x-amz-date'} <= set(signed),
                                str(signed))
                self.assertIn('x-amz-content-sha256', lower)
                signature = authorization.rsplit('Signature=', 1)[1]
                self.assertEqual(len(signature), 64)
                self.assertTrue(all(c in '0123456789abcdef' for c in signature))
                # The secret never travels, and never appears in a header.
                self.assertNotIn('SECRET', str(headers))

    def test_a_5xx_is_retried_and_a_404_is_not(self):
        with BucketServer() as server:
            store = self.adapter(server)
            server.bucket.fail_once = 1
            store.put('retry.bin', self.file)       # first attempt 503, second succeeds
            self.assertTrue(store.exists('retry.bin'))

            tries = len(server.bucket.requests)
            with self.assertRaises(storage.StorageError) as caught:
                store.head('absent.bin')
            self.assertEqual(caught.exception.status, 404)
            self.assertEqual(len(server.bucket.requests), tries + 1, 'a 404 must not be retried')

    def test_verify_is_size_first_and_never_compares_an_etag_to_a_sha256(self):
        with BucketServer() as server:
            store = self.adapter(server)
            store.put('v.bin', self.file)
            digest = hashlib.sha256(self.payload).hexdigest()
            # No checksum header from this bucket: size has to carry the check.
            self.assertTrue(store.verify('v.bin', len(self.payload), digest))
            self.assertFalse(store.verify('v.bin', len(self.payload) + 1, digest))
            self.assertFalse(store.verify('missing.bin', len(self.payload), digest))

    def test_one_put_has_a_stated_ceiling(self):
        with BucketServer() as server:
            store = self.adapter(server)
            with mock.patch.object(storage, 'MAX_PUT', 10):
                with self.assertRaises(storage.StorageError) as caught:
                    store.put('big.bin', self.file)
            self.assertIn('capped', str(caught.exception))
            self.assertFalse(store.exists('big.bin'))

    def test_a_key_cannot_escape_the_prefix(self):
        with BucketServer() as server:
            store = self.adapter(server)
            for key in ('../other-app/object', 'a/../../other-app/object', 'a//b', ''):
                with self.assertRaises(storage.StorageError):
                    store.put(key, self.file)
            self.assertEqual(server.bucket.objects, {})

    def test_a_leading_slash_is_normalised_rather_than_refused(self):
        # Callers build keys by joining path pieces, which can leave a leading slash. It is
        # stripped, so the object still lands under the app's prefix and never at the bucket root.
        with BucketServer() as server:
            store = self.adapter(server)
            written = store.put('/a/b.flac', self.file)
            self.assertEqual(written['key'], 'a/b.flac')
            self.assertIn('music/hardlylistening/a/b.flac', server.bucket.objects)


# ---- the local adapter ----------------------------------------------------------------

class LocalStorage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / 'music'
        self.root.mkdir()
        self.file = Path(self.dir.name) / 'in.flac'
        self.file.write_bytes(b'0123456789' * 10)

    def test_put_read_range_and_delete(self):
        store = storage.LocalStorage(self.root)
        written = store.put('a/b.flac', self.file)
        self.assertEqual(written['size'], 100)
        self.assertEqual(written['sha256'], hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertTrue((self.root / 'a' / 'b.flac').is_file())
        self.assertEqual(store.read_range('a/b.flac', 5, 9), b'56789')
        self.assertTrue(store.verify('a/b.flac', 100, written['sha256']))
        self.assertFalse(store.verify('a/b.flac', 99, written['sha256']))
        store.delete('a/b.flac')
        self.assertFalse(store.exists('a/b.flac'))
        store.delete('a/b.flac')          # deleting twice is not an error

    def test_a_key_cannot_leave_the_root(self):
        store = storage.LocalStorage(self.root)
        with self.assertRaises(storage.StorageError):
            store.put('../escape.flac', self.file)

    def test_put_leaves_no_partial_file_behind(self):
        store = storage.LocalStorage(self.root)
        store.put('one.flac', self.file)
        listing = sorted(p.name for p in (self.root).rglob('*') if p.is_file())
        self.assertEqual(listing, ['one.flac'])


# ---- what the settings select ---------------------------------------------------------

class Selection(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(db, 'DB_PATH', os.path.join(self.dir.name, 'd.db'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({'root': os.path.join(self.dir.name, 'music')})

    def test_local_is_the_default_and_needs_nothing(self):
        adapter = storage.build(self.store)
        self.assertEqual(adapter.mode, 'local')
        self.assertEqual(storage.public(self.store)['mode'], 'local')

    def test_s3_without_a_credential_refuses_rather_than_falling_back(self):
        self.store.set_settings({'storage_mode': 's3', 'storage_endpoint': 'https://s3.example',
                                 'storage_bucket': 'music'})
        with mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': '',
                                          'HARDLYLISTENING_S3_SECRET_KEY': ''}):
            with self.assertRaises(storage.StorageError) as caught:
                storage.build(self.store)
        self.assertIn('HARDLYLISTENING_S3_ACCESS_KEY', str(caught.exception))

    def test_s3_with_an_environment_credential_builds_an_adapter(self):
        self.store.set_settings({'storage_mode': 's3', 'storage_endpoint': 'https://s3.example',
                                 'storage_bucket': 'music', 'storage_region': 'us-west-004'})
        with mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': 'K',
                                          'HARDLYLISTENING_S3_SECRET_KEY': 'S'}):
            adapter = storage.build(self.store)
        self.assertEqual(adapter.mode, 's3')
        self.assertEqual(adapter.region, 'us-west-004')
        # The prefix defaults to the app's own, so a shared bucket is never touched outside it.
        self.assertEqual(adapter.prefix, 'hardlylistening/')

    def test_an_unknown_mode_is_an_error_not_a_default(self):
        self.store.set_settings({'storage_mode': 'webdav'})
        with self.assertRaises(storage.StorageError):
            storage.build(self.store)

    def test_the_public_view_never_carries_the_secret(self):
        self.store.set_settings({'storage_mode': 's3', 'storage_endpoint': 'https://s3.example',
                                 'storage_bucket': 'music'})
        with mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': 'KEYID',
                                          'HARDLYLISTENING_S3_SECRET_KEY': 'TOPSECRET'}):
            view = storage.public(self.store)
        self.assertEqual(view['credential'], 'environment')
        self.assertNotIn('TOPSECRET', repr(view))
        self.assertNotIn('KEYID', repr(view))


class ChecksumHelper(unittest.TestCase):
    def test_a_bucket_checksum_is_compared_when_present(self):
        payload = b'hello'
        digest = hashlib.sha256(payload).hexdigest()
        want = base64.b64encode(bytes.fromhex(digest)).decode()
        store = storage.S3Storage('https://s3.example', 'music', access_key='k', secret_key='s')
        with mock.patch.object(store, 'head', return_value={
                'size': len(payload), 'etag': 'x', 'checksum': want}):
            self.assertTrue(store.verify('k', len(payload), digest))
        with mock.patch.object(store, 'head', return_value={
                'size': len(payload), 'etag': 'x', 'checksum': 'AAAA'}):
            self.assertFalse(store.verify('k', len(payload), digest))


# ---- streaming a track whose bytes are in the bucket ----------------------------------

class BucketPlayback(unittest.TestCase):
    """The audio route's decision, with the bytes 1000 miles away.

    `library.playback.plan` is what every surface calls, so if it answers identically here the
    web player, the mixer and Subsonic all stream a remote track without knowing it is remote.
    """

    def setUp(self):
        from library import playback
        self.playback = playback
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / 'music'
        self.root.mkdir()
        patcher = mock.patch.object(db, 'DB_PATH', os.path.join(self.dir.name, 'd.db'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({'root': str(self.root)})
        self.payload = bytes(range(256)) * 20        # 5 KiB, every offset distinct
        self.track_id = self.store.track('local', 'remote', 'Remote', artist_name='Test',
                                         album='Test', source_url='')
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?',
                                    (str(self.root / 'artist' / 'remote.flac'), self.track_id))
            self.store.conn.commit()

    def configure(self, server):
        self.store.set_settings({'storage_mode': 's3', 'storage_bucket': 'music',
                                 'storage_endpoint': server.endpoint,
                                 'storage_region': 'us-east-1'})
        server.bucket.objects['music/hardlylistening/artist/remote.flac'] = self.payload

    def test_a_remote_track_answers_like_a_local_one(self):
        with BucketServer() as server:
            self.configure(server)
            with mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': 'K',
                                              'HARDLYLISTENING_S3_SECRET_KEY': 'S'}):
                whole = self.playback.plan(self.store, self.track_id)
                self.assertEqual((whole.status, whole.length), (200, len(self.payload)))
                self.assertEqual(b''.join(whole.chunks()), self.payload)

                part = self.playback.plan(self.store, self.track_id, 'bytes=100-199')
                self.assertEqual((part.status, part.content_range),
                                 (206, 'bytes 100-199/%d' % len(self.payload)))
                self.assertEqual(b''.join(part.chunks()), self.payload[100:200])

                tail = self.playback.plan(self.store, self.track_id, 'bytes=-8')
                self.assertEqual(b''.join(tail.chunks()), self.payload[-8:])

                past = self.playback.plan(self.store, self.track_id, 'bytes=%d-' % len(self.payload))
                self.assertEqual((past.status, past.length), (416, 0))
                self.assertEqual(list(past.chunks()), [])

                self.assertTrue(self.playback.available(self.store, self.track_id))

    def test_the_local_copy_wins_when_there_is_one(self):
        with BucketServer() as server:
            self.configure(server)
            local = self.root / 'artist' / 'remote.flac'
            local.parent.mkdir(parents=True)
            local.write_bytes(b'local bytes')
            with mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': 'K',
                                              'HARDLYLISTENING_S3_SECRET_KEY': 'S'}):
                stream = self.playback.plan(self.store, self.track_id)
            self.assertEqual(b''.join(stream.chunks()), b'local bytes')
            self.assertEqual(server.bucket.requests, [], 'a local file must not cost a request')

    def test_an_object_that_is_not_there_is_missing_media_not_an_error(self):
        with BucketServer() as server:
            self.configure(server)
            del server.bucket.objects['music/hardlylistening/artist/remote.flac']
            with mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': 'K',
                                              'HARDLYLISTENING_S3_SECRET_KEY': 'S'}):
                self.assertIsNone(self.playback.plan(self.store, self.track_id))
                self.assertFalse(self.playback.available(self.store, self.track_id))

    def test_a_track_outside_the_library_root_never_reaches_the_bucket(self):
        with BucketServer() as server:
            self.configure(server)
            with self.store.lock:
                self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?',
                                        ('/etc/passwd', self.track_id))
                self.store.conn.commit()
            with mock.patch.dict(os.environ, {'HARDLYLISTENING_S3_ACCESS_KEY': 'K',
                                              'HARDLYLISTENING_S3_SECRET_KEY': 'S'}):
                # A row pointing outside the root has no key to derive, so it is missing media —
                # and the bucket is never asked for a path it does not own.
                self.assertIsNone(self.playback.plan(self.store, self.track_id))
            self.assertEqual(server.bucket.requests, [])

    def test_a_key_is_the_stored_path_relative_to_the_root(self):
        with BucketServer() as server:
            self.configure(server)
            key = storage.key_for(self.store, self.root / 'artist' / 'remote.flac')
            self.assertEqual(key, 'artist/remote.flac')
            with self.assertRaises(storage.StorageError):
                storage.key_for(self.store, '/etc/passwd')


if __name__ == '__main__':
    unittest.main()