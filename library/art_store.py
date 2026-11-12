"""One copy of each cover, and a display-size copy inside every track.

Content-addressed: the store's file name is the image's own hash, so the same
cover found in twenty files is written once and every track points at it by hash
(`tracks.art_hash`). The track itself keeps a 640 px JPEG — what the UI actually
displays — instead of the uploader's 3 MB original.

Nothing here is a chore you run by hand. It happens on three paths:

  * a download finishes        -> engine/queue.py calls normalise()
  * a cover is first requested  -> artwork.for_track() fills in what it finds
  * a background sweep          -> start() at boot, for rows not checked yet
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from library import id3art

from paths import CACHE_DIR

CACHE = CACHE_DIR / "artwork"
# The width the album page and the fullscreen disc draw. A bigger embedded picture
# buys nothing on screen and costs megabytes in every copy of the file.
WIDTH = 640
SWEEP_EVERY = 120.0

_lock = threading.Lock()
_thread: threading.Thread | None = None
_state = {"running": False, "done": 0, "slimmed": 0, "skipped": 0, "failed": 0,
          "current": "", "error": ""}


def folder(store) -> Path:
    """The store lives beside the library, so moving the library moves the art."""
    root = Path(store.setting("root")).expanduser()
    return root.parent / f"{root.name}-artwork"


def digest(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _suffix(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "bin"


_TOOLS: dict[str, str | None] = {}
_TOOL_PATHS = {
    "cjxl": ("/home/linuxbrew/.linuxbrew/bin/cjxl", "/usr/bin/cjxl", "/usr/local/bin/cjxl"),
    "djxl": ("/home/linuxbrew/.linuxbrew/bin/djxl", "/usr/bin/djxl", "/usr/local/bin/djxl"),
}


def _tool(name: str) -> str | None:
    """cjxl/djxl, or None: a machine without them stores the original format instead."""
    if name not in _TOOLS:
        found = shutil.which(name)
        if not found:
            found = next((path for path in _TOOL_PATHS[name] if Path(path).is_file()), None)
        _TOOLS[name] = found
    return _TOOLS[name]


def _jxl_encode(data: bytes, suffix: str) -> bytes | None:
    """JPEG -> JXL by lossless transcode, PNG -> JXL lossless. None when impossible.

    `--lossless_jpeg=1` keeps the original JPEG *file* inside the .jxl, so `djxl`
    gives the exact bytes back. A PNG has no such mode: `-d 0` reproduces the
    pixels exactly, not the file's chunk layout.
    """
    tool = _tool("cjxl")
    if tool is None or suffix not in ("jpg", "png"):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / f"in.{suffix}"
        target = Path(tmp) / "out.jxl"
        source.write_bytes(data)
        command = [tool, str(source), str(target)]
        command += ["--lossless_jpeg=1"] if suffix == "jpg" else ["-d", "0"]
        try:
            done = subprocess.run(command, capture_output=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0 or not target.is_file():
            return None
        return target.read_bytes()


def _jxl_verify(jxl: bytes, data: bytes, suffix: str) -> bool:
    """Prove the .jxl holds what we are about to drop before dropping it."""
    tool = _tool("djxl")
    if tool is None:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        encoded = Path(tmp) / "in.jxl"
        decoded = Path(tmp) / f"out.{suffix}"
        encoded.write_bytes(jxl)
        try:
            done = subprocess.run([tool, str(encoded), str(decoded)], capture_output=True,
                                  timeout=300)
        except (OSError, subprocess.SubprocessError):
            return False
        if done.returncode != 0 or not decoded.is_file():
            return False
        if suffix == "jpg":
            return decoded.read_bytes() == data
        return _pixels(decoded) == _pixels(data, suffix)


def _pixels(data_or_path, suffix: str = "") -> str | None:
    """Decoded pixels, so a PNG re-encode can be compared without trusting a container."""
    if isinstance(data_or_path, (bytes, bytearray)):
        with tempfile.NamedTemporaryFile(suffix=f".{suffix}", delete=False) as handle:
            handle.write(data_or_path)
            path = Path(handle.name)
        try:
            return _pixels(path)
        finally:
            path.unlink(missing_ok=True)
    try:
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", str(data_or_path),
                               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return hashlib.md5(done.stdout).hexdigest() if done.stdout else None


def put(store, data: bytes) -> str:
    """Store this image unless the same bytes are already there. Returns its hash.

    The canonical copy is JPEG XL: one image, one file, ~20% smaller than the JPEG
    it came from and reversible back to it.
    """
    key = digest(data)
    if image(store, key) is not None:
        return key
    suffix = _suffix(data)
    encoded = _jxl_encode(data, suffix)
    extension = "jxl" if encoded else suffix
    target = folder(store) / f"{key}.{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".part")
    temp.write_bytes(encoded or data)
    os.replace(temp, target)
    return key


def image(store, key: str) -> Path | None:
    if not key:
        return None
    found = [path for path in folder(store).glob(f"{key}.*")
             if path.is_file() and path.suffix != ".part"]
    if not found:
        return None
    return sorted(found, key=lambda path: path.suffix != ".jxl")[0]


def _is_image(path: Path) -> bool:
    """Magic bytes, not a size threshold: a solid-colour 120 px cover is ~300 bytes."""
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return False
    return (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n"
            or head[:12] == b"\x00\x00\x00\x0cJXL \r\n\x87\n")


def migrate(store, progress=None) -> dict:
    """Convert the store's existing JPEG/PNG copies to JPEG XL, one verified file at a time.

    Nothing is unlinked until the .jxl has been decoded again and compared with what
    is being removed: identical bytes for a JPEG, identical pixels for a PNG.
    """
    seen = {"converted": 0, "kept": 0, "saved": 0, "failed": 0}
    folder_path = folder(store)
    if not folder_path.exists():
        return seen
    for path in sorted(folder_path.glob("*")):
        suffix = path.suffix.lstrip(".").lower()
        if not path.is_file() or suffix not in ("jpg", "png") or ".part" in path.name:
            continue
        data = path.read_bytes()
        jxl = _jxl_encode(data, suffix)
        if jxl is None or not _jxl_verify(jxl, data, suffix):
            seen["kept"] += 1
            if progress:
                progress("KEEP", path.name, len(data), len(data))
            continue
        target = path.with_suffix(".jxl")
        temp = target.with_name(target.name + ".part")
        temp.write_bytes(jxl)
        os.replace(temp, target)
        path.unlink()
        seen["converted"] += 1
        seen["saved"] += len(data) - len(jxl)
        if progress:
            progress("DEL", path.name, len(data), len(jxl))
    return seen


def display(store, key: str) -> Path | None:
    """The 640 px JPEG for this cover, generated once and shared by every track."""
    if not key:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    jpeg = CACHE / f"{key}@{WIDTH}.jpg"
    if jpeg.is_file() and _is_image(jpeg):
        return jpeg
    source = image(store, key)
    if source is None:
        return None
    temp = CACHE / f"{key}.part.jpg"
    try:
        subprocess.run(
            # min() so a cover that is already smaller than 640 px is never upscaled
            ["ffmpeg", "-v", "error", "-y", "-i", str(source),
             "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale=min({WIDTH}\\,iw):-2",
             "-q:v", "4", "-f", "image2", str(temp)],
            capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if temp.is_file() and _is_image(temp):
        os.replace(temp, jpeg)
        return jpeg
    temp.unlink(missing_ok=True)
    return None


def _mark(store, track_id: int, key: str) -> None:
    with store.lock:
        store.conn.execute(
            "UPDATE tracks SET art_hash=?, art_checked=? WHERE id=?",
            (key, time.time(), track_id))
        store.conn.commit()


#: Rows the first sweep marked "checked" without looking: every non-mp3 file was skipped
#: because rewriting an MP4 tag was out of scope, and that skip was written back as "no
#: cover". Extraction for those files is now done (the file itself is never rewritten), so
#: they are put back in the queue once. Stored in `settings`, so it runs once per database.
EXTRACT_REVISION = "2"


def _extract(store, path: Path, known: str) -> tuple[str, str]:
    """A file we do not rewrite: take its cover out, leave the file alone.

    `.m4a` keeps its picture in a nested atom and `.webm`/`.flac` in their own places; ffmpeg
    reads all of them, and the store only needs the image. Nothing is logged as 'slimmed'
    because the file is not touched — the cover is filed, the audio stays exactly as it is.
    """
    if known and display(store, known) is not None:
        return "already", known
    CACHE.mkdir(parents=True, exist_ok=True)
    temp = CACHE / f"{digest(str(path).encode())}.extract.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(path),
             "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale=min({WIDTH}\\,iw):-2",
             "-q:v", "4", "-f", "image2", str(temp)],
            capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return "failed", known
    if not (temp.is_file() and _is_image(temp)):
        temp.unlink(missing_ok=True)
        return "no art", known
    try:
        key = put(store, temp.read_bytes())
    finally:
        temp.unlink(missing_ok=True)
    shared = display(store, key)
    return ("kept", key) if shared is not None else ("failed", key)


def _slim(store, path: Path, known: str) -> tuple[str, str]:
    """The work itself: (outcome, cover hash). One file, never the audio.

    Returns one of: 'slimmed', 'already', 'kept', 'no art', 'skipped', 'failed'.
    """
    if path.suffix.lower() != ".mp3":
        return _extract(store, path, known)
    try:
        raw = path.read_bytes()
        pictures = id3art.covers(raw)
    except (OSError, id3art.Unsupported):
        return "failed", known
    if not pictures:
        return "no art", known
    picture = pictures[0][1]
    if known:
        # Re-running on a file that already holds the display copy must not file
        # that copy as a new "master".
        shared = display(store, known)
        if shared is not None and shared.read_bytes() == picture:
            return "already", known
    key = put(store, picture)
    jpeg = display(store, key)
    if jpeg is None:
        return "failed", key
    if jpeg.stat().st_size >= len(picture):
        # The picture already in the file is no larger than the display copy. Keep
        # it: rewriting would only make the file bigger.
        return "kept", key
    try:
        new = id3art.embed(raw, jpeg.read_bytes())
    except id3art.Unsupported:
        return "skipped", key
    if new is None:
        return "already", key
    if id3art.audio(new) != id3art.audio(raw):
        return "failed", key                        # never replace on a doubt
    temp = path.with_name(path.name + ".art")
    try:
        temp.write_bytes(new)
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        return "failed", key
    return "slimmed", key


def normalise(store, track_id: int) -> str:
    """Deduplicate one track's cover and leave a display-size copy inside it."""
    row = store.conn.execute("SELECT file, art_hash FROM tracks WHERE id=?",
                             (track_id,)).fetchone()
    path = Path((row["file"] if row else "") or "")
    if not path.is_file():
        _mark(store, track_id, "")
        return "missing"
    outcome, key = _slim(store, path, (row["art_hash"] or "") if row else "")
    _mark(store, track_id, key)
    return outcome


