"""Importing music that is already on this machine.

The point is that an imported file and a downloaded file are the same kind of
thing afterwards: one row in `tracks`, one file under the library root, in the
same Artist/Album/Title layout.

Identity for an imported file is a content key (size + head + tail), not a path —
so re-importing the same album from a different folder, or after moving the
library, does not create a second copy.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from library import store as library_store

MIN_BYTES = 16 * 1024            # below this it is a stub or a slice, not a track
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".aiff",
              ".aif", ".wma", ".alac", ".webm"}
KEY_BYTES = 256 * 1024          # head and tail sampled, not the whole file
#: `NN - Title`: what `library.store.path_for` writes, and what a folder of downloads
#: looks like. The leading number is the track's position, never part of the title.
STEM_NUMBER = re.compile(r"^\s*(\d{1,3})\s*[-–—._)]\s*(?=\S)")
#: `Title (2)`: how `library.store.unique_any_suffix` keeps a second file with the
#: same stem from overwriting the first. It marks a copy, never a different song.
COPY_SUFFIX = re.compile(r"\s+\((\d{1,3})\)$")


def strip_copy_suffix(title: str) -> str:
    """The library's own "(2)" collision marker, removed from a file name.

    Left on, "Song (2)" read as a title of its own and the sweep adopted a second
    row for one recording. Provider titles never carry this marker on disk; the
    downloader appends it after the fact.
    """
    return COPY_SUFFIX.sub("", title).strip() or title


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def content_key(path: Path) -> str:
    """Cheap, stable identity: size plus the first and last 256 KB."""
    size = path.stat().st_size
    digest = hashlib.sha1(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(KEY_BYTES))
        if size > KEY_BYTES * 2:
            handle.seek(-KEY_BYTES, 2)
            digest.update(handle.read(KEY_BYTES))
    return digest.hexdigest()


def from_filename(path: Path) -> tuple[str, int]:
    """(title, track number) from a file name in the library's own layout.

    A leading number is the track position — never part of the title, and never an
    artist. Falling back to the raw stem made an artist called "76" out of
    "76 - I Believe (feat. Asha) [Nemexi Remix]".
    """
    stem = path.stem.strip()
    match = STEM_NUMBER.match(stem)
    if match:
        title = stem[match.end():].strip()
        return (strip_copy_suffix(title) or stem), int(match.group(1))
    return strip_copy_suffix(stem), 0


def artist_folder(path: Path, root: Path | None) -> str:
    """The artist level of `<root>/<Artist>/<Collection>/<file>`, or "".

    The app writes that layout itself, so an adopted file that lost its tags still
    knows whose folder it sits in. One level under the root is a collection, not an
    artist, and is not guessed.
    """
    if root is None:
        return ""
    try:
        relative = path.resolve().relative_to(Path(root).expanduser().resolve())
    except (OSError, ValueError):
        return ""
    return relative.parts[0].strip() if len(relative.parts) >= 3 else ""


def probe(path: Path, root: Path | None = None) -> dict:
    """Tags and duration via ffprobe. Missing tags fall back to the file name and
    the folder the app itself put the file in."""
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration:format_tags=artist,album,title,track,date,album_artist",
           "-of", "json", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout or "{}").get("format") or {}
    except Exception:
        data = {}
    tags = {k.lower(): v for k, v in (data.get("tags") or {}).items()}
    tagged_title = (tags.get("title") or "").strip()
    artist = (tags.get("artist") or tags.get("album_artist") or "").strip()
    album = (tags.get("album") or "").strip()
    number = 0
    raw_track = (tags.get("track") or "").split("/")[0]
    if raw_track.strip().isdigit():
        number = int(raw_track.strip())
    if tagged_title:
        title = tagged_title
    else:
        title, number = from_filename(path)
    if not artist:
        artist = artist_folder(path, root)
    if not artist and " - " in title:
        head, rest = [part.strip() for part in title.split(" - ", 1)]
        # A number in front of a dash is a track position, not a performer.
        if head and not head.isdigit():
            artist, title = head, rest
    duration = 0.0
    try:
        duration = float(data.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"title": title, "artist": artist, "album": album,
            "track_number": number, "duration": duration}


def collect(paths: list[str], limit: int = 5000) -> list[Path]:
    """Expand the given files/folders into a list of audio files."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if len(found) >= limit:
                    break
                if any(part.startswith(".") for part in child.relative_to(path).parts):
                    continue
                if child.is_file() and is_audio(child):
                    found.append(child)
        elif path.is_file() and is_audio(path):
            found.append(path)
    return found


