"""Per-member storage allowance.

Who added what is already recorded in `member_items` (kind `track` for an
imported/uploaded file, kind `playlist` for a pasted set). A track's owner is its
own claim, else the claim on the playlist it came in with; bytes are the file
sizes of the tracks they own. Admins are measured but never blocked — the owner
of the box is not going to be locked out of his own library.

The cap is a setting (`member_quota_bytes`), 10 GiB by default.
"""

from __future__ import annotations

import sqlite3

DEFAULT_CAP = 10 * 1024 ** 3


def cap(store) -> int:
    """The allowance in bytes; 0 means unlimited.

    Admin-editable from Settings (`member_quota_gb`, GB). A missing or broken
    value falls back to the default rather than to "no limit", because silently
    removing a cap is the worse failure.
    """
    raw = store.setting("member_quota_gb", "")
    try:
        gigabytes = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_CAP
    if gigabytes <= 0:
        return 0
    return int(gigabytes * 1024 ** 3)


def owners(store) -> dict[int, str]:
    """track id -> member id, direct claim first, then the playlist's claim."""
    with store.lock:
        direct: dict[int, str] = {}
        playlist_owner: dict[int, str] = {}
        via_playlist: dict[int, str] = {}
        try:
            for row in store.conn.execute(
                    "SELECT target_id, member_id FROM member_items WHERE kind='track' "
                    "ORDER BY rowid"):
                direct.setdefault(row["target_id"], row["member_id"])
            for row in store.conn.execute(
                    "SELECT target_id, member_id FROM member_items WHERE kind='playlist' "
                    "ORDER BY rowid"):
                playlist_owner.setdefault(row["target_id"], row["member_id"])
            for row in store.conn.execute(
                    "SELECT pt.track_id AS track_id, pt.playlist_id AS playlist_id "
                    "FROM playlist_tracks pt ORDER BY pt.playlist_id"):
                owner = playlist_owner.get(row["playlist_id"])
                if owner:
                    via_playlist.setdefault(row["track_id"], owner)
        except sqlite3.OperationalError:
            # A store that never ran the security schema has no claims at all.
            return {}
    out = dict(via_playlist)
    out.update(direct)                      # a direct claim always wins
    return out


def usage(store) -> dict[str, int]:
    """member id -> bytes attributed to them."""
    owner = owners(store)
    out: dict[str, int] = {}
    with store.lock:
        rows = store.conn.execute(
            "SELECT id, size FROM tracks WHERE size > 0").fetchall()
    for row in rows:
        member = owner.get(row["id"])
        if member:
            out[member] = out.get(member, 0) + int(row["size"] or 0)
    return out


def used(store, member_id: str) -> int:
    return usage(store).get(member_id, 0)


def remaining(store, member_id: str) -> int:
    return max(0, cap(store) - used(store, member_id))


def human(size: int) -> str:
    return f"{size / 1024 ** 3:.1f} GB"


def refuse(store, member: dict | None, extra: int = 0) -> str:
    """Why this member may not add `extra` bytes right now, or '' if they may.

    Returns a sentence so every caller reports the same numbers.
    """
    if not member or member.get("role") == "admin":
        return ""
    limit = cap(store)
    if limit <= 0:                          # 0 GB in Settings = unlimited
        return ""
    total = used(store, member["id"]) + max(0, int(extra or 0))
    if total <= limit:
        return ""
    return (f"storage allowance reached: {human(used(store, member['id']))} of "
            f"{human(limit)} used, this add needs {human(max(0, int(extra or 0)))} more")
