"""The daily boost: one rolling 24-hour slot per member, prepended to the Feed.

Split out of `activity.py` (SOCIAL-001) because it is its own table and its own expiry rule.
Switching songs inside the window swaps the track and keeps the expiry; the same song again does
not refresh it. `social.service` is the entry point; `social.activity` re-exports these names so
`activity.boost` keeps working.
"""
from __future__ import annotations

import time
from security import Denied

BOOST_TTL = 24 * 60 * 60


def boost(store, member_id: str, track_id: int, now: float | None = None) -> dict:
    """Set this member's 24-hour slot; switching songs never extends its expiry."""
    if not member_id:
        raise Denied(401, 'Sign in to boost a song')
    if type(track_id) is not int or track_id < 1:
        raise Denied(400, 'Positive track_id required')
    now = time.time() if now is None else float(now)
    with store.lock:
        if not store.conn.execute('SELECT id FROM tracks WHERE id=?', (track_id,)).fetchone():
            raise Denied(404, 'Track not found')
        row = store.conn.execute('SELECT track_id,boosted_at,expires_at FROM song_boosts WHERE member_id=?', (member_id,)).fetchone()
        if row and row['expires_at'] > now:
            swapped = row['track_id'] != track_id
            if swapped:
                store.conn.execute('UPDATE song_boosts SET track_id=? WHERE member_id=?', (track_id, member_id))
                store.conn.commit()
            return {'track_id': track_id, 'boosted_at': row['boosted_at'],
                    'expires_at': row['expires_at'], 'swapped': swapped,
                    'already_active': not swapped}
        expires = now + BOOST_TTL
        store.conn.execute('''INSERT INTO song_boosts(member_id,track_id,boosted_at,expires_at) VALUES(?,?,?,?)
          ON CONFLICT(member_id) DO UPDATE SET track_id=excluded.track_id,
          boosted_at=excluded.boosted_at,expires_at=excluded.expires_at''',
                           (member_id, track_id, now, expires))
        store.conn.commit()
        return {'track_id': track_id, 'boosted_at': now, 'expires_at': expires, 'swapped': False}


def current_boost(store, member_id: str, now: float | None = None) -> dict | None:
    now = time.time() if now is None else float(now)
    with store.lock:
        row = store.conn.execute('''SELECT b.track_id,b.boosted_at,b.expires_at,t.title,t.artist_name
          FROM song_boosts b JOIN tracks t ON t.id=b.track_id
          WHERE b.member_id=? AND b.expires_at>?''', (member_id, now)).fetchone()
    if not row:
        return None
    return {'track_id': row['track_id'], 'boosted_at': row['boosted_at'],
            'expires_at': row['expires_at'], 'track': {'id': row['track_id'],
            'title': row['title'], 'artist_name': row['artist_name']}}


def active_boosts(store, limit: int = 80, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else float(now)
    with store.lock:
        rows = store.conn.execute('''SELECT b.member_id,b.track_id,b.boosted_at,b.expires_at,
          m.display_name,m.profile_icon,t.title,t.artist_name FROM song_boosts b
          JOIN members m ON m.id=b.member_id JOIN tracks t ON t.id=b.track_id
          WHERE b.expires_at>? ORDER BY b.boosted_at DESC,b.member_id LIMIT ?''',
                                  (now, max(1, int(limit)))).fetchall()
    return [{'kind': 'boost', 'member_id': r['member_id'],
             'member': (r['display_name'] or '').strip() or 'Someone',
             'member_icon': r['profile_icon'] or '', 'ts': r['boosted_at'],
             'expires_at': r['expires_at'], 'track': {'id': r['track_id'],
             'title': r['title'], 'artist_name': r['artist_name']}} for r in rows]
