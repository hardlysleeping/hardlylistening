"""The download queue.

One worker, one pace. Persisted in `download_jobs`, so "what is happening" is a
database fact rather than a variable in a process that might die.

Three sources of failure are treated differently, on purpose:

  retryable   network, 5xx, ffmpeg hiccup  -> backoff, same source
  rate        HTTP 429 / bot wall          -> pause the PROVIDER, keep the job
  unusable    DRM, geo, "video unavailable" -> another source, no attempt burned
  permanent   deleted, unsupported URL     -> fail, do not churn
"""

from __future__ import annotations

import json

import random
import threading
import time
from pathlib import Path

import replacements
from db import Store
from downloaders import ai_match, ytdlp
import ranking
from library import store as library_store
from providers import base as provider_base

# 30 s / 2 m / 10 m / 1 h — a source that is merely flaky recovers quickly, and
# a source that is down does not get hammered for the rest of the day.
BACKOFF = (30, 120, 600, 3600)
PACING_DEFAULT = 5.0
COOLDOWN_DEFAULT = 900


# A near-exact metadata match is not worth a human click. Above this, the best
# candidate is taken and the download starts even if the judging model said no.
AUTO_ACCEPT = 0.90
MIN_ACCEPT = 0.45          # same floor ytdlp.search_candidates applies by default


