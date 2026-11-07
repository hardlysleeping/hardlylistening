"""Repair tracks imported with a provider id where the title should be.

Older SoundCloud imports stored the numeric track id as the title (the set
expansion had no title for those entries), so the library shows rows called
`2289507644`. The audio files themselves carry the real tags — yt-dlp stamped
them at download time — so the repair reads the file, not the network.

It never renames or moves audio: only the `title`, `artist_name` and `album`
columns change, and only where the stored value is missing or is that bare id.
A dry run is the default; `apply=True` writes.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

ID_TITLE = re.compile(r"^\s*\d{6,}\s*$")
NUMBERED_FILE = re.compile(r"^\s*\d+\s*-\s*")


def _tags(path: Path) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return {}
        return {k.lower(): v for k, v in (json.loads(out.stdout).get("format", {}).get("tags") or {}).items()}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _from_filename(path: Path) -> str:
    stem = path.stem
    stem = NUMBERED_FILE.sub("", stem).strip()
    return stem if stem and not ID_TITLE.match(stem) else ""


def _tidy_artist(artist: str) -> str:
    """SoundCloud joins co-artists with a full-width comma; a plain ", " reads."""
    return re.sub(r"\s*[，、]\s*", ", ", (artist or "").strip()).strip(" ,")


def _network_meta(provider: str, track_id: str) -> dict:
    """Metadata for a track whose audio was never downloaded.

    A queued SoundCloud track still has its own API record, so a row with no
    file is not a dead end: one request returns the real title and uploader.
    """
    if provider != "soundcloud" or not str(track_id or "").strip().isdigit():
        return {}
    try:
        from downloaders import ytdlp
        info = ytdlp.json_info(
            f"https://api-v2.soundcloud.com/tracks/{str(track_id).strip()}", timeout=120)
    except Exception:
        return {}
    if not isinstance(info, dict):
        return {}
    return {"title": (info.get("track") or info.get("title") or "").strip(),
            "artist": _tidy_artist(info.get("uploader") or info.get("artist") or "")}


def looks_like_id(title: str, provider_track_id: str = "") -> bool:
    title = (title or "").strip()
    if not title:
        return True
    if provider_track_id and title == str(provider_track_id).strip():
        return True
    return bool(ID_TITLE.match(title))


def _fallback_artists(store) -> set[int]:
    """Track ids whose artist is just the playlist's dominant name.

    A flat SoundCloud set has no per-track artist, so an import that failed to
    read each track stamped the account that published the set on every row
    ("Dilroop S. Gill" for 251 of 255 Favorites tracks). Measured per playlist:
    an artist owning at least 60% of a playlist of 3+ is the fallback, not a
    coincidence. The repair still only writes when the file's own tag disagrees.
    """
    rows = store.conn.execute(
        "SELECT pt.track_id, pt.playlist_id, t.artist_name "
        "FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id").fetchall()
    counts: dict[int, dict[str, int]] = {}
    for row in rows:
        artist = (row["artist_name"] or "").strip()
        if not artist:
            continue
        bucket = counts.setdefault(row["playlist_id"], {})
        bucket[artist] = bucket.get(artist, 0) + 1
    dominant: dict[int, str] = {}
    for playlist_id, bucket in counts.items():
        total = sum(bucket.values())
        artist, n = max(bucket.items(), key=lambda kv: kv[1])
        if total >= 3 and n / total >= 0.6:
            dominant[playlist_id] = artist
    out = set()
    for row in rows:
        if dominant.get(row["playlist_id"]) and \
                (row["artist_name"] or "").strip() == dominant[row["playlist_id"]]:
            out.add(row["track_id"])
    return out


def candidates(store, limit: int = 500) -> list[dict]:
    rows = store.conn.execute(
        "SELECT id, title, artist_name, album, file, provider, provider_track_id "
        "FROM tracks ORDER BY id LIMIT ?", (limit,)).fetchall()
    suspect_artists = _fallback_artists(store)
    out = []
    for row in rows:
        if not (looks_like_id(row["title"], row["provider_track_id"])
                or row["id"] in suspect_artists):
            continue
        out.append({k: row[k] for k in ("id", "title", "artist_name", "album", "file",
                                        "provider", "provider_track_id")})
    return out


def preview(store, limit: int = 500) -> dict:
    """What would change, without touching anything.

    Only a handful of files are probed: the point of a preview is a fast answer,
    and reading 250 tags inside a request blocks every other page load.
    """
    found = candidates(store, limit)
    samples = []
    for row in found[:3]:
        path = Path(row["file"] or "")
        tags = _tags(path) if path.is_file() else {}
        samples.append({"id": row["id"], "current": row["title"],
                        "title": (tags.get("title") or "").strip() or _from_filename(path),
                        "artist": (tags.get("artist") or "").strip()})
    return {"count": len(found), "samples": samples,
            "missing_file": sum(1 for r in found if not Path(r["file"] or "").is_file())}


_state = {"running": False, "total": 0, "done": 0, "changed": 0, "skipped": 0,
          "missing_file": 0, "error": "", "finished": 0.0}
# Re-entrant: several helpers answer by calling status() while holding it.
_state_lock = threading.RLock()


def status() -> dict:
    with _state_lock:
        out = dict(_state)
    out["percent"] = round(100 * out["done"] / out["total"], 1) if out["total"] else 0.0
    return out


def repair_async(store, limit: int = 500) -> dict:
    """Run the repair off the request thread; the UI polls status()."""
    with _state_lock:
        if _state["running"]:
            return {"started": False, "reason": "already running", **status()}
    # Query outside the status lock: the worker takes store.lock and then
    # _state_lock, so doing it the other way round here would deadlock.
    found = candidates(store, limit)
    if not found:
        return {"started": False, "reason": "nothing to fix", **status()}
    with _state_lock:
        if _state["running"]:
            return {"started": False, "reason": "already running", **status()}
        _state.update({"running": True, "total": len(found), "done": 0, "changed": 0,
                       "skipped": 0, "missing_file": 0, "error": "", "finished": 0.0})

    def work():
        try:
            for row in found:
                path = Path(row["file"] or "")
                tags = _tags(path) if path.is_file() else {}
                if not tags and not path.is_file():
                    tags = _network_meta(row["provider"], row["provider_track_id"])
                    if not tags:
                        with _state_lock:
                            _state["missing_file"] += 1
                            _state["done"] += 1
                        continue
                tag_title = (tags.get("title") or "").strip()
                title = "" if looks_like_id(tag_title, row["provider_track_id"]) else tag_title
                title = title or _from_filename(path)
                if not title or looks_like_id(title, row["provider_track_id"]):
                    with _state_lock:
                        _state["skipped"] += 1
                        _state["done"] += 1
                    continue
                # The file's own tag is the track page's own metadata, so it beats
                # the playlist-owner name an earlier import stamped on the row.
                artist = _tidy_artist((tags.get("artist") or "").strip()
                                      or (row["artist_name"] or "").strip())
                album = (row["album"] or "").strip()
                if title == (row["title"] or "").strip() \
                        and artist == (row["artist_name"] or "").strip():
                    with _state_lock:
                        _state["skipped"] += 1
                        _state["done"] += 1
                    continue
                with store.lock:
                    store.conn.execute(
                        "UPDATE tracks SET title = ?, artist_name = ?, album = ? WHERE id = ?",
                        (title, artist, album, row["id"]))
                    store.conn.commit()
                with _state_lock:
                    _state["changed"] += 1
                    _state["done"] += 1
        except Exception as error:                      # never leave the flag stuck on
            with _state_lock:
                _state["error"] = str(error)
        finally:
            with _state_lock:
                _state["running"] = False
                _state["finished"] = time.time()

    threading.Thread(target=work, name="titles", daemon=True).start()
    return {"started": True, **status()}


def repair(store, limit: int = 500, apply: bool = False) -> dict:
    """Fill numeric/missing titles from the file's own tags. Never renames files."""
    found = candidates(store, limit)
    changes, skipped, missing = [], 0, 0
    for row in found:
        path = Path(row["file"] or "")
        tags = _tags(path) if path.is_file() else {}
        if not tags and not path.is_file():
            tags = _network_meta(row["provider"], row["provider_track_id"])
            if not tags:
                missing += 1
                continue
        tag_title = (tags.get("title") or "").strip()
        title = "" if looks_like_id(tag_title, row["provider_track_id"]) else tag_title
        title = title or _from_filename(path)
        if not title or looks_like_id(title, row["provider_track_id"]):
            skipped += 1
            continue
        artist = _tidy_artist((tags.get("artist") or "").strip()
                              or (row["artist_name"] or "").strip())
        album = (row["album"] or "").strip()
        if title == (row["title"] or "").strip() \
                and artist == (row["artist_name"] or "").strip():
            skipped += 1
            continue
        changes.append({"id": row["id"], "from": row["title"], "title": title,
                        "artist": artist, "album": album})
        if apply:
            with store.lock:
                store.conn.execute(
                    "UPDATE tracks SET title = ?, artist_name = ?, album = ? WHERE id = ?",
                    (title, artist, album, row["id"]))
                store.conn.commit()
    return {"applied": bool(apply), "found": len(found), "changed": len(changes),
            "skipped": skipped, "missing_file": missing, "sample": changes[:20]}
