"""Queue and acquisition actions: pause, retry, rank, skip, unpick, source.

These are the writes that make the server *do work* — reach a provider, run yt-dlp, take a
queue slot — and `boundary()` gates each of them on an acquire grant before this module runs.
The adapters keep two rules: a refusal is a status (`Denied` / `ValueError`), and a manual
choice is recorded (`ranking`) rather than applied to a private copy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..runtime import boot


class QueueActions:
    def do_pause(self) -> None:
        _, queue = boot()
        queue.pause(bool(self.body().get("on")))
        self.json({"ok": True, "paused": queue.paused})

    def do_clear_cooldown(self) -> None:
        store, queue = boot()
        with store.lock:
            store.conn.execute(
                "UPDATE provider_state SET cooldown_until=0, last_error=''")
            store.conn.commit()
        store.log("rate-limit cooldown cleared by hand", "warn")
        queue.wake()
        self.json({"ok": True})

    def do_clear_completed(self) -> None:
        store, queue = boot()
        with store.lock:
            rows = store.conn.execute(
                "SELECT id FROM download_jobs WHERE status IN ('completed','cancelled')"
            ).fetchall()
            for row in rows:
                store.conn.execute("DELETE FROM download_jobs WHERE id=?", (row["id"],))
            store.conn.commit()
        store.log(f"cleared {len(rows)} finished jobs", "warn")
        queue.wake()
        self.json({"cleared": len(rows)})

    def do_retry_failed(self) -> None:
        store, queue = boot()
        with store.lock:
            rows = [dict(r) for r in store.conn.execute(
                "SELECT * FROM download_jobs WHERE status IN ('failed','unavailable')")]
            for row in rows:
                store.conn.execute("DELETE FROM download_jobs WHERE id=?", (row["id"],))
            store.conn.commit()
        for row in rows:
            store.enqueue(row["track_id"], row["provider"], row["source_url"])
        store.log(f"re-queued {len(rows)} failed tracks", "warn")
        queue.wake()
        self.json({"requeued": len(rows)})

    def do_ranking(self) -> None:
        """What the ranking has learned, and from which picks."""
        import ranking
        store, _ = boot()
        picks = ranking.history(store)
        self.json({"picks": len(picks),
                   "recent": [{"query": r["query"], "uploader": r["uploader"],
                               "title": r["candidate_title"],
                               "version_match": bool(r["version_match"])}
                              for r in picks[:50]]})

    def do_queue_auto(self) -> None:
        """Apply the >=90% rule to rows already parked for review.

        Stored candidates were scored by an older formula, so they are re-scored
        against the current one first — otherwise "strong match" would mean the
        number the old ranking produced, which ranked a different remix above the
        requested version.
        """
        from engine.queue import AUTO_ACCEPT
        import ranking
        store, queue = boot()
        with store.lock:
            rows = [dict(r) for r in store.conn.execute(
                "SELECT * FROM download_jobs WHERE status='review'").fetchall()]
        applied = 0
        for job in rows:
            try:
                pool = json.loads(job.get("candidates") or "[]")
            except ValueError:
                continue
            track = store.track_by_id(job["track_id"]) or {}
            query = " ".join(x for x in (track.get("artist_name"), track.get("title")) if x)
            if query and pool:
                pool = ranking.rank(store, query, pool)
                store.update_job(job["id"], candidates=json.dumps(pool),
                                 match=max(c["match"] for c in pool))
            best = max(pool, key=lambda c: c.get("match") or 0, default=None)
            if not best or not best.get("url") or (best.get("match") or 0) < AUTO_ACCEPT:
                continue
            store.update_job(job["id"], resolved_url=best["url"], pinned=1, tried="",
                             status="queued", attempts=0, next_retry_at=0,
                             error_message="", error_type="", match=best["match"],
                             resolved_title=f"{best.get('artist', '')} — {best.get('title', '')}")
            applied += 1
        queue.wake()
        self.json({"ok": True, "applied": applied, "considered": len(rows)})

    def do_track_retry(self, tid: int) -> None:
        store, queue = boot()
        job = store.job_for_track(tid)
        queued = False
        if job and job["status"] in ("failed", "unavailable", "cancelled", "completed"):
            with store.lock:
                store.conn.execute("DELETE FROM download_jobs WHERE id=?", (job["id"],))
                store.conn.commit()
            queued = bool(store.enqueue(tid, job["provider"], job["source_url"]))
            queue.wake()
        # The UI shows "Back in the queue" or "Nothing to retry" from this, instead
        # of a button that appears to do nothing when the job was not eligible.
        self.json({"ok": True, "queued": queued,
                   "max_attempts": (job or {}).get("max_attempts", 4)})

    def do_track_skip(self, tid: int) -> None:
        store, queue = boot()
        job = store.job_for_track(tid)
        if job and job["status"] != "completed":
            import replacements
            with store.lock:
                replacement = replacements.pending(store, job["id"])
                if replacement:
                    if not queue.cancel(job["id"]):
                        replacements.terminate(store, replacement, "cancelled", "Replacement cancelled; original retained")
                    self.json({"ok": True})
                    return
            store.update_job(job["id"], status="cancelled",
                             error_message="skipped by hand")
            # If it is the job in flight, kill yt-dlp now — otherwise the worker
            # would finish the download and flip the row to completed.
            queue.cancel(job["id"])
        queue.wake()
        self.json({"ok": True})

    def do_track_unpick(self, tid: int) -> None:
        """Undo a hand-picked source: forget it taught the ranking, drop the file
        it produced (when asked), and put the track back in review."""
        body = self.body() or {}
        store, queue = boot()
        job = store.job_for_track(tid)
        if not job:
            self.json({"error": "that track has no download job"}, 400)
            return
        if job.get("status") in ("resolving", "downloading"):
            self.json({"error": "stop the active job first"}, 409)
            return
        track = store.track_by_id(tid) or {}
        url = job.get("resolved_url") or ""
        picked_url = url or (job.get("source_url") or "")
        import ranking
        forgot = ranking.forget(store, ranking.query_for(track), picked_url) if picked_url else False
        removed = ""
        if body.get("delete_file") and track.get("file"):
            import deletion
            path = track["file"]
            if deletion._inside(Path(path), deletion._root(store)) and Path(path).is_file():
                Path(path).unlink()
                removed = path
                store.conn.execute("UPDATE tracks SET file='', size=0 WHERE id=?", (tid,))
                store.conn.commit()
        # Back to review with the pool it already had, so the choice is visible
        # again instead of the track silently staying attached to a wrong upload.
        store.update_job(job["id"], resolved_url="", pinned=0, tried="", status="review",
                         attempts=0, next_retry_at=0, error_type="", error_message="",
                         progress=0, bytes=0, speed=0, eta=0)
        queue.wake()
        self.json({"ok": True, "forgot_pick": bool(forgot), "removed_file": removed,
                   "status": "review"})

    def do_track_source(self, tid: int) -> None:
        url = (self.body().get("url") or "").strip()
        if url and not re.match(r"^https?://", url, re.I):
            raise ValueError("that is not a URL")
        store, queue = boot()
        job = store.job_for_track(tid)
        # Choosing a candidate is the strongest signal there is: record it so the
        # ranking can prefer what this person actually picks.
        if url and job:
            import ranking
            track = store.track_by_id(tid) or {}
            try:
                pool = json.loads(job.get("candidates") or "[]")
            except ValueError:
                pool = []
            picked = next((c for c in pool if c.get("url") == url), None)
            if not picked:
                # A pasted URL is a deliberate choice even when the search never
                # offered it: ask what it is, so the ranking learns from the paste
                # too (and undo can forget it).
                picked = ranking.metadata_for(url) or None
            if picked:
                ranking.record(store, track, picked)
        if not job:
            self.json({"error": "that track has no download job"}, 400)
            return
        track = store.track_by_id(tid)
        if track and track.get('file') and Path(track['file']).is_file():
            import replacements
            try:
                replacements.stage(store, tid, url)
            except ValueError as exc:
                self.json({'error': str(exc)}, 409)
                return
            queue.wake()
            self.json({'ok': True, 'replacement': 'queued'})
            return
        if job['status'] in ('resolving', 'downloading'):
            self.json({'error': 'Stop the active job before changing its source'}, 409)
            return
        if url:
            store.update_job(job["id"], resolved_url=url, pinned=1, tried="",
                             status="queued", attempts=0, next_retry_at=0,
                             error_type="", error_message="")
        else:
            store.update_job(job["id"], resolved_url="", pinned=0, tried="",
                             status="queued", attempts=0, next_retry_at=0,
                             error_type="", error_message="")
        queue.wake()
        self.json({"ok": True})
