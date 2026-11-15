"""Where the member left off: the playing song, its queue and the position.

Two copies exist — this one and `localStorage` on the device — and the client restores the
most recent one that still has a queue. Nothing here plays anything: it is a bookmark.
"""
import json
import time

from security import Denied

MAX_QUEUE = 500


def schema(store):
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_now (
          member_id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          updated REAL NOT NULL);
        ''')
        store.conn.commit()


def get(store, uid):
    schema(store)
    with store.lock:
        row = store.conn.execute('SELECT payload, updated FROM listening_now WHERE member_id=?',
                                 (uid,)).fetchone()
    if not row:
        return None
    try:
        state = json.loads(row['payload'])
    except Exception:
        return None
    state['updated'] = row['updated']
    return state


def put(store, uid, state):
    if not isinstance(state, dict):
        raise Denied(400, 'state object required')
    queue = state.get('queue')
    if not isinstance(queue, list) or len(queue) > MAX_QUEUE:
        raise Denied(400, 'queue must be a list of track ids')
    ids = []
    for item in queue:
        if type(item) is not int or item < 1:
            raise Denied(400, 'queue must be a list of track ids')
        ids.append(item)
    track = state.get('id')
    if track is not None and (type(track) is not int or track < 1):
        raise Denied(400, 'id must be a track id')
    try:
        position = max(0.0, min(float(state.get('time') or 0), 24 * 3600))
    except (TypeError, ValueError):
        position = 0.0
    clean = {'id': track, 'queue': ids, 'time': position,
             'index': ids.index(track) if track in ids else -1}
    now = time.time()
    schema(store)
    with store.lock:
        store.conn.execute('INSERT OR REPLACE INTO listening_now VALUES (?,?,?)',
                           (uid, json.dumps(clean), now))
        store.conn.commit()
    clean['updated'] = now
    return clean


def dispatch(handler, store, security):
    member = security.authenticate(handler, store)
    uid = member['id']
    if handler.command == 'GET':
        security.limit(uid, 'now', 120)
        handler.json({'state': get(store, uid)})
        return
    if handler.command != 'POST':
        raise Denied(405, 'Method not allowed')
    security.csrf(handler)
    security.limit(uid, 'now-write', 120)
    handler.json({'state': put(store, uid, handler.body())})
