"""Mirroring a finished track into the bucket, through a queue that survives a restart.

The plan is specific about how this may work, and each clause is a decision this file records:

* **Local first, always.** A track is only offered here *after* it exists as a verified file on
  this disk. Nothing is downloaded into a bucket and nothing is uploaded before it plays locally.
* **Its own queue, persistent, with retries and progress.** The row lives in SQLite, so a restart
  mid-upload resumes rather than losing the work; attempts back off; a failure that will never
  succeed stops instead of spinning.
* **Verified, not assumed.** `storage.put` already HEADs the object and compares the size it
  reported with the bytes sent. `verify()` is called again with the SHA-256 we computed locally
  where the bucket reports a checksum at all.
* **Mirror, not move.** This is the mode: the local original is never deleted, not after a
  successful upload and not after a failure. Remote-with-cache is a different mode with different
  failure behaviour and is not implemented here.
* **Silent in local mode.** A server with no bucket configured does nothing at all — `offer()` is a
  cheap settings read and its answer is False.
* **Migration is asked for, not started.** `migrate()` is what an administrator calls to queue the
  library that already exists; it refuses a bucket it cannot build *before* writing thousands of
  rows, records who asked and when, and is safe to run again because a row that is already
  pending or stored is skipped. Draining stays the standing worker's job.
* **Restore is verified.** `restore()` writes an object back to the library root through a
  staging file, checks the byte count and the SHA-256 when the queue recorded one, and only then
  renames it into place.
* **Deleting a local original does not delete the object.** Mirror mode's whole point: the bucket
  copy survives a local delete, which is the restore path. Nothing here deletes an object to
  mirror a local deletion, and no eviction ever touches the library root.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

#: Attempts before a row is called failed. Three is enough for a transient 5xx or a dropped
#: connection, and few enough that a bad key or a wrong bucket stops rather than hammering.
MAX_TRIES = 5
BACKOFF = 60.0          # doubled per attempt, capped
BACKOFF_MAX = 3600.0
SWEEP_EVERY = 60.0
BATCH = 8

SCHEMA = """\
CREATE TABLE IF NOT EXISTS storage_uploads (
  key TEXT PRIMARY KEY,
  size INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending',
  attempts INTEGER DEFAULT 0,
  next_try REAL DEFAULT 0,
  last_error TEXT DEFAULT '',
  created REAL DEFAULT 0,
  updated REAL DEFAULT 0,
  sha256 TEXT DEFAULT '',
  verified REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS storage_migrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id TEXT DEFAULT '',
  started REAL DEFAULT 0,
  finished REAL DEFAULT 0,
  queued INTEGER DEFAULT 0,
  skipped INTEGER DEFAULT 0,
  missing INTEGER DEFAULT 0,
  repaired INTEGER DEFAULT 0,
  note TEXT DEFAULT ''
);"""

_thread = None
_lock = threading.Lock()


def schema(store) -> None:
    with store.lock:
        store.conn.executescript(SCHEMA)
        # Columns added after the first version: a queue that already exists cannot gain them
        # from `CREATE TABLE IF NOT EXISTS`. Each is idempotent.
        columns = {row['name'] for row in
                   store.conn.execute('PRAGMA table_info(storage_uploads)')}
        if 'sha256' not in columns:
            store.conn.execute("ALTER TABLE storage_uploads ADD COLUMN sha256 TEXT DEFAULT ''")
        if 'verified' not in columns:
            store.conn.execute('ALTER TABLE storage_uploads ADD COLUMN verified REAL DEFAULT 0')
        runs = {row['name'] for row in
                store.conn.execute('PRAGMA table_info(storage_migrations)')}
        if 'repaired' not in runs:
            store.conn.execute(
                'ALTER TABLE storage_migrations ADD COLUMN repaired INTEGER DEFAULT 0')
        store.conn.commit()


def offer(store, path) -> bool:
    """Queue one finished file for the bucket. True when a row was written.

    Never raises: this is called from the download path, where a storage problem must not turn a
    completed download into a failed one. The reason is logged and the row is left alone.
    """
    try:
        from library import storage
        settings = storage.config(store)
        if settings['mode'] != 's3':
            return False
        key = storage.key_for(store, path)
        size = Path(path).stat().st_size
        if size <= 0:
            return False
        schema(store)
        with store.lock:
            row = store.conn.execute('SELECT status FROM storage_uploads WHERE key=?',
                                     (key,)).fetchone()
            if row and row['status'] == 'stored':
                return False
            store.conn.execute(
                'INSERT INTO storage_uploads(key,size,status,attempts,next_try,created,updated)'
                ' VALUES(?,?,\'pending\',0,0,?,?)'
                ' ON CONFLICT(key) DO UPDATE SET size=excluded.size, status=\'pending\','
                ' next_try=0, last_error=\'\'', (key, size, time.time(), time.time()))
            store.conn.commit()
        return True
    except Exception as error:                      # noqa: BLE001 — see the docstring
        try:
            store.log('could not queue %s for storage: %s' % (Path(path).name, error), 'warn')
        except Exception:
            pass
        return False


def due(store, now: float | None = None, limit: int = BATCH) -> list[dict]:
    now = time.time() if now is None else float(now)
    schema(store)
    with store.lock:
        rows = store.conn.execute(
            "SELECT * FROM storage_uploads WHERE status='pending' AND next_try<=?"
            " ORDER BY next_try, key LIMIT ?", (now, int(limit))).fetchall()
    return [dict(row) for row in rows]


def _fail(store, key: str, error: str, attempts: int, now: float) -> str:
    """Record one failed attempt and answer the status the row now has."""
    status = 'failed' if attempts >= MAX_TRIES else 'pending'
    delay = min(BACKOFF * (2 ** max(0, attempts - 1)), BACKOFF_MAX)
    with store.lock:
        store.conn.execute(
            'UPDATE storage_uploads SET status=?, attempts=?, next_try=?, last_error=?, updated=?'
            ' WHERE key=?',
            (status, attempts, now + delay, str(error)[:400], now, key))
        store.conn.commit()
    return status


def tick(store, adapter=None, now: float | None = None, limit: int = BATCH) -> dict:
    """Attempt every row that is due. Answers what happened, for a log line or a test."""
    from library import storage
    now = time.time() if now is None else float(now)
    result = {'stored': 0, 'failed': 0, 'retried': 0, 'missing': 0, 'skipped': ''}
    rows = due(store, now, limit)
    if not rows:
        return result
    if adapter is None:
        try:
            adapter = storage.build(store)
        except storage.StorageError as error:
            # A refused configuration is not a per-file failure: retrying each row against a
            # bucket that cannot be built would burn every attempt on the same answer.
            result['skipped'] = str(error)
            return result
    if getattr(adapter, 'mode', 'local') != 's3':
        result['skipped'] = 'storage is local'
        return result
    # The key is relative to the *resolved* root (that is how `storage.key_for` derived it), so
    # the path is rebuilt the same way rather than from the configured spelling.
    root = Path(store.setting('root')).expanduser().resolve()
    for row in rows:
        key = row['key']
        local = root / key
        if not local.is_file():
            # The original is gone: mirror mode has nothing to mirror, and guessing is worse than
            # saying so. A later download of the same key offers it again.
            with store.lock:
                store.conn.execute("UPDATE storage_uploads SET status='missing', updated=?"
                                   " WHERE key=?", (now, key))
                store.conn.commit()
            result['missing'] += 1
            continue
        try:
            written = adapter.put(key, local)
            if not adapter.verify(key, written['size'], written['sha256']):
                raise storage.StorageError('the bucket did not verify %s after upload' % key)
        except storage.StorageError as error:
            status = _fail(store, key, str(error), row['attempts'] + 1, now)
            result['failed' if status == 'failed' else 'retried'] += 1
            continue
        with store.lock:
            store.conn.execute(
                "UPDATE storage_uploads SET status='stored', attempts=attempts+1, size=?,"
                " last_error='', sha256=?, updated=? WHERE key=?",
                (written['size'], written.get('sha256') or '', now, key))
            store.conn.commit()
        # The object at this key is now these bytes. A cached copy of an older object would be
        # served as the new one, so it goes. A cache problem must not fail a stored upload.
        try:
            storage.cache_forget(store, key, remove=True)
        except Exception:                       # noqa: BLE001 — see the comment
            pass
        result['stored'] += 1
    return result


def migrate(store, by: str = '', limit: int | None = None) -> dict:
    """Queue the library that already exists for the bucket, then check what it says is stored.

    This is the one-shot decision the docstring above used to leave open. It is deliberately not
    automatic: it reads every track row and writes a queue row per file, and a mistake here is a
    bucket full of the wrong objects. It refuses before writing anything when the bucket cannot
    be built, so a missing credential is one refusal rather than ten thousand failed attempts.

    Two passes, because "queued once" is not "still there":

    * **Queue.** A key not yet known is queued; `pending` is skipped; `failed`/`missing` with a
      file that exists is put back to `pending` with attempts reset.
    * **Verify.** A `stored` row is HEADed. An object that is gone (404) or whose size no longer
      matches what was uploaded is put back to `pending` — a vanished object is discovered here,
      by the migration an administrator asked for, instead of by a member's play. The least
      recently verified rows go first, so `limit` walks the whole library over several runs
      rather than checking the same first rows forever.

    Any other bucket answer (403, 5xx) aborts the run with its reason: that is the bucket
    refusing, not an object that is lost, and requeueing every row for it would be wrong.

    Safe to run again and interruptible: the rows are the progress, and a restart resumes through
    the standing worker. `limit` caps each pass, so a large library is walked in batches.
    """
    from library import storage
    settings = storage.config(store)
    if settings['mode'] != 's3':
        raise ValueError('storage is local; there is no bucket to migrate into')
    adapter = storage.build(store)          # refuses a broken configuration by name
    if getattr(adapter, 'mode', 'local') != 's3':
        raise ValueError('storage is local; there is no bucket to migrate into')
    schema(store)
    with store.lock:
        tracks = store.conn.execute(
            "SELECT id, file FROM tracks WHERE COALESCE(file,'')!='' ORDER BY id").fetchall()
        known = {row['key']: row['status'] for row in
                 store.conn.execute('SELECT key,status FROM storage_uploads')}
    started = time.time()
    queued = skipped = missing = 0
    rows = []
    for track in tracks:
        if limit is not None and queued >= int(limit):
            break
        try:
            path = Path(track['file']).expanduser().resolve()
            key = storage.key_for(store, path)
        except (storage.StorageError, OSError):
            # A row pointing outside the root, or at a path that cannot be resolved, has no key
            # to give the bucket. It is counted, not invented.
            missing += 1
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            missing += 1
            continue
        status = known.get(key)
        if status in ('pending', 'stored'):
            skipped += 1
            continue
        rows.append((key, path.stat().st_size))
        queued += 1
    now = time.time()
    with store.lock:
        store.conn.executemany(
            'INSERT INTO storage_uploads(key,size,status,attempts,next_try,created,updated)'
            " VALUES(?,?,'pending',0,0,?,?)"
            " ON CONFLICT(key) DO UPDATE SET size=excluded.size, status='pending',"
            " attempts=0, next_try=0, last_error=''",
            [(key, size, now, now) for key, size in rows])
    verified, repaired = _verify(store, adapter, limit, now)
    with store.lock:
        cursor = store.conn.execute(
            'INSERT INTO storage_migrations(member_id,started,finished,queued,skipped,missing,'
            " repaired,note) VALUES(?,?,?,?,?,?,?,'')",
            (str(by or ''), started, time.time(), queued, skipped, missing, repaired))
        store.conn.commit()
        migration_id = cursor.lastrowid
    return {'id': migration_id, 'queued': queued, 'skipped': skipped, 'missing': missing,
            'verified': verified, 'repaired': repaired,
            'by': str(by or ''), 'summary': summary(store)}


def _verify(store, adapter, limit, now: float) -> tuple[int, int]:
    """HEAD every stored row the budget allows, oldest-verified first. (verified, repaired)."""
    from library import storage
    sql = ("SELECT key, size FROM storage_uploads WHERE status='stored'"
           ' ORDER BY verified, key')
    with store.lock:
        if limit is None:
            stored = store.conn.execute(sql).fetchall()
        else:
            stored = store.conn.execute(sql + ' LIMIT ?', (max(1, int(limit)),)).fetchall()
    verified = repaired = 0
    for row in stored:
        key = row['key']
        try:
            head = adapter.head(key)
        except storage.StorageError as error:
            if getattr(error, 'status', None) != 404:
                # The bucket refused the request (auth, 5xx). That is not a lost object, and
                # requeueing every row for it would bury the real reason under retries.
                raise
            head = None
        size = int((head or {}).get('size') or 0)
        if head is None or size <= 0 or (row['size'] and size != int(row['size'])):
            with store.lock:
                store.conn.execute(
                    "UPDATE storage_uploads SET status='pending', attempts=0, next_try=0,"
                    ' verified=0, last_error=?, updated=? WHERE key=?',
                    ('the object was missing or sized differently when migration checked it',
                     now, key))
                store.conn.commit()
            repaired += 1
            continue
        with store.lock:
            store.conn.execute(
                'UPDATE storage_uploads SET verified=?, size=?, updated=? WHERE key=?',
                (now, size, now, key))
            store.conn.commit()
        verified += 1
    return verified, repaired


def migrations(store, limit: int = 5) -> list[dict]:
    """The newest migration runs, with the live queue totals beside them."""
    schema(store)
    with store.lock:
        rows = store.conn.execute(
            'SELECT * FROM storage_migrations ORDER BY id DESC LIMIT ?',
            (max(1, int(limit)),)).fetchall()
    return [dict(row) for row in rows]


def restore(store, key: str, adapter=None) -> dict:
    """Write one object back to the library root, verified, through a staging file.

    The destination is the key itself, so the track row that names it needs no change. The size
    is checked against the object's own HEAD and the SHA-256 against the queue's record when one
    exists; a mismatch removes the staging file and raises rather than leaving a half-written
    track that looks playable.
    """
    import hashlib
    import os
    from library import storage
    schema(store)
    key = storage.check_key(key)
    if adapter is None:
        adapter = storage.build(store)
    if getattr(adapter, 'mode', 'local') != 's3':
        raise storage.StorageError('storage is local; there is nothing to restore from')
    head = adapter.head(key)
    size = int(head.get('size') or 0)
    if size <= 0:
        raise storage.StorageError('object %s is empty or missing' % key)
    root = Path(store.setting('root')).expanduser().resolve()
    target = (root / key).resolve()
    if root not in target.parents:
        raise storage.StorageError('object key leaves the library root')
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
            raise storage.StorageError('restored %s is %d bytes, the object reports %d'
                                       % (key, written, size))
        sha = digest.hexdigest()
        with store.lock:
            row = store.conn.execute('SELECT sha256 FROM storage_uploads WHERE key=?',
                                     (key,)).fetchone()
        recorded = (row['sha256'] if row else '') or ''
        if recorded and recorded != sha:
            raise storage.StorageError('restored %s does not match the recorded checksum' % key)
        os.replace(staging, target)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    with store.lock:
        if row:
            store.conn.execute(
                "UPDATE storage_uploads SET status='stored', size=?, sha256=?, last_error='',"
                ' updated=? WHERE key=?', (size, sha, time.time(), key))
        store.conn.commit()
    # The restored file and the object are the same bytes; a cached copy from before the restore
    # may not be, so it is dropped rather than guessed about.
    try:
        storage.cache_forget(store, key, remove=True)
    except Exception:                           # noqa: BLE001 — a cache row is not the restore
        pass
    return {'key': key, 'path': str(target), 'size': size, 'sha256': sha}


def summary(store) -> dict:
    schema(store)
    with store.lock:
        rows = store.conn.execute('SELECT status, COUNT(*) AS n FROM storage_uploads'
                                  ' GROUP BY status').fetchall()
    counts = {row['status']: row['n'] for row in rows}
    return {'pending': counts.get('pending', 0), 'stored': counts.get('stored', 0),
            'failed': counts.get('failed', 0), 'missing': counts.get('missing', 0)}


def start(store) -> None:
    """The standing worker: one pass soon after boot, then one a minute while the server is up."""
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, args=(store,), name='storage-upload', daemon=True)
        _thread.start()


def _loop(store) -> None:
    time.sleep(20)          # let the queue finish opening the database first
    while True:
        try:
            tick(store)
        except Exception as error:                  # noqa: BLE001 — a worker must not die
            try:
                store.log('storage upload pass failed: %s' % error, 'warn')
            except Exception:
                pass
        time.sleep(SWEEP_EVERY)