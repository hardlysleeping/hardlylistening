"""Deleting a playlist or an album, and the files that only it needed.

The library is one recording = one file. A file may be referenced by more than one
playlist, so a delete has to work out what becomes orphaned *before* unlinking
anything, and it must never touch a path outside the library root — a wrong root
setting must not turn "delete album" into "delete my home directory".

Nothing here decides *who* may delete; that is `security.may_delete`, checked by
the route before calling in.

A delete moves the file into `.trash/` under the media root instead of unlinking it
(HL-306). Same filesystem, so the move is a rename: it costs no space and no copy. The
trash is the one place space can be reclaimed without taking a song away from anybody —
everything in it was already deleted — so the 95% ceiling empties it oldest-first, and
each purge is logged with the file it took. `empty trash` in Settings does the same by
hand; a restored song comes back out of the trash before any download is considered.
"""
from __future__ import annotations

import os
import sqlite3
import shutil
import time
import uuid
from pathlib import Path

AUDIO_SUFFIXES = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac", ".webm"}

#: Hidden on purpose: the library scan skips dot-directories, so a trashed file is never
#: adopted as a loose track, and a person browsing the media folder does not see it twice.
TRASH_NAME = ".trash"


def _root(store) -> Path:
    return Path(store.setting("root")).expanduser().resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _trash_dir(store) -> Path:
    return _root(store) / TRASH_NAME


def _move_to_trash(store, path: Path, *, kind: str = "track", track_id: int = 0,
                   title: str = "") -> str | None:
    """Rename `path` into `.trash/` and remember where it came from.

    Returns the new path, or None when the file is outside the root or could not be
    moved — the caller then falls back to what it did before. The name keeps the
    original basename so a person looking inside the folder can tell what is in there.
    """
    root = _root(store)
    if not _inside(path, root):
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    target = _trash_dir(store) / (
        "%d-%s-%s" % (int(time.time()), uuid.uuid4().hex[:8], path.name))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, target)
    except OSError:
        return None
    with store.lock:
        store.conn.execute(
            "INSERT INTO library_trash(path,trash_path,track_id,title,kind,bytes,created)"
            " VALUES(?,?,?,?,?,?,?)",
            (str(path), str(target), int(track_id or 0), title or "", kind, size,
             time.time()))
        store.conn.commit()
    return str(target)


def _trashed_for(store, track_id: int) -> dict | None:
    """The newest trash row for a track, if its file is still there."""
    with store.lock:
        row = store.conn.execute(
            "SELECT * FROM library_trash WHERE track_id=? ORDER BY created DESC,id DESC"
            " LIMIT 1", (track_id,)).fetchone()
    if row is None:
        return None
    row = dict(row)
    if not Path(row["trash_path"]).is_file():
        with store.lock:
            store.conn.execute("DELETE FROM library_trash WHERE id=?", (row["id"],))
            store.conn.commit()
        return None
    return row


def trash_usage(store) -> dict:
    with store.lock:
        row = store.conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(bytes),0) b FROM library_trash").fetchone()
    return {"count": row["n"], "bytes": row["b"]}


def _drop(store, row: dict) -> bool:
    """Unlink one trashed file and forget its row. False when the file had already gone."""
    try:
        Path(row["trash_path"]).unlink(missing_ok=True)
        existed = True
    except OSError:
        existed = False
    with store.lock:
        store.conn.execute("DELETE FROM library_trash WHERE id=?", (row["id"],))
        store.conn.commit()
    return existed


def empty_trash(store) -> dict:
    """Throw the trash away, oldest first. This is the only place a file is erased."""
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT * FROM library_trash ORDER BY created,id")]
    removed = freed = 0
    for row in rows:
        Path(row["trash_path"]).unlink(missing_ok=True)
        _drop(store, row)
        removed += 1
        freed += int(row["bytes"] or 0)
    if removed:
        store.log(f"emptied the trash: {removed} file(s), {freed / (1024 ** 2):.0f} MB",
                  "warn", "library")
    return {"removed": removed, "bytes": freed}


