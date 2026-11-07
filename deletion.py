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
            " WHERE pt.playlist_id=?", (playlist_id,)).fetchall()]


def _release_tracks(store, release_id: int) -> list[dict]:
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            "SELECT * FROM tracks WHERE release_id=?", (release_id,)).fetchall()]


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
