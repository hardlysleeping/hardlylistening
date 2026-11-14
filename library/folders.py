"""A member's folders: the tree they file their own songs into (LIB-001).

Split out of `listening.py` with the folder rules kept as rules, not as request parsing: a name is
1–80 printable characters, siblings cannot share a name case-insensitively, a parent must be the
caller's own folder, and adding a track never duplicates it. The refusals keep the status codes the
API already answered with, so the move is invisible from outside.

Folders are personal: every query is scoped by `member_id` and a folder id alone never addresses
another member's row.
"""
from security import Denied


def schema(store):
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_folders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
          parent_id INTEGER, name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
          created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(member_id,id),
          FOREIGN KEY(member_id,parent_id) REFERENCES listening_folders(member_id,id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS listening_folder_tracks (
          member_id TEXT NOT NULL, folder_id INTEGER NOT NULL,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(member_id,folder_id,track_id),
          FOREIGN KEY(member_id,folder_id) REFERENCES listening_folders(member_id,id) ON DELETE CASCADE);
        ''')


def listing(store, uid):
    schema(store)
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            'SELECT id,parent_id,name,position FROM listening_folders'
            ' WHERE member_id=? ORDER BY position,id', (uid,))]


def name_of(value):
    """A folder name, or the 400 the endpoint answers with."""
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
        raise Denied(400, 'Folder name must be 1–80 printable characters')
    return value.strip()


def create(store, uid, name, parent):
    schema(store)
    clean = name_of(name)
    with store.lock:
        if parent is not None and (type(parent) is not int or parent < 1 or not store.conn.execute(
                'SELECT id FROM listening_folders WHERE id=? AND member_id=?', (parent, uid)).fetchone()):
            raise Denied(404, 'Parent folder not found')
        if store.conn.execute('SELECT id FROM listening_folders WHERE member_id=? AND parent_id IS ? AND lower(name)=lower(?)',
                              (uid, parent, clean)).fetchone():
            raise Denied(409, 'A folder with that name already exists here')
        position = store.conn.execute(
            'SELECT COALESCE(MAX(position),-1)+1 FROM listening_folders WHERE member_id=? AND parent_id IS ?',
            (uid, parent)).fetchone()[0]
        folder_id = store.conn.execute(
            'INSERT INTO listening_folders(member_id,parent_id,name,position) VALUES(?,?,?,?)',
            (uid, parent, clean, position)).lastrowid
        store.conn.commit()
    return folder_id


def rename(store, uid, folder_id, name):
    schema(store)
    with store.lock:
        row = store.conn.execute('SELECT id,parent_id FROM listening_folders WHERE id=? AND member_id=?',
                                 (folder_id, uid)).fetchone()
        if not row:
            raise Denied(404, 'Folder not found')
        clean = name_of(name)
        if store.conn.execute('SELECT id FROM listening_folders WHERE member_id=? AND parent_id IS ? AND id<>? AND lower(name)=lower(?)',
                              (uid, row['parent_id'], folder_id, clean)).fetchone():
            raise Denied(409, 'A folder with that name already exists here')
        store.conn.execute('UPDATE listening_folders SET name=? WHERE id=? AND member_id=?',
                           (clean, folder_id, uid))
        store.conn.commit()
    return folder_id


def delete(store, uid, folder_id):
    schema(store)
    with store.lock:
        if not store.conn.execute('SELECT id FROM listening_folders WHERE id=? AND member_id=?',
                                  (folder_id, uid)).fetchone():
            raise Denied(404, 'Folder not found')
        store.conn.execute('DELETE FROM listening_folders WHERE id=? AND member_id=?', (folder_id, uid))
        store.conn.commit()
    return folder_id


def add_track(store, uid, folder_id, track_id):
    schema(store)
    with store.lock:
        if not store.conn.execute('SELECT id FROM listening_folders WHERE id=? AND member_id=?',
                                  (folder_id, uid)).fetchone():
            raise Denied(404, 'Folder not found')
        if not store.conn.execute('SELECT id FROM tracks WHERE id=?', (track_id,)).fetchone():
            raise Denied(404, 'Track not found')
        position = store.conn.execute(
            'SELECT COALESCE(MAX(position),-1)+1 FROM listening_folder_tracks WHERE member_id=? AND folder_id=?',
            (uid, folder_id)).fetchone()[0]
        store.conn.execute('INSERT OR IGNORE INTO listening_folder_tracks(member_id,folder_id,track_id,position)'
                           ' VALUES(?,?,?,?)', (uid, folder_id, track_id, position))
        store.conn.commit()
    return folder_id


def remove_track(store, uid, folder_id, track_id):
    schema(store)
    with store.lock:
        if not store.conn.execute('SELECT id FROM listening_folders WHERE id=? AND member_id=?',
                                  (folder_id, uid)).fetchone():
            raise Denied(404, 'Folder not found')
        if not store.conn.execute('SELECT id FROM tracks WHERE id=?', (track_id,)).fetchone():
            raise Denied(404, 'Track not found')
        store.conn.execute('DELETE FROM listening_folder_tracks WHERE member_id=? AND folder_id=? AND track_id=?',
                           (uid, folder_id, track_id))
        store.conn.commit()
    return folder_id