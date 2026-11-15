"""Does the source still have this track?

A playlist entry can outlive the recording: Apple drops a track from its catalog
and the playlist keeps a hole, SoundCloud deletes an upload, a label pulls a
release. The file already downloaded keeps working — that is the whole point of a
local library — but the row should say so instead of failing forever.

Two independent checks:

* `verify_playlist()` re-reads the playlist's own source URL and compares the
  provider track ids it returns with the membership stored here. A stored track
  the source no longer lists is `gone`.
* `verify_catalog()` asks the Apple catalog directly about known ids, which also
  catches a recording that is still listed but no longer sold in this storefront
  (`missing`).

Neither check deletes anything. Only rows are labelled.
"""

from __future__ import annotations

import json
import time
import urllib.request

from providers import base as provider_base

STORE_DEFAULT = "us"
BATCH = 100


def _apple_ids(store) -> list[str]:
    with store.lock:
        rows = store.conn.execute(
            "SELECT provider_track_id FROM tracks WHERE provider='apple'"
            " AND provider_track_id<>'' AND source_status<>'gone'").fetchall()
    return [str(row["provider_track_id"]) for row in rows if str(row["provider_track_id"]).isdigit()]


def _itunes_lookup(ids: list[str], storefront: str) -> set[str]:
    url = ("https://itunes.apple.com/lookup?id=" + ",".join(ids[:BATCH])
           + f"&country={storefront}&entity=song&limit=200")
    request = urllib.request.Request(url, headers={"User-Agent": "HardlyListening/1"})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.load(response)
    found = set()
    for row in payload.get("results") or []:
        if row.get("trackId"):
            found.add(str(row["trackId"]))
    return found


def verify_catalog(store) -> dict:
    """Label Apple tracks the storefront no longer returns. Never guesses on failure."""
    ids = _apple_ids(store)
    if not ids:
        return {"checked": 0, "missing": 0, "present": 0, "error": ""}
    storefront = store.setting("apple_store", STORE_DEFAULT) or STORE_DEFAULT
    found: set[str] = set()
    error = ""
    try:
        for start in range(0, len(ids), BATCH):
            found |= _itunes_lookup(ids[start:start + BATCH], storefront)
    except Exception as failure:                      # network down ≠ tracks removed
        return {"checked": 0, "missing": 0, "present": 0, "error": str(failure)}
    now = time.time()
    missing = 0
    with store.lock, store.conn:
        for track_id in ids:
            present = track_id in found
            missing += 0 if present else 1
            store.conn.execute(
                "UPDATE tracks SET source_status=?, source_checked=?"
                " WHERE provider='apple' AND provider_track_id=?",
                ("present" if present else "missing", now, track_id))
    return {"checked": len(ids), "missing": missing,
            "present": len(ids) - missing, "error": ""}


def verify_playlist(store, playlist_id: int) -> dict:
    with store.lock:
        playlist = store.conn.execute(
            "SELECT id, title, provider, source_url FROM playlists WHERE id=?",
            (playlist_id,)).fetchone()
        members = [dict(r) for r in store.conn.execute(
            "SELECT t.id, t.provider_track_id, t.file FROM playlist_tracks pt"
            " JOIN tracks t ON t.id = pt.track_id WHERE pt.playlist_id=?",
            (playlist_id,))]
    if playlist is None:
        raise ValueError("unknown playlist")
    if not playlist["source_url"]:
        raise ValueError("this playlist has no source link to re-read")

    release = provider_base.for_url(playlist["source_url"]).resolve(playlist["source_url"])
    if not release.tracks:
        # An empty read is a broken fetch, not proof that everything vanished.
        raise RuntimeError("the source returned no tracks — nothing changed")
    current = {track.provider_track_id for track in release.tracks if track.provider_track_id}

    now = time.time()
    gone, kept, rescued = [], 0, 0
    with store.lock, store.conn:
        for member in members:
            listed = member["provider_track_id"] in current
            if listed:
                kept += 1
                status = "present"
            else:
                gone.append(member["id"])
                status = "gone"
            rescued += 1 if (not listed and member["file"]) else 0
            store.conn.execute(
                "UPDATE tracks SET source_status=?, source_checked=? WHERE id=?",
                (status, now, member["id"]))
    return {"playlist": playlist["title"], "checked": len(members),
            "present": kept, "gone": len(gone), "kept_locally": rescued,
            "gone_ids": gone, "source": release.title}
