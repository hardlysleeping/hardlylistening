"""Deleting a playlist or an album, and the files that only it needed.

The library is one recording = one file. A file may be referenced by more than one
playlist, so a delete has to work out what becomes orphaned *before* unlinking
anything, and it must never touch a path outside the library root — a wrong root
setting must not turn "delete album" into "delete my home directory".

Nothing here decides *who* may delete; that is `security.may_delete`, checked by
the route before calling in.
"""
from __future__ import annotations

import sqlite3
import shutil
import time
from pathlib import Path

AUDIO_SUFFIXES = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac", ".webm"}


def _root(store) -> Path:
    return Path(store.setting("root")).expanduser().resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


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

    removed, failed = [], []
    if delete_files:
        for name in files:
            path = Path(name)
            try:
                path.unlink()
                removed.append(name)
            except OSError as exc:
                failed.append(f"{path.name}: {exc}")
    store.log(
        f"deleted “{report['title']}”: {report['deleting']} track(s), "
        f"{len(removed)} file(s), {report['bytes'] / (1024 ** 2):.0f} MB freed",
        "warn", f"{kind}:{target_id}")
    report.update({"ok": True, "deleted_files": len(removed), "failed": failed,
                   "delete_files": delete_files})
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
    removed, kept_by = False, None
    if path and path.is_file():
        with store.lock:
            shared = store.conn.execute(
                "SELECT id FROM tracks WHERE id<>? AND file=? AND deleted=0",
                (track_id, raw)).fetchone()
        if shared:
            kept_by = shared["id"]          # another row still plays this exact file
        elif delete_files and _inside(path, _root(store)):
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
    store.log(f"deleted “{track.get('title') or ''}” from the library", "warn", "library")
    return {"id": track_id, "deleted": True, "title": track.get("title") or "",
            "removed_file": removed, "kept_by": kept_by}


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
