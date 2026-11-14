"""One member, everything the group can see about them.

Clicking a name or icon anywhere in the app opens this: listening history,
what they added to the library, the playlists they made, their votes (up and
down, with what they voted on) and what they saved. Votes are not anonymous —
the voter is recorded in `social_votes.member_id` / `party_votes.voter`, so the
page can simply show it.

Everything is read-only and scoped to an existing member id; nothing here is a
privileged view, because the app has no private profiles by design.
"""

from __future__ import annotations

from collections import Counter


def _track(store, track_id: int) -> dict:
    row = store.conn.execute(
        "SELECT id, title, artist_name FROM tracks WHERE id=?", (track_id,)).fetchone()
    if not row:
        return {}
    return {"id": row["id"], "title": row["title"], "artist": row["artist_name"]}


def _names(store, ids: list[str]) -> dict[str, dict]:
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    rows = store.conn.execute(
        "SELECT id, display_name, profile_icon, role FROM members WHERE id IN ("
        + ",".join("?" for _ in ids) + ")", ids).fetchall()
    return {r["id"]: {"id": r["id"], "name": (r["display_name"] or "").strip() or "Member",
                      "icon": r["profile_icon"] or "music", "role": r["role"]} for r in rows}


def profile(store, member_id: str, limit: int = 40) -> dict | None:
    with store.lock:
        member = store.conn.execute(
            "SELECT id, display_name, profile_icon, role FROM members WHERE id=?",
            (member_id,)).fetchone()
        if not member:
            return None
        who = {"id": member["id"], "name": (member["display_name"] or "").strip() or "Member",
               "icon": member["profile_icon"] or "music", "role": member["role"]}

        # -- listening history: plays counted per track -------------------------
        # The dated log, not the feed's trimmed window: the same table every number on
        # this page is computed from.
        rows = store.conn.execute(
            "SELECT track_id, COUNT(*) AS plays, MAX(ts) AS last FROM listen_events"
            " WHERE member_id=? GROUP BY track_id"
            " ORDER BY last DESC LIMIT ?", (member_id, limit)).fetchall()
        listens = []
        for row in rows:
            track = _track(store, row["track_id"])
            if track:
                listens.append({**track, "plays": row["plays"], "last": row["last"]})

        # -- additions: every completed download / import they own --------------
        rows = store.conn.execute(
            "SELECT track_id, MAX(ts) AS ts FROM activity"
            " WHERE member_id=? AND kind='add' GROUP BY track_id"
            " ORDER BY ts DESC LIMIT ?", (member_id, limit)).fetchall()
        adds = [{**_track(store, row["track_id"]), "ts": row["ts"]}
                for row in rows if _track(store, row["track_id"])]

        # -- playlists they own -------------------------------------------------
        rows = store.conn.execute(
            "SELECT p.id, p.title, p.kind, (SELECT COUNT(*) FROM playlist_tracks pt"
            " WHERE pt.playlist_id=p.id) AS tracks FROM playlists p"
            " JOIN member_playlists mp ON mp.playlist_id=p.id WHERE mp.member_id=?"
            " ORDER BY p.id DESC LIMIT ?", (member_id, limit)).fetchall()
        playlists = [dict(r) for r in rows]

        # -- saves / likes ------------------------------------------------------
        try:
            rows = store.conn.execute(
                "SELECT track_id FROM listening_tracks WHERE member_id=? LIMIT ?",
                (member_id, limit)).fetchall()
            saves = [t for t in (_track(store, r["track_id"]) for r in rows) if t]
        except Exception:
            # A database that never had the personal-listening tables is still a
            # valid answer to "who is this person".
            saves = []

        # -- votes, with the voter kept (never anonymous) ----------------------
        votes = []
        for row in store.conn.execute(
                "SELECT kind, target, value FROM social_votes WHERE member_id=?"
                " ORDER BY rowid DESC LIMIT ?", (member_id, limit)).fetchall():
            label = ""
            if row["kind"] in ("track", "playlist") and str(row["target"]).isdigit():
                table = "tracks" if row["kind"] == "track" else "playlists"
                found = store.conn.execute(
                    f"SELECT title FROM {table} WHERE id=?", (int(row["target"]),)).fetchone()
                label = found["title"] if found else ""
            else:
                label = (row["target"] or "")[:12]
            votes.append({"kind": row["kind"], "value": row["value"], "label": label})
        party = []
        try:
            for row in store.conn.execute(
                    "SELECT v.value, r.title FROM party_votes v"
                    " JOIN party_entries e ON e.id=v.entry JOIN party_rooms r ON r.id=e.party"
                    " WHERE v.voter=? ORDER BY v.rowid DESC LIMIT ?",
                    ("m:" + member_id, limit)).fetchall():
                party.append({"value": row["value"], "room": row["title"]})
        except Exception:
            party = []   # no party has ever run on this database

        counts = store.conn.execute(
            "SELECT (SELECT COUNT(*) FROM listen_events WHERE member_id=?) AS listens,"
            " (SELECT COUNT(*) FROM activity WHERE member_id=? AND kind='add') AS adds,"
            " (SELECT COUNT(*) FROM social_votes WHERE member_id=?) AS votes,"
            " (SELECT COUNT(DISTINCT track_id) FROM listen_events WHERE member_id=?) AS distinct_tracks",
            (member_id, member_id, member_id, member_id)).fetchone()

    # The Listening section's first paint rides with the profile, so opening a name
    # does not need a second request before it can show numbers. A period switch asks
    # `/api/stats/<id>` for the rest.
    try:
        import stats as stats_module
        listening_stats = stats_module.summary(store, member_id, "week")
    except Exception:
        listening_stats = None

    return {"member": who, "listens": listens, "adds": adds, "playlists": playlists,
            "saves": saves, "votes": votes, "party_votes": party,
            "stats": listening_stats,
            "counts": {k: counts[k] for k in counts.keys()}}


def recent(store, limit: int = 20) -> list[dict]:
    """Newest faces, for the People panel's Activity tab header."""
    with store.lock:
        rows = store.conn.execute(
            "SELECT member_id, COUNT(*) AS events, MAX(ts) AS last FROM activity"
            " GROUP BY member_id ORDER BY last DESC LIMIT ?", (limit,)).fetchall()
    names = _names(store, [r["member_id"] for r in rows])
    return [{**names.get(r["member_id"], {"id": r["member_id"], "name": "Member",
                                           "icon": "music", "role": "member"}),
             "events": r["events"], "last": r["last"]} for r in rows]
