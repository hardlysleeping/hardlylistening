"""Prepare a removable drive for a CDJ: plan it, copy it, verify it (HL-228).

`dj_export` hands a DJ app a list of paths on *this* machine. That is a hand-off, not
preparation: carried to another computer, every path in it is dead. This module writes
the audio onto the drive and then writes the Rekordbox XML that points at those drive
paths, so the set imports with no network and no second copy step.

Four statements, because each one is a way this goes wrong:

* **A plan before any bytes are written.** The drive's free space is measured, every
  track is weighed (its own size, or what a 320 kbit/s conversion of it should take), and
  the answer names the tracks that will not fit. A destination that cannot be written at
  all is a refusal with a reason, not a partial run.
* **A copy that verifies.** The file is streamed into `<name>.part` while its SHA-256 is
  computed, then that staged file is read back and hashed again; only when both the hash
  and the size match is it renamed into place. A half-written file never wears a real name
  and never appears in the XML.
* **A drive that can be pulled out.** A write error mid-copy (ENOSPC, EIO, the device gone)
  fails the job with the reason, removes the `.part`, and leaves the track uncopied — never
  reported as one that arrived.
* **No new daemon.** One worker thread inside this process, started on demand, one job at
  a time, with the job's state in SQLite so a status poll is a database read.

Nothing on the drive is deleted except this app's own staged `.part` files: a re-run
overwrites its own names and leaves everything else alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

import zip_export
from library import dj_export

FOLDER = "HardlyListening"
CHUNK = 1 << 20          # 1 MiB: large enough to be fast, small enough to notice a pull-out
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

EXPORTS = """CREATE TABLE IF NOT EXISTS usb_exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id TEXT NOT NULL DEFAULT '',
  member_name TEXT NOT NULL DEFAULT '',
  destination TEXT NOT NULL,
  playlist_id INTEGER,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  plan TEXT NOT NULL DEFAULT '{}',
  tracks INTEGER NOT NULL DEFAULT 0,
  copied INTEGER NOT NULL DEFAULT 0,
  bytes INTEGER NOT NULL DEFAULT 0,
  copied_bytes INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  started REAL NOT NULL DEFAULT 0,
  finished REAL NOT NULL DEFAULT 0
)"""

FILES = """CREATE TABLE IF NOT EXISTS usb_export_files (
  export_id INTEGER NOT NULL,
  track_id INTEGER,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  size INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (export_id, name)
)"""

_lock = threading.Lock()
_wake = threading.Event()
_state = {"running": False, "export_id": 0, "current": "", "message": ""}


def schema(store) -> None:
    with store.lock:
        store.conn.execute(EXPORTS)
        store.conn.execute(FILES)
        store.conn.commit()


# ---- planning -----------------------------------------------------------------

def _safe(text: str) -> str:
    return _UNSAFE.sub(" ", text or "").strip().strip(".") or "Untitled"


def _name(track: dict, suffix: str) -> str:
    artist = (track.get("artist_name") or "").strip()
    title = (track.get("title") or "").strip() or f"track {track.get('id')}"
    return (_safe(f"{artist} - {title}") if artist else _safe(title)) + suffix


def _rows(store, playlist_id: int | None) -> tuple[list[dict], str]:
    """Track rows that have a filename, restricted to a playlist when one is named."""
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT id,title,artist_name,album,duration,file,bpm FROM tracks"
            " WHERE file<>'' ORDER BY artist_name, album, id")]
        title = "HardlyListening library"
        if playlist_id is not None:
            playlist = store.conn.execute(
                "SELECT id,title FROM playlists WHERE id=?", (playlist_id,)).fetchone()
            if playlist is None:
                raise ValueError("unknown playlist")
            allowed = {row["track_id"] for row in store.conn.execute(
                "SELECT track_id FROM playlist_tracks WHERE playlist_id=?", (playlist_id,))}
            rows = [row for row in rows if row["id"] in allowed]
            title = playlist["title"] or title
    return rows, title


def _refuse(destination: str) -> str:
    """Why this destination cannot be written, or '' when it can."""
    if not str(destination or "").strip():
        return "Give the folder the drive is mounted at."
    path = Path(str(destination)).expanduser()
    if not path.is_absolute():
        return "Use the drive's full path, as it is mounted on this machine."
    if not path.exists():
        return f"There is no folder at {path}."
    if not path.is_dir():
        return f"{path} is a file, not a folder."
    if not os.access(path, os.W_OK | os.X_OK):
        return f"This app cannot write to {path}. Check the drive is mounted read-write."
    return ""


def _inside_library(store, path: Path) -> bool:
    root = store.setting("root")
    if not root:
        return False
    try:
        path.expanduser().resolve().relative_to(Path(root).expanduser().resolve())
        return True
    except (ValueError, OSError):
        return False


def _cues(store, member: dict, track_id: int) -> list[dict]:
    """This member's hot cues — the ones a CDJ shows as POSITION_MARKs."""
    if not member:
        return []
    try:
        rows = store.conn.execute(
            "SELECT slot,position FROM listening_cues WHERE member_id=? AND track_id=?"
            " ORDER BY slot", (member.get("id", ""), track_id)).fetchall()
    except Exception:                      # the table is created with the first cue
        return []
    return [{"slot": int(row["slot"]), "position": float(row["position"])} for row in rows]


