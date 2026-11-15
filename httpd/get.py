"""The GET route chain: pages, media, and the reads that are not a POST action.

Order is significant and is the reason this is one function rather than a table. Public PWA and
party assets answer before any authentication; a share capability URL answers before any
session exists; the boundary runs before a page is chosen; and the session redirect for `/`
must happen before the page renders. A table would have to encode that ordering anyway, so it
is written as the chain it is.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlparse

import db
from security import Denied
from .constants import ARTIST_ART_DIR
from .runtime import boot, queue, security


class Pages:
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        # Party shell contains no catalog, credentials or personalized data.
        if path in ('/party', '/party.html', '/party.js', '/party.css'):
            self.static('party.html' if path in ('/party', '/party.html') else path.lstrip('/'))
            return
        # The Swipe shell holds no catalog: its data comes from authenticated /api/swipe.
        if path in ('/swipe', '/swipe.html', '/swipe.js', '/swipe.css'):
            self.static('swipe.html' if path in ('/swipe', '/swipe.html') else path.lstrip('/'))
            return
        # Public, non-personal PWA assets only; no boot, session or database access.
        if path in ('/app.webmanifest', '/icon-192.png', '/icon-512.png',
                    '/genres.js', '/preferences.js', '/lifecycle.js', '/sw.js', '/offline.html'):
            self.static(path.lstrip('/'))
            return
        if path == '/robots.txt':
            self._send(200, b'User-agent: *\nDisallow: /\n', 'text/plain')
            return
        # The member's colours as a stylesheet, linked from <head>: the browser has them
        # before the first paint, so a saved theme never flashes the default first.
        if path == '/theme.css':
            self.theme_css()
            return
        # Public share pages are capability URLs: no session, no catalogue.
        if path.startswith('/s/'):
            import shares
            try:
                shares.public(self, boot()[0], security())
            except Denied as exc:
                self._send(exc.code, exc.message.encode(), 'text/plain; charset=utf-8')
            return
        if not self.boundary(path):
            return
        if security().required and path in ('/', '/index.html', '/dj', '/dj.html'):
            if self.web_ui_off():
                return      # no page to send them to: the UI was switched off at boot
            try:
                self.member = security().authenticate(self, boot()[0])
            except Denied as exc:
                if exc.code == 401:
                    self.redirect('/login')
                else:
                    data = {'error': exc.message}
                    headers = None
                    if exc.retry_after is not None:
                        data['retry_after'] = exc.retry_after
                        headers = {'Retry-After': str(exc.retry_after)}
                    self.json(data, exc.code, extra_headers=headers)
                return
        store, _ = boot()
        if path == "/api/state":
            from repo import service as repo_service
            snapshot = repo_service.snapshot(store)
            # The snapshot's own list is the raw table, so it has no idea who owns
            # what. The UI adopts this on every poll: handing it the bare rows is
            # what emptied the member's own list and hid the follow seat.
            snapshot["subscriptions"] = self.subscriptions_with_ownership(store)
            snapshot.pop("follows", None)
            # QUEUE.current is live (sub-second, straight from yt-dlp); the
            # snapshot's own copy is the last persisted row, which is all there is
            # after a restart or while the worker is between tracks.
            snapshot["current"] = (q.current if (q := queue()) else None) or snapshot.get("current")
            admin = not security().required or bool(self.member and self.member['role'] == 'admin')
            snapshot['viewer'] = {
                'id': (self.member or {}).get('id', ''),
                'is_admin': admin,
                'auth_required': security().required,
                'display_name': (self.member or {}).get('display_name', '') or 'Member',
                'role': (self.member or {}).get('role', ''),
                'profile_icon': (self.member or {}).get('profile_icon', 'music'),
            }
            owners = {}
            if security().required:
                security().schema(store)
                with store.lock:
                    owners = dict(store.conn.execute('SELECT playlist_id,member_id FROM member_playlists').fetchall())
            for playlist in snapshot.get('playlists', []):
                # Same rule the delete route enforces: admin, or whoever added it.
                playlist['can_remove'] = admin or bool(
                    self.member and (owners.get(playlist['id']) == self.member['id']
                                     or security().owner_of(store, 'playlist', playlist['id']) == self.member['id']))
            self.json(snapshot)
            return
        if path == "/api/health":
            self.json({"ok": True, "version": db.VERSION})
            return
        if path == "/api/federation":
            # Read-only: what this node's mirror is set to (FED-MIRROR-002/003/004). Writing the
            # mode or the peer list is an operator action, not a web form: a peer has to be
            # authenticated before this node can serve it bytes, and that half is still open.
            from repo import mirror
            self.json(mirror.status(store))
            return
        if path == "/api/feed":
            query = parse_qs(urlparse(self.path).query)
            decision = (query.get("decision") or [""])[0]
            # Releases are catalogue, not activity: Repo owns the read (REPO-012).
            from repo import discovery as repo_discovery
            self.json({"items": repo_discovery.releases(store, 200, decision)})
            return
        if path == "/api/popular":
            # The community chart: the only read over `listen_events` that is not one
            # member's, so it takes no member and lives beside Activity in the Feed.
            # Calendar windows (stats.calendar_window): today, this week, this month.
            query = parse_qs(urlparse(self.path).query)
            period = (query.get("period") or ["day"])[0]
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            import stats as stats_module
            try:
                start, end = stats_module.calendar_window(period)
            except ValueError as error:
                self.json({"error": str(error)}, 400)
                return
            self.json({"period": period, "start": start, "end": end,
                       "items": stats_module.popular_tracks(
                           store, start, end, max(1, min(200, limit)))})
            return
        if path == "/api/activity":
            query = parse_qs(urlparse(self.path).query)
            try:
                limit = int((query.get("limit") or ["80"])[0])
            except ValueError:
                limit = 80
            from social import service as social_service
            self.json({"items": social_service.feed(store, max(1, min(200, limit)))})
            return
        if path.startswith("/api/member/"):
            target = path.rsplit("/", 1)[-1]
            if not target or len(target) > 64:
                self.json({"error": "Unknown member"}, 404)
                return
            from social import service as social_service
            found = social_service.profile(store, target)
            if not found:
                self.json({"error": "Unknown member"}, 404)
                return
            self.json(found)
            return
        if path == "/api/members":
            if not self.member or self.member['role'] != 'admin':
                self.json({"error": "Administrator required"}, 403)
                return
            security().schema(store)
            with store.lock:
                rows = [dict(r) for r in store.conn.execute(
                    "SELECT id, display_name, role, profile_icon FROM members ORDER BY CASE role WHEN 'pending' THEN 0"
                    " WHEN 'member' THEN 1 ELSE 2 END, display_name")]
            # How much of their allowance each member has used. Measured for
            # everyone, enforced for members (admins are never blocked).
            from library import quota
            spent, limit = quota.usage(store), quota.cap(store)
            for row in rows:
                row["used_bytes"] = spent.get(row["id"], 0)
                row["used"] = quota.human(row["used_bytes"])
                row["cap"] = quota.human(limit) if limit else "unlimited"
                row["over"] = bool(limit) and row["role"] != "admin" \
                    and row["used_bytes"] > limit
            self.json({"members": rows, "quota_bytes": limit})
            return
        if path == "/api/artist":
            # One artist page: what the library knows, whether it is followed, the
            # source's own RSS feed, and the releases that feed has produced.
            name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0].strip()
            if not name:
                self.json({"error": "name is required"}, 400)
                return
            from repo import service as repo_service
            self.json(repo_service.artist(store, name))
            return
        if path == "/api/tracks":
            # The songs inside one album. Read-only and user-initiated: the Feed
            # asks only when somebody opens a row.
            from urllib.parse import parse_qs as _qs, urlparse as _up
            raw = (_qs(_up(self.path).query).get("url") or [""])[0].strip()
            if not raw:
                self.json({"error": "url is required"}, 400)
                return
            from repo import service as repo_service
            try:
                self.json({"tracks": repo_service.tracks(raw)})
            except ValueError as error:
                self.json({"error": str(error)}, 400)
            except Exception as error:                      # noqa: BLE001
                self.json({"error": f"Could not read that tracklist ({error})"}, 502)
            return
        if path == "/api/catalogue":
            from urllib.parse import parse_qs as _qs, urlparse as _up
            query = _qs(_up(self.path).query)
            raw = (query.get("watch") or [""])[0].strip()
            if not raw.isdigit():
                self.json({"error": "watch is required"}, 400)
                return
            try:
                offset = int((query.get("offset") or ["0"])[0])
                limit = int((query.get("limit") or ["60"])[0])
                if offset < 0 or limit < 1 or limit > 200:
                    raise ValueError
            except ValueError:
                self.json({"error": "invalid catalogue page"}, 400)
                return
            from repo import service as repo_service
            try:
                self.json(repo_service.releases(store, int(raw), offset=offset, limit=limit))
            except ValueError as error:
                self.json({"error": str(error)}, 400)
            except Exception as error:                      # noqa: BLE001
                self.json({"error": f"Could not read that catalogue ({error})"}, 502)
            return
        if path == "/api/subscriptions":
            self.json({"subscriptions": self.subscriptions_with_ownership(store)})
            return
        if path == "/api/titles/status":
            from library import metadata_repair
            self.json(metadata_repair.status())
            return
        published = re.fullmatch(r"/api/track/(\d+)/publish", path)
        if published:
            # The progress of one upload to another server. Administrator-only for the same
            # reason the POST is: it reports what the server's own credential did.
            if not self.member or self.member["role"] != "admin":
                self.json({"error": "Administrator required"}, 403)
                return
            from library import publishing
            self.json(publishing.status(int(published.group(1))))
            return
        if path == "/api/tempo/status":
            from library import tempo
            self.json(tempo.status())
            return
        if path == "/api/sonic/status":
            from library import sonic
            # Additive and free when the table is there; a database that has never analysed
            # anything has no table yet, and a status read must still answer.
            sonic.schema(store)
            state = sonic.status()
            state["analysed"] = sonic.count(store)
            self.json(state)
            return
        if path.startswith("/api/sonic/similar/"):
            from library import sonic
            sonic.schema(store)
            raw = path.rsplit("/", 1)[-1]
            if not raw.isdigit():
                self.json({"error": "Not a track id"}, 400)
                return
            rows = sonic.similar(store, int(raw), limit=20)
            self.json({"track_id": int(raw), "similar": self._sonic_songs(rows)})
            return
        if path == "/api/sonic/path":
            from library import sonic
            sonic.schema(store)
            query = dict(parse_qsl(urlparse(self.path).query))
            ends = [query.get("from", ""), query.get("to", "")]
            if not all(value.isdigit() for value in ends):
                self.json({"error": "from and to are required track ids"}, 400)
                return
            try:
                walk = sonic.path(store, int(ends[0]), int(ends[1]),
                                  hops=max(1, min(int(query.get("hops") or 8), 50)))
            except ValueError as error:
                # "not analysed yet" is a 409: the request is fine, the catalogue is not ready.
                self.json({"error": str(error)}, 409)
                return
            self.json({"path": self._sonic_songs([{"id": track_id} for track_id in walk])})
            return
        if path == "/api/deleted":
            # Tombstones, for the Settings page. Admin-only, like deleting itself.
            if security().required and not (self.member and self.member['role'] == 'admin'):
                self.json({"error": "Administrator required"}, 403)
                return
            rows = store.deleted_tracks()
            self.json({"deleted": [{
                "id": row["id"], "title": row["title"],
                "artist": row["artist_name"], "album": row["album"],
                "provider": row["provider"], "deleted": row["deleted"],
                "has_file": bool(row.get("file")) and Path(row["file"]).is_file(),
                "playlists": row.get("playlists") or 0,
            } for row in rows]})
            return
        if path == "/api/deletions":
            # The durable half of "who deleted what". Admin-only, like deleting itself,
            # and not the tombstones: this is the record of the act, not the songs.
            if security().required and not (self.member and self.member['role'] == 'admin'):
                self.json({"error": "Administrator required"}, 403)
                return
            import deletion
            self.json({"deletions": deletion.history(store)})
            return
        if path == "/api/library":
            from social import annotate
            try:
                from library import artist_art
                artist_art.warm(store)
            except Exception:
                pass
            # Who added each playlist travels with it: the tile names them on the
            # count line. A playlist added before owners were recorded simply has
            # no name, and the tile says nothing rather than inventing one.
            try:
                playlists = [dict(r) for r in store.conn.execute(
                    "SELECT p.id,p.title,p.provider,p.source_url,"
                    " COALESCE(NULLIF(TRIM(m.display_name),''),'') AS added_by"
                    " FROM playlists p LEFT JOIN member_playlists o ON o.playlist_id=p.id"
                    " LEFT JOIN members m ON m.id=o.member_id"
                    " WHERE " + db.playlist_rows("p") +
                    " ORDER BY p.position,p.id")]
            except Exception:
                playlists = [dict(r) for r in store.conn.execute(
                    "SELECT id,title,provider,source_url FROM playlists p WHERE "
                    + db.playlist_rows("p") + " ORDER BY position,id")]
            self.json({"tracks": annotate(store.library()), "all_tracks": annotate(store.library(include_pending=True)), "artist_art": store.artist_art_map(), "playlists": playlists})
            return
        if path == "/api/library/export":
            # Hand-off to DJ software, not an audio download: m3u8 for anything
            # that reads playlists, Rekordbox XML for the import path everything
            # else converts from. Only files that exist are listed.
            from library import dj_export
            from urllib.parse import quote as _quote
            query = parse_qs(urlparse(self.path).query)
            fmt = (query.get("format") or ["m3u8"])[0]
            raw_playlist = (query.get("playlist") or [""])[0]
            try:
                playlist_id = int(raw_playlist) if raw_playlist else None
                body, filename = dj_export.build(store, fmt, playlist_id)
            except ValueError as error:
                self.json({"error": str(error)}, 400)
                return
            payload = body.encode("utf-8")
            ctype = "application/xml; charset=utf-8" if fmt == "rekordbox" else "text/plain; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition",
                             "attachment; filename=\"export\"; filename*=UTF-8''" + _quote(filename, safe=""))
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)
            return
        backup = re.fullmatch(r"/api/playlist/(\d+)/apple-backup", path)
        if backup:
            # One restore file per playlist: ordered catalog IDs, no audio, no auth
            # beyond the session that can already read this playlist.
            from library import playlist_backup
            from urllib.parse import quote as _quote
            try:
                body = playlist_backup.dumps(store, int(backup[1]))
                name = playlist_backup.filename(store, int(backup[1]))
            except ValueError as error:
                self.json({"error": str(error)}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition",
                             "attachment; filename=\"playlist.hardlyplaylist.json\"; filename*=UTF-8''"
                             + _quote(name, safe=""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        # All browser exports pass through one boundary; future shared auth must
        # guard this dispatcher alongside audio and the rest of the API.
        if self.browser_download(path, store):
            return
        audio = re.match(r"^/api/track/(\d+)/audio$", path)
        if audio:
            self.stream_audio(int(audio.group(1)))
            return
        cover = re.match(r"^/api/track/(\d+)/artwork$", path)
        if cover:
            from library import artwork
            image = artwork.for_track(store, int(cover.group(1)))
            if not image:
                self.json({"error": "no embedded cover"}, 404)
                return
            payload = image.read_bytes()
            self.send_response(200)
            # Say which digest this cover now lives under, so the page can switch the car's
            # artwork URL to our own route without waiting for the next `/api/state` poll.
            record = store.conn.execute(
                'SELECT art_hash FROM tracks WHERE id=?', (int(cover.group(1)),)).fetchone()
            if record and record['art_hash']:
                self.send_header('X-Art-Hash', record['art_hash'])
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(payload)
            return
        artist_art = re.match(r"^/api/artist/art/([a-f0-9]{24})$", path)
        if artist_art:
            folder = ARTIST_ART_DIR
            for suffix, ctype in (("png","image/png"), ("jpg","image/jpeg"),
                                  ("jpeg","image/jpeg"), ("gif","image/gif"),
                                  ("webp","image/webp")):
                target = folder / f"{artist_art.group(1)}.{suffix}"
                if target.is_file():
                    self._send(200, target.read_bytes(), ctype)
                    return
            self._send(404, b"no art", "text/plain")
            return
        if path in ("/", "/index.html"):
            self.static("index.html")
            return
        if path in ("/login", "/login.html"):
            self.static("login.html")
            return
        if path in ("/auth/link", "/auth/link.html"):
            self.static("link.html")
            return
        if path in ("/signup", "/signup.html"):
            self.static("signup.html")
            return
        if path in ("/dj", "/dj.html"):
            self.static("dj.html")
            return
        self.static(path.lstrip("/"))
