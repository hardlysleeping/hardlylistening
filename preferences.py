"""Self-only favorites/pins. Memberships reference canonical media, never copy it.

Likes and pins add explicit personal saves (albums snapshot current tracks,
playlists snapshot current membership). Undo removes only the preference, not
saves. Preferences remain references if a save is explicitly removed later.
"""
from urllib.parse import urlparse
from security import Denied
import social

FAVORITES_ID = 'favorites'
# Two playlists have no row in `playlists`: they are computed from listening_favorites.
# They can still be pinned and added to a member's library by their string id.
VIRTUAL_PLAYLISTS = (FAVORITES_ID, 'public-favorites')


def virtual_tracks(store, uid, target):
    """The songs a computed playlist holds right now, as track ids."""
    if target == FAVORITES_ID:
        return [r[0] for r in store.conn.execute(
            'SELECT track_id FROM listening_favorites WHERE member_id=? ORDER BY track_id', (uid,))]
    if target == 'public-favorites':
        return [r[0] for r in store.conn.execute(
            'SELECT DISTINCT track_id FROM listening_favorites ORDER BY track_id')]
    return []


def schema(store):
    store.conn.executescript('''
    CREATE TABLE IF NOT EXISTS listening_favorites (
      member_id TEXT NOT NULL REFERENCES members(id),
      track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
      PRIMARY KEY(member_id,track_id));
    CREATE TABLE IF NOT EXISTS listening_album_likes (
      member_id TEXT NOT NULL REFERENCES members(id),
      album_key TEXT NOT NULL,
      PRIMARY KEY(member_id, album_key));
    CREATE TABLE IF NOT EXISTS listening_theme (
      member_id TEXT PRIMARY KEY,
      base TEXT NOT NULL,
      accent TEXT NOT NULL,
      trim TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS listening_pins (
      member_id TEXT NOT NULL REFERENCES members(id), kind TEXT NOT NULL
      CHECK(kind IN ('track','album','playlist')), target TEXT NOT NULL,
      position INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(member_id,kind,target));
    ''')


def ui_schema(store):
    """One small row per member: which view they were on and how far down."""
    store.conn.execute(
        'CREATE TABLE IF NOT EXISTS listening_ui('
        ' member_id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL)')
    store.conn.commit()