def reclaim(store, limit_pct: float | None = None) -> int:
    """Free space by emptying the trash, oldest first, until the drive is under its ceiling.

    Only files a person already deleted live in the trash, so purging one takes nothing
    away from the library. Each purge is logged with its title, because a silent delete is
    how a feature like this stops being trustworthy. Returns the bytes freed.
    """
    import db
    limit = float(limit_pct if limit_pct is not None
                  else store.setting_float("disk_limit_pct", 95.0))
    root = _root(store)
    total, _free, used = db.disk_usage(root)
    if not total or used < limit:
        return 0
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT * FROM library_trash ORDER BY created,id")]
    freed = 0
    for row in rows:
        try:
            size = Path(row["trash_path"]).stat().st_size
        except OSError:
            size = int(row["bytes"] or 0)
        Path(row["trash_path"]).unlink(missing_ok=True)
        _drop(store, row)
        freed += size
        store.log(
            f"purged “{row['title'] or Path(row['trash_path']).name}” from the trash "
            f"to stay under the {limit:g}% disk ceiling", "warn", "library")
        total, _free, used = db.disk_usage(root)
        if not total or used < limit:
            break
    return freed


def restore_one(store, trash_id: int) -> dict:
    """Put one trashed file back where it came from, even when its track row is gone."""
    with store.lock:
        row = store.conn.execute("SELECT * FROM library_trash WHERE id=?",
                                 (trash_id,)).fetchone()
    if row is None:
        raise ValueError("no such trashed file")
    row = dict(row)
    target = Path(row["path"])
    source = Path(row["trash_path"])
    if not source.is_file():
        with store.lock:
            store.conn.execute("DELETE FROM library_trash WHERE id=?", (trash_id,))
            store.conn.commit()
        raise ValueError("that file is no longer in the trash")
    # A stale row must not be able to write outside the library.
    if not _inside(target, _root(store)):
        raise ValueError("that file was outside the library and cannot be restored")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    with store.lock:
        store.conn.execute("DELETE FROM library_trash WHERE id=?", (trash_id,))
        store.conn.commit()
    store.log(f"restored “{row['title'] or target.name}” from the trash", "ok", "library")
    return {"id": trash_id, "restored": True, "path": str(target)}


def _playlist_tracks(store, playlist_id: int) -> list[dict]:
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            "SELECT t.* FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id"
            " WHERE pt.playlist_id=? AND t.deleted=0", (playlist_id,)).fetchall()]


def _release_tracks(store, release_id: int) -> list[dict]:
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            "SELECT * FROM tracks WHERE release_id=? AND deleted=0", (release_id,)).fetchall()]


def _other_playlists(store, track_id: int, playlist_id: int | None) -> int:
    with store.lock:
        if playlist_id is None:
            row = store.conn.execute(
                "SELECT COUNT(*) n FROM playlist_tracks WHERE track_id=?",
                (track_id,)).fetchone()
        else:
            row = store.conn.execute(
                "SELECT COUNT(*) n FROM playlist_tracks WHERE track_id=? AND playlist_id<>?",
                (track_id, playlist_id)).fetchone()
    return row["n"]


