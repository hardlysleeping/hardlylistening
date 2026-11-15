"""What a member saved, and how a download lands in it (LIB-001).

Split out of `listening.py` so Library owns its own personal tables rather than the Subsonic
surface owning them: which tracks and playlists a member keeps, the download intents that fill the
library once a file exists, and the writes the web UI performs. Device credentials, the Subsonic
`/rest/` surface and request parsing stay in `listening.py` / `library/api.py`.

Personal state only. Nothing here reads another member's rows, and removing a save never deletes
media.
"""
from library.browser_downloads import local_path


def schema(store):
    """The tables a save lives in. Safe to call before `security.schema` has run."""
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_tracks (
          member_id TEXT NOT NULL REFERENCES members(id),
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          PRIMARY KEY(member_id,track_id));
        CREATE TABLE IF NOT EXISTS listening_playlists (
          member_id TEXT NOT NULL REFERENCES members(id),
          playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
          PRIMARY KEY(member_id,playlist_id));
        CREATE TABLE IF NOT EXISTS listening_playlist_tracks (
          member_id TEXT NOT NULL, playlist_id INTEGER NOT NULL,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position INTEGER NOT NULL,
          PRIMARY KEY(member_id,playlist_id,track_id),
          FOREIGN KEY(member_id,playlist_id) REFERENCES listening_playlists(member_id,playlist_id) ON DELETE CASCADE);
        -- A download that should join someone's library once the file is
        -- written. Filled when Download is pressed on the Feed, emptied by the
        -- queue: a queued row would list a song that cannot play yet, and a
        -- failed one would list a song that never will.
        CREATE TABLE IF NOT EXISTS library_intents (
          member_id TEXT NOT NULL REFERENCES members(id),
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          PRIMARY KEY(member_id,track_id));
        ''')


def snapshot(store, uid):
    """Everything the player needs to paint a member's saved scope, in one read."""
    schema(store)
    with store.lock:
        return {
            'tracks': [r[0] for r in store.conn.execute(
                'SELECT track_id FROM listening_tracks WHERE member_id=?', (uid,))],
            'playlists': [r[0] for r in store.conn.execute(
                'SELECT playlist_id FROM listening_playlists WHERE member_id=?', (uid,))],
            'playlist_tracks': [dict(r) for r in store.conn.execute(
                'SELECT playlist_id,track_id,position FROM listening_playlist_tracks'
                ' WHERE member_id=? ORDER BY playlist_id,position,track_id', (uid,))],
            'folder_tracks': [dict(r) for r in store.conn.execute(
                'SELECT folder_id,track_id,position FROM listening_folder_tracks'
                ' WHERE member_id=? ORDER BY folder_id,position,track_id', (uid,))],
        }


def save_tracks(store, uid, track_ids, saved):
    """Save or unsave a list of tracks that is already known (a computed playlist)."""
    schema(store)
    with store.lock:
        if saved:
            store.conn.executemany('INSERT OR IGNORE INTO listening_tracks VALUES(?,?)',
                                   [(uid, track_id) for track_id in track_ids])
        else:
            store.conn.executemany('DELETE FROM listening_tracks WHERE member_id=? AND track_id=?',
                                   [(uid, track_id) for track_id in track_ids])
        store.conn.commit()
    return len(track_ids)


def set_saved(store, uid, kind, identity, saved):
    """Save/unsave one track or one playlist row, raising the 404 the API answers.

    Saving a playlist snapshots its current membership, so later catalogue additions never
    silently widen a member's scope.
    """
    from security import Denied
    schema(store)
    table = 'tracks' if kind == 'track' else 'playlists'
    target = 'listening_' + table
    column = kind + '_id'
    with store.lock:
        if not store.conn.execute('SELECT id FROM ' + table + ' WHERE id=?', (identity,)).fetchone():
            raise Denied(404, 'Not found')
        if saved:
            store.conn.execute(f'INSERT OR IGNORE INTO {target}(member_id,{column}) VALUES(?,?)',
                               (uid, identity))
            if kind == 'playlist':
                store.conn.execute(
                    'INSERT OR IGNORE INTO listening_playlist_tracks'
                    ' SELECT ?,playlist_id,track_id,position FROM playlist_tracks WHERE playlist_id=?',
                    (uid, identity))
        else:
            store.conn.execute(f'DELETE FROM {target} WHERE member_id=? AND {column}=?', (uid, identity))
        store.conn.commit()
    return saved


def want_playlist(store, member_id: str, playlist_id: int) -> int:
    """Downloading from the Feed also puts the release in the library.

    Songs the repository already holds go in straight away; the rest wait in
    `library_intents` so the library is never given a placeholder row.
    """
    if not member_id or not playlist_id:
        return 0
    schema(store)
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            'SELECT t.* FROM tracks t JOIN playlist_tracks pt ON pt.track_id=t.id'
            ' WHERE pt.playlist_id=?', (playlist_id,))]
        for track in rows:
            if local_path(store, track):
                store.conn.execute(
                    'INSERT OR IGNORE INTO listening_tracks(member_id,track_id) VALUES(?,?)',
                    (member_id, track['id']))
            else:
                store.conn.execute(
                    'INSERT OR IGNORE INTO library_intents(member_id,track_id) VALUES(?,?)',
                    (member_id, track['id']))
        store.conn.commit()
    return len(rows)


def deliver_track(store, track_id: int) -> int:
    """The queue wrote this track: hand it to everyone who pressed Download."""
    schema(store)
    with store.lock:
        members = [r[0] for r in store.conn.execute(
            'SELECT member_id FROM library_intents WHERE track_id=?', (track_id,))]
        for member_id in members:
            store.conn.execute(
                'INSERT OR IGNORE INTO listening_tracks(member_id,track_id) VALUES(?,?)',
                (member_id, track_id))
        store.conn.execute('DELETE FROM library_intents WHERE track_id=?', (track_id,))
        store.conn.commit()
    return len(members)


def forget_track(store, track_id: int) -> None:
    """The download ended without a file; drop the promise rather than save it."""
    schema(store)
    with store.lock:
        store.conn.execute('DELETE FROM library_intents WHERE track_id=?', (track_id,))
        store.conn.commit()