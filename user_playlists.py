"""Member-owned shared playlists over canonical IDs; never saves or acquisition.

Membership is an ordered set (no duplicate recording within one playlist).
Personal listening snapshots deliberately remain independent of these edits.
"""
import re
import time
from urllib.parse import urlparse
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
    where = '' if pid is None else ' WHERE p.id=?'
    rows = [dict(r) for r in store.conn.execute('''SELECT p.id,p.title,p.kind,
        COALESCE(o.member_id,i.member_id,'') AS owner_id
        FROM playlists p LEFT JOIN member_playlists o ON o.playlist_id=p.id
        LEFT JOIN member_items i ON i.kind='playlist' AND i.target_id=p.id'''
        + where + ' ORDER BY p.position,p.id', () if pid is None else (pid,))]
    links = {}
    for r in store.conn.execute('SELECT playlist_id,track_id FROM playlist_tracks ORDER BY playlist_id,position,track_id'):
        links.setdefault(r[0], []).append(r[1])
    for row in rows:
        row['can_edit'] = row['owner_id'] == uid
        row['track_ids'] = links.get(row['id'], [])
    return rows


def dispatch(handler, store, security):
    member = security.authenticate(handler, store)
    handler.member = member
    path = urlparse(handler.path).path
    match = re.fullmatch(r'/api/playlists(?:/([1-9][0-9]*))?', path)
    if not match:
        raise Denied(404, 'Not found')
    pid = int(match[1]) if match[1] else None
    if pid is not None and pid > 9223372036854775807:
        raise Denied(404, 'Not found')
    with store.lock:
        if handler.command == 'GET':
            rows = listing(store, member['id'], pid)
            if pid is not None and not rows:
                raise Denied(404, 'No such playlist')
            handler.json({'playlists': rows} if pid is None else {'playlist': rows[0]})
            return
        if handler.command != 'POST':
            raise Denied(405, 'Method not allowed')
        security.limit(member['id'], 'playlist-write', 60)
        body = handler.body()
        if not isinstance(body, dict) or set(body) - {'title', 'track_ids', 'expected_track_ids'}:
            raise Denied(400, 'Only title, track_ids and expected_track_ids are accepted')
        name = title(body['title']) if 'title' in body else None
        tracks = ids(body['track_ids']) if 'track_ids' in body else None
        expected = ids(body['expected_track_ids']) if 'expected_track_ids' in body else None
        if pid is None and (name is None or expected is not None):
            raise Denied(400, 'New playlists require title; expected_track_ids is only for edits')
        if pid is not None and (not body or (tracks is not None and expected is None)):
            raise Denied(400, 'Membership edits require expected_track_ids from the last read')
        # One transaction includes validation, ownership, metadata and all membership.
        # Do not call Store.playlist/add_to_playlist or Security.claim: they commit.
        created = False
        try:
            store.conn.execute('BEGIN IMMEDIATE')
            if pid is not None:
                rows = listing(store, member['id'], pid)
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
                store.conn.execute('INSERT INTO member_playlists VALUES(?,?)', (pid, member['id']))
                store.conn.execute("INSERT INTO member_items VALUES('playlist',?,?)", (pid, member['id']))
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
        result = listing(store, member['id'], pid)[0]
    # The Feed shows playlist news; written after the transaction so it can never
    # undo an edit.
    import activity
    activity.record(store,
                    activity.KIND_PLAYLIST_NEW if created else activity.KIND_PLAYLIST_EDIT,
                    member_id=member['id'], playlist_id=pid,
                    detail=name or result.get('title') or '')
    handler.json({'playlist': result})
