"""Destructive actions: delete, restore, empty the trash, replace a file.

Every method here changes files on disk, so each one asks `_may_delete` first — ownership and
role — and then hands the work to `deletion`/`replacements`, which own the trash semantics. The
adapter never unlinks anything itself, and a refused delete is a status, not a silent no-op.
"""

from __future__ import annotations

from ..runtime import boot, security


class TrashActions:
    def do_track_move(self, tid: int) -> None:
        direction = (self.body().get("dir") or "").lower()
        if direction not in ("up", "down"):
            raise ValueError("dir must be up or down")
        store, queue = boot()
        job = store.job_for_track(tid)
        if not job:
            self.json({"error": "that track has no download job"}, 400)
            return
        moved = store.move_job(job["id"], -1 if direction == "up" else 1)
        queue.wake()
        self.json({"moved": moved})

    def _may_delete(self, store, kind: str, target_id: int) -> bool:
        """Local mode has no members, so it behaves like the admin it already is."""
        if not security().required:
            return True
        return security().may_delete(store, self.member, kind, target_id)

    def do_track_delete(self, tid: int) -> None:
        import deletion
        store, queue = boot()
        if security().required and not (self.member and self.member['role'] == 'admin'):
            self.json({"error": "Administrator required"}, 403)
            return
        try:
            report = deletion.delete_track(
                store, tid, delete_files=self.body().get("delete_file") is not False,
                actor=self.member)
        except ValueError as error:
            self.json({"error": str(error)}, 404)
            return
        queue.wake()
        self.json(report)

    def do_trash_empty(self) -> None:
        """Throw the trash away. Admin-only, because it is the end of the undo."""
        import deletion
        store, _queue = boot()
        if security().required and not (self.member and self.member['role'] == 'admin'):
            self.json({"error": "Administrator required"}, 403)
            return
        self.json(deletion.empty_trash(store))

    def do_replacements_collect(self) -> None:
        """Collect previous media whose window has passed. Admin-only, CSRF-checked.

        The same collection the boot thread runs, offered as a button: an administrator who
        has just changed the window should not have to wait six hours to see it happen.
        """
        import replacements
        store, _queue = boot()
        security().csrf(self)
        if security().required and not (self.member and self.member['role'] == 'admin'):
            self.json({"error": "Administrator required"}, 403)
            return
        self.json(replacements.collect(store))

    def do_trash_restore(self, tid: int) -> None:
        """Put one trashed file back where it came from, playlist and album deletes too."""
        import deletion
        store, _queue = boot()
        if security().required and not (self.member and self.member['role'] == 'admin'):
            self.json({"error": "Administrator required"}, 403)
            return
        try:
            self.json(deletion.restore_one(store, tid))
        except ValueError as error:
            self.json({"error": str(error)}, 404)

    def do_track_restore(self, tid: int) -> None:
        import deletion
        store, queue = boot()
        if security().required and not (self.member and self.member['role'] == 'admin'):
            self.json({"error": "Administrator required"}, 403)
            return
        try:
            report = deletion.restore_track(store, tid)
        except ValueError as error:
            self.json({"error": str(error)}, 404)
            return
        if report.get("queued"):
            queue.wake()
        self.json(report)

    def do_playlist_remove(self, pid: int) -> None:
        import deletion
        store, queue = boot()
        if not self._may_delete(store, "playlist", pid):
            self.json({"error": "Only the person who added this playlist, or an "
                                "administrator, may delete it"}, 403)
            return
        body = self.body()
        delete_files = body.get("delete_files") is True
        if body.get("dry"):
            report = deletion.plan(store, "playlist", pid)
            report.pop("_rows", None)
            self.json(report)
            return
        report = deletion.apply(store, "playlist", pid, delete_files=delete_files,
                                actor=self.member)
        queue.wake()
        self.json(report)

    def do_release_remove(self, rid: int) -> None:
        import deletion
        store, queue = boot()
        if not self._may_delete(store, "release", rid):
            self.json({"error": "Only the person who added this album, or an "
                                "administrator, may delete it"}, 403)
            return
        body = self.body()
        delete_files = body.get("delete_files") is True
        if body.get("dry"):
            report = deletion.plan(store, "release", rid)
            report.pop("_rows", None)
            self.json(report)
            return
        report = deletion.apply(store, "release", rid, delete_files=delete_files,
                                actor=self.member)
        queue.wake()
        self.json(report)

    def do_playlist_requeue(self, pid: int) -> None:
        store, queue = boot()
        rows = store.conn.execute(
            "SELECT t.id, t.provider, t.source_url FROM tracks t"
            " JOIN playlist_tracks pt ON pt.track_id=t.id WHERE pt.playlist_id=?",
            (pid,)).fetchall()
        requeued = 0
        for row in rows:
            job = store.job_for_track(row["id"])
            if job and job["status"] in ("failed", "unavailable", "cancelled"):
                with store.lock:
                    store.conn.execute("DELETE FROM download_jobs WHERE id=?",
                                       (job["id"],))
                    store.conn.commit()
                if store.enqueue(row["id"], row["provider"], row["source_url"]):
                    requeued += 1
        queue.wake()
        self.json({"requeued": requeued})
