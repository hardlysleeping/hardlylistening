"""What members did — the Feed's Activity tab.

Written at the moment something happens (a play, a download, a playlist edit, a
join, a rename). Plays are the noisy kind, so they are deduplicated on write and
**stacked on read**: one member's consecutive listens inside
`LISTEN_GAP` seconds become a single "played N songs" entry that always lists
every song — it is not a collapsible group, because hiding the songs is exactly
what makes a listening feed useless.

Attribution comes from `member_items` (see library/quota.py), so a track's owner
is whoever claimed it directly, else whoever claimed the playlist it came in with.
"""

from __future__ import annotations

import time
from security import Denied

LISTEN_GAP = 30 * 60        # listens by one member inside this window stack
LISTEN_REPEAT = 10 * 60     # the same track again this soon is not a new event
KEEP = 4000                 # rows kept; the feed only reads the newest ~80
BOOST_TTL = 24 * 60 * 60

KIND_ADD = "add"
KIND_LISTEN = "listen"
KIND_JOIN = "join"
KIND_NAME = "name"
KIND_PLAYLIST_NEW = "playlist_new"
KIND_PLAYLIST_EDIT = "playlist_edit"
KIND_PLAYLIST_DELETE = "playlist_delete"


def record(store, kind: str, member_id: str = "", track_id: int = 0,
           playlist_id: int = 0, detail: str = "", commit: bool = True) -> None:
    """Append one event. Silent: the feed must never break a write path."""
    try:
        now = time.time()
        with store.lock:
            if kind == KIND_LISTEN and member_id and track_id:
                last = store.conn.execute(
                    "SELECT ts, track_id FROM activity WHERE kind='listen' "
                    "AND member_id=? ORDER BY id DESC LIMIT 1", (member_id,)).fetchone()
                if last and last["track_id"] == track_id and now - last["ts"] < LISTEN_REPEAT:
                    return
            store.conn.execute(
                "INSERT INTO activity(ts,member_id,kind,track_id,playlist_id,detail)"
                " VALUES(?,?,?,?,?,?)",
                (now, member_id or "", kind, int(track_id or 0), int(playlist_id or 0),
                 (detail or "")[:300]))
            store.conn.execute(
                "DELETE FROM activity WHERE id < (SELECT MAX(id)-? FROM activity)", (KEEP,))
            if commit:
                store.conn.commit()
    except Exception:
        # Activity is a view of history, never a reason for a download or an edit
        # to fail.
        try:
            store.conn.rollback()
        except Exception:
            pass


def _track(store, track_id: int) -> dict:
    if not track_id:
        return {}
    row = store.conn.execute(
        "SELECT id, title, artist_name FROM tracks WHERE id=?", (track_id,)).fetchone()
    return dict(row) if row else {}


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


def feed(store, limit: int = 80, now: float | None = None) -> list[dict]:
    """Active boosts first, then newest activity with consecutive listens stacked."""
    boosts = active_boosts(store, limit, now)
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT a.*, m.display_name AS member_name, m.profile_icon AS member_icon,"
            " p.title AS playlist_title"
            " FROM activity a LEFT JOIN members m ON m.id=a.member_id"
            " LEFT JOIN playlists p ON p.id=a.playlist_id"
            " ORDER BY a.ts DESC, a.id DESC LIMIT ?", (int(limit) * 3,)).fetchall()]
        items: list[dict] = []
        open_listen: dict | None = None
        for row in rows:
            name = (row["member_name"] or "").strip() or "Someone"
            if row["kind"] == KIND_LISTEN:
                track = _track(store, row["track_id"])
                if not track:
                    continue
                if (open_listen and open_listen["member_id"] == row["member_id"]
                        and open_listen["ts"] - row["ts"] <= LISTEN_GAP):
                    open_listen["tracks"].append(track)
                    open_listen["count"] = len(open_listen["tracks"])
                    open_listen["ts"] = row["ts"]          # the stack's oldest edge
                    continue
                open_listen = {"kind": KIND_LISTEN, "member_id": row["member_id"],
                               "member": name, "member_icon": row["member_icon"] or "",
                               "ts": row["ts"], "count": 1, "tracks": [track]}
                items.append(open_listen)
                continue
            open_listen = None
            items.append({
                "kind": row["kind"], "member_id": row["member_id"], "member": name,
                "member_icon": row["member_icon"] or "", "ts": row["ts"],
                "count": 1, "detail": row["detail"] or "",
                "playlist": row["playlist_title"] or "",
                "playlist_id": row["playlist_id"],
                "track": _track(store, row["track_id"]),
            })
            if len(items) >= limit:
                break
    # Rows arrive newest-first; a stack reads best oldest-first, the way it was
    # actually listened to.
    for item in items:
        if item.get("kind") == KIND_LISTEN:
            item["tracks"].reverse()
    return (boosts + items)[:limit]


def listen(store, member: dict | None, track_id: int) -> None:
    """A play, from the browser. Deduped; the stack happens at read time."""
    if not member or not track_id:
        return
    record(store, KIND_LISTEN, member_id=member["id"], track_id=track_id)
