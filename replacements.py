"""Explicit staged replacements. Retained media is only ever removed by `collect`."""
import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from downloaders import ytdlp

#: How long a replaced file stays available for a reverse. A person can change it
#: (`replacements_keep_days`); `0` keeps everything, which is what this module did before
#: there was a collector.
KEEP_DAYS = 30
#: The collector's own cadence. It also runs once at boot.
COLLECT_EVERY = 6 * 3600

_stop = threading.Event()
_guard = threading.Lock()
_thread = None


def schema(store):
    store.conn.execute('''CREATE TABLE IF NOT EXISTS source_replacements (
        id INTEGER PRIMARY KEY, track_id INTEGER NOT NULL, job_id INTEGER NOT NULL,
        created REAL NOT NULL, old_file TEXT NOT NULL, old_url TEXT NOT NULL,
        old_pinned INTEGER NOT NULL, new_url TEXT NOT NULL, new_file TEXT NOT NULL,
        status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', hidden INTEGER NOT NULL DEFAULT 0,
        pruned REAL NOT NULL DEFAULT 0)''')
    # A database created before retention existed has the table without the column: adding it
    # here keeps `CREATE TABLE IF NOT EXISTS` from being a lie about an old file.
    columns = {row[1] for row in store.conn.execute('PRAGMA table_info(source_replacements)')}
    if 'pruned' not in columns:
        store.conn.execute('ALTER TABLE source_replacements ADD COLUMN pruned REAL NOT NULL DEFAULT 0')


def pending(store, job_id):
    with store.lock:
        schema(store)
        row = store.conn.execute("SELECT * FROM source_replacements WHERE job_id=? AND status IN ('queued','downloading') ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        return dict(row) if row else None


def stage(store, tid, url):
    if not url.startswith(('http://', 'https://')):
        raise ValueError('Replacement requires an explicit http(s) source URL')
    with store.lock:
        schema(store)
        track, job = store.track_by_id(tid), store.job_for_track(tid)
        if not track or not job or not Path(track['file']).is_file():
            raise ValueError('Replacement requires an existing file and download job')
        if job['status'] in ('queued', 'retry_wait', 'resolving', 'downloading', 'verifying') or pending(store, job['id']):
            raise ValueError('Stop the active job before changing its source')
        # Private unique directory inside the canonical root: no collision with old output.
        fmt = store.setting('audio_format', 'mp3')
        target = Path(store.setting('root')).expanduser() / '.replacements' / uuid.uuid4().hex / ('audio.' + fmt)
        store.conn.execute('''INSERT INTO source_replacements
            (track_id,job_id,created,old_file,old_url,old_pinned,new_url,new_file,status)
            VALUES (?,?,?,?,?,?,?,?, 'queued')''',
            (tid, job['id'], time.time(), track['file'], job['resolved_url'] or job['source_url'], job['pinned'], url, str(target)))
        store.conn.execute("UPDATE download_jobs SET status='queued',next_retry_at=0,error_message='' WHERE id=?", (job['id'],))
        store.conn.commit()


def terminate(store, row, status, error=''):
    # A swap that never switched must leave the library exactly as it was: the
    # media parked under `.replacements/retained/` goes back to its own path.
    track = store.track_by_id(row['track_id'])
    if track and track.get('file') == row['old_file']:
        _restore_retained(store, row)
    with store.lock:
        store.conn.execute('UPDATE source_replacements SET status=?,error=? WHERE id=?', (status,error,row['id']))
        store.conn.execute("UPDATE download_jobs SET status='completed',error_message=?,speed=0,eta=0 WHERE id=?", (error,row['job_id']))
        store.conn.commit()


def recover(store):
    with store.lock:
        schema(store)
        rows = [dict(r) for r in store.conn.execute("SELECT * FROM source_replacements WHERE status='downloading'")]
        for row in rows:
            terminate(store, row, 'interrupted', 'Replacement interrupted; original retained. Change source to try again.')


def _taken(store, exclude: int = 0) -> set[str]:
    """Files other rows own. The row being replaced is not competition for its
    own canonical name: it is about to point somewhere else."""
    with store.lock:
        rows = store.conn.execute(
            "SELECT file FROM tracks WHERE file<>'' AND id<>?", (exclude,)).fetchall()
    return {str(Path(r[0])) for r in rows}


def _unique(target: Path, store, exclude: int = 0) -> Path:
    from library import store as library_store
    return library_store.unique(target, _taken(store, exclude))


def _relocate(queue, store, row, written: Path) -> Path:
    """Put the verified file where a download would have put it.

    Staging keeps a swap isolated, but a library that lives in uuid folders is not
    browsable. When the new file's proper name is the one the old file already
    occupies, the old media is moved aside rather than overwritten, and the
    replacement record follows it so `reverse` still has its original.
    """
    from library import store as library_store
    root = Path(store.setting('root')).expanduser()
    track = store.track_by_id(row['track_id'])
    if not track:
        raise OSError('track disappeared')
    target = library_store.path_for(
        root, artist=track.get('artist_name') or '', album=track.get('album') or '',
        playlist_title=queue._collection(track) if queue else '',
        number=track.get('track_number') or 0,
        title=track.get('title') or written.stem,
        extension=written.suffix.lstrip('.'))
    old_file = Path(row['old_file'])
    if target.resolve() == written.resolve():
        return written
    # Retained media must leave the library tree. A file no row points at looks
    # exactly like an orphan to the adoption sweep, which would re-add the song
    # we just replaced at source quality. Keeping it under `.replacements/` (a
    # path the sweep skips) makes "no row, still in the tree" mean one thing:
    # a download that died before it could be recorded.
    with store.lock:
        shared = store.conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE file=? AND id<>?",
            (str(old_file), row['track_id'])).fetchone()[0]
    if not shared and old_file.is_file():
        retained = root / '.replacements' / 'retained' / str(row['id'])
        retained.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_file), str(retained / old_file.name))
    if target.exists() or str(target) in _taken(store, row['track_id']):
        target = _unique(target, store, row['track_id'])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(written), str(target))
    # This path now holds different bytes. If they are also in the playback cache, that copy is
    # the old song, so drop it. A cache row failing to clear is not a failed replacement.
    try:
        from library import storage
        storage.cache_forget(store, storage.key_for(store, target), remove=True)
    except Exception:                           # noqa: BLE001 — see the comment
        pass
    return target


