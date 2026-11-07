"""Self-only favorites/pins. Memberships reference canonical media, never copy it.

Likes and pins add explicit personal saves (albums snapshot current tracks,
playlists snapshot current membership). Undo removes only the preference, not
saves. Preferences remain references if a save is explicitly removed later.
"""
from urllib.parse import urlparse
from security import Denied
import social

FAVORITES_ID = 'favorites'


def schema(store):
    store.conn.executescript('''
    CREATE TABLE IF NOT EXISTS listening_favorites (
      member_id TEXT NOT NULL REFERENCES members(id),
      track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
      PRIMARY KEY(member_id,track_id));
    CREATE TABLE IF NOT EXISTS listening_pins (
      member_id TEXT NOT NULL REFERENCES members(id), kind TEXT NOT NULL
      CHECK(kind IN ('track','album','playlist')), target TEXT NOT NULL,
      PRIMARY KEY(member_id,kind,target));
    ''')


def snapshot(store, uid):
    # Hide stale polymorphic references after catalog deletion/retagging.
    tracks = {str(r[0]) for r in store.conn.execute('SELECT id FROM tracks')}
    playlists = {str(r[0]) for r in store.conn.execute('SELECT id FROM playlists')}
    albums = {social.album_id(dict(r)) for r in store.conn.execute('SELECT artist_name,album FROM tracks')}
    valid = {'track': tracks, 'playlist': playlists, 'album': albums}
    return {'favorites_id': FAVORITES_ID,
            'favorites': [r[0] for r in store.conn.execute('SELECT track_id FROM listening_favorites WHERE member_id=? ORDER BY track_id', (uid,)) if str(r[0]) in tracks],
            'pins': [dict(r) for r in store.conn.execute('SELECT kind,target FROM listening_pins WHERE member_id=? ORDER BY kind,target', (uid,)) if r['target'] in valid[r['kind']]]}


def save(store, uid, kind, target):
    if kind == 'playlist':
        store.conn.execute('INSERT OR IGNORE INTO listening_playlists VALUES(?,?)', (uid, int(target)))
        store.conn.execute('INSERT OR IGNORE INTO listening_playlist_tracks SELECT ?,playlist_id,track_id,position FROM playlist_tracks WHERE playlist_id=?', (uid, int(target)))
    else:
        ids = [int(target)] if kind == 'track' else [r['id'] for r in store.conn.execute('SELECT id,artist_name,album FROM tracks') if social.album_id(dict(r)) == target]
        store.conn.executemany('INSERT OR IGNORE INTO listening_tracks VALUES(?,?)', [(uid, identity) for identity in ids])


def browser(handler, store, security):
    import listening
    member = security.authenticate(handler, store)
    uid = member['id']
    handler.member = member
    path = urlparse(handler.path).path
    if path != '/api/listening/preferences':
        raise Denied(404, 'Not found')
    if handler.command not in ('GET', 'POST'):
        raise Denied(405, 'Method not allowed')
    if handler.command == 'POST':
        security.csrf(handler)
        security.limit(uid, 'preferences-write', 60)
        body = handler.body()
        if not isinstance(body, dict) or set(body) != {'action', 'kind', 'id', 'enabled'}:
            raise Denied(400, 'action, kind, id and enabled required; no member override')
        action, kind, target, enabled = (body[k] for k in ('action', 'kind', 'id', 'enabled'))
        if action not in ('favorite', 'pin') or type(enabled) is not bool or not isinstance(kind, str):
            raise Denied(400, 'Invalid preference')
        if action == 'favorite' and kind != 'track':
            raise Denied(400, 'Only songs can be favorites')
        if kind in ('track', 'playlist'):
            if type(target) is not int:
                raise Denied(400, 'Integer id required')
            target = str(target)
        elif not isinstance(target, str):
            raise Denied(400, 'Invalid target')
    with store.lock:
        listening.schema(store, security)
        schema(store)
        if handler.command == 'POST':
            social.validate_target(store, kind, target)
            try:
                if enabled:
                    save(store, uid, kind, target)
                if action == 'favorite':
                    if enabled:
                        store.conn.execute('INSERT OR IGNORE INTO listening_favorites VALUES(?,?)', (uid, int(target)))
                    else:
                        store.conn.execute('DELETE FROM listening_favorites WHERE member_id=? AND track_id=?', (uid, int(target)))
                elif enabled:
                    store.conn.execute('INSERT OR IGNORE INTO listening_pins VALUES(?,?,?)', (uid, kind, target))
                else:
                    store.conn.execute('DELETE FROM listening_pins WHERE member_id=? AND kind=? AND target=?', (uid, kind, target))
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        handler.json(snapshot(store, uid))
