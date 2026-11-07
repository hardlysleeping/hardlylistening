"""Explicit staged replacements. Retained media is never removed by this module."""
import json
import time
import uuid
from pathlib import Path
from downloaders import ytdlp


def schema(store):
    store.conn.execute('''CREATE TABLE IF NOT EXISTS source_replacements (
        id INTEGER PRIMARY KEY, track_id INTEGER NOT NULL, job_id INTEGER NOT NULL,
        created REAL NOT NULL, old_file TEXT NOT NULL, old_url TEXT NOT NULL,
        old_pinned INTEGER NOT NULL, new_url TEXT NOT NULL, new_file TEXT NOT NULL,
        status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', hidden INTEGER NOT NULL DEFAULT 0)''')


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
        if job['status'] in ('queued', 'retry_wait', 'resolving', 'downloading') or pending(store, job['id']):
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


def run(queue, job, row):
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
        ok, why = ytdlp.verify_audio(written)
        if not ok:
            terminate(store, row, 'failed', why if rc == 0 else ytdlp.error_line(output) or 'Replacement download failed')
            return
        with store.lock, store.conn:
            # Skip/cancel and switch serialize on the same lock; SQL errors roll back before unlocking.
            current = store.job(job['id'])
            latest = pending(store, job['id'])
            if queue._cancel.is_set() or current['status'] == 'cancelled' or not latest or latest['id'] != row['id']:
                terminate(store, row, 'cancelled', 'Replacement cancelled; original retained')
                return
            changed = store.conn.execute('UPDATE tracks SET file=?,size=? WHERE id=? AND file=?',
                (str(written), written.stat().st_size, row['track_id'], row['old_file'])).rowcount
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


def reverse(store, rid):
    with store.lock:
        schema(store)
        row = store.conn.execute('SELECT * FROM source_replacements WHERE id=?', (rid,)).fetchone()
        if not row or row['status'] != 'completed':
            raise ValueError('Only a completed replacement can be reversed')
        track, job = store.track_by_id(row['track_id']), store.job(row['job_id'])
        if not job or job['status'] != 'completed' or pending(store, row['job_id']):
            raise ValueError('Stop the active job before reversing')
        if track['file'] != row['new_file'] or job['resolved_url'] != row['new_url']:
            raise ValueError('Source/file changed; reverse the latest replacement first')
        from library.browser_downloads import local_path
        if local_path(store, {'file': row['old_file']}) is None:
            raise ValueError('Retained previous file is not valid within the current library root')
        ok, why = ytdlp.verify_audio(Path(row['old_file']))
        if not ok:
            raise ValueError('Retained previous file is not valid: ' + why)
        store.conn.execute('UPDATE tracks SET file=?,size=? WHERE id=?', (row['old_file'],Path(row['old_file']).stat().st_size,row['track_id']))
        store.conn.execute('UPDATE download_jobs SET resolved_url=?,pinned=? WHERE id=?', (row['old_url'],row['old_pinned'],row['job_id']))
        store.conn.execute("UPDATE source_replacements SET status='reversed' WHERE id=?", (rid,))
        store.conn.commit()


def entries(store):
    with store.lock:
        schema(store)
        return [dict(id=-r['id'], track_id=r['track_id'], job_id=r['job_id'], created=r['created'], title=t['title'] if t else '', artist=t['artist_name'] if t else '', prior_url=r['old_url'], chosen_url=r['new_url'], candidates='[]', reason=r['error'] or 'Explicit staged source replacement; previous media retained', status='replacement: ' + r['status']) for r in store.conn.execute('SELECT * FROM source_replacements WHERE hidden=0 ORDER BY id DESC LIMIT 200') for t in [store.track_by_id(r['track_id'])]]