def run(queue, job, row, expected_seconds: float = 0):
    store = queue.store
    target = Path(row['new_file'])
    fmt = target.suffix[1:]
    tmpl = str(target.with_suffix('.%(ext)s'))
    try:
        with store.lock:
            latest = pending(store, job['id'])
            if not latest or latest['id'] != row['id']:
                return
            queue._cancel.clear()
            queue._active_job = job['id']
            store.conn.execute("UPDATE source_replacements SET status='downloading' WHERE id=?", (row['id'],))
            store.conn.commit()
        store.update_job(job['id'], status='downloading')
        target.parent.mkdir(parents=True, exist_ok=True)
        cmd = ytdlp.build_command(row['new_url'], tmpl, ytdlp.YtDlpSettings(audio_format=fmt, archive=None))
        rc, output = ytdlp.run(cmd, cancel=queue._cancel)
        if queue._cancel.is_set():
            terminate(store, row, 'cancelled', 'Replacement cancelled; original retained')
            return
        written = ytdlp.find_output(tmpl, fmt) if rc == 0 else None
        ok, why = ytdlp.verify_audio(written, expected_seconds=expected_seconds)
        if not ok:
            terminate(store, row, 'failed', why if rc == 0 else ytdlp.error_line(output) or 'Replacement download failed')
            return
        try:
            written = _relocate(queue, store, row, written)
        except OSError as error:
            terminate(store, row, 'failed', f'could not place the new file: {error}')
            return
        with store.lock, store.conn:
            # Skip/cancel and switch serialize on the same lock; SQL errors roll back before unlocking.
            current = store.job(job['id'])
            latest = pending(store, job['id'])
            if queue._cancel.is_set() or current['status'] == 'cancelled' or not latest or latest['id'] != row['id']:
                terminate(store, row, 'cancelled', 'Replacement cancelled; original retained')
                return
            changed = store.conn.execute('UPDATE tracks SET file=?,size=?,art_hash=?,art_checked=0 WHERE id=? AND file=?',
                (str(written), written.stat().st_size, '', row['track_id'], row['old_file'])).rowcount
            if not changed:
                terminate(store, row, 'failed', 'Canonical file changed; original retained')
                return
            store.conn.execute("UPDATE download_jobs SET resolved_url=?,pinned=1,status='completed',error_message='',progress=100 WHERE id=?", (row['new_url'], job['id']))
            store.conn.execute("UPDATE source_replacements SET status='completed',new_file=? WHERE id=?", (str(written),row['id']))
            store.conn.commit()
    except Exception as exc:
        with store.lock:
            store.conn.rollback()  # a switch is all-or-nothing, even on SQL failure
        terminate(store, row, 'failed', str(exc))
    finally:
        queue._active_job = 0
        queue._cancel.clear()
        # Failed staged files remain isolated; never glob/delete canonical media.


def _restore_retained(store, row) -> None:
    """Move media out of `.replacements/retained/<id>/` back where it came from.

    A swap parks the old file outside the library tree so the adoption sweep
    cannot mistake it for an orphan. Reversing puts it back at its own path, so
    the library keeps naming media the way it always did.
    """
    old = Path(row['old_file'])
    if old.is_file():
        return
    retained = Path(store.setting('root')).expanduser() / '.replacements' / 'retained' / str(row['id'])
    if not retained.is_dir():
        return
    media = [p for p in retained.iterdir() if p.is_file()]
    if not media:
        return
    old.parent.mkdir(parents=True, exist_ok=True)
    if not old.exists():
        shutil.move(str(sorted(media)[0]), str(old))
        try:
            retained.rmdir()
        except OSError:
            pass


