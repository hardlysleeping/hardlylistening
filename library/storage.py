"""Where a recording's bytes live.

SQLite stays the source of truth: a track row names a canonical object, and this module is the
only place that knows whether that object is a file on this machine or an object in a bucket.
Local-only remains the default and stays first-class — a server with no bucket configured runs
exactly as it did before this file existed.

Why hand-written SigV4 instead of boto3: the whole server is standard library only, and the part
of S3 that matters here is small — PUT, HEAD, ranged GET, DELETE. A dependency that pulls in a
transport stack of its own to sign four requests is not a trade this app makes.

The rules this file exists to keep:

* **Private.** Credentials come from the process environment, never from SQLite, the settings
  API, a log line or the browser. Objects are never made public; playback is ranged requests
  through the server, not signed URLs the client keeps.
* **Verified, not assumed.** An upload is not "stored" until a HEAD agrees with the bytes we
  sent. A multipart ETag is not a content hash and is never treated as one.
* **Scoped.** Every key lives under `hardlylistening/` inside the bucket, so another app's
  objects — and the bucket's lifecycle rules and policies — are never touched.
* **Additive.** Nothing here deletes a local original. Watching is explicit, and the mirror
  mode's whole point is that the local file survives the upload.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

import paths

DEFAULT_PREFIX = 'hardlylistening/'
# One PUT. Multipart would be three more request shapes and a second failure mode, so the limit
# is stated instead: a track file larger than this is refused by name, not silently truncated.
MAX_PUT = 5 * 1024 ** 3
CHUNK = 256 * 1024
RETRIES = 3


class StorageError(RuntimeError):
    """A storage operation failed, with the request that failed kept for the log."""

    def __init__(self, message, status=None, body=b''):
        super().__init__(message)
        self.status = status
        self.body = body


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def check_key(key: str) -> str:
    """Object keys are relative, slash-separated and cannot escape the prefix."""
    key = str(key or '').strip().lstrip('/')
    parts = key.split('/')
    if not key or '..' in parts or any(part == '' for part in parts):
        raise StorageError('invalid object key: %r' % key)
    return key


def key_for(store, path) -> str:
    """The canonical object key for a stored file: its path relative to the library root.

    `LocalStorage.put` writes exactly this key under the root, so one track row names the same
    object in either mode — there is no second column and no second naming rule to keep in sync.
    """
    root = Path(store.setting('root')).expanduser().resolve()
    target = Path(path).expanduser().resolve()
    if root not in target.parents:
        raise StorageError('%s is outside the library root' % target.name)
    return check_key(str(target.relative_to(root)).replace(os.sep, '/'))


# --------------------------------------------------------------------------- local

class LocalStorage:
    """The default: a canonical file under the library root, exactly as before."""

    mode = 'local'

    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, key: str) -> Path:
        target = (self.root / check_key(key)).resolve()
        if self.root not in target.parents:
            raise StorageError('object key leaves the library root')
        return target

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def size(self, key: str) -> int:
        return self.path(key).stat().st_size

    def put(self, key: str, source) -> dict:
        """Copy in, verify, and answer what the caller should record."""
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(target.suffix + '.part')
        shutil.copyfile(source, staging)
        digest = sha256_file(staging)
        size = staging.stat().st_size
        os.replace(staging, target)
        return {'key': key, 'size': size, 'sha256': digest, 'etag': None}

    def read_range(self, key: str, start: int = 0, end: int | None = None) -> bytes:
        target = self.path(key)
        with open(target, 'rb') as handle:
            handle.seek(max(0, int(start)))
            want = None if end is None else max(0, int(end) - int(start) + 1)
            return handle.read() if want is None else handle.read(want)

    def delete(self, key: str) -> None:
        try:
            self.path(key).unlink()
        except FileNotFoundError:
            pass

    def verify(self, key: str, size: int, sha256: str | None = None) -> bool:
        target = self.path(key)
        if not target.is_file() or target.stat().st_size != size:
            return False
        return sha256 is None or sha256_file(target) == sha256


# --------------------------------------------------------------------------- S3 / B2

def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def sign_v4(method, host, canonical_uri, query, headers, payload_sha256,
            access_key, secret_key, region, service='s3',
            amz_date=None) -> tuple[str, str]:
    """The SigV4 `Authorization` value and the signed headers, for one request.

    Kept as a free function because it is the one piece with a published test vector: AWS's own
    "Example: GET Object" is asserted against it in `tests/test_storage.py`, so a change here is
    checked against something other than itself.
    """
    amz_date = amz_date or headers.get('x-amz-date', '')
    day = amz_date[:8]
    signed = sorted(name.lower() for name in headers)
    canonical_headers = ''.join('%s:%s\n' % (name, str(headers[name]).strip()) for name in signed)
    canonical_request = '\n'.join([
        method.upper(), canonical_uri, query or '', canonical_headers,
        ';'.join(signed), payload_sha256,
    ])
    scope = '%s/%s/%s/aws4_request' % (day, region, service)
    to_sign = '\n'.join([
        'AWS4-HMAC-SHA256', amz_date, scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    key = ('AWS4' + secret_key).encode()
    key = _hmac(key, day)
    key = _hmac(key, region)
    key = _hmac(key, service)
    key = _hmac(key, 'aws4_request')
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = 'AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s' % (
        access_key, scope, ';'.join(signed), signature)
    return authorization, ';'.join(signed)


class S3Storage:
    """An S3-compatible bucket (Backblaze B2 speaks this), path-style, one prefix.

    Stated limits: single PUT (no multipart) up to `MAX_PUT`; path-style addressing only, which
    B2 and MinIO accept and AWS still serves for bucket-scoped endpoints; no signed URLs, because
    playback goes through the server so a URL never becomes a credential a client keeps.
    """

    mode = 's3'

    def __init__(self, endpoint, bucket, region='us-east-1', prefix=DEFAULT_PREFIX,
                 access_key='', secret_key='', timeout=30):
        if not endpoint or not bucket:
            raise StorageError('S3 storage needs an endpoint and a bucket')
        self.endpoint = endpoint.rstrip('/')
        self.bucket = bucket
        self.region = region or 'us-east-1'
        self.prefix = (prefix or '').strip('/')
        self.prefix = (self.prefix + '/') if self.prefix else ''
        self.access_key = access_key
        self.secret_key = secret_key
        self.timeout = timeout
        parts = urlsplit(self.endpoint)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise StorageError('S3 endpoint must be an http(s) URL')
        self._scheme, self._host, self._base = parts.scheme, parts.netloc, parts.path.rstrip('/')

    # -- addressing ---------------------------------------------------------
    def _uri(self, key: str | None = None) -> str:
        path = '%s/%s' % (self._base, self.bucket)
        if key:
            path += '/' + check_key(key)
        return path

    def _object(self, key: str) -> str:
        """The full key inside the bucket, always under the app's own prefix."""
        return self.prefix + check_key(key) if self.prefix else check_key(key)

    def _request(self, method, key=None, body=b'', headers=None, start=None, end=None,
                 payload_hash=None):
        # `key=None` is a bucket-level request; an empty string is not — it is a key that failed
        # validation, and it must not turn into "the bucket" and pass unnoticed.
        uri = self._uri() if key is None else self._uri(self._object(key))
        # `body` may be bytes or an open file: a track can be far larger than the memory this
        # server is allowed to use, so the upload streams and the caller supplies the digest it
        # already computed (the same digest SigV4 signs).
        if payload_hash is None:
            payload = body if isinstance(body, bytes) and body else b''
            payload_hash = hashlib.sha256(payload).hexdigest()
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        send = {
            'host': self._host,
            'x-amz-content-sha256': payload_hash,
            'x-amz-date': stamp,
        }
        if start is not None:
            send['range'] = 'bytes=%d-%s' % (int(start), '' if end is None else str(int(end)))
        for name, value in (headers or {}).items():
            send[name.lower()] = str(value)
        authorization, signed = sign_v4(
            method, self._host, uri, '', send, payload_hash,
            self.access_key, self.secret_key, self.region, amz_date=stamp)
        wire = {name: value for name, value in send.items() if name != 'host'}
        wire['Authorization'] = authorization
        request = urllib.request.Request(
            self.endpoint + uri,
            data=(body if body else None) if not isinstance(body, bytes) else (body or None),
            method=method.upper(), headers=wire)
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            body = error.read()[:400]
            error.close()          # the socket is not needed once the reason has been read
            raise StorageError('%s %s failed: %s' % (method.upper(), uri, error.code),
                               status=error.code, body=body) from None

    def _retry(self, action):
        """Retry 5xx and transport failures; never retry a 4xx (it will fail the same way)."""
        last = None
        for attempt in range(RETRIES):
            try:
                return action()
            except StorageError as error:
                last = error
                if error.status and error.status < 500:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last = StorageError(str(error))
            time.sleep(0.2 * (attempt + 1))
        raise last

    # -- the four operations ------------------------------------------------
    def put(self, key: str, source) -> dict:
        key = check_key(key)
        path = Path(source)
        size = path.stat().st_size
        if size > MAX_PUT:
            raise StorageError('%s is %.1f GiB; one PUT is capped at %d GiB'
                               % (path.name, size / 1024 ** 3, MAX_PUT // 1024 ** 3))
        digest = sha256_file(path)
        # Sent from the file itself, not from a buffer: a 200 MB track is 200 MB of RSS, and the
        # retry reopens rather than rewinds, so a half-sent request cannot resume from the middle.
        def send():
            with open(path, 'rb') as body:
                return self._request('PUT', key, body, {'content-length': str(size),
                                                        'content-type': 'application/octet-stream'},
                                     payload_hash=digest).close()
        self._retry(send)
        seen = self.head(key)
        if seen['size'] != size:
            raise StorageError('upload of %s reported %d bytes, bucket has %d'
                               % (key, size, seen['size']))
        return {'key': key, 'size': size, 'sha256': digest, 'etag': seen.get('etag')}

    def head(self, key: str) -> dict:
        answer = self._retry(lambda: self._request('HEAD', key))
        try:
            length = int(answer.headers.get('Content-Length') or 0)
            return {'size': length, 'etag': (answer.headers.get('ETag') or '').strip('"'),
                    'checksum': answer.headers.get('x-amz-checksum-sha256')}
        finally:
            answer.close()

    def exists(self, key: str) -> bool:
        try:
            self.head(key)
            return True
        except StorageError as error:
            if error.status == 404:
                return False
            raise

    def size(self, key: str) -> int:
        return self.head(key)['size']

    def read_range(self, key: str, start: int = 0, end: int | None = None) -> bytes:
        answer = self._retry(lambda: self._request('GET', key, start=start, end=end))
        try:
            return answer.read()
        finally:
            answer.close()

    def iter_range(self, key: str, start: int = 0, end: int | None = None, size=CHUNK):
        """Streamed ranged read, for the audio route: never buffers the whole track."""
        answer = self._retry(lambda: self._request('GET', key, start=start, end=end))
        try:
            while True:
                block = answer.read(size)
                if not block:
                    return
                yield block
        finally:
            answer.close()

    def delete(self, key: str) -> None:
        self._retry(lambda: self._request('DELETE', key).close())

    def verify(self, key: str, size: int, sha256: str | None = None) -> bool:
        """Size now; content only when the bucket tells us a checksum.

        An ETag is not a content hash — for a single PUT it is the MD5, for a multipart upload it
        is a digest of digests — so it is never compared to a SHA-256.
        """
        try:
            seen = self.head(key)
        except StorageError:
            return False
        if seen['size'] != size:
            return False
        if seen.get('checksum'):
            import base64
            want = base64.b64encode(bytes.fromhex(sha256 or '')).decode()
            return seen['checksum'] == want
        return True


# --------------------------------------------------------------------------- selection

FIELDS = ('storage_mode', 'storage_endpoint', 'storage_bucket', 'storage_region', 'storage_prefix')


def config(store) -> dict:
    """What the bucket settings say, with the environment supplying the credential only."""
    return {
        'mode': (store.setting('storage_mode', 'local') or 'local').lower(),
        'endpoint': store.setting('storage_endpoint', ''),
        'bucket': store.setting('storage_bucket', ''),
        'region': store.setting('storage_region', '') or 'us-east-1',
        'prefix': store.setting('storage_prefix', '') or DEFAULT_PREFIX,
        'access_key': os.environ.get('HARDLYLISTENING_S3_ACCESS_KEY', ''),
        'secret_key': os.environ.get('HARDLYLISTENING_S3_SECRET_KEY', ''),
    }


def build(store, root=None) -> LocalStorage | S3Storage:
    """The adapter the settings select. A misconfigured bucket raises rather than falling back.

    Falling back to local would mean a member's uploads quietly land in a different place than
    the settings say, which is worse than a refused write with a reason.
    """
    settings = config(store)
    if settings['mode'] == 'local':
        return LocalStorage(root if root is not None else store.setting('root', '') or '.')
    if settings['mode'] != 's3':
        raise StorageError('unknown storage mode %r' % settings['mode'])
    if not settings['access_key'] or not settings['secret_key']:
        raise StorageError('S3 storage needs HARDLYLISTENING_S3_ACCESS_KEY and '
                           'HARDLYLISTENING_S3_SECRET_KEY in the process environment')
    return S3Storage(settings['endpoint'], settings['bucket'], settings['region'],
                     settings['prefix'], settings['access_key'], settings['secret_key'])


def public(store) -> dict:
    """The settings a member may see: never the credential, never a signed URL."""
    settings = config(store)
    return {'mode': settings['mode'], 'bucket': settings['bucket'],
            'endpoint': settings['endpoint'], 'region': settings['region'],
            'prefix': settings['prefix'],
            'credential': 'environment' if settings['access_key'] else 'missing'}

# --------------------------------------------------------------------------- playback cache

#: One lock per process: two requests for the same cold object must not write the same `.part`
#: file twice. Cross-process duplication is harmless (both copies are verified) and not worth a
#: lock file for.
_cache_lock = threading.Lock()

#: A cache hit is served from the metadata table without asking the bucket. After this many
#: seconds the row is revalidated with one HEAD: a size that still matches refreshes the row and
#: costs no body fetch, so a copy replaced in the bucket out of band is noticed within the window
#: instead of never.
REVALIDATE = 300.0

CACHE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS storage_cache (
  key TEXT PRIMARY KEY,
  size INTEGER DEFAULT 0,
  sha256 TEXT DEFAULT '',
  cached REAL DEFAULT 0,
  used REAL DEFAULT 0
);"""


def cache_limit(store) -> int:
    """How many bytes of bucket objects this server may keep on disk for playback. 0 = off."""
    try:
        return max(0, int(store.setting('storage_cache_bytes', '0') or 0))
    except (TypeError, ValueError):
        return 0


def cache_root(store) -> Path:
    """Where cached copies live. Never the library root: a cache delete must not remove media."""
    return paths.STORAGE_CACHE_DIR


def cache_path(store, key: str) -> Path:
    root = cache_root(store).resolve()
    target = (root / check_key(key)).resolve()
    if root not in target.parents:
        raise StorageError('cache key leaves the cache directory')
    return target


def cache_schema(store) -> None:
    # Called on the playback hot path (`cached_copy`), so the DDL runs once per connection
    # rather than a failed `CREATE TABLE` attempt and a commit on every play.
    if getattr(store, '_storage_cache_schema', False):
        return
    with store.lock:
        store.conn.execute(CACHE_SCHEMA)
        store.conn.commit()
    store._storage_cache_schema = True


def _cache_row(store, key: str):
    cache_schema(store)
    with store.lock:
        row = store.conn.execute('SELECT * FROM storage_cache WHERE key=?', (key,)).fetchone()
    return dict(row) if row else None


def cache_record(store, key: str, size: int, sha256: str = '') -> None:
    """A new cache file: its size, when it was filled and when it was last used."""
    key = check_key(key)
    now = time.time()
    cache_schema(store)
    with store.lock:
        store.conn.execute(
            'INSERT INTO storage_cache(key,size,sha256,cached,used) VALUES(?,?,?,?,?)'
            ' ON CONFLICT(key) DO UPDATE SET size=excluded.size, sha256=excluded.sha256,'
            ' cached=excluded.cached, used=excluded.used',
            (key, int(size), sha256, now, now))
        store.conn.commit()


def cache_touch(store, key: str) -> None:
    key = check_key(key)
    cache_schema(store)
    with store.lock:
        store.conn.execute('UPDATE storage_cache SET used=? WHERE key=?', (time.time(), key))
        store.conn.commit()


def cache_forget(store, key: str, remove: bool = False) -> None:
    """Drop the row, and optionally the file, for an object this server replaced.

    Called when bytes at a key change (a new mirror upload, a restore, a replacement): a cached
    copy of the old object must not be served as the new one.
    """
    key = check_key(key)
    cache_schema(store)
    with store.lock:
        store.conn.execute('DELETE FROM storage_cache WHERE key=?', (key,))
        store.conn.commit()
    if remove:
        try:
            cache_path(store, key).unlink(missing_ok=True)
        except StorageError:
            pass


def cached_copy(store, key: str, max_age: float | None = None):
    """The local copy for a key, if the metadata still describes it. No network.

    `max_age` is the revalidation window: a row used longer ago than that is treated as a miss so
    the caller does one HEAD and can compare sizes. With `max_age=None` the row is trusted, which
    is what the fill path wants after it already knows the object's size.
    """
    if cache_limit(store) <= 0:
        return None
    key = check_key(key)
    row = _cache_row(store, key)
    if not row:
        return None
    if max_age is not None and time.time() - float(row['used'] or 0) > max_age:
        return None
    target = cache_path(store, key)
    try:
        size = target.stat().st_size
    except OSError:
        # The file was removed by hand or by an older eviction: the row is a lie, so drop it.
        cache_forget(store, key)
        return None
    if size <= 0 or size != int(row['size'] or 0):
        cache_forget(store, key, remove=True)
        return None
    # Eviction orders by mtime, so a hit has to move the file as well as the row: otherwise a
    # track played all day could be evicted because it was *filled* long ago.
    try:
        os.utime(target)
    except OSError:
        cache_forget(store, key, remove=True)
        return None
    return target


def cache_report(store) -> dict:
    """What the cache holds: the files on disk, and how many rows describe them."""
    limit = cache_limit(store)
    root = cache_root(store)
    files = [p for p in root.rglob('*') if p.is_file() and not p.name.endswith('.part')] \
        if root.is_dir() else []
    cache_schema(store)
    with store.lock:
        tracked = store.conn.execute('SELECT COUNT(*) FROM storage_cache').fetchone()[0]
    return {'enabled': limit > 0, 'limit_bytes': limit,
            'bytes': sum(p.stat().st_size for p in files), 'files': len(files),
            'tracked': tracked}


def evict(store, keep=None) -> list[str]:
    """Delete least-recently-used cache files until the total fits the limit.

    `keep` is never deleted, even if it alone is the oldest: the caller just wrote it to answer
    the request in hand. Returns the keys removed, so a log line can say what went; their metadata
    rows go with them.
    """
    limit = cache_limit(store)
    root = cache_root(store)
    if limit <= 0 or not root.is_dir():
        return []
    files = [p for p in root.rglob('*') if p.is_file() and not p.name.endswith('.part')]
    files.sort(key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    kept = Path(keep).resolve() if keep is not None else None
    removed = []
    for path in files:
        if total <= limit:
            break
        if kept is not None and path.resolve() == kept:
            continue
        total -= path.stat().st_size
        path.unlink(missing_ok=True)
        removed.append(str(path.relative_to(root)))
    if removed:
        cache_schema(store)
        with store.lock:
            store.conn.executemany('DELETE FROM storage_cache WHERE key=?',
                                   [(key,) for key in removed])
            store.conn.commit()
    return removed


def ensure_cached(store, adapter, key: str, size: int) -> Path | None:
    """The local copy of one object, filling the cache if it fits. None means "stream instead".

    `size` came from a HEAD the caller already made, so a file of that size is the current object
    and is kept (the row is refreshed) rather than fetched again. A cache is an optimisation,
    never a source of truth: a download that fails or a copy that does not match its size leaves
    no file behind, and the caller falls back to a ranged read. An object larger than the whole
    budget is not cached at all — pretending to cache it would mean evicting everything else for
    one track.
    """
    limit = cache_limit(store)
    if limit <= 0 or size <= 0 or size > limit:
        return None
    key = check_key(key)
    target = cache_path(store, key)
    with _cache_lock:
        if target.is_file() and target.stat().st_size == size:
            os.utime(target)                    # most recently used
            cache_touch(store, key)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(target.suffix + '.part')
        digest = hashlib.sha256()
        try:
            written = 0
            with open(staging, 'wb') as handle:
                for chunk in adapter.iter_range(key, 0, size - 1):
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
            if written != size:
                raise StorageError('cached %s is %d bytes, the object reports %d'
                                   % (key, written, size))
            os.replace(staging, target)
        except Exception:
            staging.unlink(missing_ok=True)
            raise
        cache_record(store, key, size, digest.hexdigest())
        evict(store, keep=target)
    return target
