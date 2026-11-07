"""Tempo analysis for the library — one background worker, no blocking requests.

Detection reads the whole file with ffmpeg (~0.5 s per track), so it can never
run inside a request. A single worker thread pulls ids from a bounded queue, and
the status dict is what the UI polls while it works.

A `manual` value is never overwritten: if the owner has halved, doubled or typed
a tempo, re-analysis skips that row. Only `''` (never analysed) and `auto` rows
are candidates.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from library.analysis import detect

MAX_BATCH = 500
RATE_PER_SECOND = 3.0           # politeness ceiling; detection is CPU-local

_lock = threading.Lock()
_queue: list[int] = []
_thread: threading.Thread | None = None
_state = {"running": False, "total": 0, "done": 0, "skipped": 0, "failed": 0,
          "current": "", "started": 0.0, "finished": 0.0, "error": ""}


def status() -> dict:
    with _lock:
        snapshot = dict(_state)
    snapshot["queued"] = len(_queue)
    if snapshot["running"] and snapshot["total"]:
        snapshot["percent"] = round(100 * snapshot["done"] / snapshot["total"], 1)
    else:
        snapshot["percent"] = 100.0 if snapshot["total"] and not snapshot["running"] else 0.0
    return snapshot


def candidates(store, limit: int = MAX_BATCH) -> list[int]:
    """Tracks with a real file and no manual tempo, newest work first."""
    rows = store.conn.execute(
        "SELECT id FROM tracks WHERE file != '' AND bpm_source != 'manual' "
        "ORDER BY bpm_at ASC, id ASC LIMIT ?", (limit,)
    ).fetchall()
    return [row[0] for row in rows]


def _run(store) -> None:
    global _thread
    try:
        while True:
            with _lock:
                if not _queue:
                    break
                track_id = _queue.pop(0)
                _state["current"] = ""
            row = store.conn.execute(
                "SELECT id, title, file, bpm_source FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
            if row is None or (row[3] == "manual"):
                with _lock:
                    _state["skipped"] += 1
                continue
            path = Path(row[2] or "")
            if not path.is_file():
                with _lock:
                    _state["failed"] += 1
                continue
            with _lock:
                _state["current"] = row[1] or f"track {track_id}"
            try:
                result = detect(path)
            except Exception:                      # a broken file must not kill the worker
                result = {"bpm": 0.0, "confidence": 0.0, "phase": 0.0}
            with store.lock:
                store.conn.execute(
                    "UPDATE tracks SET bpm = ?, bpm_conf = ?, bpm_phase = ?, bpm_source = ?, bpm_at = ? "
                    "WHERE id = ? AND bpm_source != 'manual'",
                    (result["bpm"], result["confidence"], result["phase"],
                     "auto" if result["bpm"] else "", time.time(), track_id),
                )
                store.conn.commit()
            with _lock:
                _state["done"] += 1
            time.sleep(1.0 / RATE_PER_SECOND)
    except Exception as error:                     # never leave the flag stuck on
        with _lock:
            _state["error"] = str(error)
    finally:
        with _lock:
            _state["running"] = False
            _state["finished"] = time.time()
            _state["current"] = ""
            _thread = None


def start(store, ids: list[int] | None = None, limit: int = MAX_BATCH) -> dict:
    """Queue ids (or every unanalysed track) for detection. Returns status."""
    global _thread
    chosen = [int(i) for i in (ids or []) if str(i).isdigit()][:MAX_BATCH] or candidates(store, limit)
    with _lock:
        if _state["running"]:
            return {"started": False, "reason": "already running", **status()}
        if not chosen:
            return {"started": False, "reason": "nothing to analyse", **status()}
        _queue[:] = chosen
        _state.update({"running": True, "total": len(chosen), "done": 0, "skipped": 0,
                       "failed": 0, "current": "", "started": time.time(),
                       "finished": 0.0, "error": ""})
        _thread = threading.Thread(target=_run, args=(store,), name="tempo", daemon=True)
        _thread.start()
    return {"started": True, "count": len(chosen), **status()}


def set_manual(store, track_id: int, bpm: float | None, action: str = "") -> dict:
    """Correct a tempo by hand: a number, 'double', 'halve', or None to clear."""
    with store.lock:
        row = store.conn.execute("SELECT bpm FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if row is None:
            return {"error": "no such track"}
        current = float(row[0] or 0)
        if action == "double":
            value = current * 2
        elif action == "halve":
            value = current / 2
        elif action == "clear":
            value = 0.0
        else:
            value = float(bpm or 0)
        if action in ("double", "halve"):
            # A doubled fast track (174 -> 348) belongs back in range, so fold it.
            while value and value > 300:
                value /= 2
            while value and value < 60:
                value *= 2
        elif value and not (20 <= value <= 400):
            # A typed number is taken at face value: refuse the absurd rather than
            # silently inventing a different tempo.
            return {"error": "tempo outside 20–400 BPM"}
        store.conn.execute(
            "UPDATE tracks SET bpm = ?, bpm_conf = 1, bpm_source = ?, bpm_at = ? WHERE id = ?",
            (round(value, 2), "manual" if value else "", time.time(), track_id),
        )
        store.conn.commit()
    return {"id": track_id, "bpm": round(value, 2)}