def import_paths(store, paths: list[str], *, copy: bool = True, limit: int = 5000) -> dict:
    """Copy (or move) files into the library and register them as tracks."""
    root = Path(store.setting("root")).expanduser()
    files = collect(paths, limit=limit)
    imported, skipped, failed = [], [], []
    track_ids, playlist_ids = [], []
    playlist_title = ""

    for path in files:
        try:
            key = content_key(path)
            with store.lock:
                existing = store.conn.execute(
                    "SELECT id FROM tracks WHERE provider='local' AND provider_track_id=?",
                    (key,)).fetchone()
            if existing:
                skipped.append(path.name)
                continue

            if path.stat().st_size < MIN_BYTES:
                failed.append(f"{path.name}: too small to be a track")
                continue
            meta = probe(path, root)
            if meta["duration"] <= 0:
                failed.append(f"{path.name}: no audio stream found")
                continue
            target = library_store.path_for(
                root, artist=meta["artist"], album=meta["album"],
                playlist_title="Imports", number=meta["track_number"],
                title=meta["title"], extension=path.suffix.lstrip("."))
            if target.resolve() == path.resolve():
                written = path                       # already in place
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target = library_store.unique(target, {
                    str(r["file"]) for r in store.conn.execute(
                        "SELECT file FROM tracks WHERE file<>''")})
                if copy:
                    shutil.copy2(path, target)
                else:
                    shutil.move(str(path), str(target))
                written = target

            artist_id = store.artist("local", meta["artist"] or "Unknown Artist",
                                     meta["artist"] or "Unknown Artist")
            track_id = store.track(
                "local", key, meta["title"], artist_name=meta["artist"],
                album=meta["album"] or "Imports", track_number=meta["track_number"],
                duration=meta["duration"], source_url=str(path), artist_id=artist_id)
            with store.lock:
                store.conn.execute("UPDATE tracks SET file=?, size=?, art_hash='', art_checked=0 WHERE id=?",
                                   (str(written), written.stat().st_size, track_id))
                store.conn.commit()
            if not playlist_title:
                playlist_title = "Imported music"
            playlist_id = store.playlist(playlist_title, "local", "local:imports",
                                         kind="manual")
            store.add_to_playlist(playlist_id, track_id,
                                  int(store.stats()["tracks"]))
            track_ids.append(track_id)
            if playlist_id not in playlist_ids:
                playlist_ids.append(playlist_id)
            imported.append(meta["title"])
        except Exception as exc:                     # noqa: BLE001
            failed.append(f"{path.name}: {exc}")

    store.log(f"imported {len(imported)} files, skipped {len(skipped)} duplicates"
              f"{', ' + str(len(failed)) + ' failed' if failed else ''}",
              "ok" if imported else "warn")
    return {"imported": len(imported), "skipped": len(skipped),
            "failed": len(failed), "files": imported[:50], "errors": failed[:20],
            # Who added these is decided by the route, not by this function.
            "track_ids": track_ids, "playlist_ids": playlist_ids}


def _relative(path: str, root: Path):
    """`path` below `root`, or None. Resolved on both sides, since a row keeps what was typed."""
    try:
        return Path(path).expanduser().resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None


def _replacement_rows(store, columns: str) -> list[dict]:
    """The swap history, or nothing on a database that predates it."""
    from replacements import schema
    with store.lock:
        schema(store)
        return [dict(r) for r in store.conn.execute(f"SELECT {columns} FROM source_replacements")]


def _unserviceable(store, old_root: Path) -> list[int]:
    """Completed swaps whose previous file is already outside the root.

    A move rewrites what it carries, but it cannot make an absolute path under someone else's
    folder part of this library, so these rows are reported rather than quietly pretended to
    work. They are not a reason to refuse: a reverse already refuses them today, and a move
    must not be blocked by an undo that was never available.
    """
    return [row["id"] for row in _replacement_rows(store, "id,old_file,status,pruned")
            if row["status"] == "completed" and not row["pruned"]
            and _relative(row["old_file"], old_root) is None]