def ui_state(store, uid):
    try:
        row = store.conn.execute(
            'SELECT state FROM listening_ui WHERE member_id=?', (uid,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        import json
        return json.loads(row['state'])
    except Exception:
        return None


def set_ui_state(store, uid, body):
    """Store the view pointer. Bounded size, one upsert, no history kept."""
    import json, time
    if not isinstance(body, dict):
        raise Denied(400, 'state object required')
    allowed = {'view', 'scope', 'libview', 'detail', 'y', 'pins'}
    if set(body) - allowed:
        raise Denied(400, 'unknown view state keys')
    if 'pins' in body and body['pins'] not in (0, 1, True, False):
        raise Denied(400, 'pins must be 0 or 1')
    # A partial write keeps the rest: the nav pointer and the pin hide are saved by
    # different callers, and replacing the row would drop whichever came first.
    merged = dict(ui_state(store, uid) or {})
    merged.update(body)
    encoded = json.dumps(merged, separators=(',', ':'))
    if len(encoded) > 512:
        raise Denied(400, 'view state too large')
    with store.lock:
        ui_schema(store)
        store.conn.execute(
            'INSERT INTO listening_ui(member_id, state, updated) VALUES(?,?,?)'
            ' ON CONFLICT(member_id) DO UPDATE SET state=excluded.state, updated=excluded.updated',
            (uid, encoded, time.time()))
        store.conn.commit()
    return body


def public_favorites(store):
    """Who liked what, across every member.

    Favorites are the one personal preference that is deliberately public: they are what
    makes a shared "everyone's favorites" playlist possible. Pins stay private. Members
    whose account is gone still count, so a like never disappears silently.
    """
    tracks = {str(r[0]) for r in store.conn.execute('SELECT id FROM tracks')}
    out = {}
    for row in store.conn.execute('''
            SELECT f.track_id, f.member_id,
                   COALESCE(m.display_name, '') AS name,
                   COALESCE(m.profile_icon, '') AS icon
              FROM listening_favorites f
              LEFT JOIN members m ON m.id = f.member_id
             ORDER BY f.track_id, name'''):
        if str(row['track_id']) not in tracks:
            continue
        out.setdefault(str(row['track_id']), []).append(
            {'id': row['member_id'],
             'name': (row['name'] or '').strip() or 'Member',
             'icon': row['icon'] or ''})
    return out


def snapshot(store, uid):
    # Hide stale polymorphic references after catalog deletion/retagging.
    tracks = {str(r[0]) for r in store.conn.execute('SELECT id FROM tracks')}
    playlists = {str(r[0]) for r in store.conn.execute('SELECT id FROM playlists')}
    playlists.update(VIRTUAL_PLAYLISTS)
    albums = {social.album_id(dict(r)) for r in store.conn.execute('SELECT artist_name,album FROM tracks')}
    valid = {'track': tracks, 'playlist': playlists, 'album': albums}
    return {'favorites_id': FAVORITES_ID,
            'album_likes': [r[0] for r in store.conn.execute(
                'SELECT album_key FROM listening_album_likes WHERE member_id=? ORDER BY album_key', (uid,))
                if r[0] in albums],
            'favorites': [r[0] for r in store.conn.execute('SELECT track_id FROM listening_favorites WHERE member_id=? ORDER BY track_id', (uid,)) if str(r[0]) in tracks],
            'pins': [dict(r) for r in store.conn.execute(
                'SELECT kind,target FROM listening_pins WHERE member_id=?'
                ' ORDER BY position, kind, target', (uid,)) if r['target'] in valid[r['kind']]],
            # Favorites are the public half of preferences: the client builds two
            # dynamic playlists from them — yours, and everyone's.
            'public_favorites': public_favorites(store),
            'theme': theme(store, uid)}


PIN_LIMIT = 18

DEFAULT_THEME = {'base': '#101014', 'accent': '#58d3a8', 'trim': '#343641'}
_COLOUR = __import__('re').compile(r'^#[0-9a-fA-F]{6}$')


def theme(store, uid):
    """Each member's own base/accent/trim; the app falls back to the defaults."""
    schema(store)
    with store.lock:
        row = store.conn.execute(
            'SELECT base,accent,trim FROM listening_theme WHERE member_id=?', (uid,)).fetchone()
    if not row:
        return dict(DEFAULT_THEME)
    return {'base': row['base'], 'accent': row['accent'], 'trim': row['trim']}


def set_theme(store, uid, body):
    if not isinstance(body, dict):
        raise Denied(400, 'Invalid theme')
    clean = {}
    for key in ('base', 'accent', 'trim'):
        value = body.get(key)
        if not isinstance(value, str) or not _COLOUR.match(value):
            raise Denied(400, '%s must be a #rrggbb colour' % key)
        clean[key] = value.lower()
    schema(store)
    with store.lock:
        store.conn.execute('INSERT OR REPLACE INTO listening_theme VALUES (?,?,?,?)',
                           (uid, clean['base'], clean['accent'], clean['trim']))
        store.conn.commit()
    return clean


def save(store, uid, kind, target):
    if kind == 'playlist' and target in VIRTUAL_PLAYLISTS:
        # No playlist row to reference: the songs themselves are the save.
        store.conn.executemany('INSERT OR IGNORE INTO listening_tracks VALUES(?,?)',
                               [(uid, track_id) for track_id in virtual_tracks(store, uid, target)])
        return
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
    if path == '/api/listening/pin-order':
        # Dragging the pinned tiles writes their order; only the member's own pins move.
        if handler.command != 'POST':
            raise Denied(405, 'Method not allowed')
        security.csrf(handler)
        security.limit(uid, 'preferences-write', 60)
        body = handler.body()
        order = body.get('order') if isinstance(body, dict) else None
        if not isinstance(order, list) or len(order) > PIN_LIMIT:
            raise Denied(400, 'order must list at most %d pins' % PIN_LIMIT)
        cleaned = []
        for item in order:
            if not isinstance(item, dict):
                raise Denied(400, 'Invalid order')
            kind, target = item.get('kind'), item.get('target')
            if kind not in ('track', 'album', 'playlist') or not isinstance(target, str):
                raise Denied(400, 'Invalid pin')
            cleaned.append((kind, target))
        with store.lock:
            listening.schema(store, security)
            schema(store)
            held = [(r['kind'], r['target']) for r in store.conn.execute(
                'SELECT kind,target FROM listening_pins WHERE member_id=?'
                ' ORDER BY position, kind, target', (uid,))]
            known = set(held)
            given = set(cleaned)
            # Anything pinned but missing from the payload keeps its place at the end.
            ordered = [pin for pin in cleaned if pin in known]
            ordered += [pin for pin in held if pin not in given]
            for index, (kind, target) in enumerate(ordered):
                store.conn.execute(
                    'UPDATE listening_pins SET position=?'
                    ' WHERE member_id=? AND kind=? AND target=?',
                    (index, uid, kind, target))
            store.conn.commit()
        handler.json(snapshot(store, uid))
        return
    if path == '/api/listening/ui':
        if handler.command == 'GET':
            handler.json({'state': ui_state(store, uid)})
            return
        if handler.command != 'POST':
            raise Denied(405, 'Method not allowed')
        security.csrf(handler)
        security.limit(uid, 'preferences-write', 60)
        handler.json({'state': set_ui_state(store, uid, handler.body())})
        return
    if path == '/api/listening/theme':
        if handler.command == 'GET':
            handler.json({'theme': theme(store, uid)})
            return
        if handler.command != 'POST':
            raise Denied(405, 'Method not allowed')
        security.csrf(handler)
        security.limit(uid, 'preferences-write', 60)
        handler.json({'theme': set_theme(store, uid, handler.body())})
        return
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
        if action not in ('favorite', 'pin', 'like') or type(enabled) is not bool or not isinstance(kind, str):
            raise Denied(400, 'Invalid preference')
        if action == 'like' and kind != 'album':
            raise Denied(400, 'Only albums can be liked')
        if action == 'favorite' and kind != 'track':
            raise Denied(400, 'Only songs can be favorites')
        if kind in ('track', 'playlist'):
            # A computed playlist is addressed by its string id, not a row id.
            virtual = kind == 'playlist' and target in VIRTUAL_PLAYLISTS
            if virtual:
                pass
            elif type(target) is not int:
                raise Denied(400, 'Integer id required')
            else:
                target = str(target)
        elif not isinstance(target, str):
            raise Denied(400, 'Invalid target')
    with store.lock:
        listening.schema(store, security)
        schema(store)
        if handler.command == 'POST':
            # A computed playlist has no row for the social validator to find; it is a
            # real target for pins and saves all the same.
            if not (kind == 'playlist' and target in VIRTUAL_PLAYLISTS):
                social.validate_target(store, kind, target)
            if action == 'pin' and enabled:
                # The pinned carousel shows six at a time, three pages deep.
                held = store.conn.execute(
                    'SELECT COUNT(*) n FROM listening_pins WHERE member_id=?', (uid,)).fetchone()['n']
                already = store.conn.execute(
                    'SELECT 1 FROM listening_pins WHERE member_id=? AND kind=? AND target=?',
                    (uid, kind, target)).fetchone()
                if held >= PIN_LIMIT and not already:
                    raise Denied(400, 'Up to %d pins — unpin one first' % PIN_LIMIT)
            try:
                if enabled:
                    save(store, uid, kind, target)
                if action == 'favorite':
                    if enabled:
                        store.conn.execute('INSERT OR IGNORE INTO listening_favorites VALUES(?,?)', (uid, int(target)))
                    else:
                        store.conn.execute('DELETE FROM listening_favorites WHERE member_id=? AND track_id=?', (uid, int(target)))
                elif action == 'like':
                    if enabled:
                        store.conn.execute('INSERT OR IGNORE INTO listening_album_likes VALUES(?,?)', (uid, target))
                    else:
                        store.conn.execute('DELETE FROM listening_album_likes WHERE member_id=? AND album_key=?', (uid, target))
                elif enabled:
                    store.conn.execute(
                        'INSERT OR IGNORE INTO listening_pins (member_id,kind,target,position)'
                        ' VALUES (?,?,?,COALESCE((SELECT MAX(position)+1 FROM listening_pins'
                        ' WHERE member_id=?),0))', (uid, kind, target, uid))
                else:
                    store.conn.execute('DELETE FROM listening_pins WHERE member_id=? AND kind=? AND target=?', (uid, kind, target))
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        handler.json(snapshot(store, uid))
