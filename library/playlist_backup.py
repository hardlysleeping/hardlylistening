"""Per-playlist restore backups.

One contract, one file per playlist: ordered catalog IDs plus enough metadata to
rebuild the playlist elsewhere (Apple Music first) and to read it years later. The
shared Shortcut is the client; this module only writes the file, so a new provider
becomes restorable by filling in `provider_track_id`.

The JSON is archival: order lives in `position`, never in array order, and a
duplicate position is treated as a corrupt backup rather than silently sorted.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "hardlylistening-playlist"
CONTRACT_VERSION = 1
FILENAME_SUFFIX = ".hardlyplaylist.json"

# Database provider name → contract provider name. The contract is public, the
# database keys are not, so the mapping stays here instead of leaking to callers.
CONTRACT_PROVIDERS = {"apple": "apple_music"}

_STOREFRONT = re.compile(r"music\.apple\.com/([a-z]{2})/", re.I)
_PLAYLIST_ID = re.compile(r"/playlist/[^/]+/(pl\.[A-Za-z0-9._-]+)", re.I)
_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')


def app_version() -> str:
    path = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def contract_provider(provider: str) -> str:
    return CONTRACT_PROVIDERS.get(provider or "", provider or "")


def storefront(source_url: str):
    match = _STOREFRONT.search(source_url or "")
    return match.group(1).lower() if match else None


def provider_playlist_id(source_url: str):
    match = _PLAYLIST_ID.search(source_url or "")
    return match.group(1) if match else None


def safe_name(value: str, fallback: str = "playlist") -> str:
    cleaned = _UNSAFE.sub("_", str(value or "")).strip(" .")[:120]
    return cleaned or fallback


def rows(store, playlist_id: int):
    """The playlist row and its members, in membership order."""
    with store.lock:
        playlist = store.conn.execute(
            "SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        if not playlist:
            raise ValueError("No such playlist")
        members = store.conn.execute(
            "SELECT t.*, pt.position AS member_position FROM tracks t"
            " JOIN playlist_tracks pt ON pt.track_id=t.id"
            " WHERE pt.playlist_id=? ORDER BY pt.position,t.id", (playlist_id,)).fetchall()
    return dict(playlist), [dict(row) for row in members]


def payload(store, playlist_id: int, now=None) -> dict:
    playlist, members = rows(store, playlist_id)
    positions = [int(member["member_position"]) for member in members]
    if len(set(positions)) != len(positions):
        raise ValueError("This playlist has duplicate membership positions")
    provider = contract_provider(playlist.get("provider"))
    source_url = playlist.get("source_url") or ""
    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    tracks = []
    for member in members:
        duration = member.get("duration")
        tracks.append({
            "position": int(member["member_position"]),
            "provider": contract_provider(member.get("provider")),
            "provider_track_id": str(member.get("provider_track_id") or ""),
            "title": member.get("title") or "",
            "artist": member.get("artist_name") or "",
            "album": member.get("album") or None,
            "duration_ms": int(round(float(duration) * 1000)) if duration else None,
        })
    return {
        "format": FORMAT,
        "version": CONTRACT_VERSION,
        "backup": {
            "id": str(uuid.uuid4()),
            "created_at": created.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "app_version": app_version(),
        },
        "playlist": {
            "title": playlist.get("title") or "Playlist",
            "provider": provider,
            "provider_playlist_id": provider_playlist_id(source_url),
            "source_url": source_url or None,
            "storefront": storefront(source_url),
        },
        "tracks": tracks,
    }


def dumps(store, playlist_id: int, now=None) -> bytes:
    """The exact bytes of one backup file — the button and the ZIP serve these."""
    body = json.dumps(payload(store, playlist_id, now=now), ensure_ascii=False, indent=2)
    return (body + "\n").encode("utf-8")


def filename(store, playlist_id: int) -> str:
    playlist, _ = rows(store, playlist_id)
    return safe_name(playlist.get("title")) + FILENAME_SUFFIX
