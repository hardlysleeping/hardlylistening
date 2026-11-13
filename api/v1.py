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


# -- the table ------------------------------------------------------------------------------------

ROUTES = (
    ('GET', r'/api/v1/repo/snapshot', repo_snapshot),
    ('GET', r'/api/v1/repo/releases', repo_releases),
    ('GET', r'/api/v1/repo/tracks', repo_tracks),
    ('GET', r'/api/v1/repo/artist', repo_artist),
    ('GET', r'/api/v1/repo/subscriptions', repo_subscriptions),
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