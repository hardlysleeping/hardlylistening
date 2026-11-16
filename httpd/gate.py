"""The authorization boundary: who may reach which route at all.

Two kinds of decision live here, and they are deliberately not spread through the routes. The
first is *transport*: party and share controllers, the anonymous artwork capability URL, the
Subsonic surface, the capability document, and the versioned API each have their own entry point
and their own idea of what a session is. The second is *role*: an acquire route is gated by a
grant rather than by membership, a member-scoped API requires a session, and an administrator
route is checked here before the adapter ever runs.

A `boundary()` that returns False means the request has already been answered. Everything that
returns True is a route the rest of the handler may serve.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import db
from security import Denied
from .runtime import boot, features, security

ACQUIRE_ROUTES = re.compile(
    r'^/api/(add|library/grab|failed/retry|feed/refresh-all|track/queue'
    r'|track/\d+/(retry|source|unpick)|playlist/\d+/(requeue|verify)|feed/\d+/queue'
    r'|library/verify|sonic/analyse)$')


class Gate:
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
                party.dispatch(self, boot()[0], security())
                return False
            # Shares are member-only. Guests hold just the party cookie and are
            # refused; the capability URL itself is served publicly by do_GET.
            if path == '/api/shares' or path.startswith('/api/shares/'):
                import shares
                shares.dispatch(self, boot()[0], security())
                return False
            # A cover is a capability URL: the digest is the secret, so the one route a head
            # unit's artwork fetch needs is anonymous. Public for the same reason `/s/<token>`
            # is: iOS fetches media-session artwork itself, outside the page's cookies.
            art = re.fullmatch(r'/api/art/([a-f0-9]{32})', path)
            if art:
                if self.command != 'GET':
                    raise Denied(405, 'Method not allowed')
                self.art_image(art.group(1))
                return False
            # Playback telemetry: what the page saw when a song stopped, so a drive leaves
            # evidence instead of an anecdote. Member-scoped, capped, and never a URL.
            if path == '/api/playback/events':
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if self.command != 'POST':
                    raise Denied(405, 'Method not allowed')
                security().limit(self.member['id'], 'playback', 60)
                self.record_playback_event()
                return False
            # Temporary party credentials NEVER enable the legacy local bypass.
            # A real independent browser membership may still use its own scope.
            if security().cookie(self, security().cookie_name(self, '__Host-hl_party')) and path not in ('/login', '/auth/link', '/auth/google', '/auth/google/callback', '/signup', '/api/health', '/api/auth/session', '/api/auth/logout', '/api/auth/signup', '/api/auth/signup/begin', '/api/auth/signup/password', '/api/auth/login/password', '/api/auth/providers'):
                security().authenticate(self, boot()[0])
            # Device auth is independent of browser/email login, never the local bypass.
            if path.startswith('/rest/'):
                import listening
                if not features().get('subsonic', True):
                    # The Subsonic error envelope, not a bare 404: a client that speaks the
                    # protocol should get an answer in the protocol's own shape.
                    listening.envelope(self, {},
                                       error=(0, 'OpenSubsonic is disabled on this server'))
                    return False
                listening.rest(self, boot()[0], security())
                return False
            if path == '/api/playlists' or path.startswith('/api/playlists/'):
                import user_playlists
                user_playlists.dispatch(self, boot()[0], security())
                return False
            if path == '/api/storage' or path in ('/api/storage/migrate',
                                                 '/api/storage/restore'):
                # What the bucket settings say, whether they can actually be used, and the two
                # actions an administrator owns: queue the library that already exists, and put
                # one object back on disk. The credential is never here, only whether one exists.
                from library import storage, uploads
                store, _ = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                if path == '/api/storage':
                    if self.command != 'GET':
                        raise Denied(405, 'Method not allowed')
                    view = storage.public(store)
                    try:
                        storage.build(store)
                        view['ready'] = True
                    except storage.StorageError as error:
                        # A refused configuration is answered, not hidden: the reason is the point.
                        view['ready'] = False
                        view['reason'] = str(error)
                    view['uploads'] = uploads.summary(store)
                    view['objects'] = uploads.objects(store)
                    view['migrations'] = uploads.migrations(store)
                    view['cache'] = storage.cache_report(store)
                    self.json(view)
                    return False
                if self.command != 'POST':
                    raise Denied(405, 'Method not allowed')
                security().csrf(self)
                payload = self.body()
                if path == '/api/storage/migrate':
                    security().limit(self.member['id'], 'storage-migrate', 3)
                    try:
                        result = uploads.migrate(store, by=self.member['id'])
                    except (ValueError, storage.StorageError) as error:
                        raise Denied(400, str(error))
                    self.json({'ok': True, **result})
                    return False
                key = str(payload.get('key') or '').strip()
                if not key and payload.get('track_id'):
                    try:
                        track_id = int(payload['track_id'])
                    except (TypeError, ValueError):
                        raise Denied(400, 'track_id must be a number')
                    track = store.track_by_id(track_id)
                    if not track or not track.get('file'):
                        raise Denied(400, 'that track has no stored file')
                    try:
                        key = storage.key_for(store, track['file'])
                    except storage.StorageError as error:
                        raise Denied(400, str(error))
                if not key:
                    raise Denied(400, 'key or track_id is required')
                try:
                    self.json({'ok': True, 'restored': uploads.restore(store, key)})
                except storage.StorageError as error:
                    raise Denied(400, str(error))
                return False
            if path == '/api/artist/art/candidates':
                store, _ = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                if self.command != 'GET':
                    raise Denied(405, 'Method not allowed')
                name = (parse_qs(urlparse(self.path).query).get('name') or [''])[0].strip()
                if not name:
                    raise Denied(400, 'name is required')
                from library import artist_art
                self.json(artist_art.candidates(store, name))
                return False
            candidates_route = re.fullmatch(r'/api/track/(\d+)/candidates', path)
            if candidates_route:
                # "Wrong song?" — the options a person needs for a track that already
                # downloaded: what else the sources have, and whether the previous file is
                # still held. Read-only; the change itself stays on /api/track/<id>/source.
                import search, replacements
                store, _ = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                if self.command != 'GET':
                    raise Denied(405, 'Method not allowed')
                security().limit(self.member['id'], 'songsearch', 30)
                track_id = int(candidates_route.group(1))
                track = store.track_by_id(track_id) or {}
                if not track:
                    raise Denied(404, 'No such track')
                job = store.job_for_track(track_id) or {}
                if not job:
                    raise Denied(400, 'that track has no download job')
                asked = (parse_qs(urlparse(self.path).query).get('q') or [''])[0].strip()
                query = asked or ' '.join(p for p in (track.get('artist_name'), track.get('title')) if p).strip()
                try:
                    results = search.find(query, limit=25) if query else []
                except ValueError as exc:
                    raise Denied(400, str(exc))
                latest = replacements.latest(store, track_id)
                self.json({
                    'track': {
                        'id': track_id, 'title': track.get('title') or '',
                        'artist': track.get('artist_name') or '',
                        'album': track.get('album') or '',
                        'duration': track.get('duration') or 0,
                        'provider': track.get('provider') or '',
                        'file': bool(track.get('file')),
                        'source_url': track.get('source_url') or '',
                        'resolved_url': job.get('resolved_url') or '',
                        'pinned': bool(job.get('pinned')),
                        'status': job.get('status') or '',
                    },
                    'query': query,
                    'results': results,
                    'replacement': None if not latest else {
                        'id': latest['id'], 'status': latest['status'],
                        'created': latest['created'], 'prior_url': latest['old_url'],
                        'new_url': latest['new_url'], 'error': latest.get('error') or '',
                    },
                })
                return False
            if path == '/api/stats' or path.startswith('/api/stats/'):
                # Listening numbers for one member, read-only. Own by default; the
                # `/<member_id>` form is another member's, and the app has no private
                # profiles, so nothing is hidden.
                import stats
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if self.command != 'GET':
                    raise Denied(405, 'Method not allowed')
                query = parse_qs(urlparse(self.path).query)
                target = (query.get('member') or [''])[0] if path == '/api/stats' \
                    else path.rsplit('/', 1)[-1]
                if path != '/api/stats' and (not target or len(target) > 64):
                    raise Denied(404, 'Unknown member')
                if target and target != self.member['id']:
                    with store.lock:
                        exists = store.conn.execute(
                            'SELECT 1 FROM members WHERE id=?', (target,)).fetchone()
                    if not exists:
                        raise Denied(404, 'Unknown member')
                period = (query.get('period') or ['week'])[0]
                year = (query.get('year') or [''])[0]
                try:
                    summary = stats.summary(
                        store, target or self.member['id'], period,
                        year=int(year) if year.isdigit() else None)
                except ValueError as error:
                    raise Denied(400, str(error))
                self.json(summary)
                return False
            if path.startswith('/api/listening/'):
                import listening
                listening.browser(self, boot()[0], security())
                return False
            if path.startswith('/api/decisions'):
                import decisions
                store, _ = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                if self.command == 'GET' and path == '/api/decisions':
                    self.json({'entries': decisions.entries(store)})
                elif self.command == 'POST' and path == '/api/decisions/clear':
                    security().csrf(self)
                    decisions.clear(store, self.body().get('id'))
                    self.json({'ok': True})
                elif self.command == 'POST' and path == '/api/decisions/reverse':
                    security().csrf(self)
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
                swipe.dispatch(self, boot()[0], security())
                return False
            if path == '/api/search':
                # A member searches and downloads a single song the same way they may add
                # a playlist; the hit is pinned so the queue fetches that exact upload.
                store, queue = boot()
                self.member = security().authenticate(self, store)
                import search
                if self.command == 'GET':
                    security().limit(self.member['id'], 'songsearch', 30)
                    query = (parse_qs(urlparse(self.path).query).get('q') or [''])[0]
                    try:
                        self.json({'results': search.find(query)})
                    except ValueError as exc:
                        raise Denied(400, str(exc))
                elif self.command == 'POST':
                    security().csrf(self)
                    security().limit(self.member['id'], 'songsearch-add', 20)
                    try:
                        self.json(search.queue_hit(store, queue, self.body()))
                    except (ValueError, TypeError) as exc:
                        raise Denied(400, str(exc))
                else:
                    raise Denied(405, 'Method not allowed')
                return False
            if path == '/api/genres' or path.startswith('/api/genres/'):
                import genres
                genres.dispatch(self, boot()[0], security(), path)
                return False
            lyric_route = re.fullmatch(r'/api/track/(\d+)/lyrics', path)
            if lyric_route:
                import lyrics
                store, _ = boot()
                self.member = security().authenticate(self, store)
                track_id = int(lyric_route.group(1))
                if self.command == 'GET':
                    security().limit(self.member['id'], 'lyrics', 30)
                    result = lyrics.get(store, track_id)
                elif self.command == 'POST':
                    # A member's correction, not an acquisition: it is text about a track they
                    # can already play, stored against the track so a provider refresh cannot
                    # undo it. `reset` drops it again. The response is the shape a GET answers
                    # with, so the client repaints from either.
                    security().limit(self.member['id'], 'lyrics-edit', 30)
                    body = self.body()
                    if body.get('reset'):
                        result = lyrics.clear_edit(store, track_id)
                    else:
                        try:
                            result = lyrics.save_edit(store, track_id, body.get('text'), self.member)
                        except (ValueError, TypeError) as exc:
                            raise Denied(400, str(exc))
                else:
                    raise Denied(405, 'Method not allowed')
                if result is None:
                    raise Denied(404, 'No such track')
                self.json(result)
                return False
            if path == '/api/library/usb/plan' or path == '/api/library/usb/start':
                # Preparing a removable drive (HL-228). Reading the plan writes nothing, so
                # a person can ask "does this fit?" without touching the stick; starting is
                # the write, and it is a queued job rather than a request that sits there
                # copying a set. Same permission as the DJ export itself: any approved
                # member handing the shared library to a device they brought.
                from library import usb_export
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if self.command != 'POST':
                    raise Denied(405, 'Method not allowed')
                security().csrf(self)
                security().limit(self.member['id'], 'usb-export', 10)
                body = self.body()
                destination = str(body.get('destination') or '')
                raw_playlist = body.get('playlist')
                try:
                    playlist_id = int(raw_playlist) if raw_playlist not in (None, '', 0) else None
                    if path == '/api/library/usb/plan':
                        result = usb_export.plan(store, self.member, destination, playlist_id)
                    else:
                        result = usb_export.start(store, self.member, destination, playlist_id)
                except (ValueError, TypeError) as exc:
                    raise Denied(400, str(exc))
                self.json(result)
                return False
            if path == '/api/library/usb':
                from library import usb_export
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if self.command != 'GET':
                    raise Denied(405, 'Method not allowed')
                self.json(usb_export.status(store))
                return False
            if path == '/api/library/move':
                store, queue = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                security().csrf(self)
                target = (self.body().get('to') or '').strip()
                if not target:
                    raise Denied(400, 'give the new library folder')
                from library import import_files
                was_paused = queue.paused if queue else False
                if queue:
                    queue.pause(True)
                try:
                    result = import_files.move_library(store, target)
                except ValueError as exc:
                    raise Denied(400, str(exc))
                finally:
                    if queue:
                        queue.pause(was_paused)
                self.json(result)
                return False
            if path == '/api/profile':
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if self.command == 'GET':
                    # The account row rides with the profile: the dialog shows one panel, and the
                    # email is safe metadata (no hashes, salts or tokens ever leave here).
                    import oauth_google
                    self.json({**security().account(store, self.member),
                               # Whether the button belongs on the panel at all.
                               'google_available': oauth_google.available(),
                               **{k: self.member[k] for k in ('display_name', 'profile_icon')}})
                elif self.command == 'POST':
                    self.json(security().profile(store, self.member, self.body()))
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
                if path == '/login' and security().required:
                    try:
                        self.member = security().authenticate(self, boot()[0])
                    except Denied:
                        pass
                    else:
                        self.redirect('/')
                        return False
                return True     # health is a liveness ping, not data
            if path == '/api/auth/providers':
                # What this deployment can offer. A boolean is not a secret, and the
                # login page has to render before anybody holds a session.
                import oauth_google
                self.json({'google': oauth_google.available()})
                return False
            if path in ('/auth/google', '/auth/google/callback'):
                self.google_auth(path)
                return False
            if path == '/api/v1/capabilities':
                # What this server actually offers, told without asking for a session: a
                # client (or a person reading curl) should not have to probe endpoints to
                # find out whether a feature exists. Facts only, derived from the modules
                # that implement them — this endpoint cannot claim more than the server does.
                import subsonic
                import oauth_google
                self.json({
                    'server': {'name': subsonic.SERVER_TYPE, 'version': db.VERSION,
                               'api': 'v1', 'requires_members': bool(security().required)},
                    'web_ui': {'enabled': bool(features().get('web_ui', True))},
                    'subsonic': {'enabled': bool(features().get('subsonic', True)),
                                 'version': subsonic.PROTOCOL_VERSION,
                                 'server_version': subsonic.SERVER_VERSION,
                                 'open_subsonic': True,
                                 'extensions': subsonic.EXTENSIONS,
                                 'transcode_targets': sorted(subsonic.TRANSCODE_TARGETS),
                                 'default_transcode': subsonic.DEFAULT_TRANSCODE},
                    'features': {
                        'accounts': {'password': True, 'login_link': True,
                                     'invite_only': True, 'google': oauth_google.available()},
                        'library': {'scope': 'member', 'bookmarks': True, 'ratings': True,
                                    'export_zip': True, 'shares': True, 'radio': True,
                                    'podcasts': True, 'lyrics': True},
                        'playback': {'range': True, 'transcode': True, 'hls': False},
                        'party': {'enabled': True, 'chat': True, 'jukebox': False},
                        'social': {'comments': True, 'votes': True, 'likes': True,
                                   'activity': True},
                        'discovery': {'search': True, 'sonic_analysis': False},
                    },
                })
                return False
            if path.startswith('/api/v1/'):
                # The versioned surface has its own table (`api/v1.py`) so a client can rely
                # on a shape without the unversioned routes being rewritten underneath it.
                # It authenticates per route: shared reads follow `/api/artist`, personal
                # reads follow `/api/listening`, and `capabilities` was answered above.
                import api.v1 as api_v1
                api_v1.dispatch(self, boot()[0], security())
                return False
            if self.command == 'POST' and path == '/api/auth/session':
                security().csrf(self)
                security().limit(security().client_ip(self), 'enrollment', 15)
                payload = self.body()
                sid = security().exchange(boot()[0], payload.get('invite'), payload.get('display_name'), payload.get('profile_icon'))
                self.signed_in(sid)
                return False
            # -- accounts ------------------------------------------------------
            # Signup and password login are the only paths that mint a session without one, the
            # same shape as the login-link exchange above.
            if path in ('/api/auth/signup', '/api/auth/signup/begin', '/api/auth/signup/password'):
                store, _ = boot()
                if path == '/api/auth/signup' and self.command == 'GET':
                    # Does this browser still hold a validated signup link?
                    self.json(security().signup_state(store, security().cookie(self, security().cookie_name(self, '__Host-hl_signup'))))
                    return False
                if self.command != 'POST':
                    raise Denied(405, 'Method not allowed')
                security().csrf(self)
                security().limit(security().client_ip(self), 'signup', 20)
                if path == '/api/auth/signup/begin':
                    self.signup_flow(security().open_signup(store, self.body().get('token')))
                    return False
                payload = self.body()
                sid = security().signup(
                    store, security().cookie(self, security().cookie_name(self, '__Host-hl_signup')),
                    payload.get('display_name'), payload.get('profile_icon'),
                    payload.get('email'), payload.get('password'))
                self.signed_in(sid, clear_signup=True)
                return False
            if self.command == 'POST' and path == '/api/auth/login/password':
                store, _ = boot()
                security().csrf(self)
                security().limit(security().client_ip(self), 'login', 60)
                payload = self.body()
                member = security().authenticate_password(store, payload.get('email'), payload.get('password'))
                self.signed_in(security().create_session(store, member['id'], 'password'))
                return False
            if path == '/api/account/google/unlink':
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if self.command != 'POST':
                    raise Denied(405, 'Method not allowed')
                security().csrf(self)
                security().limit(self.member['id'], 'unlink', 10)
                import oauth_google
                self.json({'ok': oauth_google.unlink(store, self.member['id'])})
                return False
            if path in ('/api/account', '/api/account/password'):
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if path == '/api/account' and self.command == 'GET':
                    self.json(security().account(store, self.member))
                elif path == '/api/account/password' and self.command == 'POST':
                    security().limit(self.member['id'], 'password-change', 10)
                    self.json(security().change_password(store, self.member, self.body()))
                else:
                    raise Denied(405, 'Method not allowed')
                return False
            if path == '/api/signup-invite':
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if self.command == 'GET':
                    # Active and when, never the link: the secret is shown once, when minted.
                    self.json(security().signup_invite_state(store, self.member))
                elif self.command == 'POST':
                    self.json(security().mint_member_signup(store, self.member))
                elif self.command == 'DELETE':
                    self.json({'ok': security().revoke_member_signup(store, self.member)})
                else:
                    raise Denied(405, 'Method not allowed')
                return False
            if self.command == 'POST' and path == '/api/admin/signup-invite':
                store, _ = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                token, expires = security().mint_signup(store, self.member['id'])
                self.json({'url': security().signup_url(token, self), 'expires_at': expires})
                return False
            if path.startswith('/api/members'):
                store, _ = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                if self.command == 'POST' and path != '/api/members':
                    payload = self.body()
                    if path == '/api/members/create':
                        uid = security().create_user(store, self.member, payload.get('display_name'), payload.get('role'))
                        self.json({'id': uid, 'invite': security().invite(store, self.member, uid, handler=self)})
                    elif path == '/api/members/invite':
                        self.json({'invite': security().invite(store, self.member, payload.get('id'), handler=self)})
                    elif path == '/api/members/revoke':
                        if payload.get('id') == self.member['id']:
                            raise Denied(400, 'Use Sign out to end your own session')
                        security().revoke(store, payload.get('id'))
                        self.json({'ok': True})
                    else:
                        raise Denied(404, 'Not found')
                    return False
            if self.command == 'POST' and path == '/api/auth/logout':
                self.body()
                store, _ = boot()
                security().csrf(self)
                security().schema(store)
                with store.lock:
                    store.conn.execute('DELETE FROM browser_sessions WHERE digest=?',
                                   (security().digest(security().cookie(
                                       self, security().cookie_name(self, '__Host-hl_session'))),))
                    store.conn.commit()
                self.send_response(200)
                self.send_header('Set-Cookie', f'{security().cookie_name(self, "__Host-hl_session")}=; '
                                 f'{security().cookie_attrs(self)}; Max-Age=0')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return False
            if path == '/api/mixer' or path.startswith('/api/mixer/'):
                # Shared mixer room. Slider drags are chatty, so this route gets its
                # own generous bucket instead of the ordinary 600/min.
                store, _ = boot()
                if security().required:
                    self.member = security().authenticate(self, store, requests_limit=1200)
                    if self.command == 'POST':
                        security().limit(self.member['id'], 'mixer', 900)
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
                self.member = security().authenticate(self, store)
                from social import service as social_service
                if self.command == 'GET':
                    self.json({'boost': social_service.current_boost(store, self.member['id'])})
                elif self.command == 'POST':
                    security().csrf(self)
                    security().limit(self.member['id'], 'boost', 30)
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
                self.member = security().authenticate(self, store)
                if self.command == 'POST':
                    security().limit(self.member['id'], 'social', 30)
                from social import api as social_api
                social_api.dispatch(self, store)
                return False
            if not security().required:
                return True
            security().config(self)
            if path.startswith('/auth/') and self.command == 'GET':
                raise Denied(404, 'Not found')
            if path == '/api/acquisitions':
                # Who may start work. Read and write are administrator-only, and granting
                # is the only way in: the check itself never consults a role.
                import acquisitions
                store, _ = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                security().schema(store)
                if self.command == 'GET':
                    self.json({'grants': acquisitions.grants(store)})
                    return False
                if self.command != 'POST':
                    raise Denied(405, 'Method not allowed')
                security().csrf(self)
                payload = self.body()
                member_id = str(payload.get('member_id') or '').strip()
                if not member_id or len(member_id) > 64:
                    raise Denied(400, 'member_id is required')
                try:
                    if payload.get('grant', True):
                        changed = acquisitions.grant(store, member_id, self.member['id'])
                    else:
                        changed = acquisitions.revoke(store, member_id)
                except ValueError as error:
                    # A named member that does not exist is a request problem, not a crash:
                    # the handler answers with the reason instead of dropping the connection.
                    raise Denied(400, str(error))
                self.json({'ok': True, 'changed': changed,
                           'grants': acquisitions.grants(store)})
                return False
            if path == '/api/extensions' or path.startswith('/api/extensions/'):
                # The opt-in host's administrator surface: what is on disk, what is installed,
                # what is trusted, and what a run asked for. Installation reads a directory the
                # administrator already placed under `paths.EXTENSIONS_DIR`; code is never
                # accepted over HTTP, because this runs as the server's own user.
                import extensions
                import extension_host
                store, _ = boot()
                self.member = security().authenticate(self, store)
                security().admin(self.member)
                try:
                    if self.command == 'GET':
                        if path == '/api/extensions':
                            self.json({'extensions': extensions.installed(store),
                                       'on_disk': extension_host.on_disk(),
                                       'directory': str(extension_host.EXTENSIONS_DIR),
                                       'runs': extension_host.runs(store, limit=10)})
                            return False
                        found = re.fullmatch(r'/api/extensions/([^/]+)/calls', path)
                        if not found:
                            raise Denied(404, 'Not found')
                        self.json({'calls': extension_host.calls(store, found.group(1)),
                                   'runs': extension_host.runs(store, found.group(1))})
                        return False
                    if self.command != 'POST':
                        raise Denied(405, 'Method not allowed')
                    security().csrf(self)
                    payload = self.body()
                    if path == '/api/extensions/install':
                        ext_id = str(payload.get('id') or '').strip()
                        extension_host.install_from_disk(store, ext_id, by=self.member['id'])
                        # The answer is the record the list would show, so an administrator who
                        # installed it can see that it is installed and *not* enabled.
                        record = next(item for item in extensions.installed(store)
                                      if item['id'] == ext_id)
                        self.json({'ok': True, 'extension': record})
                        return False
                    found = re.fullmatch(
                        r'/api/extensions/([^/]+)/(enable|revoke|remove|run)', path)
                    if not found:
                        raise Denied(404, 'Not found')
                    ext_id, action = found.group(1), found.group(2)
                    if action == 'enable':
                        self.json({'ok': True,
                                   'extension': extensions.enable(store, ext_id,
                                                                  self.member['id'])})
                    elif action == 'revoke':
                        extensions.revoke(store, ext_id)
                        self.json({'ok': True})
                    elif action == 'remove':
                        # The row goes; the directory stays. A POST does not delete files an
                        # administrator put here — reinstalling is the same directory again.
                        extensions.remove(store, ext_id)
                        self.json({'ok': True})
                    else:
                        security().limit(self.member['id'], 'extension-run', 10)
                        self.json(extension_host.run(store, ext_id, member=self.member))
                    return False
                except ValueError as error:
                    # A bad manifest, a missing entry file or an unknown id is a request problem,
                    # not a dropped connection.
                    raise Denied(400, str(error))
            if path.startswith('/api/'):
                store, _ = boot()
                self.member = security().authenticate(self, store)
                if self.command == 'POST':
                    security().schema(store)
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
                    if ACQUIRE_ROUTES.match(path):
                        # Everyone, administrators included. A role is not a grant.
                        import acquisitions
                        acquisitions.schema(store)
                        if not acquisitions.may_acquire(store, self.member):
                            raise Denied(
                                403,
                                'Acquisition is a separate permission on this server. '
                                'Ask an administrator to grant it.')
                    with store.lock:
                        store.conn.execute('INSERT INTO member_requests(member_id,path) VALUES(?,?)', (self.member['id'], path))
                        store.conn.commit()
            return True
        except Denied as exc:
            self.close_connection = True
            data = {'error': exc.message}
            headers = None
            if exc.retry_after is not None:
                data['retry_after'] = exc.retry_after
                headers = {'Retry-After': str(exc.retry_after)}
            self.json(data, exc.code, extra_headers=headers)
            return False
