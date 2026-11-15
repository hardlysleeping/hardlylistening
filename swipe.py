"""Swipe: a card feed of Repository songs the member does not own yet.

Repository is shared; this is the private triage of it. A card is always a playable
track that is *not* in the member's library and that the member has not swiped:

    drop   (left)   remember it, never offer it again, add nothing
    keep   (right)  add it to the member's library
    like   (up)     add it to the library and like it

Swipes are per member and permanent until the same member's decision is cleared by
un-saving the song (the row keeps the song out of the feed either way).
"""
import time as _time
from urllib.parse import parse_qs, urlparse

from security import Denied

DECISIONS = ('drop', 'keep', 'like')


def schema(store):
    # The library/favourite tables belong to listening/preferences; make sure they exist
    # before this module joins against them.
    import listening
    from security import Security
    listening.schema(store, Security())
    import preferences
    preferences.schema(store)
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS swipe_swipes (
          member_id TEXT NOT NULL,
          track_id INTEGER NOT NULL,
          decision TEXT NOT NULL CHECK(decision IN ('drop','keep','like')),
          at REAL NOT NULL,
          PRIMARY KEY(member_id, track_id));
        CREATE INDEX IF NOT EXISTS swipe_swipes_member ON swipe_swipes(member_id, at);
        ''')
        store.conn.commit()


def _owned(uid):
    return ('SELECT track_id FROM listening_tracks WHERE member_id=?'
            ' UNION SELECT track_id FROM swipe_swipes WHERE member_id=?')


def candidates(store, uid, limit=8):
    schema(store)
    import genres
    with store.lock:
        rows = store.conn.execute(
            'SELECT * FROM tracks WHERE file IS NOT NULL AND file<>\'\''
            ' AND id NOT IN (' + _owned(uid) + ')'
            ' ORDER BY RANDOM() LIMIT ?', (uid, uid, max(1, min(int(limit), 30)))).fetchall()
    out = [dict(r) for r in rows]
    if out:
        genres.annotate(store, out)
    return [{'id': t['id'], 'title': t.get('title') or '', 'artist_name': t.get('artist_name') or '',
             'album': t.get('album') or '', 'artwork': t.get('artwork') or '',
             'duration': t.get('duration') or 0, 'bpm': t.get('bpm') or 0,
             'genres': t.get('genres') or []} for t in out]


def stats(store, uid):
    schema(store)
    with store.lock:
        rows = store.conn.execute(
            'SELECT decision, COUNT(*) n FROM swipe_swipes WHERE member_id=? GROUP BY decision',
            (uid,)).fetchall()
        left = store.conn.execute(
            'SELECT COUNT(*) n FROM tracks WHERE file IS NOT NULL AND file<>\'\''
            ' AND id NOT IN (' + _owned(uid) + ')', (uid, uid)).fetchone()['n']
    counts = {'keep': 0, 'like': 0, 'drop': 0}
    for row in rows:
        counts[row['decision']] = row['n']
    counts['left'] = left
    return counts


def decide(store, uid, track_id, decision):
    if decision not in DECISIONS:
        raise ValueError('decision must be drop, keep or like')
    schema(store)
    with store.lock:
        row = store.conn.execute('SELECT * FROM tracks WHERE id=?', (int(track_id),)).fetchone()
        if not row or not row['file']:
            raise ValueError('no such playable song')
        store.conn.execute(
            'INSERT OR REPLACE INTO swipe_swipes (member_id,track_id,decision,at) VALUES (?,?,?,?)',
            (uid, int(track_id), decision, _time.time()))
        store.conn.commit()
    if decision in ('keep', 'like'):
        import preferences
        with store.lock:
            preferences.save(store, uid, 'track', str(int(track_id)))
            if decision == 'like':
                store.conn.execute('INSERT OR IGNORE INTO listening_favorites VALUES(?,?)',
                                   (uid, int(track_id)))
            store.conn.commit()
    return stats(store, uid)


def dispatch(handler, store, security):
    member = security.authenticate(handler, store)
    uid = member['id']
    path = urlparse(handler.path).path
    if path == '/api/swipe/next':
        if handler.command != 'GET':
            raise Denied(405, 'Method not allowed')
        security.limit(uid, 'swipe', 120)
        limit = (parse_qs(urlparse(handler.path).query).get('limit') or ['8'])[0]
        try:
            limit = int(limit)
        except ValueError:
            limit = 8
        handler.json({'tracks': candidates(store, uid, limit), 'stats': stats(store, uid)})
        return
    if path == '/api/swipe/decide':
        if handler.command != 'POST':
            raise Denied(405, 'Method not allowed')
        security.csrf(handler)
        security.limit(uid, 'swipe-write', 240)
        body = handler.body()
        if not isinstance(body, dict) or 'id' not in body:
            raise Denied(400, 'id and decision required')
        try:
            result = decide(store, uid, body['id'], str(body.get('decision') or ''))
        except (ValueError, TypeError) as exc:
            raise Denied(400, str(exc))
        handler.json({'ok': True, 'stats': result})
        return
    if path == '/api/swipe':
        handler.json({'stats': stats(store, uid)})
        return
    raise Denied(404, 'Not found')