def plan(store, member: dict, destination: str, playlist_id: int | None = None) -> dict:
    """What this copy would do, without doing it.

    `fits` answers the question a person asks before writing to a stick: does the whole
    set go on, and if not, what stays behind. `blocked` answers a different one — do not
    write here at all.
    """
    schema(store)
    path = Path(str(destination or "")).expanduser()
    blocked = _refuse(destination)
    if not blocked and _inside_library(store, path):
        blocked = "That folder is inside the library, so the copy would include itself."
    rows, title = _rows(store, playlist_id)
    copying, converts, missing = [], [], []
    seen: dict[str, int] = {}
    for row in rows:
        source = Path(row["file"])
        if not source.is_file():
            missing.append({"track_id": row["id"], "title": row["title"] or ""})
            continue
        converting = source.suffix.lower() not in zip_export.DJ_SAFE
        name = _name(row, ".mp3" if converting else source.suffix)
        # Two songs can honestly share an artist and a title; the drive cannot.
        count = seen.get(name.lower(), 0) + 1
        seen[name.lower()] = count
        if count > 1:
            name = f"{Path(name).stem} ({count}){Path(name).suffix}"
        size = source.stat().st_size
        # A conversion's size is not known until it runs. 320 kbit/s over the track's own
        # length is the figure used to decide whether it fits, and the plan calls it an
        # estimate rather than a measurement.
        estimate = int(round((row.get("duration") or 0) * 40_000)) if converting else 0
        entry = {"track_id": row["id"], "name": name, "source": str(source),
                 "artist": row.get("artist_name") or "", "title": row.get("title") or "",
                 "bytes": max(size, estimate), "size": size, "convert": converting,
                 "estimate": converting, "bpm": float(row.get("bpm") or 0.0),
                 "cues": _cues(store, member, row["id"])}
        (converts if converting else copying).append(entry)
    ordered = copying + converts
    needed = sum(entry["bytes"] for entry in ordered)
    free = 0
    if not blocked:
        try:
            free = shutil.disk_usage(path).free
        except OSError as error:
            blocked = f"This app cannot read the free space at {path}: {error}"
    overflow, running = [], 0
    if not blocked:
        for entry in ordered:
            if running + entry["bytes"] > free:
                overflow.append({"track_id": entry["track_id"], "name": entry["name"],
                                 "bytes": entry["bytes"]})
            else:
                running += entry["bytes"]
    return {
        "destination": str(path),
        "folder": str(path / FOLDER),
        "playlist": playlist_id,
        "title": title,
        "tracks": len(ordered),
        "bytes": needed,
        "free": free,
        "fits": not blocked and not overflow,
        "blocked": blocked,
        "copy": copying,
        "convert": converts,
        "missing": missing,
        "overflow": overflow,
        "cues": sum(len(entry["cues"]) for entry in ordered),
        "bpm": sum(1 for entry in ordered if entry["bpm"] > 0),
    }


# ---- starting and reading jobs ------------------------------------------------

