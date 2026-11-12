#!/usr/bin/env python3
"""HardlyListening — HTTP surface.

Thin on purpose: routes translate JSON into Store/Queue calls. No download logic,
no provider knowledge, no SQL in the handlers.

  python3 server.py [--port 8470] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config
import db
import paths
from security import SECURITY, Denied
from engine.queue import Queue

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
ARTIST_ART_DIR = paths.ARTIST_ART_DIR

STORE: db.Store | None = None
QUEUE: Queue | None = None
SCHEDULER = None
SEEDED = False
LOCK = threading.Lock()

ROUTES = [
    ("POST", r"^/api/add$", "add"),
    ("POST", r"^/api/pause$", "pause"),
    ("POST", r"^/api/cooldown/clear$", "clear_cooldown"),
    ("POST", r"^/api/failed/retry$", "retry_failed"),
    ("POST", r"^/api/queue/clear-completed$", "clear_completed"),
    ("POST", r"^/api/queue/auto-accept$", "queue_auto"),
    ("POST", r"^/api/settings$", "settings"),
    ("POST", r"^/api/import$", "import_paths"),
    ("POST", r"^/api/import/upload$", "import_upload"),
    ("POST", r"^/api/artist/art$", "artist_art"),
    ("POST", r"^/api/library/move$", "library_move"),
    ("POST", r"^/api/track/(\d+)/retry$", "track_retry"),
    ("POST", r"^/api/track/(\d+)/skip$", "track_skip"),
    ("POST", r"^/api/track/(\d+)/source$", "track_source"),
    ("POST", r"^/api/track/(\d+)/unpick$", "track_unpick"),
    ("POST", r"^/api/track/(\d+)/move$", "track_move"),
    ("POST", r"^/api/playlist/(\d+)/remove$", "playlist_remove"),
    ("POST", r"^/api/release/(\d+)/remove$", "release_remove"),
    ("POST", r"^/api/playlist/(\d+)/requeue$", "playlist_requeue"),
    ("POST", r"^/api/playlist/(\d+)/verify$", "playlist_verify"),
    ("POST", r"^/api/library/verify$", "library_verify"),
    ("POST", r"^/api/library/grab$", "library_grab"),
    ("POST", r"^/api/library/tempo$", "library_tempo"),
    ("POST", r"^/api/library/titles$", "library_titles"),
    ("POST", r"^/api/track/(\d+)/bpm$", "track_bpm"),
    ("POST", r"^/api/activity/listen$", "activity_listen"),
    ("POST", r"^/api/members$", "members_update"),
    ("POST", r"^/api/subscriptions$", "subscription_add"),
    ("POST", r"^/api/subscriptions/(\d+)/check$", "subscription_check"),
    ("POST", r"^/api/subscriptions/(\d+)/toggle$", "subscription_toggle"),
    ("POST", r"^/api/subscriptions/(\d+)/remove$", "subscription_remove"),
    ("POST", r"^/api/subscriptions/(\d+)/unsubscribe$", "subscription_leave"),
    ("POST", r"^/api/feed/refresh-all$", "feed_refresh_all"),
    ("POST", r"^/api/feed/(\d+)/queue$", "feed_queue"),
    ("POST", r"^/api/track/queue$", "track_queue"),
    ("POST", r"^/api/feed/(\d+)/ignore$", "feed_ignore"),
    ("POST", r"^/api/feed/(\d+)/seen$", "feed_seen"),
]


def boot() -> tuple[db.Store, Queue]:
    global STORE, QUEUE, SCHEDULER
    with LOCK:
        if STORE is None:
            paths.ensure()
            STORE = db.Store()
            # `MUSIC_DIR` supplies the first root only: once the settings table holds a value
            # the admin chose, it wins and the variable is inert (see config.seed_music_root).
            if config.seed_music_root(STORE):
                print(f"library root seeded from HARDLYLISTENING_MUSIC_DIR: {STORE.setting('root')}",
                      flush=True)
            # Choices made by hand before the ranking kept a record still count.
            import ranking
            ranking.seed_from_jobs(STORE)
            QUEUE = Queue(STORE)
            QUEUE.start()
            from engine.scheduler import Scheduler
            SCHEDULER = Scheduler(STORE)
            SCHEDULER.start()
    assert STORE and QUEUE
    return STORE, QUEUE


class Handler(BaseHTTPRequestHandler):
    server_version = "HardlyListening"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # A keep-alive timeout can log before parse_request set self.path.
        if not getattr(self, "path", ""):
            print(f"{self.address_string()} {fmt % args}", flush=True)
            return
        if self.path.startswith(('/party', '/api/party', '/auth/', '/api/auth/', '/api/members', '/rest/', '/api/listening/', '/s/')):
            return  # Never log OAuth callback codes.
        if self.path.startswith("/api/state"):
            return                       # 1.5 s polling would drown the log
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def end_headers(self):
        self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
        if SECURITY.required:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        super().end_headers()

    def boundary(self, path):
        self.member = None
        try:
            if self.command == 'POST':
                length = self.headers.get('Content-Length', '0')
                if self.headers.get('Transfer-Encoding') or not length.isdigit():
                    raise Denied(400, 'Invalid body framing')
                if int(length) > 28 * 1024 * 1024:
                    raise Denied(413, 'Request body exceeds 28 MiB')
            if path == '/api/party' or path.startswith('/api/party/'):
                import party
                party.dispatch(self, boot()[0], SECURITY)
                return False
            # Shares are member-only. Guests hold just the party cookie and are
            # refused; the capability URL itself is served publicly by do_GET.
            if path == '/api/shares' or path.startswith('/api/shares/'):
                import shares
                shares.dispatch(self, boot()[0], SECURITY)
                return False
            # Temporary party credentials NEVER enable the legacy local bypass.
            # A real independent browser membership may still use its own scope.
            if SECURITY.cookie(self, '__Host-hl_party') and path not in ('/login', '/auth/link', '/signup', '/api/health', '/api/auth/session', '/api/auth/logout', '/api/auth/signup', '/api/auth/signup/begin', '/api/auth/signup/password', '/api/auth/login/password'):
                SECURITY.authenticate(self, boot()[0])
            # Device auth is independent of browser/email login, never the local bypass.
            if path.startswith('/rest/'):
                import listening
                listening.rest(self, boot()[0], SECURITY)
                return False
            if path == '/api/playlists' or path.startswith('/api/playlists/'):
                import user_playlists
                user_playlists.dispatch(self, boot()[0], SECURITY)
                return False
            if path == '/api/artist/art/candidates':
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                SECURITY.admin(self.member)
                if self.command != 'GET':
                    raise Denied(405, 'Method not allowed')
                name = (parse_qs(urlparse(self.path).query).get('name') or [''])[0].strip()
                if not name:
                    raise Denied(400, 'name is required')
                from library import artist_art
                self.json(artist_art.candidates(store, name))
                return False
            if path.startswith('/api/listening/'):
                import listening
                listening.browser(self, boot()[0], SECURITY)
                return False
            if path.startswith('/api/decisions'):
                import decisions
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                SECURITY.admin(self.member)
                if self.command == 'GET' and path == '/api/decisions':
                    self.json({'entries': decisions.entries(store)})
                elif self.command == 'POST' and path == '/api/decisions/clear':
                    SECURITY.csrf(self)
                    decisions.clear(store, self.body().get('id'))
                    self.json({'ok': True})
                elif self.command == 'POST' and path == '/api/decisions/reverse':
                    SECURITY.csrf(self)
                    try:
                        decisions.reverse(store, int(self.body().get('id')))
                    except (ValueError, TypeError) as exc:
                        raise Denied(409, str(exc))
                    self.json({'ok': True})
                else:
                    raise Denied(404, 'Not found')
                return False
            if path.startswith('/api/swipe'):
                import swipe
                swipe.dispatch(self, boot()[0], SECURITY)
                return False
            if path == '/api/search':
                # A member searches and downloads a single song the same way they may add
                # a playlist; the hit is pinned so the queue fetches that exact upload.
                store, queue = boot()
                self.member = SECURITY.authenticate(self, store)
                import search
                if self.command == 'GET':
                    SECURITY.limit(self.member['id'], 'songsearch', 30)
                    query = (parse_qs(urlparse(self.path).query).get('q') or [''])[0]
                    try:
                        self.json({'results': search.find(query)})
                    except ValueError as exc:
                        raise Denied(400, str(exc))
                elif self.command == 'POST':
                    SECURITY.csrf(self)
                    SECURITY.limit(self.member['id'], 'songsearch-add', 20)
                    try:
                        self.json(search.queue_hit(store, queue, self.body()))
                    except (ValueError, TypeError) as exc:
                        raise Denied(400, str(exc))
                else:
                    raise Denied(405, 'Method not allowed')
                return False
            if path == '/api/genres' or path.startswith('/api/genres/'):
                import genres
                genres.dispatch(self, boot()[0], SECURITY, path)
                return False
            lyric_route = re.fullmatch(r'/api/track/(\d+)/lyrics', path)
            if lyric_route:
                import lyrics
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                if self.command != 'GET':
                    raise Denied(405, 'Method not allowed')
                SECURITY.limit(self.member['id'], 'lyrics', 30)
                result = lyrics.get(store, int(lyric_route.group(1)))
                if result is None:
                    raise Denied(404, 'No such track')
                self.json(result)
                return False
            if path == '/api/library/move':
                store, queue = boot()
                self.member = SECURITY.authenticate(self, store)
                SECURITY.admin(self.member)
                SECURITY.csrf(self)
                target = (self.body().get('to') or '').strip()
                if not target:
                    raise Denied(400, 'give the new library folder')
                from library import import_files
                was_paused = queue.paused if queue else False
                if queue:
                    queue.pause(True)
                try:
                    result = import_files.move_library(store, target)
                finally:
                    if queue:
                        queue.pause(was_paused)
                self.json(result)
                return False
            if path == '/api/profile':
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                if self.command == 'GET':
                    # The account row rides with the profile: the dialog shows one panel, and the
                    # email is safe metadata (no hashes, salts or tokens ever leave here).
                    self.json({**SECURITY.account(store, self.member),
                               **{k: self.member[k] for k in ('display_name', 'profile_icon')}})
                elif self.command == 'POST':
                    self.json(SECURITY.profile(store, self.member, self.body()))
                else:
                    raise Denied(405, 'Method not allowed')
                return False
            # Sign-in is the one thing that has to work before a session exists.
            if self.command == 'GET' and path in ('/login', '/auth/link', '/signup', '/api/health'):
                # A signed-in member must never be parked on the invitation
                # screen. Arriving at "/" from another site (a chat link, a
                # bookmark in another app) is a cross-site navigation, and the
                # session cookie is SameSite=Lax — Lax is what keeps that
                # navigation signed in instead of bouncing it to /login.
                if path == '/login' and SECURITY.required:
                    try:
                        self.member = SECURITY.authenticate(self, boot()[0])
                    except Denied:
                        pass
                    else:
                        self.redirect('/')
                        return False
                return True     # health is a liveness ping, not data
            if self.command == 'POST' and path == '/api/auth/session':
                SECURITY.csrf(self)
                SECURITY.limit(SECURITY.client_ip(self), 'enrollment', 15)
                payload = self.body()
                sid = SECURITY.exchange(boot()[0], payload.get('invite'), payload.get('display_name'), payload.get('profile_icon'))
                self.signed_in(sid)
                return False
            # -- accounts ------------------------------------------------------
            # Signup and password login are the only paths that mint a session without one, the
            # same shape as the login-link exchange above.
            if path in ('/api/auth/signup', '/api/auth/signup/begin', '/api/auth/signup/password'):
                store, _ = boot()
                if path == '/api/auth/signup' and self.command == 'GET':
                    # Does this browser still hold a validated signup link?
                    self.json(SECURITY.signup_state(store, SECURITY.cookie(self, '__Host-hl_signup')))
                    return False
                if self.command != 'POST':
                    raise Denied(405, 'Method not allowed')
                SECURITY.csrf(self)
                SECURITY.limit(SECURITY.client_ip(self), 'signup', 20)
                if path == '/api/auth/signup/begin':
                    self.signup_flow(SECURITY.open_signup(store, self.body().get('token')))
                    return False
                payload = self.body()
                sid = SECURITY.signup(
                    store, SECURITY.cookie(self, '__Host-hl_signup'),
                    payload.get('display_name'), payload.get('profile_icon'),
                    payload.get('email'), payload.get('password'))
                self.signed_in(sid, clear_signup=True)
                return False
            if self.command == 'POST' and path == '/api/auth/login/password':
                store, _ = boot()
                SECURITY.csrf(self)
                SECURITY.limit(SECURITY.client_ip(self), 'login', 20)
                payload = self.body()
                member = SECURITY.authenticate_password(store, payload.get('email'), payload.get('password'))
                self.signed_in(SECURITY.create_session(store, member['id'], 'password'))
                return False
            if path in ('/api/account', '/api/account/password'):
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                if path == '/api/account' and self.command == 'GET':
                    self.json(SECURITY.account(store, self.member))
                elif path == '/api/account/password' and self.command == 'POST':
                    SECURITY.limit(self.member['id'], 'password-change', 10)
                    self.json(SECURITY.change_password(store, self.member, self.body()))
                else:
                    raise Denied(405, 'Method not allowed')
                return False
            if path == '/api/signup-invite':
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                if self.command == 'GET':
                    # Active and when, never the link: the secret is shown once, when minted.
                    self.json(SECURITY.signup_invite_state(store, self.member))
                elif self.command == 'POST':
                    self.json(SECURITY.mint_member_signup(store, self.member))
                elif self.command == 'DELETE':
                    self.json({'ok': SECURITY.revoke_member_signup(store, self.member)})
                else:
                    raise Denied(405, 'Method not allowed')
                return False
            if self.command == 'POST' and path == '/api/admin/signup-invite':
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                SECURITY.admin(self.member)
                token, expires = SECURITY.mint_signup(store, self.member['id'])
                self.json({'url': SECURITY.signup_url(token), 'expires_at': expires})
                return False
            if path.startswith('/api/members'):
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                SECURITY.admin(self.member)
                if self.command == 'POST' and path != '/api/members':
                    payload = self.body()
                    if path == '/api/members/create':
                        uid = SECURITY.create_user(store, self.member, payload.get('display_name'), payload.get('role'))
                        self.json({'id': uid, 'invite': SECURITY.invite(store, self.member, uid)})
                    elif path == '/api/members/invite':
                        self.json({'invite': SECURITY.invite(store, self.member, payload.get('id'))})
                    elif path == '/api/members/revoke':
                        if payload.get('id') == self.member['id']:
                            raise Denied(400, 'Use Sign out to end your own session')
                        SECURITY.revoke(store, payload.get('id'))
                        self.json({'ok': True})
                    else:
                        raise Denied(404, 'Not found')
                    return False
            if self.command == 'POST' and path == '/api/auth/logout':
                self.body()
                store, _ = boot()
                SECURITY.csrf(self)
                SECURITY.schema(store)
                with store.lock:
                    store.conn.execute('DELETE FROM browser_sessions WHERE digest=?', (SECURITY.digest(SECURITY.cookie(self, '__Host-hl_session')),))
                    store.conn.commit()
                self.send_response(200)
                self.send_header('Set-Cookie', '__Host-hl_session=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return False
            if path == '/api/mixer' or path.startswith('/api/mixer/'):
                # Shared mixer room. Slider drags are chatty, so this route gets its
                # own generous bucket instead of the ordinary 240/min.
                store, _ = boot()
                if SECURITY.required:
                    self.member = SECURITY.authenticate(self, store, requests_limit=1200)
                    if self.command == 'POST':
                        SECURITY.limit(self.member['id'], 'mixer', 900)
                else:
                    # Local mode has no session: each tab says who it is in the query.
                    import mixer_room
                    self.member = mixer_room._identity_from_query(self)
                import mixer_room
                mixer_room.dispatch(self, self.member, path)
                return False
            if path == '/api/boost':
                # Boosts require a verified member in both hosted and local modes.
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                from social import service as social_service
                if self.command == 'GET':
                    self.json({'boost': social_service.current_boost(store, self.member['id'])})
                elif self.command == 'POST':
                    SECURITY.csrf(self)
                    SECURITY.limit(self.member['id'], 'boost', 30)
                    payload = self.body()
                    if not isinstance(payload, dict):
                        raise Denied(400, 'Object required')
                    result = social_service.boost(store, self.member['id'],
                                                  payload.get('track_id'))
                    self.json({'ok': True, 'boost': result})
                else:
                    raise Denied(405, 'Method not allowed')
                return False
            if path == '/api/social' or path.startswith('/api/social/'):
                # Social is never anonymous, even in legacy local mode. Reuse
                # verified membership, CSRF and central per-member rate buckets.
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                if self.command == 'POST':
                    SECURITY.limit(self.member['id'], 'social', 30)
                from social import api as social_api
                social_api.dispatch(self, store)
                return False
            if not SECURITY.required:
                return True
            SECURITY.config()
            if path.startswith('/auth/') and self.command == 'GET':
                raise Denied(404, 'Not found')
            if path.startswith('/api/'):
                store, _ = boot()
                self.member = SECURITY.authenticate(self, store)
                if self.command == 'POST':
                    SECURITY.schema(store)
                    allowed = (path == '/api/add'
                               or bool(re.fullmatch(r'/api/(playlist|release)/\d+/remove', path)))
                    if self.member['role'] != 'admin':
                        if not allowed:
                            raise Denied(403, 'Administrator required for shared controls')
                        if path != '/api/add':
                            pid = int(path.split('/')[3])
                            owner = store.conn.execute('SELECT member_id FROM member_playlists WHERE playlist_id=?', (pid,)).fetchone()
                            if not owner or owner[0] != self.member['id']:
                                raise Denied(403, 'Only the playlist owner may edit it')
                    with store.lock:
                        store.conn.execute('INSERT INTO member_requests(member_id,path) VALUES(?,?)', (self.member['id'], path))
                        store.conn.commit()
            return True
        except Denied as exc:
            self.close_connection = True
            self.json({'error': exc.message}, exc.code)
            return False

    # -- plumbing --
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def json(self, data, code: int = 200) -> None:
        if getattr(self, 'member', None) and self.member['role'] != 'admin' and code < 400:
            def public(value):
                if isinstance(value, list):
                    return [public(v) for v in value]
                if isinstance(value, dict):
                    result = {k: ('available' if v else '') if k == 'file' else public(v)
                            for k, v in value.items() if k not in ('logs', 'settings', 'error_message', 'error', 'cooldown_reason', 'playlist_projection_error', 'yt_dlp', 'root')}
                    if 'settings' in value:
                        result['settings'] = {}
                    if 'logs' in value:
                        result['logs'] = []
                    return result
                return value
            data = public(data)
        self._send(code, json.dumps(data, default=str).encode(), "application/json")

    def body(self) -> dict:
        """Memoised: rfile.read() only answers once, so a second call would hang."""
        if getattr(self, "_body", None) is not None:
            return self._body
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            self._body = json.loads(raw.decode("utf-8", "replace") or "{}")
            if not isinstance(self._body, dict):
                self._body = {}
        except json.JSONDecodeError:
            self._body = {}
        return self._body

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
        # Public share pages are capability URLs: no session, no catalogue.
        if path.startswith('/s/'):
            import shares
            try:
                shares.public(self, boot()[0], SECURITY)
            except Denied as exc:
                self._send(exc.code, exc.message.encode(), 'text/plain; charset=utf-8')
            return
        if not self.boundary(path):
            return
        if SECURITY.required and path in ('/', '/index.html', '/dj', '/dj.html'):
            try:
                self.member = SECURITY.authenticate(self, boot()[0])
            except Denied:
                self.redirect('/login')
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
            snapshot["current"] = (QUEUE.current if QUEUE else None) or snapshot.get("current")
            admin = not SECURITY.required or bool(self.member and self.member['role'] == 'admin')
            snapshot['viewer'] = {
                'id': (self.member or {}).get('id', ''),
                'is_admin': admin,
                'auth_required': SECURITY.required,
                'display_name': (self.member or {}).get('display_name', '') or 'Member',
                'role': (self.member or {}).get('role', ''),
                'profile_icon': (self.member or {}).get('profile_icon', 'music'),
            }
            owners = {}
            if SECURITY.required:
                SECURITY.schema(store)
                with store.lock:
                    owners = dict(store.conn.execute('SELECT playlist_id,member_id FROM member_playlists').fetchall())
            for playlist in snapshot.get('playlists', []):
                # Same rule the delete route enforces: admin, or whoever added it.
                playlist['can_remove'] = admin or bool(
                    self.member and (owners.get(playlist['id']) == self.member['id']
                                     or SECURITY.owner_of(store, 'playlist', playlist['id']) == self.member['id']))
            self.json(snapshot)
            return
        if path == "/api/health":
            self.json({"ok": True, "version": db.VERSION})
            return
        if path == "/api/feed":
            query = parse_qs(urlparse(self.path).query)
            decision = (query.get("decision") or [""])[0]
            self.json({"items": store.feed_list(200, decision)})
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
            SECURITY.schema(store)
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
        if path == "/api/tempo/status":
            from library import tempo
            self.json(tempo.status())
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
                    " ORDER BY p.position,p.id")]
            except Exception:
                playlists = [dict(r) for r in store.conn.execute(
                    "SELECT id,title,provider,source_url FROM playlists ORDER BY position,id")]
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

    def browser_download(self, path, store):
        from library.browser_downloads import archive, local_path, safe_name, selection, track_name
        from urllib.parse import quote
        import os
        single = re.fullmatch(r'/api/track/(\d+)/download', path)
        playlist = re.fullmatch(r'/api/playlist/(\d+)/download', path)
        if not (single or playlist or path == '/api/library/download'):
            return False
        try:
            if single:
                track = store.track_by_id(int(single[1]))
                media = local_path(store, track)
                if media is None:
                    self.json({'error': 'Unavailable: only existing local library files can be downloaded.'}, 404)
                    return True
                handle = media.open('rb')
                name = track_name(track, media, with_id=False)   # a single file needs no id prefix
                ctype = mimetypes.guess_type(media.name)[0] or 'application/octet-stream'
            else:
                query = parse_qs(urlparse(self.path).query)
                title, tracks = selection(store, 'playlist' if playlist else 'ids', int(playlist[1]) if playlist else query.get('ids', [''])[0])
                handle = archive(store, tracks, title)
                name = safe_name(title) + '.zip'
                ctype = 'application/zip'
        except ValueError:
            self.json({'error': 'Invalid or unavailable selection'}, 400)
            return True
        except OSError:
            self.json({'error': 'Local files unavailable for download'}, 404)
            return True
        with handle:
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Disposition', 'attachment; filename="download' + ('.zip' if ctype == 'application/zip' else '') + '"; filename*=UTF-8\'\'' + quote(name, safe=''))
            self.send_header('Content-Length', str(os.fstat(handle.fileno()).st_size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                while chunk := handle.read(65536):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
        return True

    def stream_audio(self, track_id: int) -> None:
        """Serve a library file with Range support — an <audio> deck needs it to
        seek, and seeking is most of what a DJ board does.

        The decision (file, slice, status) is `library.playback`'s; this method only writes it,
        so the mixer, the public share page and Subsonic stream the same way.
        """
        from library import playback
        store, _ = boot()
        stream = playback.plan(store, track_id, self.headers.get("Range"))
        if stream is None:
            self._send(404, b"file is missing", "text/plain")
            return
        self.send_response(stream.status)
        self.send_header("Content-Type", stream.content_type)
        self.send_header("Content-Length", str(stream.length))
        self.send_header("Accept-Ranges", "bytes")
        if stream.status in (206, 416):
            self.send_header("Content-Range", stream.content_range)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        for chunk in stream.chunks():
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def signed_in(self, sid: str, clear_signup: bool = False) -> None:
        """A session was minted: hand over the cookie, and end the signup flow if there was one."""
        self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Set-Cookie', f'__Host-hl_session={sid}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={SECURITY.SESSION_TTL}')
        if clear_signup:
            self.send_header('Set-Cookie', '__Host-hl_signup=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def signup_flow(self, digest: str) -> None:
        """A validated signup link becomes a short-lived cookie; the page erases the fragment."""
        self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Set-Cookie', f'__Host-hl_signup={digest}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={SECURITY.SIGNUP_FLOW_TTL}')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def static(self, name: str) -> None:
        bases = (WEB_DIR, APP_DIR) if name in ('app.webmanifest', 'icon-192.png', 'icon-512.png') else (WEB_DIR,)
        for base in bases:
            target = (base / name).resolve()
            if base.resolve() not in target.parents:
                continue                       # no traversal
            if target.is_file():
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix == ".webmanifest":
                    ctype = "application/manifest+json"
                self._send(200, target.read_bytes(), ctype)
                return
        self._send(404, b"not found", "text/plain")

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not self.boundary(path):
            return
        self.json({"error": "no such endpoint"}, 404)

    def do_POST(self) -> None:
        self._body = None  # Handler instances survive HTTP keep-alive requests.
        path = urlparse(self.path).path
        if not self.boundary(path):
            return
        boot()
        self.body()  # Drain once, including actions that do not inspect a payload.
        for _, pattern, action in ROUTES:
            match = re.match(pattern, path)
            if not match:
                continue
            args = [int(g) for g in match.groups() if g.isdigit()]
            try:
                getattr(self, "do_" + action)(*args)
            except ValueError as exc:
                self.json({"error": str(exc)}, 400)
            except RuntimeError as exc:
                self.json({"error": str(exc)}, 400)
            except Exception as exc:                          # noqa: BLE001
                self.json({"error": 'Request failed' if SECURITY.required else f"{type(exc).__name__}: {exc}"}, 500)
            return
        self.json({"error": "no such endpoint"}, 404)

    # -- actions --
    # -- source availability -------------------------------------------------
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

    def do_members_update(self) -> None:
        """Approve, demote or re-approve one member. Admins only (route gate)."""
        if not self.member or self.member['role'] != 'admin':
            self.json({"error": "Administrator required"}, 403)
            return
        payload = self.body()
        member_id = str((payload or {}).get("id", ""))
        role = (payload or {}).get("role", "")
        if member_id == self.member['id'] and role != 'admin':
            self.json({'error': 'Cannot demote or block yourself'}, 400)
            return
        if role not in ("member", "admin", "pending", "blocked"):
            self.json({"error": "role must be member, admin or pending"}, 400)
            return
        store, _ = boot()
        SECURITY.schema(store)
        with store.lock:
            row = store.conn.execute("SELECT role FROM members WHERE id=?", (member_id,)).fetchone()
            if not row:
                self.json({"error": "No such member"}, 404)
                return
            if row["role"] == "admin" and role != "admin":
                admins = store.conn.execute(
                    "SELECT COUNT(*) FROM members WHERE role='admin'").fetchone()[0]
                if admins <= 1:
                    self.json({"error": "At least one admin must remain"}, 400)
                    return
            store.conn.execute("UPDATE members SET role=? WHERE id=?", (role, member_id))
            store.conn.commit()
        if role not in ('member', 'admin'):
            SECURITY.revoke(store, member_id)
        self.json({"ok": True})

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

    def do_activity_listen(self) -> None:
        """The browser reporting one play. Deduped server-side, stacked on read."""
        payload = self.body()
        track_id = int((payload or {}).get("id") or 0)
        if track_id <= 0:
            raise ValueError("id is required")
        store, _ = boot()
        from social import service as social_service
        social_service.listen(store, self.member, track_id)
        self.json({"ok": True})

    def do_library_titles(self) -> None:
        """Preview or apply title repair for tracks imported with a numeric id."""
        payload = self.body() if self.command == "POST" else {}
        store, _ = boot()
        from library import metadata_repair
        if (payload or {}).get("apply"):
            self.json(metadata_repair.repair_async(store))
            return
        self.json(metadata_repair.preview(store))

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
                    result = queue.ingest(one)
                    if self.member and result.get('playlist_id') and result['playlist_id'] not in existing:
                        store.conn.execute('INSERT OR IGNORE INTO member_playlists(playlist_id,member_id) VALUES(?,?)', (result['playlist_id'], self.member['id']))
                        store.conn.commit()
                        SECURITY.claim(store, 'playlist', result['playlist_id'], self.member['id'])
                out.append(result)
            except Exception as exc:                          # noqa: BLE001
                out.append({"url": one, "error": str(exc)})
        self.json({"added": out})

    # -- subscriptions / feed --
    def subscriptions_with_ownership(self, store) -> list[dict]:
        """Subscriptions, each marked with whether this member may call it theirs.

        The rule (an unowned follow belongs to nobody; an anonymous reader sees all of
        them) lives in Repo, so the endpoint and the feed cannot drift apart.
        """
        from repo import service as repo_service
        member = getattr(self, "member", None) or {}
        return repo_service.subscriptions(store, str(member.get("id") or ""))

    def do_subscription_add(self) -> None:
        body = self.body()
        store, _ = boot()
        from repo import service as repo_service
        member_id = str((getattr(self, "member", None) or {}).get("id") or "")
        # From a link, or — from an artist page that has no known link — by name, with
        # the subscribable source resolved from the library.
        self.json(repo_service.subscribe(
            store, str(body.get("url") or "").strip(),
            name=str(body.get("name") or "").strip(),
            provider=str(body.get("provider") or ""), member_id=member_id))

    def do_subscription_check(self, watch_id: int) -> None:
        store, _ = boot()
        from repo import service as repo_service
        self.json(repo_service.check(store, repo_service.watch(store, watch_id)))

    def do_subscription_toggle(self, watch_id: int) -> None:
        store, _ = boot()
        from repo import service as repo_service
        watch = repo_service.watch(store, watch_id)
        self.json(repo_service.set_enabled(store, watch_id, not bool(watch["enabled"])))

    def do_subscription_remove(self, watch_id: int) -> None:
        store, _ = boot()
        from repo import service as repo_service
        self.json(repo_service.remove(store, watch_id))

    def do_subscription_leave(self, watch_id: int) -> None:
        """Stop calling this artist one of mine — without stopping the tracking.

        The row stays, the artist stays on the feed for everybody else, and the
        seat goes back to the "+" so it can be picked up again.
        """
        store, _ = boot()
        from repo import service as repo_service
        member_id = str((getattr(self, "member", None) or {}).get("id") or "")
        self.json(repo_service.release_seat(store, watch_id, member_id))

    def do_feed_refresh_all(self) -> None:
        """Manual sweep: every enabled follow, once, in order.

        Sequential on purpose — one provider request at a time is what keeps a
        big list from getting rate-limited — so a long list means a long wait,
        with the button spinning until the last artist answers.
        """
        store, _ = boot()
        from repo import service as repo_service
        done = []
        for watch in repo_service.subscriptions(store):
            if not watch["enabled"]:
                continue
            try:
                done.append(repo_service.check(store, watch))
            except Exception as exc:                    # noqa: BLE001
                done.append({"artist": watch["artist_name"], "error": str(exc)})
        self.json({"checked": done})

    def do_feed_queue(self, item_id: int) -> None:
        """The only place a feed item becomes work — a click, never a timer."""
        store, queue = boot()
        item = store.feed_item(item_id)
        if not item:
            raise ValueError("no such feed item")
        if item["decision"] == "queued":
            # Already queued by someone — pressing Download again still claims it
            # for this person's library.
            self.want_in_library(item["playlist_id"])
            self.json({"ok": True, "already": True, "playlist_id": item["playlist_id"]})
            return
        try:
            result = queue.ingest(item["source_url"])
        except Exception as exc:                       # noqa: BLE001
            store.feed_mark(item_id, outcome="failed")
            raise ValueError(str(exc))
        store.feed_mark(item_id, decision="queued",
                        outcome="running" if result.get("queued") else "complete",
                        playlist_id=result.get("playlist_id"))
        self.want_in_library(result.get("playlist_id"))
        self.json({"ok": True, **result})

    def do_track_queue(self) -> None:
        """Download one song from an opened album, not the whole release.

        The album stays in the Feed afterwards: only the download button on the
        release row takes it out of the list.
        """
        body = self.body()
        url = str(body.get("url") or "").strip()
        if not url:
            raise ValueError("nothing to download")
        _, queue = boot()
        result = queue.ingest(url)
        self.want_in_library(result.get("playlist_id"))
        self.json({"ok": True, **result})

    def want_in_library(self, playlist_id) -> None:
        """A download from the Feed joins the library of whoever pressed it, once
        the file is there — not before, or the library lists songs that cannot play."""
        member = getattr(self, "member", None)
        if not member or not playlist_id:
            return
        import listening
        try:
            listening.want_playlist(boot()[0], member["id"], int(playlist_id))
        except Exception:                              # noqa: BLE001
            # A library row is a convenience; a failure here must not fail the
            # download the person just asked for.
            pass

    def do_feed_ignore(self, item_id: int) -> None:
        store, _ = boot()
        store.feed_mark(item_id, decision="ignored")
        self.json({"ok": True})

    def do_feed_seen(self, item_id: int) -> None:
        store, _ = boot()
        item = store.feed_item(item_id)
        if item and item["decision"] == "new":
            store.feed_mark(item_id, decision="seen")
        self.json({"ok": True})

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
        if not SECURITY.required:
            return True
        return SECURITY.may_delete(store, self.member, kind, target_id)

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
        report = deletion.apply(store, "playlist", pid, delete_files=delete_files)
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
        report = deletion.apply(store, "release", rid, delete_files=delete_files)
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
        self.member = SECURITY.authenticate(self, store)
        SECURITY.admin(self.member)
        SECURITY.csrf(self)
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
        if not SECURITY.required or not self.member:
            return
        for track_id in result.get("track_ids") or []:
            SECURITY.claim(store, "track", track_id, self.member["id"])
        for playlist_id in result.get("playlist_ids") or []:
            SECURITY.claim(store, "playlist", playlist_id, self.member["id"])

    def do_library_move(self) -> None:
        raise Denied(403, "Move library is an administrator-only action")
    def do_settings(self) -> None:
        store, _ = boot()
        self.json({"settings": store.set_settings(self.body())})


def shutdown(httpd: ThreadingHTTPServer) -> None:
    """Stop in the order the systemd unit and Docker both depend on.

    HTTP first, so no new request can touch a half-closed database; then the scheduler, which
    would otherwise start a follow check mid-shutdown; then the queue worker, which is given a
    short grace period and otherwise hands its job back to the queue; then SQLite. A bare kill
    skips all of this, which is why the handler exists at all.
    """
    httpd.shutdown()
    httpd.server_close()
    if SCHEDULER is not None:
        SCHEDULER.stop()
        SCHEDULER.join(5)
    if QUEUE is not None:
        QUEUE.stop(grace=5.0)
    if STORE is not None:
        STORE.close()


def main(argv: list[str] | None = None) -> int:
    """Serve until a signal arrives.

    `--host`/`--port` beat the environment, which beats today's defaults, so a unit file can
    set both and a one-off run can still override either.
    """
    try:
        default_host, default_port = config.host(), config.port()
    except ValueError as exc:
        print(f"config: {exc}", flush=True)
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--host", default=default_host)
    args = parser.parse_args(argv)

    boot()      # fail fast if the DB or yt-dlp is broken
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"HardlyListening on http://{args.host}:{args.port}  (db: {db.DB_PATH})",
          flush=True)

    # `serve_forever` blocks, and `shutdown()` must be called from another thread, so the
    # signal handler's only job is to set a flag and the main thread does the stopping.
    stop = threading.Event()

    def received(signum, _frame):
        print(f"\n{signal.Signals(signum).name}: shutting down", flush=True)
        stop.set()

    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), received)

    serving = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    serving.start()
    try:
        while not stop.wait(0.5):
            pass
    finally:
        shutdown(httpd)
        print("bye", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