def plan(store, kind: str, target_id: int) -> dict:
    """What a delete of this playlist/album would do, without doing it."""
    if kind == "playlist":
        rows = _playlist_tracks(store, target_id)
        keep = lambda t: _other_playlists(store, t["id"], target_id) > 0
        with store.lock:
            meta = store.conn.execute("SELECT title FROM playlists WHERE id=?",
                                      (target_id,)).fetchone()
    elif kind == "release":
        rows = _release_tracks(store, target_id)
        # A track that also sits in a playlist survives the album delete.
        keep = lambda t: _other_playlists(store, t["id"], None) > 0
        with store.lock:
            meta = store.conn.execute("SELECT title FROM releases WHERE id=?",
                                      (target_id,)).fetchone()
    else:
        raise ValueError("kind must be playlist or release")

    root = _root(store)
    deleting, kept = [], []
    files, outside, missing = [], [], []
    for track in rows:
        if keep(track):
            kept.append(track)
            continue
        deleting.append(track)
        raw = (track.get("file") or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_file():
            missing.append(raw)
        elif not _inside(path, root):
            outside.append(raw)          # row goes, the file stays put
        else:
            files.append(path)

    # A file can be the same recording shared by a row that survives this delete
    # (the queue links rather than copies). Unlinking it would leave that row
    # pointing at nothing.
    survivors = set()
    if deleting:
        marks = ",".join("?" * len(deleting))
        with store.lock:
            survivors = {row[0] for row in store.conn.execute(
                f"SELECT file FROM tracks WHERE file<>'' AND id NOT IN ({marks})",
                tuple(t["id"] for t in deleting))}
    files = [path for path in files if str(path) not in survivors]

    return {
        "kind": kind, "id": target_id, "title": (meta["title"] if meta else ""),
        "tracks": len(rows), "deleting": len(deleting), "kept": len(kept),
        "files": [str(p) for p in files], "outside_root": outside,
        "missing": missing,
        "bytes": sum((p.stat().st_size for p in files if p.exists()), 0),
        "_rows": deleting,
    }


def apply(store, kind: str, target_id: int, *, delete_files: bool = True) -> dict:
    """Carry the plan out. Rows always go; files only with `delete_files`."""
    report = plan(store, kind, target_id)
    rows, files = report.pop("_rows"), report["files"]

    with store.lock:
        for track in rows:
            store.conn.execute("DELETE FROM playlist_tracks WHERE track_id=?",
                               (track["id"],))
            store.conn.execute("DELETE FROM download_jobs WHERE track_id=?",
                               (track["id"],))
            store.conn.execute("DELETE FROM tracks WHERE id=?", (track["id"],))
        if kind == "playlist":
            store.conn.execute("DELETE FROM playlists WHERE id=?", (target_id,))
        elif kind == "release":
            # Tracks a playlist still holds survive the album, so they have to let
            # go of the release row first (foreign key) — they keep their album
            # name in `tracks.album`.
            store.conn.execute("UPDATE tracks SET release_id=NULL WHERE release_id=?",
                               (target_id,))
            store.conn.execute("DELETE FROM releases WHERE id=?", (target_id,))
        # Ownership tables only exist once authentication has been set up.
        for sql, args in (("DELETE FROM member_playlists WHERE playlist_id=?", (target_id,)),
                          ("DELETE FROM member_items WHERE kind=? AND target_id=?",
                           (kind, target_id))):
            try:
                store.conn.execute(sql, args)
            except sqlite3.OperationalError:
                pass
        store.conn.commit()

    removed, trashed, failed = [], 0, []
    if delete_files:
        for name in files:
            path = Path(name)
            if _move_to_trash(store, path, kind=kind, title=report["title"]):
                removed.append(name)
                trashed += 1
                continue
            # Falling back to an unlink keeps the delete working where the rename
            # cannot happen; the file is then simply gone, exactly as before HL-306.
            try:
                path.unlink()
                removed.append(name)
            except OSError as exc:
                failed.append(f"{path.name}: {exc}")
    how = (f"{trashed} file(s) to the trash" if trashed else "no files")
    if removed and len(removed) > trashed:
        how += f", {len(removed) - trashed} erased"
    store.log(
        f"deleted “{report['title']}”: {report['deleting']} track(s), {how}, "
        f"{report['bytes'] / (1024 ** 2):.0f} MB", "warn", f"{kind}:{target_id}")
    report.update({"ok": True, "deleted_files": len(removed), "trashed": trashed,
                   "failed": failed, "delete_files": delete_files})
    return report


def delete_track(store, track_id: int, *, delete_files: bool = True) -> dict:
    """Delete one song: its file, its job, and its visibility — not its identity.

    The row stays as a tombstone. Keeping it means the next playlist refresh finds
    an existing song instead of downloading it again, and Settings can put it back
    with its playlists and cover intact. The file only goes when no other row in
    the library still points at it.
    """
    track = store.track_by_id(track_id)
    if not track:
        raise ValueError("no such song")
    if track.get("deleted"):
        return {"id": track_id, "deleted": False, "already": True,
                "title": track.get("title") or ""}

    raw = (track.get("file") or "").strip()
    path = Path(raw).expanduser() if raw else None
    removed, kept_by, trashed = False, None, None
    if path and path.is_file():
        with store.lock:
            shared = store.conn.execute(
                "SELECT id FROM tracks WHERE id<>? AND file=? AND deleted=0",
                (track_id, raw)).fetchone()
        if shared:
            kept_by = shared["id"]          # another row still plays this exact file
        elif delete_files and _inside(path, _root(store)):
            trashed = _move_to_trash(store, path, kind="track", track_id=track_id,
                                     title=track.get("title") or "")
            if trashed:
                removed = True
            else:
                try:
                    path.unlink()
                    removed = True
                except OSError as error:
                    return {"id": track_id, "deleted": False, "error": str(error),
                            "title": track.get("title") or ""}

    with store.lock:
        store.conn.execute("UPDATE tracks SET deleted=?, size=0 WHERE id=?",
                           (time.time(), track_id))
        # A queued retry would otherwise re-create the file the person just removed.
        store.conn.execute("DELETE FROM download_jobs WHERE track_id=?", (track_id,))
        store.conn.commit()
    store.log(f"deleted “{track.get('title') or ''}” from the library"
              + (" (in the trash)" if trashed else ""), "warn", "library")
    return {"id": track_id, "deleted": True, "title": track.get("title") or "",
            "removed_file": removed, "kept_by": kept_by, "trashed": bool(trashed)}


def restore_track(store, track_id: int) -> dict:
    """Undo a song delete: show it again, and get its audio back if it is gone.

    A download is re-queued from the provider it came from. An import has no
    provider to ask, so it is copied back from the file it was imported from —
    and when that is gone too, the tombstone stays: a row that can never play is
    not a restored song.
    """
    track = store.track_by_id(track_id)
    if not track:
        raise ValueError("no such song")
    if not track.get("deleted"):
        return {"id": track_id, "restored": False, "already": True,
                "title": track.get("title") or ""}

    raw = (track.get("file") or "").strip()
    path = Path(raw).expanduser() if raw else None
    if path and path.is_file():
        with store.lock:
            store.conn.execute("UPDATE tracks SET deleted=0, size=? WHERE id=?",
                               (path.stat().st_size, track_id))
            store.conn.commit()
        store.log(f"restored “{track.get('title') or ''}”", "ok", "library")
        return {"id": track_id, "restored": True, "queued": False,
                "title": track.get("title") or ""}

    trashed = _trashed_for(store, track_id)
    if trashed is not None:
        # The delete kept the audio, so restoring is a rename back — no provider, no
        # copy, nothing to download, even for an import whose source has since gone.
        target = Path(trashed["path"]) if trashed["path"] else path
        if target is None or not _inside(target, _root(store)):
            return {"id": track_id, "restored": False,
                    "error": "The trashed file has no place inside the library to go back to.",
                    "title": track.get("title") or ""}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(trashed["trash_path"], target)
        except OSError as error:
            return {"id": track_id, "restored": False, "error": str(error),
                    "title": track.get("title") or ""}
        with store.lock:
            store.conn.execute("DELETE FROM library_trash WHERE id=?", (trashed["id"],))
            store.conn.execute("UPDATE tracks SET deleted=0, size=? WHERE id=?",
                               (target.stat().st_size, track_id))
            store.conn.commit()
        store.log(f"restored “{track.get('title') or ''}” from the trash", "ok", "library")
        return {"id": track_id, "restored": True, "queued": False, "copied": True,
                "from_trash": True, "title": track.get("title") or ""}

    if (track.get("provider") or "") == "local":
        source = Path((track.get("source_url") or "").strip()).expanduser()
        if not path or not source.is_file():
            return {"id": track_id, "restored": False,
                    "error": "The imported file is gone and an import has no source "
                             "to download from, so this song cannot be restored.",
                    "title": track.get("title") or ""}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, path)
        except OSError as error:
            return {"id": track_id, "restored": False, "error": str(error),
                    "title": track.get("title") or ""}
        with store.lock:
            store.conn.execute("UPDATE tracks SET deleted=0, size=? WHERE id=?",
                               (path.stat().st_size, track_id))
            store.conn.commit()
        store.log(f"restored “{track.get('title') or ''}” from its import", "ok", "library")
        return {"id": track_id, "restored": True, "queued": False, "copied": True,
                "title": track.get("title") or ""}

    with store.lock:
        store.conn.execute("UPDATE tracks SET deleted=0 WHERE id=?", (track_id,))
        store.conn.commit()
    job = store.enqueue(track_id, track.get("provider") or "",
                        track.get("source_url") or "")
    store.log(f"restored “{track.get('title') or ''}”"
              + (" and queued it to download again" if job else ""), "ok", "library")
    return {"id": track_id, "restored": True, "queued": bool(job),
            "title": track.get("title") or ""}