def normalise_file(store, path: Path) -> str:
    """The same work for an audio file that no track row points at."""
    row = store.conn.execute("SELECT art_hash, size, mtime FROM art_files WHERE path=?",
                             (str(path),)).fetchone()
    known = row["art_hash"] if row else ""
    outcome, key = _slim(store, path, known or "")
    try:
        stat = path.stat()
    except OSError:
        return outcome
    with store.lock:
        store.conn.execute(
            "INSERT INTO art_files (path, size, mtime, art_hash, checked) VALUES (?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
            "art_hash=excluded.art_hash, checked=excluded.checked",
            (str(path), stat.st_size, stat.st_mtime, key, time.time()))
        store.conn.commit()
    return outcome


def candidates(store, limit: int = 50) -> list[int]:
    rows = store.conn.execute(
        "SELECT id FROM tracks WHERE file != '' AND art_checked = 0 AND deleted = 0 ORDER BY id LIMIT ?",
        (limit,)).fetchall()
    return [row[0] for row in rows]


def requeue_skipped(store) -> int:
    """Put back the rows an older sweep marked checked without looking at the file.

    Those are all non-mp3 (the skip above used to be written as `art_checked`, with no
    cover hash), so they are exactly the rows whose covers were never extracted. Runs once
    per database: without the marker this would re-check the same cover-less files on every
    boot, for nothing.
    """
    if store.setting("art_extract_revision") == EXTRACT_REVISION:
        return 0
    with store.lock:
        changed = store.conn.execute(
            "UPDATE tracks SET art_checked=0 WHERE file<>'' AND art_checked>0"
            " AND (art_hash IS NULL OR art_hash='') AND file NOT LIKE '%.mp3'").rowcount
        store.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                           ("art_extract_revision", EXTRACT_REVISION))
        store.conn.commit()
    return changed


