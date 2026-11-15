"""No audio file in the library is invisible to the app.

A download can leave a finished file behind when its job dies after the write: a
killed retry, a verification error, a crash between writing the file and updating
the row. Nothing in `tracks` points at it, so the UI cannot see it and the queue
cannot know it exists — the same song gets downloaded again and another copy is
made.

This sweep closes that loop. An orphan file is linked to the empty row for the
same song if one exists (so it lands back in its playlists), otherwise it becomes
a `local` row of its own. It never deletes, never moves, and never folds a
different recording into an existing row: identity is ISRC first, then
title+artist with a duration check.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
import json
import unicodedata
from pathlib import Path

from library import import_files

SWEEP_EVERY = 600.0
MIN_BYTES = import_files.MIN_BYTES

_lock = threading.RLock()
_thread: threading.Thread | None = None
_state = {"running": False, "adopted": 0, "failed": 0, "error": ""}


def norm(text: str) -> str:
    """Compare titles the way a person would: case, accents and punctuation free."""
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


def song_title(text: str) -> str:
    """A title without the downloader's `(2)` copy marker.

    "Song (2)" on disk is one recording, not a second song: the marker only says
    a file with that stem already existed.
    """
    return norm(import_files.strip_copy_suffix((text or "").strip()))


def _probe(path: Path, root: Path | None = None) -> dict:
    """Tags and duration. ffprobe rather than the file name, like the importer.

    Falls back to the file name and the folder the app itself writes the file in:
    a layout of `<root>/<Artist>/<Collection>/NN - Title.ext` says who the artist is
    even when the tags are missing. Reading the raw stem made an artist called "76"
    out of a track numbered 76.
    """
    command = ["ffprobe", "-v", "error", "-show_entries",
               "format=duration:format_tags=artist,album,title,track,isrc,album_artist",
               "-of", "json", str(path)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
        data = json.loads(done.stdout or "{}").get("format") or {}
    except Exception:                                  # noqa: BLE001 - never break the sweep
        data = {}
    tags = {key.lower(): value for key, value in (data.get("tags") or {}).items()}
    tagged_title = (tags.get("title") or "").strip()
    title = tagged_title or import_files.from_filename(path)[0]
    artist = (tags.get("artist") or tags.get("album_artist") or "").strip()
    number = 0
    raw_track = (tags.get("track") or "").split("/")[0].strip()
    if raw_track.isdigit():
        number = int(raw_track)
    elif not tagged_title:
        number = import_files.from_filename(path)[1]
    if not artist:
        artist = import_files.artist_folder(path, root)
    if not artist and " - " in title:
        head, rest = [part.strip() for part in title.split(" - ", 1)]
        # A number in front of a dash is a track position, not a performer.
        if head and not head.isdigit():
            artist, title = head, rest
    try:
        duration = float(data.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"title": title, "artist": artist, "album": (tags.get("album") or "").strip(),
            "track_number": number, "duration": duration,
            "isrc": (tags.get("isrc") or "").strip()}


def root(store) -> Path:
    return Path(store.setting("root")).expanduser()


def owned(store) -> set[str]:
    """Files a row already points at, resolved so two spellings are one path."""
    found = set()
    with store.lock:
        rows = store.conn.execute("SELECT file FROM tracks WHERE file<>''").fetchall()
    for row in rows:
        try:
            found.add(str(Path(row[0]).resolve()))
        except OSError:
            found.add(row[0])
    return found


def orphans(store, limit: int = 500) -> list[Path]:
    """Audio files under the library root that no row claims."""
    base = root(store)
    if not base.is_dir():
        return []
    claimed = owned(store) | retained(store)
    skipped = _skipped(store)
    found = []
    for path in base.rglob("*"):
        if len(found) >= limit:
            break
        if not path.is_file() or not import_files.is_audio(path) or ".part" in path.name:
            continue
        # `.replacements/` holds staged swaps that are not library media yet;
        # adopting one would put a half-finished file in the library.
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        try:
            if str(path.resolve()) in claimed:
                continue
            stat = path.stat()
            if stat.st_size < MIN_BYTES:
                continue
            if skipped.get(str(path)) == (stat.st_size, stat.st_mtime):
                continue
        except OSError:
            continue
        found.append(path)
    return found


def retained(store) -> set[str]:
    """Files a completed swap kept on purpose: old media, not library content.

    They are normally moved under `.replacements/`, but a swap that ran before
    that rule (or a file another row still shares) can leave one in the tree.
    Adopting it would add the song back as a duplicate of the row that just
    replaced it.
    """
    try:
        with store.lock:
            rows = store.conn.execute(
                "SELECT old_file FROM source_replacements WHERE status='completed'").fetchall()
    except Exception:                                  # noqa: BLE001 - older DB
        return set()
    found = set()
    for row in rows:
        if row[0]:
            try:
                found.add(str(Path(row[0]).resolve()))
            except OSError:
                found.add(row[0])
    return found


def _skipped(store) -> dict[str, tuple[int, float]]:
    try:
        with store.lock:
            rows = store.conn.execute("SELECT path, size, mtime FROM adopt_skipped").fetchall()
    except Exception:                                  # noqa: BLE001 - older DB
        return {}
    return {row[0]: (row[1], row[2]) for row in rows}


def _remember(store, path: Path, reason: str) -> None:
    try:
        stat = path.stat()
    except OSError:
        return
    with store.lock:
        store.conn.execute(
            "INSERT INTO adopt_skipped (path, size, mtime, reason, checked) VALUES (?,?,?,?,?)"
            " ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,"
            " reason=excluded.reason, checked=excluded.checked",
            (str(path), stat.st_size, stat.st_mtime, reason, time.time()))
        store.conn.commit()


def _rows(store) -> list[dict]:
    with store.lock:
        return [dict(row) for row in store.conn.execute(
            "SELECT id,title,artist_name,isrc,file,duration FROM tracks")]


def find_row(rows: list[dict], meta: dict) -> dict | None:
    """The row this file belongs to, or None when it is a new recording.

    Only rows without a usable file are candidates: a row that already has one is
    a different recording, and pointing it here would lose that file silently.
    """
    open_rows = [row for row in rows if not (row.get("file") or "").strip()
                 or not Path(row["file"]).is_file()]
    isrc = (meta.get("isrc") or "").strip().lower()
    if isrc:
        exact = [row for row in open_rows if (row.get("isrc") or "").strip().lower() == isrc]
        if exact:
            return exact[0]
    wanted_artist = norm(meta["artist"])
    same = [row for row in open_rows
            if song_title(row["title"]) == song_title(meta["title"])
            and norm(row["artist_name"]) == wanted_artist]
    if not same:
        return None
    duration = meta.get("duration") or 0
    if duration:
        # A row with a known duration that disagrees is a different version, not
        # the same song recorded twice.
        close = [row for row in same
                 if not row.get("duration") or abs(row["duration"] - duration) <= 5]
        same = close or []
    if not same:
        return None
    return sorted(same, key=lambda row: row["id"])[0]


def adopt(store, path: Path) -> tuple[str, str]:
    """Give one orphan a row. Returns (action, detail)."""
    if not path.is_file():
        return "missing", ""
    try:
        size = path.stat().st_size
    except OSError as error:
        return "failed", str(error)
    if size < MIN_BYTES:
        _remember(store, path, "too small to be a track")
        return "skipped", "too small to be a track"
    meta = _probe(path, root(store))
    if meta["duration"] <= 0:
        _remember(store, path, "no audio stream")
        return "skipped", "no audio stream"

    rows = _rows(store)
    row = find_row(rows, meta)
    stripped = str(path)
    if row:
        with store.lock:
            store.conn.execute(
                "UPDATE tracks SET file=?, size=?, art_hash='', art_checked=0 WHERE id=?",
                (stripped, size, row["id"]))
            # A row that already failed is the reason this file was invisible;
            # leaving the failure in place invites another download of it.
            store.conn.execute(
                "UPDATE download_jobs SET status='completed', error_type='',"
                " error_message='', finished=? WHERE track_id=? AND status IN"
                " ('failed','review')", (time.time(), row["id"]))
            store.conn.commit()
        store.log(f"adopted “{meta['title']}” into the existing track row",
                  "ok", "adopt")
        return "linked", f"track {row['id']}"

    # A local row's identity is the file's content, so a re-run finds this row
    # instead of making a second one.
    key = import_files.content_key(path)
    existing = None
    with store.lock:
        existing = store.conn.execute(
            "SELECT id FROM tracks WHERE provider='local' AND provider_track_id=?",
            (key,)).fetchone()
    if existing:
        return "already", f"track {existing['id']}"

    same_song = [row for row in rows
                 if song_title(row["title"]) == song_title(meta["title"])
                 and norm(row["artist_name"]) == norm(meta["artist"])
                 and (row.get("file") or "").strip()
                 and abs((row.get("duration") or 0) - meta["duration"]) <= 5]
    if same_song:
        # Two encodings of one recording. Keeping both would put the same song in
        # the library twice; the owner is already visible, so leave the spare for
        # an explicit cleanup decision and say which row it duplicates.
        _remember(store, path, f"duplicate of track {same_song[0]['id']}")
        return "duplicate", f"track {same_song[0]['id']}"

    artist_id = store.artist("local", meta["artist"] or "Unknown Artist",
                             meta["artist"] or "Unknown Artist")
    track_id = store.track("local", key, meta["title"], artist_name=meta["artist"],
                           album=meta["album"], track_number=meta["track_number"],
                           duration=meta["duration"], source_url=stripped,
                           artist_id=artist_id, isrc=meta["isrc"])
    with store.lock:
        store.conn.execute(
            "UPDATE tracks SET file=?, size=?, art_hash='', art_checked=0 WHERE id=?",
            (stripped, size, track_id))
        store.conn.commit()
    store.log(f"adopted “{meta['title']}” as a local track", "ok", "adopt")
    return "imported", f"track {track_id}"


def sweep(store, progress=None) -> dict:
    """Adopt every orphan. Safe to run as often as you like."""
    seen: dict[str, int] = {}
    with _lock:
        _state["running"] = True
        try:
            for path in orphans(store):
                action, detail = adopt(store, path)
                seen[action] = seen.get(action, 0) + 1
                with _lock:
                    _state["adopted"] += action in ("linked", "imported")
                    _state["failed"] += action == "failed"
                if progress:
                    progress(action, path, detail)
            duplicates = seen.get("duplicate", 0)
            if seen:
                store.log(
                    "adopted " + ", ".join(f"{count} {name}" for name, count in sorted(seen.items()))
                    + (f" ({duplicates} spare copy of a song already in the library)"
                       if duplicates else ""),
                    "warn" if seen.get("failed") or duplicates else "ok", "adopt")
        except Exception as error:                     # noqa: BLE001 - keep the thread alive
            with _lock:
                _state["error"] = str(error)
            store.log(f"adopt sweep failed: {error}", "error", "adopt")
        finally:
            with _lock:
                _state["running"] = False
    return seen


def status() -> dict:
    with _lock:
        return dict(_state)


def start(store) -> None:
    """Keep watching: a boot pass, then one every ten minutes."""
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, args=(store,), name="adopt", daemon=True)
        _thread.start()


def _loop(store) -> None:
    time.sleep(30)            # let the queue open the DB first
    while True:
        sweep(store)
        time.sleep(SWEEP_EVERY)