def reverse(store, rid):
    with store.lock:
        schema(store)
        row = store.conn.execute('SELECT * FROM source_replacements WHERE id=?', (rid,)).fetchone()
        if not row or row['status'] != 'completed':
            raise ValueError('Only a completed replacement can be reversed')
        if row['pruned']:
            # The copy existed and was let go on a known day: say when, so the answer is a
            # fact about this library rather than a missing-file error from further down.
            when = time.strftime('%Y-%m-%d', time.localtime(row['pruned']))
            raise ValueError('The previous file was collected on ' + when
                             + ' and cannot be restored; change the source again to get another copy')
        _restore_retained(store, row)
        track, job = store.track_by_id(row['track_id']), store.job(row['job_id'])
        if not job or job['status'] != 'completed' or pending(store, row['job_id']):
            raise ValueError('Stop the active job before reversing')
        # Two swaps of one track can share a canonical path, so the file name alone
        # no longer says which one is current. The newest completed swap does.
        newer = store.conn.execute(
            "SELECT 1 FROM source_replacements WHERE track_id=? AND id>?"
            " AND status IN ('completed','downloading') LIMIT 1",
            (row['track_id'], row['id'])).fetchone()
        # After a swap is relocated, the file this replacement installed may have
        # been moved again by a later one; the job's source still identifies it,
        # and `old_file` is held explicitly, so a reverse restores the media and
        # only leaves the newer copy unreferenced.
        if newer or job['resolved_url'] != row['new_url']:
            raise ValueError('Source/file changed; reverse the latest replacement first')
        from library.browser_downloads import local_path
        if local_path(store, {'file': row['old_file']}) is None:
            raise ValueError('Retained previous file is not valid within the current library root')
        ok, why = ytdlp.verify_audio(Path(row['old_file']))
        if not ok:
            raise ValueError('Retained previous file is not valid: ' + why)
        store.conn.execute('UPDATE tracks SET file=?,size=?,art_hash=?,art_checked=0 WHERE id=?', (row['old_file'],Path(row['old_file']).stat().st_size,'',row['track_id']))
        store.conn.execute('UPDATE download_jobs SET resolved_url=?,pinned=? WHERE id=?', (row['old_url'],row['old_pinned'],row['job_id']))
        store.conn.execute("UPDATE source_replacements SET status='reversed' WHERE id=?", (rid,))
        store.conn.commit()


def latest(store, tid):
    """The most recent staged swap for a track, whatever its state — the dialog's undo."""
    with store.lock:
        schema(store)
        row = store.conn.execute(
            "SELECT * FROM source_replacements WHERE track_id=? AND hidden=0 ORDER BY id DESC LIMIT 1",
            (tid,)).fetchone()
        return dict(row) if row else None


def entries(store):
    with store.lock:
        schema(store)
        return [dict(id=-r['id'], track_id=r['track_id'], job_id=r['job_id'], created=r['created'], title=t['title'] if t else '', artist=t['artist_name'] if t else '', prior_url=r['old_url'], chosen_url=r['new_url'], candidates='[]', reason=r['error'] or 'Explicit staged source replacement; previous media retained', status='replacement: ' + r['status']) for r in store.conn.execute('SELECT * FROM source_replacements WHERE hidden=0 ORDER BY id DESC LIMIT 200') for t in [store.track_by_id(r['track_id'])]]


# ---- retention ------------------------------------------------------------------
# A swap keeps two things under `.replacements/`: the media it replaced (so `reverse` can
# put it back) and the scratch directory the new file downloaded into. Both used to stay
# forever — the module's own docstring promised it — so the folder grew with every source
# change and nothing ever decided when a keep had done its job.
#
# The decision is a window, per replacement, and it is the whole policy: inside it a
# reverse works, outside it the copy is collected and the row says so, so `reverse` answers
# with the truth instead of half-working. `0` turns collection off entirely.

def keep_days(store) -> float:
    """The retention window in days, from settings; the default is `KEEP_DAYS`."""
    return store.setting_float('replacements_keep_days', KEEP_DAYS)


