"""Administrative metadata decision audit, independent of media ownership."""
import json
import time
from pathlib import Path


def schema(store):
    store.conn.execute('''CREATE TABLE IF NOT EXISTS match_decisions (
        id INTEGER PRIMARY KEY, created REAL NOT NULL, job_id INTEGER NOT NULL,
        track_id INTEGER NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL,
        prior_url TEXT NOT NULL, chosen_url TEXT NOT NULL, candidates TEXT NOT NULL,
        reason TEXT NOT NULL, status TEXT NOT NULL)''')


def record(store, job, track, chosen, candidates, reason, status):
    with store.lock:
        schema(store)
        store.conn.execute('''INSERT INTO match_decisions
            (created,job_id,track_id,title,artist,prior_url,chosen_url,candidates,reason,status)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (time.time(), job['id'], track['id'], track['title'],
            track.get('artist_name', ''), job.get('resolved_url') or '',
            chosen['url'] if chosen else '', json.dumps(candidates), reason, status))
        store.conn.commit()


def entries(store):
    with store.lock:
        schema(store)
        import replacements
        rows = [dict(r) for r in store.conn.execute(
            'SELECT * FROM match_decisions ORDER BY id DESC LIMIT 200')]
        return sorted(rows + replacements.entries(store), key=lambda r: r['created'], reverse=True)[:200]


def clear(store, entry_id=None):
    import replacements
    with store.lock:
        replacements.schema(store)
        if entry_id is None:
            store.conn.execute("UPDATE source_replacements SET hidden=1")
        elif int(entry_id) < 0:
            store.conn.execute("UPDATE source_replacements SET hidden=1 WHERE id=?", (-int(entry_id),))
        store.conn.commit()
    with store.lock:
        schema(store)
        if entry_id is None:
            store.conn.execute('DELETE FROM match_decisions')
        else:
            store.conn.execute('DELETE FROM match_decisions WHERE id=?', (entry_id,))
        store.conn.commit()


def reverse(store, entry_id):
    if entry_id < 0:
        import replacements
        return replacements.reverse(store, -entry_id)
    with store.lock:
        schema(store)
        row = store.conn.execute('SELECT * FROM match_decisions WHERE id=?', (entry_id,)).fetchone()
        if not row:
            raise ValueError('Decision no longer exists')
        job = store.conn.execute('SELECT * FROM download_jobs WHERE id=?', (row['job_id'],)).fetchone()
        if not job:
            raise ValueError('Download job no longer exists; history only')
        if job['status'] in ('resolving', 'downloading'):
            raise ValueError('Stop the active job before reversing its decision')
        if job['resolved_url'] != row['chosen_url']:
            raise ValueError('Source has changed since this decision; use the latest entry')
        track = store.track_by_id(row['track_id'])
        if track and track.get('file') and Path(track['file']).is_file():
            # Never undo ownership or hide a verified file behind a review job.
            raise ValueError('Downloaded audio preserved. Use Change source to stage a verified replacement; this metadata-only decision has no retained previous file')
        store.conn.execute('''UPDATE download_jobs SET status='review', resolved_url='', pinned=0,
            candidates=?, error_type='review', error_message='Automatic selection reversed by administrator'
            WHERE id=?''', (row['candidates'], row['job_id']))
        store.conn.execute("UPDATE match_decisions SET status='reversed' WHERE id=?", (entry_id,))
        store.conn.commit()
