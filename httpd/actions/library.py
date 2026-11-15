"""Library actions: import, verify, repair, tempo/analysis, artist art.

Each method is an adapter over a module that owns the behaviour — `library.import_files`,
`library.source_health`, `library.metadata_repair`, `library.analysis` — and its job is to turn
a JSON body into that module's arguments and the result back into JSON. The exceptions are the
two upload paths, which decode base64 because a browser cannot hand a server a real path.

`do_members_update` is not here: members are their own domain (`actions/members.py`).
"""

from __future__ import annotations

import re
from pathlib import Path

from security import Denied
from ..constants import ARTIST_ART_DIR
from ..runtime import boot, security


class LibraryActions:
    def do_playlist_verify(self, pid: int) -> None:
        """Re-read a playlist's source and label tracks it no longer lists."""
        store, _ = boot()
        from library import source_health
        try:
            result = source_health.verify_playlist(store, pid)
        except ValueError as error:
            self.json({"error": str(error)}, 400)
            return
        except RuntimeError as error:
            self.json({"error": str(error)}, 502)
            return
        self.json(result)

    def do_library_grab(self) -> None:
        """Queue the tracks of an album/playlist that never made it to disk."""
        payload = self.body()
        raw = payload.get("ids") if isinstance(payload, dict) else None
        ids = [int(value) for value in (raw or []) if str(value).isdigit()][:500]
        store, queue = boot()
        queued = 0
        for track_id in ids:
            track = store.track_by_id(track_id)
            if not track:
                continue
            if track["file"] and Path(track["file"]).is_file():
                continue
            if store.enqueue(track_id, track["provider"], track["source_url"]):
                queued += 1
        if queue is not None:
            queue.wake()
        self.json({"queued": queued, "asked": len(ids)})

    def do_library_titles(self) -> None:
        """Preview or apply title repair for tracks imported with a numeric id."""
        payload = self.body() if self.command == "POST" else {}
        store, _ = boot()
        from library import metadata_repair
        if (payload or {}).get("apply"):
            self.json(metadata_repair.repair_async(store))
            return
        self.json(metadata_repair.preview(store))

    def _sonic_songs(self, rows) -> list:
        """Track ids decorated with what a row needs, in the order given."""
        store, _ = boot()
        wanted = [int(row["id"]) for row in rows]
        if not wanted:
            return []
        marks = ",".join("?" * len(wanted))
        with store.lock:
            found = {row["id"]: dict(row) for row in store.conn.execute(
                f"SELECT id,title,artist_name,album,duration,file FROM tracks "
                f"WHERE id IN ({marks})", wanted)}
        return [{"id": track_id, "title": found[track_id]["title"],
                 "artist": found[track_id]["artist_name"] or "",
                 "album": found[track_id]["album"] or "",
                 "duration": int(found[track_id]["duration"] or 0),
                 "score": row.get("score")}
                for row, track_id in ((row, int(row["id"])) for row in rows)
                if track_id in found]

    def do_sonic_analyse(self) -> None:
        """Fingerprint ids, or everything not analysed yet. Never inside the request."""
        payload = self.body()
        store, _ = boot()
        from library import sonic
        sonic.schema(store)
        ids = payload.get("ids") if isinstance(payload, dict) else None
        limit = int((payload or {}).get("limit") or 500) if isinstance(payload, dict) else 500
        force = bool((payload or {}).get("force")) if isinstance(payload, dict) else False
        self.json(sonic.start(store, ids, max(1, min(500, limit)), force=force))

    def do_library_tempo(self) -> None:
        """Detect tempo for ids, or for everything not yet analysed."""
        payload = self.body()
        store, _ = boot()
        from library import tempo
        ids = payload.get("ids") if isinstance(payload, dict) else None
        limit = int((payload or {}).get("limit") or 500) if isinstance(payload, dict) else 500
        result = tempo.start(store, ids, max(1, min(500, limit)))
        self.json(result)

    def do_track_bpm(self, track_id: str) -> None:
        """Manual tempo correction: a number, double/halve, or clear."""
        payload = self.body() if self.command == "POST" else {}
        store, _ = boot()
        from library import tempo
        action = str((payload or {}).get("action") or "")
        raw = (payload or {}).get("bpm")
        try:
            value = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError("bpm must be a number")
        result = tempo.set_manual(store, int(track_id), value, action)
        if result.get("error"):
            self.json(result, 400)
            return
        self.json(result)

    def do_track_publish(self, track_id: str) -> None:
        """Publish one stored track to the Funkwhale instance this server is configured for.

        A person's explicit action and nothing else — nothing here is automatic, and the
        credential is the server's own environment, which is why the generic `/api/` policy
        makes this an administrator route. The adapter (`library/funkwhale.py`) owns the only
        honest definition of success: a finished upload *with* a track id. An accepted upload
        that never finishes is a failure with a reason, and the reason is what is answered.
        """
        security().csrf(self)
        store, _ = boot()
        from library import funkwhale
        if not funkwhale.instance():
            self.json({"error": "No Funkwhale instance is configured (%s is unset)"
                                % funkwhale.INSTANCE_ENV}, 400)
            return
        if not funkwhale.token():
            self.json({"error": "No Funkwhale token is configured (%s is unset)"
                                % funkwhale.TOKEN_ENV}, 400)
            return
        track = store.track_by_id(int(track_id))
        if not track:
            self.json({"error": "No such track"}, 404)
            return
        source = Path(str(track.get("file") or ""))
        if not source.is_file():
            self.json({"error": "that track has no stored file to publish"}, 400)
            return
        if getattr(self, "member", None):
            security().limit(self.member["id"], "publish", 5)
        from library import publishing
        # Off the request thread: the adapter waits on another server for up to a minute, and
        # a browser gives up on a request long before that honest answer. The page polls
        # `GET /api/track/<id>/publish` for the outcome.
        self.json({**publishing.start(track["id"], source),
                   "title": track.get("title") or "", "instance": funkwhale.instance()})

    def do_library_verify(self) -> None:
        """Ask the Apple catalog about the ids already in the library."""
        store, _ = boot()
        from library import source_health
        result = source_health.verify_catalog(store)
        if result.get("error"):
            self.json({"error": "catalog check failed: " + result["error"]}, 502)
            return
        self.json(result)

    def do_add(self) -> None:
        url = (self.body().get("url") or "").strip()
        urls = [line.strip() for line in url.splitlines() if line.strip()] if url else []
        if not urls:
            raise ValueError("paste a playlist link")
        _, queue = boot()
        from repo import acquisition as repo_acquisition
        out = []
        for one in urls:
            try:
                store, _ = boot()
                with store.lock:
                    existing = {r[0] for r in store.conn.execute('SELECT id FROM playlists')}
                    if self.member and self.member['role'] != 'admin':
                        prior = store.conn.execute('SELECT p.id, o.member_id FROM playlists p LEFT JOIN member_playlists o ON o.playlist_id=p.id WHERE p.source_url=?', (one,)).fetchone()
                        if prior and prior['member_id'] != self.member['id']:
                            out.append({'error': 'Only the playlist owner may refresh this playlist'})
                            continue
                    result = repo_acquisition.ingest(queue, one)
                    if self.member and result.get('playlist_id') and result['playlist_id'] not in existing:
                        store.conn.execute('INSERT OR IGNORE INTO member_playlists(playlist_id,member_id) VALUES(?,?)', (result['playlist_id'], self.member['id']))
                        store.conn.commit()
                        security().claim(store, 'playlist', result['playlist_id'], self.member['id'])
                out.append(result)
            except Exception as exc:                          # noqa: BLE001
                out.append({"url": one, "error": str(exc)})
        self.json({"added": out})

    def do_import_paths(self) -> None:
        raw = (self.body().get("paths") or "").strip()
        paths = [line.strip() for line in re.split(r"[\n;]+", raw) if line.strip()]
        if not paths:
            raise ValueError("give a file path")
        from library import import_rules
        if len(paths) > 1:
            # One song per import: a list of paths is how a folder dump sneaks in.
            raise ValueError(import_rules.one_audio_file())
        single = import_rules.check_path(paths[0])
        store, queue = boot()
        from library import quota
        blocked = quota.refuse(store, self.member, single.stat().st_size)
        if blocked:
            raise ValueError(blocked)
        from library import import_files
        result = import_files.import_paths(store, [str(single)],
                                           copy=bool(self.body().get("copy", True)))
        queue.wake()
        self.json(result)

    def do_import_upload(self) -> None:
        """One file, base64 in JSON — the browser cannot hand us a real path."""
        import base64
        import tempfile
        body = self.body()
        name = (body.get("name") or "upload").split("/")[-1]
        data = body.get("data") or ""
        if not data:
            raise ValueError("no file data")
        from library import import_rules
        import_rules.check_name(name)
        raw = base64.b64decode(data.split(",")[-1])
        import_rules.check_size(len(raw))
        store, queue = boot()
        from library import quota
        blocked = quota.refuse(store, self.member, len(raw))
        if blocked:
            raise ValueError(blocked)
        from library import import_files
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp) / name
            temp.write_bytes(raw)
            result = import_files.import_paths(store, [str(temp)], copy=True)
        self._claim_import(store, result)
        queue.wake()
        self.json(result)

    def do_artist_art(self) -> None:
        """Admin: set an artist's picture by hand. Manual art is never overwritten
        by the automatic fetch."""
        import base64
        import hashlib
        store, _ = boot()
        self.member = security().authenticate(self, store)
        security().admin(self.member)
        security().csrf(self)
        body = self.body()
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("artist name required")
        chosen = str(body.get("url") or "").strip()
        if chosen:
            if not chosen.startswith(("http://", "https://")):
                raise ValueError("invalid image url")
            store.set_artist_art(name, chosen, manual=True, provider="manual")
            self.json({"ok": True, "artwork": chosen})
            return
        data = str(body.get("data") or "")
        if not data:
            raise ValueError("artist name and image required")
        raw = base64.b64decode(data.split(",")[-1])
        if not raw:
            raise ValueError("empty image")
        if len(raw) > 8_000_000:
            raise ValueError("image too large (8 MB)")
        suffix = "png"
        if raw[:3] == b"\xff\xd8\xff":
            suffix = "jpg"
        elif raw[:4] == b"GIF8":
            suffix = "gif"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            suffix = "webp"
        digest = hashlib.sha256(store._artist_key(name).encode()).hexdigest()[:24]
        folder = ARTIST_ART_DIR
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob(digest + ".*"):
            old.unlink(missing_ok=True)
        (folder / f"{digest}.{suffix}").write_bytes(raw)
        url = f"/api/artist/art/{digest}"
        store.set_artist_art(name, url, manual=True, provider="manual")
        self.json({"ok": True, "artwork": url})

    def _claim_import(self, store, result) -> None:
        """Imported tracks and their playlist belong to the member who imported."""
        if not security().required or not self.member:
            return
        for track_id in result.get("track_ids") or []:
            security().claim(store, "track", track_id, self.member["id"])
        for playlist_id in result.get("playlist_ids") or []:
            security().claim(store, "playlist", playlist_id, self.member["id"])

    def do_library_move(self) -> None:
        raise Denied(403, "Move library is an administrator-only action")

    def do_settings(self) -> None:
        store, _ = boot()
        self.json({"settings": store.set_settings(self.body())})