def _collect_folder(folder: Path, store, ref: str) -> tuple[int, int]:
    """Delete the files under `folder`, one log line each, then the folders themselves."""
    freed = 0
    files = 0
    for path in sorted(p for p in folder.rglob('*') if p.is_file()):
        size = path.stat().st_size
        # Logged before the unlink, not after: a line that never had its removal must not
        # look like one that did.
        store.log(f'collected {path.name} ({size} bytes) from {folder.name}', 'info', ref)
        try:
            path.unlink()
        except OSError as error:
            store.log(f'could not collect {path.name}: {error}', 'warn', ref)
            continue
        freed += size
        files += 1
    for path in sorted((p for p in folder.rglob('*') if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass
    return freed, files


def collect(store, now: float = 0, limit: int = 500) -> dict:
    """Remove what a reverse can no longer need, and report what went.

    Two kinds of thing live under `.replacements/`, and they are decided differently:

    * **retained media** (`retained/<id>/`) — the file a completed swap replaced. A row
      inside the window is never touched, whatever else is true of it: that is what the
      window is. A row past it has its copy removed and is marked `pruned`, so `reverse`
      refuses with the reason instead of failing on a missing file.
    * **staging directories** (`<uuid>/`) — download scratch space. An empty one goes at
      once (a finished swap moves its file out, so the directory is litter). One that still
      holds something goes only when its replacement is over *and* the window has passed;
      one a queued or downloading row still points at is never touched, at any age.

    Nothing outside `.replacements/` is read or written: the library tree is not this
    function's business, and canonical media is never globbed.
    """
    with store.lock:
        schema(store)
        rows = [dict(r) for r in store.conn.execute(
            'SELECT id,track_id,created,status,new_file,pruned FROM source_replacements'
            ' ORDER BY id')]
        store.conn.commit()
    days = keep_days(store)
    now = time.time() if not now else now
    report = {'kept': 0, 'removed': 0, 'bytes': 0, 'staging': 0, 'dirs': 0, 'rows': 0,
              'window_days': days, 'reason': ''}
    root = Path(store.setting('root')).expanduser()
    base = root / '.replacements'
    if not base.is_dir():
        return report
    if days <= 0:
        report['reason'] = 'retention is off: replacements_keep_days is 0'
        return report
    cutoff = now - days * 86400
    live = {'queued', 'downloading'}
    done = {'completed', 'reversed', 'failed', 'cancelled', 'interrupted'}
    owner = {Path(r['new_file']).parent.name: r for r in rows if r['new_file']}
    busy = {name for name, row in owner.items() if row['status'] in live}

    # 1. The media a swap parked, oldest first.
    for row in rows:
        if row['pruned'] or row['status'] not in done or not (row['created'] < cutoff):
            continue
        if report['rows'] >= limit:
            break
        folder = base / 'retained' / str(row['id'])
        if not folder.is_dir():
            continue
        freed, files = _collect_folder(folder, store, f'replacement:{row["id"]}')
        report['rows'] += 1
        report['removed'] += files
        report['bytes'] += freed
        if files:
            with store.lock:
                store.conn.execute('UPDATE source_replacements SET pruned=? WHERE id=?',
                                   (now, row['id']))
                store.conn.commit()

    # 2. The scratch directories.
    for folder in sorted(p for p in base.iterdir() if p.is_dir() and p.name != 'retained'):
        row = owner.get(folder.name)
        if folder.name in busy:
            report['kept'] += 1
            continue
        content = [p for p in folder.rglob('*') if p.is_file()]
        if not content:
            # Litter from a swap that moved its file out. No window: nothing to lose.
            try:
                folder.rmdir()
                report['dirs'] += 1
            except OSError:
                report['kept'] += 1
            continue
        if row is not None and (row['status'] in live or not (row['created'] < cutoff)):
            report['kept'] += 1
            continue
        if row is None and folder.stat().st_mtime >= cutoff:
            # No row names it and it is recent: something may be writing right now.
            report['kept'] += 1
            continue
        freed, files = _collect_folder(
            folder, store, f'replacement:{row["id"]}' if row else 'replacement:orphan')
        report['staging'] += files
        report['dirs'] += 1
        report['removed'] += files
        report['bytes'] += freed

    if report['removed']:
        store.log(f'Replacement retention collected {report["removed"]} file(s), '
                  f'{report["bytes"]} bytes (window {days:g} days)', 'info')
    return report


def start(store, interval: float = COLLECT_EVERY) -> bool:
    """Run the collector at boot and then every `interval` seconds, in one thread.

    A daemon, not a scheduler job: collection is housekeeping that has to happen whether or
    not anybody downloads anything, and it must not hold up the queue.
    """
    global _thread
    with _guard:
        if _thread is not None and _thread.is_alive():
            return False
        _thread = threading.Thread(target=_loop, args=(store, interval),
                                   name='replacement-retention', daemon=True)
        _thread.start()
        return True


def stop() -> None:
    _stop.set()


def _loop(store, interval: float) -> None:
    while not _stop.is_set():
        try:
            collect(store)
        except Exception as error:                  # noqa: BLE001 — never kill the process
            store.log(f'Retention collection failed: {error}', 'warn')
        _stop.wait(interval)
