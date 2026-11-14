"""Member-owned shared playlists (LIB-001), over canonical IDs; never acquisition.

This is the domain behind `/api/playlists`: membership is an ordered set (no duplicate inside one
playlist), an edit must carry the membership the caller last read, and only the owner may write —
admins moderate elsewhere, not here. Personal listening snapshots deliberately remain independent
of these edits, and nothing here downloads, saves or copies audio.

The on-disk M3U projection is a different module: `library/playlist_files.py`.
"""
import time

import db
from security import Denied

MAX_TRACKS = 10000


def ids(value):
    if (not isinstance(value, list) or len(value) > MAX_TRACKS
            or any(type(i) is not int or not 0 < i <= 9223372036854775807 for i in value)
            or len(set(value)) != len(value)):
        raise Denied(400, 'track_ids must be unique positive integer IDs (maximum 10000)')
    return value


def title(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
        raise Denied(400, 'title must be 1–200 characters')
    return value.strip()


def listing(store, uid, pid=None):
    # A downloaded album or single groups its work in `playlists` too; only a set is a
    # playlist, so the editor never offers a release as one.
    where = ' WHERE ' + db.playlist_rows('p')
    args = []
    if pid is not None:
        where += ' AND p.id=?'
        args.append(pid)
    rows = [dict(r) for r in store.conn.execute('''SELECT p.id,p.title,p.kind,
        COALESCE(o.member_id,i.member_id,'') AS owner_id
        FROM playlists p LEFT JOIN member_playlists o ON o.playlist_id=p.id
        LEFT JOIN member_items i ON i.kind='playlist' AND i.target_id=p.id'''
        + where + ' ORDER BY p.position,p.id', args)]
    links = {}
    for r in store.conn.execute('SELECT playlist_id,track_id FROM playlist_tracks ORDER BY playlist_id,position,track_id'):
        links.setdefault(r[0], []).append(r[1])
    for row in rows:
        row['can_edit'] = row['owner_id'] == uid
        row['track_ids'] = links.get(row['id'], [])
    return rows


def write(store, uid, pid, name=None, tracks=None, expected=None):
    """Create (`pid is None`) or edit one playlist, in one transaction.

    Validation, ownership, metadata and membership share the transaction on purpose; the
    `Denied`s carry the status codes the endpoint already answered with. Returns
    `{'playlist': row, 'created': bool}` — the caller decides whether to record Activity.
    """
    created = False
    with store.lock:
        try:
            store.conn.execute('BEGIN IMMEDIATE')
            if pid is not None:
                rows = listing(store, uid, pid)
                if not rows:
                    raise Denied(404, 'No such playlist')
                current = rows[0]
                # Admins have moderation/deletion elsewhere, not authorship here.
                if not current['can_edit']:
                    raise Denied(403, 'Only the playlist owner may edit it')
                if expected is not None and expected != current['track_ids']:
                    raise Denied(409, 'Playlist changed; reload before editing')
            if tracks is not None:
                for start in range(0, len(tracks), 500):
                    batch = tracks[start:start+500]
                    count = store.conn.execute('SELECT count(*) FROM tracks WHERE id IN (' + ','.join('?' for _ in batch) + ')', batch).fetchone()[0]
                    if count != len(batch):
                        raise Denied(404, 'One or more tracks do not exist')
            if pid is None:
                pid = store.conn.execute('''INSERT INTO playlists(title,provider,source_url,kind,added,position)
                    VALUES(?,'local','','manual',?,(SELECT COALESCE(MAX(position),0)+1 FROM playlists))''', (name, time.time())).lastrowid
                store.conn.execute('INSERT INTO member_playlists VALUES(?,?)', (pid, uid))
                store.conn.execute("INSERT INTO member_items VALUES('playlist',?,?)", (pid, uid))
                created = True
            elif name is not None:
                store.conn.execute('UPDATE playlists SET title=? WHERE id=?', (name, pid))
            if tracks is not None:
                store.conn.execute('DELETE FROM playlist_tracks WHERE playlist_id=?', (pid,))
                store.conn.executemany('INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES(?,?,?)',
                                       [(pid, tid, pos) for pos, tid in enumerate(tracks)])
            store.conn.commit()
        except Exception:
            store.conn.rollback()
            raise
        result = listing(store, uid, pid)[0]
    return {'playlist': result, 'created': created}