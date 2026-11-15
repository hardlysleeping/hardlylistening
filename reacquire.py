"""Re-acquire the back catalogue from the origin that produced it.

Every track with a recorded origin is downloaded again in the stream the origin
actually serves (`best`: SoundCloud MP3 stays MP3, YouTube stays Opus). Nothing is
transcoded and nothing is deleted: `replacements` stages the file, verifies it
against the track's own length, moves it to its canonical library path, and keeps
the previous media for reversal.

The pass is resumable. A track counts as done when a completed replacement holds
the same URL as its origin, so stopping and starting again continues rather than
starting over.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import replacements
from library import store as library_store

CLASSES = ("native-opus", "mp3-only")
MAX_FAILURES = 20          # one provider outage should not burn the whole pass
PAUSE = 0.6                # polite between origins; SoundCloud rate-limits bursts


def audit_path(store=None) -> Path | None:
    """The newest catalogue audit; the report is the plan's source of truth."""
    base = Path(__file__).resolve().parent / "backups"
    reports = sorted(base.glob("opus-audit-*.json"))
    return reports[-1] if reports else None


def candidates(path: Path | None = None) -> list[dict]:
    path = path or audit_path()
    if path is None or not path.is_file():
        return []
    data = json.loads(path.read_text())
    found = []
    for row in data.get("tracks", []):
        found.append({
            "track_id": row.get("track_id"),
            "title": row.get("title") or "",
            "artist": row.get("artist") or "",
            "duration": float(row.get("duration") or 0),
            "origin": (row.get("origin") or "").strip(),
            "class": row.get("class") or "",
            "abr": float(row.get("abr") or 0),
        })
    return found


def done(store, track_id: int, origin: str) -> bool:
    """A completed swap from this same origin is this track's finished state."""
    with store.lock:
        row = store.conn.execute(
            "SELECT 1 FROM source_replacements WHERE track_id=? AND new_url=?"
            " AND status='completed' LIMIT 1", (track_id, origin)).fetchone()
    return row is not None


def plan(store, items: list[dict]) -> list[dict]:
    """What still needs re-acquiring, oldest row first.

    Pure enough to test: dead origins, missing files and tracks already swapped
    from the same origin are dropped here rather than during a download.
    """
    work = []
    for item in items:
        if item["class"] not in CLASSES:
            continue
        if not item["origin"].startswith(("http://", "https://")):
            continue
        track = store.track_by_id(item["track_id"])
        if not track or not (track.get("file") or "").strip():
            continue
        if not Path(track["file"]).is_file():
            continue
        if done(store, item["track_id"], item["origin"]):
            continue
        work.append(dict(item))
    return sorted(work, key=lambda item: item["track_id"])


def run(store, queue, work: list[dict], *, limit: int = 0, max_failures: int = MAX_FAILURES,
        progress=None, pause: float = PAUSE) -> dict:
    """Swap one track at a time; stop on a provider outage or a full disk."""
    summary = {"done": 0, "failed": 0, "skipped": 0, "stopped": ""}
    consecutive = 0
    items = work[:limit] if limit else work
    for item in items:
        stats = store.stats()
        if stats["disk_blocked"]:
            summary["stopped"] = (f"disk guard: {stats['disk_free_gb']} GB free, "
                                  f"{stats['disk_used_pct']}% used")
            break
        track_id, origin = item["track_id"], item["origin"]
        job = store.job_for_track(track_id)
        if not job:
            summary["skipped"] += 1
            continue
        try:
            replacements.stage(store, track_id, origin)
            staged = replacements.pending(store, job["id"])
            if not staged:
                raise ValueError("could not stage the replacement")
            replacements.run(queue, store.job(job["id"]), staged,
                             expected_seconds=item["duration"])
            with store.lock:
                result = store.conn.execute(
                    "SELECT status, error FROM source_replacements WHERE id=?",
                    (staged["id"],)).fetchone()
        except Exception as error:                    # noqa: BLE001 - one track, not the pass
            result = {"status": "failed", "error": str(error)}
        if result["status"] == "completed":
            summary["done"] += 1
            consecutive = 0
            store.log(f"re-acquired “{item['title'][:60]}” at source quality", "ok", "reacquire")
        else:
            summary["failed"] += 1
            consecutive += 1
            store.log(f"re-acquire failed for “{item['title'][:60]}”: "
                      f"{result['error'] or result['status']}", "warn", "reacquire")
        if progress:
            progress(item, result)
        if consecutive >= max_failures:
            summary["stopped"] = f"{consecutive} failures in a row — source looks down"
            break
        if pause:
            time.sleep(pause)
    return summary


def remaining(store, items: list[dict] | None = None) -> int:
    return len(plan(store, items if items is not None else candidates()))