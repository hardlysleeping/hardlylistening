"""The versioned JSON API: a promise in front of the domain services.

`/api/*` grew route by route. Its shapes are loose, the same word means two things
(`/api/library` is the shared pool, `/api/listening` is a member's own rows), and a client can
only learn either by reading `server.py`. This module is the surface that can be relied on:
`v1` names a contract, and every answer here is documented in `docs/api-v1.md`.

It is deliberately dull. A handler parses its arguments, calls one function on `repo.service`,
`library.service`, `library.playback`, `nowplaying` or `social.service`, and answers the
envelope below. No SQL, no domain rules, no second copy of an answer — a route that starts
deciding things has stopped being a version contract and become a second implementation.

    {"api": "v1", "resource": "<name>", "data": <what the service answered>, "count": <n>}

`count` is present only when `data` is a list. Errors keep the server's existing shape,
`{"error": "..."}`, because `Handler.denied` already owns that. The unversioned routes are
untouched; nothing here replaces them.
"""
import re
from urllib.parse import parse_qs, urlparse

from security import Denied


# -- envelope and arguments -----------------------------------------------------------------------

def envelope(handler, resource, data, count=None):
    body = {'api': 'v1', 'resource': resource, 'data': data}
    if count is not None:
        body['count'] = count
    handler.json(body)


