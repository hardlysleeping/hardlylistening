"""What members did — the Feed's Activity tab.

Written at the moment something happens (a play, a download, a playlist edit, a
join, a rename). Plays are the noisy kind, so they are deduplicated on write and
**folded into a day card on read**: everything one member did between midnight and
midnight is a single collapsed card carrying per-kind counts (played 12 · added 3 ·
2 playlists) with every entry still inside it, revealed five at a time. An entry per
sitting made an evening of listening look like a dozen separate people in the feed.

Attribution comes from `member_items` (see library/quota.py), so a track's owner
is whoever claimed it directly, else whoever claimed the playlist it came in with.
"""

from __future__ import annotations

import time

# The boost slot moved to `social/boosts.py` (SOCIAL-001). These names stay here because
# `activity.boost` is called from the queue, Subsonic and the web routes, and the top-level
# `activity` module is an alias of this one: a re-export keeps every old call site working
# and keeps the object identity a patch relies on.
from social.boosts import BOOST_TTL, active_boosts, boost, current_boost  # noqa: F401

LISTEN_GAP = 30 * 60        # listens by one member inside this window stack
LISTEN_REPEAT = 10 * 60     # the same track again this soon is not a new event
PLAY_DEDUPE = 30            # the same track this soon is one play, not two reports
KEEP = 4000                 # rows kept; the feed only reads the newest ~80

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


def log_play(store, member_id: str, track_id: int, source: str = "web",
             now: float | None = None) -> bool:
    """Append one dated play to `listen_events`, the history the stats read.

    Separate from `record()`: that one is the Feed (folded, deduped by the ten-minute
    rule, trimmed to 4000 rows), this one is evidence that must still be there in
    December. The only rule applied here is the double-report guard — a reload, a
    re-entered page or a client that sends a play twice inside half a minute is one
    play, not two. Returns whether a row was written.
    """
    if not member_id or not track_id:
        return False
    ts = time.time() if now is None else float(now)
    try:
        with store.lock:
            last = store.conn.execute(
                "SELECT ts FROM listen_events WHERE member_id=? AND track_id=?"
                " ORDER BY ts DESC LIMIT 1", (member_id, int(track_id))).fetchone()
            if last and ts - last["ts"] < PLAY_DEDUPE:
                return False
            store.conn.execute(
                "INSERT INTO listen_events(ts,member_id,track_id,source) VALUES(?,?,?,?)",
                (ts, member_id, int(track_id), source))
            store.conn.commit()
            return True
    except Exception:
        # A missed play is not worth breaking playback, exactly like the feed.
        try:
            store.conn.rollback()
        except Exception:
            pass
        return False


def _track(store, track_id: int) -> dict:
    if not track_id:
        return {}
    row = store.conn.execute(
        "SELECT id, title, artist_name FROM tracks WHERE id=?", (track_id,)).fetchone()
    return dict(row) if row else {}


def _group_for(kind: str) -> str:
    """Which part of a day card an event belongs to."""
    if kind == KIND_LISTEN:
        return "listen"
    if kind == KIND_ADD:
        return "add"
    if kind in (KIND_PLAYLIST_NEW, KIND_PLAYLIST_EDIT, KIND_PLAYLIST_DELETE):
        return "playlist"
    return "other"


def day_key(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def feed(store, limit: int = 80, now: float | None = None) -> list[dict]:
    """Active boosts first, then one card per member per day.

    A day card bundles everything that member did between midnight and midnight —
    plays, additions, playlist work, joins and renames — with a count per kind and
    the individual entries still inside it. One person listening all evening is a
    single line in the feed instead of an entry per sitting; the UI collapses the
    detail and reveals it five at a time. Cards are ordered by their newest event,
    so any new action lifts that member's card to the top.
    """
    boosts = active_boosts(store, limit, now)
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT a.*, m.display_name AS member_name, m.profile_icon AS member_icon,"
            " p.title AS playlist_title"
            " FROM activity a LEFT JOIN members m ON m.id=a.member_id"
            " LEFT JOIN playlists p ON p.id=a.playlist_id"
            " ORDER BY a.ts DESC, a.id DESC LIMIT ?", (int(limit) * 12,)).fetchall()]
        buckets: dict[tuple, dict] = {}
        order: list[tuple] = []
        for row in rows:
            ts = row["ts"]
            key = (row["member_id"], day_key(ts))
            card = buckets.get(key)
            if card is None:
                card = {
                    "kind": "day", "member_id": row["member_id"],
                    "member": (row["member_name"] or "").strip() or "Someone",
                    "member_icon": row["member_icon"] or "",
                    "day": key[1], "ts": ts, "first_ts": ts,
                    "counts": {}, "groups": {"listen": [], "add": [],
                                             "playlist": [], "other": []},
                }
                buckets[key] = card
                order.append(key)
            card["first_ts"] = ts          # rows arrive newest-first
            group = _group_for(row["kind"])
            if group in ("listen", "add"):
                track = _track(store, row["track_id"])
                if not track:
                    continue
                entry = dict(track, ts=ts)
            elif group == "playlist":
                entry = {"ts": ts, "kind": row["kind"], "id": row["playlist_id"],
                         "title": row["playlist_title"] or row["detail"] or ""}
            else:
                entry = {"ts": ts, "kind": row["kind"], "detail": row["detail"] or ""}
            card["groups"][group].append(entry)
            card["counts"][group] = card["counts"].get(group, 0) + 1
        items = [buckets[k] for k in order][:max(1, int(limit))]
    return boosts + items


def listen(store, member: dict | None, track_id: int) -> None:
    """A play, from the browser. Deduped; the stack happens at read time."""
    if not member or not track_id:
        return
    record(store, KIND_LISTEN, member_id=member["id"], track_id=track_id)
    # The feed's copy is folded and trimmed; the stats need their own dated row.
    log_play(store, member["id"], track_id, "web")