def start(store, member: dict, destination: str, playlist_id: int | None = None) -> dict:
    """Queue a write for a plan that fits; ValueError with the reason when it does not."""
    result = plan(store, member, destination, playlist_id)
    if result["blocked"]:
        raise ValueError(result["blocked"])
    if not result["tracks"]:
        raise ValueError("There are no files on this machine to copy for that scope.")
    if not result["fits"]:
        raise ValueError(
            "That does not fit: %d bytes needed, %d free, %d track(s) left behind."
            % (result["bytes"], result["free"], len(result["overflow"])))
    with store.lock:
        cursor = store.conn.execute(
            "INSERT INTO usb_exports(member_id,member_name,destination,playlist_id,title,"
            " status,plan,tracks,bytes,created) VALUES(?,?,?,?,?,'queued',?,?,?,?)",
            (member.get("id", ""), member.get("display_name", "") or "", result["destination"],
             playlist_id, result["title"], json.dumps(result), result["tracks"],
             result["bytes"], time.time()))
        export_id = cursor.lastrowid
        store.conn.commit()
    store.log(f"USB export queued: {result['tracks']} track(s) to {result['destination']}",
              "info", f"usb:{export_id}")
    wake(store)
    return job(store, export_id)


def job(store, export_id: int) -> dict | None:
    schema(store)
    with store.lock:
        row = store.conn.execute("SELECT * FROM usb_exports WHERE id=?", (export_id,)).fetchone()
        if row is None:
            return None
        files = [dict(r) for r in store.conn.execute(
            "SELECT name,status,size,sha256,error FROM usb_export_files"
            " WHERE export_id=? ORDER BY rowid", (export_id,))]
    result = dict(row)
    result["plan"] = json.loads(result["plan"] or "{}")
    result["files"] = files
    result["percent"] = (round(100 * result["copied"] / result["tracks"], 1)
                         if result["tracks"] else 0.0)
    return result


def status(store, limit: int = 5) -> dict:
    schema(store)
    with store.lock:
        ids = [row["id"] for row in store.conn.execute(
            "SELECT id FROM usb_exports ORDER BY id DESC LIMIT ?", (limit,))]
    with _lock:
        snapshot = dict(_state)
    return {"jobs": [job(store, export_id) for export_id in ids], "worker": snapshot}


def wake(store) -> None:
    """Start a worker for this store. It exits as soon as the queue is empty.

    A thread per wake rather than one long-lived thread, on purpose: a thread that outlived
    its store (a closed database, a fixture that has been torn down) can then never stand in
    front of the next job, and two workers are harmless because `_run` claims the job with a
    conditional UPDATE before it touches a byte.
    """
    _wake.set()
    threading.Thread(target=_loop, args=(store,), name="usb-export", daemon=True).start()


def startup(store) -> int:
    """A job that was running when the process died is not running now: say so.

    Called once at boot. Leaving it `copying` would make the status lie — a job nobody is
    copying would look live forever — and re-running it silently would claim work that did
    not happen. It is marked failed with the reason, and re-queuing it is a person's click.
    """
    schema(store)
    with store.lock:
        cursor = store.conn.execute(
            "UPDATE usb_exports SET status='failed',finished=?,"
            " error='interrupted — the app stopped before this finished'"
            " WHERE status='copying'", (time.time(),))
        store.conn.commit()
        stale = cursor.rowcount
    if stale:
        store.log(f"{stale} USB export job(s) were interrupted; marked failed", "warn")
    return stale


# ---- the worker ---------------------------------------------------------------