def _inside(child: Path, parent: Path) -> bool:
    """`child` at or below `parent`, resolved so two spellings of one folder agree."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _move_dotfolder(old_root: Path, new_root: Path, name: str) -> int:
    """Carry a hidden folder of the library across: `.replacements/`, `.trash/`.

    Both live inside the media root and both are named by a unique id — a retained or staging
    folder by a replacement id, a trash file by a timestamp and a uuid — so the two trees merge
    rather than overwrite. A name already present on the far side is left alone, which makes a
    move that died halfway safe to run again.
    """
    source = old_root / name
    if not source.is_dir():
        return 0
    target = new_root / name
    if source.resolve() == target.resolve():
        return 0
    target.mkdir(parents=True, exist_ok=True)
    moved = 0
    for entry in sorted(source.iterdir()):
        destination = target / entry.name
        if entry.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            for child in sorted(entry.iterdir()):
                if (destination / child.name).exists():
                    continue
                shutil.move(str(child), str(destination / child.name))
                moved += 1
        elif not destination.exists():
            shutil.move(str(entry), str(destination))
            moved += 1
    # Only now-empty directories are removed; `rmdir` refuses a folder that still holds a
    # retained copy, which is the guard against this deleting one.
    for path in sorted((p for p in source.rglob("*") if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        source.rmdir()
    except OSError:
        pass
    return moved


def _restate(value: str, old_root: Path, new_root: Path):
    """`value` under the new root, or None when it was never inside the old one."""
    if not value:
        return None
    relative = _relative(value, old_root)
    return None if relative is None else str(new_root / relative)


def _rewrite_replacements(store, old_root: Path, new_root: Path) -> int:
    """Point the swap history at the new root, so a reverse still knows where its file goes."""
    changed = 0
    for row in _replacement_rows(store, "id,old_file,new_file"):
        old_file = _restate(row["old_file"], old_root, new_root) or row["old_file"]
        new_file = _restate(row["new_file"], old_root, new_root) or row["new_file"]
        if (old_file, new_file) == (row["old_file"], row["new_file"]):
            continue
        with store.lock:
            store.conn.execute("UPDATE source_replacements SET old_file=?,new_file=? WHERE id=?",
                               (old_file, new_file, row["id"]))
            store.conn.commit()
        changed += 1
    return changed


def _rewrite_trash(store, old_root: Path, new_root: Path) -> int:
    """The trash keeps both the file and where it came from; both are absolute paths."""
    changed = 0
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT id,path,trash_path FROM library_trash")]
    for row in rows:
        path = _restate(row["path"], old_root, new_root) or row["path"]
        trashed = _restate(row["trash_path"], old_root, new_root) or row["trash_path"]
        if (path, trashed) == (row["path"], row["trash_path"]):
            continue
        with store.lock:
            store.conn.execute("UPDATE library_trash SET path=?,trash_path=? WHERE id=?",
                               (path, trashed, row["id"]))
            store.conn.commit()
        changed += 1
    return changed


def move_library(store, target: str) -> dict:
    """Move every file under the library root to a new root and rewrite paths.

    The queue must be paused by the caller: a download writing into the old tree
    while it is being moved would leave a job pointing at nothing.

    What travels besides the tracks: `.replacements/` — the media a completed swap parked so
    `reverse` can put it back, and any scratch directory a swap still owns — and the rows that
    name those paths, which are rewritten against the new root. A folder *inside* the current
    library is refused before anything moves, because that is the one destination where the
    parked undos cannot come along: the copy would be moved into the tree it is being moved out
    of. A swap whose previous file lives outside the library is reported (`unserviceable`) and
    left alone — a move cannot make somebody else's folder part of this library.
    """
    store.sync_playlists()
    old_root = Path(store.setting("root")).expanduser()
    new_root = Path(target).expanduser()
    if new_root.resolve() == old_root.resolve():
        return {"moved": 0, "root": str(new_root), "note": "already there"}
    if _inside(new_root, old_root):
        raise ValueError(
            "the new library folder is inside the current one; choose a folder outside "
            + str(old_root) + " so the library's replacement history can move with it")
    unserviceable = _unserviceable(store, old_root)
    new_root.mkdir(parents=True, exist_ok=True)

    moved, missing = 0, 0
    with store.lock:
        rows = store.conn.execute("SELECT id, file FROM tracks WHERE file<>''").fetchall()
    for row in rows:
        source = Path(row["file"])
        if not source.is_file():
            missing += 1
            continue
        try:
            relative = source.resolve().relative_to(old_root.resolve())
        except ValueError:
            relative = Path(source.name)         # outside the root: keep the name
        destination = new_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = library_store.unique(destination, {
            str(r["file"]) for r in store.conn.execute(
                "SELECT file FROM tracks WHERE file<>''")})
        shutil.move(str(source), str(destination))
        with store.lock:
            store.conn.execute("UPDATE tracks SET file=?, art_hash='', art_checked=0 WHERE id=?",
                               (str(destination), row["id"]))
            store.conn.commit()
        moved += 1

    archive = old_root / ".archive.txt"
    if archive.is_file():
        shutil.move(str(archive), str(new_root / ".archive.txt"))
    retained = _move_dotfolder(old_root, new_root, ".replacements")
    _move_dotfolder(old_root, new_root, ".trash")
    rewritten = _rewrite_replacements(store, old_root, new_root)
    trashed = _rewrite_trash(store, old_root, new_root)
    store.set_settings({"root": str(new_root)})
    store.sync_playlists()
    if unserviceable:
        store.log("library moved, but replacement(s) " + ", ".join(str(i) for i in unserviceable)
                  + " keep a previous file outside it and cannot be reversed", "warn")
    store.log(f"library moved to {new_root} ({moved} files"
              f"{', ' + str(missing) + ' missing' if missing else ''}"
              f"{', ' + str(retained) + ' replacement file(s)' if retained else ''}"
              f"{', ' + str(trashed) + ' trashed file(s)' if trashed else ''})", "warn")
    return {"moved": moved, "missing": missing, "root": str(new_root),
            "replacements": rewritten, "trash": trashed, "unserviceable": unserviceable}