def _integer(query, name, default, low=0, high=None):
    raw = (query.get(name) or [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise Denied(400, '%s must be an integer' % name)
    if value < low or (high is not None and value > high):
        raise Denied(400, '%s is out of range' % name)
    return value


def _text(query, name, default='', limit=200):
    value = (query.get(name) or [default])[0]
    if not isinstance(value, str) or len(value) > limit:
        raise Denied(400, '%s is invalid' % name)
    return value


def _reader(handler, store, security):
    """Shared reads: a session when the server has accounts, nobody when it does not.

    This is the same rule the unversioned Repo reads follow: in local mode `/api/artist` and
    `/api/catalogue` answer without a session, and in hosted mode the boundary has already
    required one. `handler.member` stays None in local mode, so ownership fields say
    "nobody" rather than being invented.
    """
    if not security.required:
        return None
    member = security.authenticate(handler, store)
    handler.member = member
    return member


def _member(handler, store, security):
    """Personal state always has an owner — the rule `/api/listening` and `/api/social` use.

    In local mode there is no account and therefore no personal row to read or write; that is
    an honest refusal, not an empty answer that looks like a lost library.
    """
    member = security.authenticate(handler, store)
    handler.member = member
    return member


# -- Repo: the shared catalogue -------------------------------------------------------------------

def repo_snapshot(handler, store, security, query):
    from repo import service as repo_service
    _reader(handler, store, security)
    snapshot = repo_service.snapshot(store)
    snapshot['subscriptions'] = repo_service.subscriptions(
        store, str((getattr(handler, 'member', None) or {}).get('id') or ''))
    snapshot.pop('follows', None)
    envelope(handler, 'repo.snapshot', snapshot)


def repo_releases(handler, store, security, query):
    from repo import service as repo_service
    _reader(handler, store, security)
    watch = _integer(query, 'watch', 0, low=1)
    offset = _integer(query, 'offset', 0)
    limit = _integer(query, 'limit', 60, low=1, high=200)
    try:
        page = repo_service.releases(store, watch, offset=offset, limit=limit)
    except ValueError as exc:
        raise Denied(400, str(exc))
    envelope(handler, 'repo.releases', page)


def repo_tracks(handler, store, security, query):
    from repo import service as repo_service
    _reader(handler, store, security)
    url = _text(query, 'url').strip()
    if not url:
        raise Denied(400, 'url is required')
    try:
        tracks = repo_service.tracks(url)
    except ValueError as exc:
        raise Denied(400, str(exc))
    envelope(handler, 'repo.tracks', tracks, len(tracks))


def repo_artist(handler, store, security, query):
    from repo import service as repo_service
    _reader(handler, store, security)
    name = _text(query, 'name').strip()
    if not name:
        raise Denied(400, 'name is required')
    try:
        page = repo_service.artist(store, name)
    except ValueError as exc:
        raise Denied(400, str(exc))
    envelope(handler, 'repo.artist', page)


def repo_subscriptions(handler, store, security, query):
    from repo import service as repo_service
    _reader(handler, store, security)
    rows = repo_service.subscriptions(
        store, str((getattr(handler, 'member', None) or {}).get('id') or ''))
    envelope(handler, 'repo.subscriptions', rows, len(rows))


# -- Library: the member's own rows ---------------------------------------------------------------

def library_songs(handler, store, security, query):
    from library import service
    member = _member(handler, store, security)
    rows = service.catalog(store, member['id'])
    envelope(handler, 'library.songs', rows, len(rows))


def library_albums(handler, store, security, query):
    from library import service
    member = _member(handler, store, security)
    rows = service.albums(store, member['id'])
    envelope(handler, 'library.albums', rows, len(rows))


def library_artists(handler, store, security, query):
    from library import service
    member = _member(handler, store, security)
    rows = service.artists(store, member['id'])
    envelope(handler, 'library.artists', rows, len(rows))


def library_playlists(handler, store, security, query):
    from library import service
    member = _member(handler, store, security)
    rows = service.playlists(store, member['id'])
    envelope(handler, 'library.playlists', rows, len(rows))


def library_playlist(handler, store, security, query, playlist_id):
    from library import service
    member = _member(handler, store, security)
    row = service.playlist(store, member['id'], int(playlist_id))
    if row is None:
        raise Denied(404, 'No such saved playlist')
    envelope(handler, 'library.playlist', row)


def library_favorites(handler, store, security, query):
    from library import service
    member = _member(handler, store, security)
    rows = service.favorites(store, member['id'])
    envelope(handler, 'library.favorites', rows, len(rows))


def library_bookmarks(handler, store, security, query):
    from library import service
    member = _member(handler, store, security)
    rows = service.bookmark_rows(store, member['id'])
    envelope(handler, 'library.bookmarks', rows, len(rows))


# -- Playback: where the member is, and where the bytes are ---------------------------------------

def playback_state(handler, store, security, query):
    import nowplaying
    member = _member(handler, store, security)
    envelope(handler, 'playback.state', nowplaying.get(store, member['id']))


def playback_save(handler, store, security, query):
    import nowplaying
    member = _member(handler, store, security)
    security.limit(member['id'], 'now-write', 120)
    envelope(handler, 'playback.state', nowplaying.put(store, member['id'], handler.body()))


def playback_queue(handler, store, security, query):
    import nowplaying
    member = _member(handler, store, security)
    state = nowplaying.get(store, member['id'])
    queue = None if not state else {
        'track_ids': state.get('queue') or [],
        'current': state.get('id'),
        'index': state.get('index', -1),
        'position': state.get('time') or 0,
        'updated': state.get('updated'),
    }
    envelope(handler, 'playback.queue', queue)


def playback_now(handler, store, security, query):
    import nowplaying
    member = _member(handler, store, security)
    state = nowplaying.get(store, member['id'])
    now = None if not state else {
        'track_id': state.get('id'),
        'position': state.get('time') or 0,
        'updated': state.get('updated'),
    }
    envelope(handler, 'playback.now', now)


def playback_stream(handler, store, security, query, track_id):
    from library import playback
    _member(handler, store, security)
    track_id = int(track_id)
    envelope(handler, 'playback.stream', {
        'track_id': track_id,
        'url': '/api/track/%d/audio' % track_id,
        'available': playback.available(store, track_id),
    })


# -- Social: what people are doing ----------------------------------------------------------------

def social_activity(handler, store, security, query):
    from social import service
    _reader(handler, store, security)
    limit = _integer(query, 'limit', 80, low=1, high=200)
    rows = service.feed(store, limit)
    envelope(handler, 'social.activity', rows, len(rows))


def social_thread(handler, store, security, query):
    from social import service
    member = _member(handler, store, security)
    kind = _text(query, 'kind', limit=20)
    target = _text(query, 'target', limit=64)
    after = _integer(query, 'after', 0)
    page = service.comments(store, kind, target, member, after)
    envelope(handler, 'social.comments', page)


def social_summary(handler, store, security, query):
    from social import service
    member = _member(handler, store, security)
    kind = _text(query, 'kind', limit=20)
    targets = _text(query, 'targets', limit=4096)
    if targets:
        rows = service.batch(store, kind, [t for t in targets.split(',') if t], member)
        envelope(handler, 'social.summary', rows, len(rows))
        return
    target = _text(query, 'target', limit=64)
    envelope(handler, 'social.summary', service.summary(store, kind, target, member))


# -- the table ------------------------------------------------------------------------------------

ROUTES = (
    ('GET', r'/api/v1/repo/snapshot', repo_snapshot),
    ('GET', r'/api/v1/repo/releases', repo_releases),
    ('GET', r'/api/v1/repo/tracks', repo_tracks),
    ('GET', r'/api/v1/repo/artist', repo_artist),
    ('GET', r'/api/v1/repo/subscriptions', repo_subscriptions),
    ('GET', r'/api/v1/library/songs', library_songs),
    ('GET', r'/api/v1/library/albums', library_albums),
    ('GET', r'/api/v1/library/artists', library_artists),
    ('GET', r'/api/v1/library/playlists', library_playlists),
    ('GET', r'/api/v1/library/playlist/([1-9][0-9]*)', library_playlist),
    ('GET', r'/api/v1/library/favorites', library_favorites),
    ('GET', r'/api/v1/library/bookmarks', library_bookmarks),
    ('GET', r'/api/v1/playback/state', playback_state),
    ('POST', r'/api/v1/playback/state', playback_save),
    ('GET', r'/api/v1/playback/queue', playback_queue),
    ('GET', r'/api/v1/playback/now', playback_now),
    ('GET', r'/api/v1/playback/stream/([1-9][0-9]*)', playback_stream),
    ('GET', r'/api/v1/social/activity', social_activity),
    ('GET', r'/api/v1/social/comments', social_thread),
    ('GET', r'/api/v1/social/summary', social_summary),
)


def dispatch(handler, store, security):
    """One entry point for `/api/v1/*`; `capabilities` is answered earlier and publicly."""
    path = urlparse(handler.path).path
    query = parse_qs(urlparse(handler.path).query)
    allowed = set()
    for method, pattern, action in ROUTES:
        match = re.fullmatch(pattern, path)
        if not match:
            continue
        allowed.add(method)
        if handler.command == method:
            action(handler, store, security, query, *match.groups())
            return
    if allowed:
        raise Denied(405, 'Method not allowed; this endpoint answers ' + ', '.join(sorted(allowed)))
    raise Denied(404, 'Unknown /api/v1 endpoint')