class Queue:
    def __init__(self, store: Store):
        self.store = store
        self.paused = False
        self.current: dict | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._disk_log = 0.0
        self._cancel = threading.Event()      # set to kill the running download
        self._active_job = 0
        # The back-catalogue pass runs on this worker when nothing else is queued,
        # so the UI stays up and nothing races it for the same job.
        self._reacquire_work: list[dict] | None = None
        self._reacquire_failures = 0
        self._reacquire_lock = threading.Lock()
        self._reacquire_started = False
        self._reacquire_stopped = False
        # A swap is staged as a queued job for a moment; this lock keeps the
        # normal worker from picking it up in that window.
        self._pick_lock = threading.Lock()
        self.thread: threading.Thread | None = None
        import replacements
        replacements.recover(store)
        # An interrupted run leaves a job that claims to be downloading.
        with store.lock:
            store.conn.execute(
                "UPDATE download_jobs SET status='queued' WHERE status IN"
                " ('downloading','resolving','verifying')")
            store.conn.commit()

    # -- lifecycle --
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="queue", daemon=True)
        self.thread.start()

    def stop(self, grace: float = 5.0) -> None:
        """Stop the worker, waiting up to `grace` seconds for the current job.

        A download in flight is not abandoned halfway: if it is still running when the grace
        expires the child is killed and its partial file removed, and the job goes back to
        `queued` (see `_run`) — a restart resumes it instead of silently cancelling someone's
        track. That is the same rule the constructor applies to rows left by a hard kill.
        """
        self._stop.set()
        self._wake.set()
        thread = self.thread
        if not (thread and thread.is_alive()):
            return
        thread.join(grace)
        if thread.is_alive():
            self._cancel.set()
            thread.join(10)

    def disk_blocked(self) -> bool:
        return self.store.stats()["disk_blocked"]

    def cancel(self, job_id: int) -> bool:
        """Stop the download for `job_id` right now, if it is the one running.

        Killing the child is the only way "skip" can mean what it says; marking
        the row and letting yt-dlp finish would let the worker overwrite the
        decision a minute later.
        """
        if job_id and job_id == self._active_job:
            self._cancel.set()
            return True
        return False

    def pause(self, on: bool) -> None:
        self.paused = bool(on)
        self.store.log("queue paused" if on else "queue resumed", "warn")
        if not on:
            self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    # -- ingest: a URL becomes a playlist plus jobs --
    def ingest(self, url: str) -> dict:
        url = (url or "").strip()
        import re
        if not re.match(r"^https?://", url, re.I):
            raise ValueError("that is not a URL")
        existing = self.store.conn.execute(
            "SELECT id,title FROM playlists WHERE source_url=?", (url,)).fetchone()
        if existing:
            return {"playlist_id": existing["id"], "title": existing["title"],
                    "existing": True}

        provider = provider_base.for_url(url)
        self.store.log(f"reading {provider.name} link…")
        release = provider.resolve(url)
        if not release.tracks:
            raise RuntimeError("no tracks found in that link")

        artist_id = self.store.artist(release.provider, release.artist_id or release.artist,
                                      release.artist, release.artist_url)
        release_row = self.store.release(
            release.provider, release.release_id, release.title, artist_id=artist_id,
            release_type=release.release_type, track_count=release.track_count,
            release_date=release.release_date, artwork=release.artwork,
            source_url=release.source_url)
        playlist_id = self.store.playlist(
            release.title, release.provider, url,
            kind="url", artwork=release.artwork)

        queued = 0
        reused = 0
        for position, track in enumerate(release.tracks, start=1):
            track_id = self.store.track(
                track.provider, track.provider_track_id, track.title,
                artist_name=track.artist, album=track.album or "",
                track_number=track.track_number or position,
                # One cover per release: a provider that names the release but not each
                # track still fills the album, the player and the car.
                duration=track.duration, artwork=track.artwork or release.artwork,
                source_url=track.source_url, release_id=release_row,
                artist_id=artist_id, isrc=track.isrc)
            row = self.store.track_by_id(track_id)
            already = bool(row and row["file"] and Path(row["file"]).is_file())
            # Membership first, always: a song we already have is still part of
            # this playlist — it links the existing file instead of copying it.
            self.store.add_to_playlist(playlist_id, track_id, position, reused=already)
            if already:
                reused += 1
                continue            # already on disk: membership only, no second copy
            if self.store.enqueue(track_id, track.provider, track.source_url):
                queued += 1
        self.store.log(
            f"queued {queued} of {len(release.tracks)} tracks"
            + (f", {reused} already in the library" if reused else "")
            + f" from “{release.title}”", "ok", f"playlist:{playlist_id}")
        self.wake()
        return {"playlist_id": playlist_id, "title": release.title,
                "tracks": len(release.tracks), "queued": queued, "reused": reused}

    # -- per-job work --
    def _collection(self, track: dict) -> str:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT p.title FROM playlist_tracks pt JOIN playlists p"
                " ON p.id = pt.playlist_id WHERE pt.track_id=?"
                " ORDER BY p.position LIMIT 1", (track["id"],)).fetchone()
        return row["title"] if row else ""

    def _resolve_source(self, job: dict, track: dict) -> str | None:
        approval = (provider_base.needs_metadata_approval(track["provider"])
                    and self.store.setting("ai_apple_enabled", "1") == "1"
                    and not job.get("pinned"))
        # Explicit human choices bypass judging; legacy auto-resolved Apple URLs
        # must be searched/judged again rather than silently grandfathered in.
        if job["resolved_url"] and not approval:
            return job["resolved_url"]
        if job["source_url"] and not approval:
            return job["source_url"]
        # The title on our side and the title on the uploader's side are not always
        # the same words, so one query is a coin flip. Try the identified variants
        # in order and stop as soon as something is unmistakably right.
        query = ranking.query_for(track) or " ".join(
            x for x in (track["artist_name"], track["title"]) if x)
        variants = ytdlp.query_variants(track) or [query]
        tried = [u for u in (job["tried"] or "").split("\n") if u]
        self.store.update_job(job["id"], status="resolving")
        self.store.log(f"searching “{query}”", "info", f"job:{job['id']}")

        def gather(wide: bool) -> list[dict]:
            """Every variant, merged, stopping once nothing better is plausible."""
            pool: list[dict] = []
            seen: set[str] = set()
            for variant in variants:
                # A normal search keeps the engine's own trust and floor; only the
                # widen pass asks for everything and decides afterwards.
                extra = {"sc_trust": 1.1, "min_accept": 0.0} if wide else {}
                got = ytdlp.search_candidates(variant, exclude=tried, **extra)
                for hit in got:
                    if hit["url"] not in seen:
                        seen.add(hit["url"])
                        pool.append(hit)
                if any((h.get("match") or 0) >= AUTO_ACCEPT for h in pool):
                    break
            return ranking.rank(self.store, query, pool)

        # A query with a recorded pick has to be searched wider than the floor,
        # because the upload he wants may sit under it; ranking then lifts it to
        # the top. Everything else keeps the ordinary floor.
        learned = ranking.has_history(self.store, query)
        hits = gather(wide=learned)
        if learned:
            hits = [h for h in hits if h["match"] >= MIN_ACCEPT or h["match"] >= ranking.EXACT]
        if not hits:
            # Nothing cleared the confidence floor. Failing here means the track
            # dies with advice nobody can act on; instead park it for a human with
            # the best of what the search did return, so one click fixes it.
            pool = gather(wide=True)[:5]
            strong = max(pool, key=lambda c: c.get("match") or 0, default=None)
            if strong and (strong.get("match") or 0) >= AUTO_ACCEPT:
                # The floor is 45%, but a 90%+ hit is not a judgement call.
                self.store.update_job(
                    job["id"], resolved_url=strong["url"], match=strong["match"],
                    error_type="", error_message="",
                    resolved_title=f"{strong.get('artist', '')} — {strong.get('title', '')}")
                self.store.log(
                    f"auto-picked {int(strong['match'] * 100)}% match for “{track['title']}”",
                    "ok", f"job:{job['id']}")
                job = dict(job, resolved_url=strong["url"], match=strong["match"])
                return strong["url"]
            if approval:
                import decisions
                decisions.record(self.store, job, track, None, pool,
                                 'No candidate cleared the search floor', 'review')
            self.store.update_job(
                job["id"], status="review", resolved_url="", error_type="review",
                match=pool[0]["match"] if pool else 0,
                candidates=json.dumps(pool),
                error_message=("only weak matches — pick one, or paste a URL"
                               if pool else "no results at all — paste a URL"))
            self.store.log(
                f"needs review: “{track['title']}”"
                f"{f' (best {int(pool[0]['match'] * 100)}%)' if pool else ''}",
                "warn", f"job:{job['id']}")
            return None
        best = hits[0]
        if approval and best["match"] >= AUTO_ACCEPT:
            # Do not spend a model call second-guessing a 94% metadata match.
            import decisions
            decisions.record(self.store, job, track, best, hits[:6],
                             f"strong metadata match {int(best['match'] * 100)}%", 'selected')
            self.store.update_job(
                job["id"], resolved_url=best["url"], match=best["match"],
                resolved_title=f"{best['artist']} — {best['title']}")
            self.store.log(
                f"auto-picked {int(best['match'] * 100)}% match for “{track['title']}”",
                "ok", f"job:{job['id']}")
            job = dict(job, resolved_url=best["url"], match=best["match"])
            return best["url"]
        if approval:
            # Judge a bounded ranked pool, not just the first search result.
            # A rejection is evidence against that URL, not against the song.
            considered = []
            remaining = list(hits[:6])
            expanded = False
            accepted = False
            # The listener's history does not change between candidates of one track;
            # scanning the audit tables again for each judgment only cost time.
            examples = ranking.taste(self.store, track)
            rejects = ranking.rejected(self.store, track)
            trusted = ranking.trusted_uploaders(self.store, track)
            while remaining and len(considered) < 6:
                candidate = remaining.pop(0)
                if candidate['url'] in {h['url'] for h in considered}:
                    continue
                considered.append(candidate)
                judgment = ai_match.judge(track, candidate,
                    self.store.setting("ai_model", ai_match.DEFAULT_MODEL),
                    base_url=self.store.setting("ai_base_url", "https://openrouter.ai/api/v1"),
                    examples=examples, rejected=rejects, trusted=trusted)
                considered[-1] = dict(candidate, metadata_decision=judgment.decision,
                                      metadata_reason=judgment.reason)
                if judgment.decision == "accept":
                    best, accepted = candidate, True
                    break
                if not remaining and not expanded and any(h.get('engine') == 'scsearch' for h in considered):
                    expanded = True
                    remaining = ytdlp.search_candidates(query, exclude=tried,
                                                       sc_trust=1.1)[:6]
                    remaining = [h for h in remaining if h['url'] not in {c['url'] for c in considered}]
            import decisions
            evidence = considered + [h for h in hits if h['url'] not in {c['url'] for c in considered}]
            decisions.record(self.store, job, track, best if accepted else None,
                             evidence[:6], judgment.reason, 'selected' if accepted else 'review')
            if not accepted:
                # The judge can be wrong; a 90%+ metadata match outranks it.
                strong = max((c for c in evidence if (c.get("match") or 0) >= AUTO_ACCEPT),
                             key=lambda c: c["match"], default=None)
                if strong:
                    self.store.update_job(
                        job["id"], resolved_url=strong["url"], match=strong["match"],
                        error_type="", error_message="",
                        resolved_title=f"{strong.get('artist', '')} — {strong.get('title', '')}")
                    self.store.log(
                        f"auto-picked {int(strong['match'] * 100)}% match for “{track['title']}”"
                        f" over the judge's reject", "ok", f"job:{job['id']}")
                    job = dict(job, resolved_url=strong["url"], match=strong["match"])
                    return strong["url"]
            if not accepted:
                self.store.update_job(
                    job["id"], status="review", resolved_url="", error_type="review",
                    match=best["match"], candidates=json.dumps(evidence[:5]),
                    error_message=judgment.reason + " — pick a source or paste a URL")
                return None
        self.store.update_job(
            job["id"], resolved_url=best["url"], match=best["match"],
            resolved_title=f"{best['artist']} — {best['title']}")
        job = dict(job, resolved_url=best["url"], match=best["match"])
        return best["url"]

    def _run(self, job: dict) -> None:
        import replacements
        replacement = replacements.pending(self.store, job["id"])
        if replacement:
            replacements.run(self, job, replacement)
            return
        track = self.store.track_by_id(job["track_id"])
        if not track:
            self.store.update_job(job["id"], status="failed",
                                  error_message="track row is gone")
            return
        # Storage allowance: a member's queued downloads stop once their bytes are
        # spent. Checked here, before the source is resolved, so a capped member
        # cannot keep pulling audio through the queue.
        from library import quota
        member_id = quota.owners(self.store).get(track["id"])
        if member_id:
            with self.store.lock:
                found = self.store.conn.execute(
                    "SELECT role FROM members WHERE id=?", (member_id,)).fetchone()
            role = found["role"] if found else "member"
            blocked = quota.refuse(self.store, {"id": member_id, "role": role})
            if blocked:
                self.store.update_job(job["id"], status="failed", error_type="quota",
                                      error_message=blocked)
                self.store.log(f"storage allowance: “{track['title']}” not downloaded",
                               "warn", f"job:{job['id']}")
                return
        # A track the source no longer lists is only worth having if it is already
        # on disk. Retrying it forever would burn the queue against a dead id.
        if (track.get("source_status") in ("gone", "missing") and not track["file"]
                and not job.get("pinned")):
            self.store.update_job(
                job["id"], status="unavailable",
                error_message="no longer listed by the source — kept only if already downloaded")
            return
        # SQLite membership plus a verified file is authoritative. A yt-dlp
        # archive can claim success after a user deletes the actual media.
        if track["file"]:
            existing = Path(track["file"])
            # Expected length included: a 30 s preview that was accepted before the
            # guard existed is re-downloaded instead of reported as completed.
            ok, _ = ytdlp.verify_audio(existing, expected_seconds=track.get("duration") or 0)
            if ok:
                self._finish(job, "completed", "", "", size=existing.stat().st_size)
                return
            with self.store.lock:
                self.store.conn.execute("UPDATE tracks SET file='', size=0 WHERE id=?",
                                        (track["id"],))
                self.store.conn.commit()
        # The same recording may already be on disk under another row (the song sits
        # in a playlist followed for two providers, or the link was added twice).
        # Link that file instead of fetching a second identical copy.
        twin = self.store.downloaded_twin(track)
        if twin:
            shared = Path(twin["file"])
            ok, _ = ytdlp.verify_audio(shared, expected_seconds=track.get("duration") or 0)
            if ok:
                with self.store.lock:
                    self.store.conn.execute(
                        "UPDATE tracks SET file=?, size=? WHERE id=?",
                        (str(shared), shared.stat().st_size, track["id"]))
                    self.store.conn.commit()
                self.store.fill_release_artwork(track["id"])
                self._finish(job, "completed", "", "", size=shared.stat().st_size)
                self.store.log(
                    f"“{track['title']}” is already in the library — linked, not downloaded",
                    "ok", f"job:{job['id']}")
                return
        url = self._resolve_source(job, track)
        if not url:
            return

        root = Path(self.store.setting("root")).expanduser()
        fmt = self.store.setting("audio_format", "best")
        collection = self._collection(track)
        # "best" keeps whatever container the origin serves (mp3, opus, webm), so
        # the planned suffix is a placeholder — the file is renamed by yt-dlp.
        extension = "mp3" if fmt == "best" else fmt
        target = library_store.path_for(
            root, artist=track["artist_name"], album=track["album"],
            playlist_title=collection, number=track["track_number"] or 0,
            title=track["title"], extension=extension)
        taken = {str(Path(r["file"])) for r in self.store.conn.execute(
            "SELECT file FROM tracks WHERE file<>''").fetchall()}
        target = (library_store.unique_any_suffix(target, taken) if fmt == "best"
                  else library_store.unique(target, taken))
        target.parent.mkdir(parents=True, exist_ok=True)

        settings = ytdlp.YtDlpSettings(
            audio_format=fmt,
            embed_thumb=self.store.setting("embed_thumb", "1") == "1",
            archive=None)  # file verification + SQLite replace stale archive skips
        outtmpl = str(target.with_suffix(".%(ext)s"))
        cmd = ytdlp.build_command(url, outtmpl, settings)

        self.store.update_job(job["id"], status="downloading", started=time.time(),
                              progress=0, bytes=0, speed=0, eta=0)
        self.current = {"job_id": job["id"], "track_id": track["id"],
                        "phase": "downloading", "title": track["title"],
                        "artist": track["artist_name"], "started": time.time(),
                        "progress": 0, "speed": 0, "eta": 0}
        self.store.log(f"downloading “{track['title']}”", "info", f"job:{job['id']}")

        self._cancel.clear()
        self._active_job = job["id"]
        last_write = [0.0]

        def on_progress(event: dict) -> None:
            # The UI polls every 1.5 s; writing every tick would be a DB write per
            # percent point for no visible gain.
            self.current.update({"progress": event.get("percent", 0),
                                 "speed": event.get("speed", 0),
                                 "eta": event.get("eta", 0)})
            now = time.time()
            if now - last_write[0] < 1.0:
                return
            last_write[0] = now
            self.store.update_job(job["id"], progress=event.get("percent") or 0,
                                  bytes=event.get("bytes") or 0,
                                  speed=event.get("speed") or 0,
                                  eta=event.get("eta") or 0)

        rc, output = ytdlp.run(cmd, on_progress=on_progress, cancel=self._cancel)
        cancelled = self._cancel.is_set()
        self._active_job = 0
        self._cancel.clear()
        self.current = None

        if cancelled:
            removed = ytdlp.discard_output(outtmpl, stop=root)   # .part, .ytdl, art
            if removed:
                self.store.log(f"removed {removed} partial file(s)", "warn",
                               f"job:{job['id']}")
            if self._stop.is_set():
                # Shutdown, not a decision: hand the job back so the next start resumes it.
                self.store.update_job(job["id"], status="queued", progress=0, speed=0,
                                      eta=0, error_message="")
                self.store.log(f"shutdown: “{track['title']}” returned to the queue",
                               "info", f"job:{job['id']}")
                return
            self.store.update_job(job["id"], status="cancelled", progress=0, speed=0,
                                  eta=0, finished=time.time(),
                                  error_message="cancelled by hand")
            self.store.log(f"cancelled “{track['title']}”", "warn", f"job:{job['id']}")
            return

        if rc == 0:
            written = ytdlp.find_output(outtmpl, fmt)
            # yt-dlp exiting 0 is not proof: ffprobe reads the file to decide. That read is
            # seconds of a large file, and while it ran the row still said "downloading
            # 100%", which is indistinguishable from a hung queue.
            self.store.update_job(job["id"], status="verifying", progress=100, speed=0,
                                  eta=0)
            self.current = {"job_id": job["id"], "track_id": track["id"],
                            "phase": "verifying", "title": track["title"],
                            "artist": track["artist_name"], "started": time.time(),
                            "progress": 100, "speed": 0, "eta": 0}
            ok, why = ytdlp.verify_audio(written, expected_seconds=track.get("duration") or 0)
            if not ok:
                # Exit 0 is not proof of a track: an empty or truncated file used
                # to be recorded as "completed" and then silently vanished.
                self.current = None
                ytdlp.discard_output(outtmpl, stop=root)   # not a track, not a file
                if "preview" in why and not job.get("pinned") and not job.get("source_url"):
                    # The match itself was a sample; another source may hold the
                    # whole track, so move on instead of retrying the same URL.
                    self._next_source(job, url, why)
                    return
                self._retry_or_fail(job, why, "retryable")
                return
            # The file is a track: from here on the row is finishing, not verifying.
            self.current = None
            self.store.conn.execute(
                "UPDATE tracks SET file=?, size=? WHERE id=?",
                (str(written), written.stat().st_size, track["id"]))
            self.store.conn.commit()
            # The row may have been queued before the release's cover was recorded,
            # so an empty artwork column is filled from the release now that the
            # track is on disk. A cover already on the row is never overwritten.
            self.store.fill_release_artwork(track["id"])
            # One copy of the cover in the store, a 640 px copy in the file. A
            # failure here must never fail the download; the sweep retries it.
            try:
                from library import art_store
                art_store.normalise(self.store, track["id"])
            except Exception:
                pass
            self._finish(job, "completed", "", "",
                         size=written.stat().st_size if written else 0)
            self.store.set_provider(job["provider"], last_success=time.time())
            # The file carries the uploader's genre; keep it as a label right away so the
            # library does not depend on the AI pass to have genres at all.
            try:
                import genres
                genres.save_from_file(self.store, track["id"], str(written))
            except Exception:
                pass
            return

        kind = ytdlp.classify(output)
        reason = ytdlp.error_line(output) or f"yt-dlp exited {rc}"
        if kind == "rate":
            self._trip_rate_limit(job, reason, output)
        elif kind == "unusable" and not job["pinned"] and not job["source_url"]:
            self._next_source(job, url, reason)
        else:
            self._retry_or_fail(job, reason, kind)

    def _trip_rate_limit(self, job: dict, reason: str, output: str) -> None:
        wait = ytdlp.retry_after(output) or self.store.setting_float(
            "cooldown", COOLDOWN_DEFAULT)
        state = self.store.provider(job["provider"])
        wait = max(wait, 60)
        deadline = time.time() + wait
        self.store.set_provider(
            job["provider"], cooldown_until=deadline,
            consecutive_limits=(state["consecutive_limits"] or 0) + 1,
            last_error=reason)
        # The track keeps its place and does NOT burn an attempt: the source said
        # "later", not "no".
        self.store.update_job(job["id"], status="queued", next_retry_at=deadline,
                              error_type="rate", error_message=reason)
        self.store.log(f"{job['provider']} rate limited — that source waits"
                       f" {int(wait // 60)} min ({reason})", "err", f"job:{job['id']}")

    def _next_source(self, job: dict, url: str, reason: str) -> None:
        tried = [u for u in (job["tried"] or "").split("\n") if u]
        if url and url not in tried:
            tried.append(url)
        self.store.update_job(job["id"], status="queued", resolved_url="",
                              tried="\n".join(tried), next_retry_at=0,
                              error_type="unusable", error_message=reason)
        self.store.log(f"source unusable, trying another: {reason}", "warn",
                       f"job:{job['id']}")

    def _retry_or_fail(self, job: dict, reason: str, kind: str) -> None:
        attempts = (job["attempts"] or 0) + 1
        limit = job["max_attempts"] or int(self.store.setting_float("max_attempts", 4))
        if kind == "permanent" or attempts >= limit:
            self._finish(job, "failed", reason, kind)
            return
        delay = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
        delay *= 1.0 + random.uniform(0, 0.15)          # jitter, up only
        self.store.update_job(job["id"], status="retry_wait", attempts=attempts,
                              next_retry_at=time.time() + delay,
                              error_type=kind, error_message=reason)
        self.store.log(f"retry in {int(delay // 60) or 1}m: {reason}", "warn",
                       f"job:{job['id']}")

    def _finish(self, job: dict, status: str, message: str, kind: str,
                size: int = 0) -> None:
        self.store.update_job(job["id"], status=status, finished=time.time(),
                              error_type=kind, error_message=message, bytes=size,
                              progress=100 if status == "completed" else 0,
                              speed=0, eta=0)
        if status == "completed":
            self.store.log("done", "ok", f"job:{job['id']}")
            # "Added to the library", attributed to whoever owns the track, for the
            # Feed's Activity tab.
            try:
                import activity
                from library import quota
                owner = quota.owners(self.store).get(job["track_id"])
                if owner:
                    activity.record(self.store, activity.KIND_ADD, member_id=owner,
                                    track_id=job["track_id"])
            except Exception:
                pass
            self._remember_artist_art(job["track_id"])
        # A download started from the Feed was meant for someone's library. The
        # file exists now, so it can join — a failed or cancelled track is dropped
        # instead of leaving a row that can never play.
        try:
            import listening
            if status == "completed":
                listening.deliver_track(self.store, job["track_id"])
            else:
                listening.forget_track(self.store, job["track_id"])
        except Exception:                           # noqa: BLE001
            self.store.log("could not update the library for this download",
                           "warn", f"job:{job['id']}")

    def _remember_artist_art(self, track_id: int) -> None:
        """One picture per artist, off the worker thread. A stored or manual picture
        means no request, so this never repeats for the same artist."""
        try:
            with self.store.lock:
                row = self.store.conn.execute(
                    "SELECT artist_name, provider, source_url FROM tracks WHERE id=?",
                    (track_id,)).fetchone()
            track = dict(row) if row else None
        except Exception:
            track = None
        if not track or not track.get("artist_name"):
            return
        def run():
            try:
                from library import artist_art
                url = ""
                if track["provider"] == "soundcloud":
                    from providers.soundcloud import _artist_url
                    url = _artist_url(track.get("source_url") or "")
                artist_art.fetch(self.store, track["artist_name"],
                                 track["provider"], url)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _start_reacquire(self) -> None:
        """Bring up the back-catalogue workers once; the queue stays responsive."""
        with self._reacquire_lock:
            if self._reacquire_started:
                return
            self._reacquire_started = True
        workers = max(1, int(self.store.setting_float("reacquire_workers", 3)))
        for number in range(workers):
            threading.Thread(target=self._reacquire_worker, name=f"reacquire-{number}",
                             daemon=True).start()

    def _reacquire_worker(self) -> None:
        import reacquire
        while not self._stop.is_set():
            if self._reacquire_stopped:
                return
            # A person waiting for a song outranks the back catalogue.
            if self.paused or self.store.next_job():
                time.sleep(2)
                continue
            if self.store.stats()["disk_blocked"]:
                time.sleep(15)
                continue
            if not self._reacquire_one():
                return

    def _reacquire_one(self) -> bool:
        """Pop and run one swap. False means the work list is exhausted.

        The list is built once per process from the audit report; `plan` drops
        whatever is already swapped, so a restart resumes instead of repeating.
        """
        import reacquire
        with self._reacquire_lock:
            if self._reacquire_work is None:
                self._reacquire_work = reacquire.plan(self.store, reacquire.candidates())
            item = self._reacquire_work.pop(0) if self._reacquire_work else None
        if item is None:
            return False
        if self._reacquire_failures >= reacquire.MAX_FAILURES:
            self._reacquire_stopped = True
            return False
        track = self.store.track_by_id(item["track_id"])
        if not track or not Path(track.get("file") or "").is_file():
            return True
        if reacquire.done(self.store, item["track_id"], item["origin"]):
            return True
        job = self.store.job_for_track(item["track_id"])
        if not job:
            return True
        try:
            with self._pick_lock:
                replacements.stage(self.store, item["track_id"], item["origin"])
                staged = replacements.pending(self.store, job["id"])
                if not staged:
                    raise ValueError("could not stage the replacement")
                # Out of the queued state before the lock is released, so the
                # normal worker cannot start the same track.
                self.store.update_job(job["id"], status="downloading")
            replacements.run(self, self.store.job(job["id"]), staged,
                             expected_seconds=item["duration"])
            with self.store.lock:
                status = self.store.conn.execute(
                    "SELECT status, error FROM source_replacements WHERE id=?",
                    (staged["id"],)).fetchone()
        except Exception as error:                     # noqa: BLE001 - one track
            self.store.log(f"re-acquire failed for “{item['title'][:60]}”: {error}",
                           "warn", "reacquire")
            self._reacquire_failed()
            return True
        if status["status"] == "completed":
            self._reacquire_failures = 0
            self.store.log(f"re-acquired “{item['title'][:60]}” at source quality",
                           "ok", "reacquire")
        else:
            self.store.log(f"re-acquire failed for “{item['title'][:60]}”: "
                           f"{status['error'] or status['status']}", "warn", "reacquire")
            self._reacquire_failed()
        time.sleep(reacquire.PAUSE)                    # polite between origins
        return True

    def _reacquire_failed(self) -> None:
        import reacquire
        with self._reacquire_lock:
            self._reacquire_failures += 1
            if self._reacquire_failures >= reacquire.MAX_FAILURES:
                self._reacquire_stopped = True
                self.store.log(
                    f"re-acquire paused after {self._reacquire_failures} failures in a row — "
                    "the source looks down; restart or resume later", "warn", "reacquire")

    # -- loop --
    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.paused:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            # A cooldown belongs to the provider that issued it. Waiting on the latest
            # one across all sources blocked the queue for a source nobody was asking
            # for, so the wait is now per provider and only when nothing else can run.
            blocked = self.store.cooldowns()
            with self._pick_lock:
                job = self.store.next_job(blocked=blocked)
            if not job and blocked:
                deadline, provider = self.store.blocking_cooldown()
                if deadline > time.time():
                    self.current = {"phase": "cooldown", "provider": provider,
                                    "remaining": round(deadline - time.time())}
                    time.sleep(min(2.0, deadline - time.time()))
                    continue
            if self.disk_blocked():
                # Refuse to start rather than die halfway through a file with
                # ENOSPC. Re-checked on every pass, so freeing space resumes it.
                stats = self.store.stats()
                self.current = {"phase": "disk",
                                "free_gb": stats["disk_free_gb"],
                                "floor_gb": stats["disk_floor_gb"]}
                if time.time() - self._disk_log > 300:
                    self._disk_log = time.time()
                    self.store.log(
                        f"only {stats['disk_free_gb']} GB free (floor "
                        f"{stats['disk_floor_gb']} GB) — queue is waiting for space",
                        "warn")
                self._wake.wait(15)
                self._wake.clear()
                continue
            if not job:
                self._start_reacquire()
                self.current = None
                self._wake.wait(1.5)
                self._wake.clear()
                continue
            self.store.update_job(job["id"], status="resolving")
            try:
                self._run(job)
            except Exception as exc:                     # noqa: BLE001
                self._retry_or_fail(job, f"internal error: {exc}", "retryable")
            pacing = self.store.setting_float("pacing", PACING_DEFAULT)
            if pacing > 0 and not self._stop.is_set():
                time.sleep(pacing)