def _loop(store) -> None:
    """Drain this store's queue one job at a time, then exit."""
    while True:
        _wake.wait(0.5)
        _wake.clear()
        with store.lock:
            row = store.conn.execute(
                "SELECT id FROM usb_exports WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return
        try:
            # `_run` claims the job itself; if another worker got there first it returns and
            # this loop simply looks for the next one.
            _run(store, int(row["id"]))
        except Exception as error:            # noqa: BLE001 — a job must never stay "copying"
            _fail(store, int(row["id"]), f"internal error: {error}")


def _fail(store, export_id: int, reason: str) -> None:
    with store.lock:
        store.conn.execute(
            "UPDATE usb_exports SET status='failed',error=?,finished=? WHERE id=?",
            (str(reason)[:500], time.time(), export_id))
        store.conn.commit()
    with _lock:
        _state.update(running=False, export_id=0, current="")
    store.log(f"USB export failed: {reason}", "warn", f"usb:{export_id}")


def _mark(store, export_id: int, entry: dict, status: str, size: int = 0,
          digest: str = "", error: str = "") -> None:
    with store.lock:
        store.conn.execute(
            "INSERT OR REPLACE INTO usb_export_files(export_id,track_id,name,status,size,sha256,error)"
            " VALUES(?,?,?,?,?,?,?)",
            (export_id, entry.get("track_id"), entry["name"], status, size, digest,
             str(error)[:300]))
        store.conn.commit()


def _hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _stage(source: Path, staged: Path) -> tuple[str, int]:
    """Stream `source` into `staged`, returning the SHA-256 and size of what was written."""
    staged.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with open(source, "rb") as reader, open(staged, "wb") as writer:
        while True:
            chunk = reader.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest(), size


def _copy_one(store, export_id: int, entry: dict, destination: Path) -> tuple[Path, int]:
    """Copy (or convert) one track, verifying it before it takes its real name."""
    source = Path(entry["source"])
    target = destination / entry["name"]
    staged = target.with_name(target.name + ".part")
    with _lock:
        _state["current"] = entry["name"]
    try:
        if entry.get("convert"):
            zip_export.to_mp3(source, staged)
            digest, size = _hash(staged)
        else:
            digest, size = _stage(source, staged)
        # Read the staged file back: what is on the drive is what is checked, not what we
        # believe was written. A device that silently truncates fails here.
        check, checked_size = _hash(staged)
        if check != digest or checked_size != size:
            raise OSError("the copy does not read back as what was written")
        os.replace(staged, target)
    except (OSError, ValueError) as error:
        try:
            staged.unlink()
        except OSError:
            pass
        _mark(store, export_id, entry, "failed", error=str(error))
        raise OSError(f"{entry['name']}: {error}") from error
    _mark(store, export_id, entry, "copied", size=size, digest=digest)
    return target, size


def _run(store, export_id: int) -> bool:
    """Copy one queued job. Returns False when it was not this caller's to run.

    The claim is a conditional UPDATE, so two workers that both saw the row cannot both
    write the same tracks: the second one's claim changes nothing and it walks away.
    """
    with store.lock:
        claimed = store.conn.execute(
            "UPDATE usb_exports SET status='copying',started=? WHERE id=? AND status='queued'",
            (time.time(), export_id)).rowcount
        store.conn.commit()
    if not claimed:
        return False
    record = job(store, export_id)
    if record is None:
        return True
    plan_json = record["plan"]
    destination = Path(record["destination"]) / FOLDER
    with _lock:
        _state.update(running=True, export_id=export_id, message="")
    written: list[tuple[dict, Path]] = []
    copied_bytes = 0
    for entry in plan_json.get("copy", []) + plan_json.get("convert", []):
        try:
            target, size = _copy_one(store, export_id, entry, destination)
        except OSError as error:
            _fail(store, export_id, str(error))
            return
        written.append((entry, target))
        copied_bytes += size
        with store.lock:
            store.conn.execute("UPDATE usb_exports SET copied=copied+1,copied_bytes=? WHERE id=?",
                               (copied_bytes, export_id))
            store.conn.commit()
    try:
        _write_indexes(record, written)
    except OSError as error:
        _fail(store, export_id, f"the index files could not be written: {error}")
        return
    with store.lock:
        store.conn.execute(
            "UPDATE usb_exports SET status='finished',finished=?,error='' WHERE id=?",
            (time.time(), export_id))
        store.conn.commit()
    with _lock:
        _state.update(running=False, export_id=0, current="")
    store.log(f"USB export finished: {len(written)} track(s) to {record['destination']}",
              "info", f"usb:{export_id}")


def _write_indexes(record: dict, written: list[tuple[dict, Path]]) -> None:
    """The two files a DJ app opens: Rekordbox XML and an M3U8, both pointing at the drive."""
    if not written:
        return
    tracks = [{"title": entry.get("title") or Path(entry["name"]).stem,
               "artist": entry.get("artist") or "", "album": "",
               "duration": 0, "kind": Path(entry["name"]).suffix.lstrip(".").upper(),
               "target": target, "bpm": entry.get("bpm") or 0.0,
               "cues": entry.get("cues") or []}
              for entry, target in written]
    title = record["title"] or "HardlyListening"
    xml = dj_export.usb_document(tracks, title)
    lines = ["#EXTM3U", f"#PLAYLIST:{title}"]
    for track in tracks:
        label = f"{track['artist']} - {track['title']}" if track["artist"] else track["title"]
        lines.append(f"#EXTINF:-1,{label}")
        lines.append(str(track["target"]))
    _atomic_write(Path(record["destination"]) / "HardlyListening.m3u8", "\n".join(lines) + "\n")
    _atomic_write(Path(record["destination"]) / "HardlyListening.rekordbox.xml", xml)


def _atomic_write(target: Path, text: str) -> None:
    staged = target.with_name(target.name + ".part")
    with open(staged, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, target)