def loose_files(store, limit: int = 200) -> list[Path]:
    """Audio files on disk that no track row claims, new or changed since last look."""
    root = Path(store.setting("root")).expanduser()
    if not root.is_dir():
        return []
    claimed = {row[0] for row in store.conn.execute(
        "SELECT file FROM tracks WHERE file != ''")}
    seen = {row["path"]: (row["size"], row["mtime"]) for row in store.conn.execute(
        "SELECT path, size, mtime FROM art_files")}
    found = []
    for path in root.rglob("*.mp3"):
        if str(path) in claimed:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if seen.get(str(path)) == (stat.st_size, stat.st_mtime):
            continue
        found.append(path)
        if len(found) >= limit:
            break
    return found


def status() -> dict:
    with _lock:
        return dict(_state)


def sweep(store, progress=None) -> dict:
    """Check every row not looked at yet. Safe to call twice; work already done is skipped."""
    requeue_skipped(store)
    seen = {"slimmed": 0, "already": 0, "kept": 0, "no art": 0, "skipped": 0,
            "missing": 0, "failed": 0}
    while True:
        rows = candidates(store, 50)
        if not rows:
            break
        for track_id in rows:
            outcome = normalise(store, track_id)
            seen[outcome] = seen.get(outcome, 0) + 1
            with _lock:
                _state["done"] += 1
                _state["slimmed"] += outcome == "slimmed"
                _state["failed"] += outcome == "failed"
            if progress:
                progress(_state["done"], outcome)
    while True:
        # Files with no row: a collided re-download, a file dropped in by hand.
        loose = loose_files(store)
        if not loose:
            break
        for path in loose:
            outcome = normalise_file(store, path)
            seen[outcome] = seen.get(outcome, 0) + 1
            with _lock:
                _state["done"] += 1
                _state["slimmed"] += outcome == "slimmed"
                _state["failed"] += outcome == "failed"
            if progress:
                progress(_state["done"], outcome)
    return seen


def _loop(store) -> None:
    time.sleep(20)                                  # let the server answer first
    while True:
        try:
            with _lock:
                _state["running"] = True
            seen = sweep(store)
            with _lock:
                _state["running"] = False
                _state["error"] = ""
            if seen["slimmed"] or seen["kept"]:
                store.log(f"artwork: {seen['slimmed']} cover(s) deduplicated, "
                          f"{seen['kept']} filed from files left untouched, "
                          f"{seen['already']} already done, {seen['failed']} left alone",
                          "info", "artwork")
        except Exception as error:                  # a sweep must never kill the server
            with _lock:
                _state["running"] = False
                _state["error"] = str(error)
        time.sleep(SWEEP_EVERY)                     # catches files added by anything else


def start(store) -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, args=(store,), name="artwork", daemon=True)
        _thread.start()