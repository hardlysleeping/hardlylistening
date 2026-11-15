"""Repo for the client SDK: the shared catalogue — what a server follows, and what that artist
has published.

`/api/v1/repo/*` is this server's Repository (acquisition) surface: `api/v1.py` registers five
reads and nothing else — `snapshot`, `releases`, `tracks`, `artist`, `subscriptions` — so those
are the five calls here, each taking `data` out of the `{"api": "v1", …}` envelope. Following an
artist, deciding what is downloadable, who may see a release and what a subscription entitles a
member to are `repo/service.py`'s answers, carried through unchanged. A client that filtered a
release list or second-guessed ownership would be a second implementation of acquisition, and
this module is not one.

**This module is the one the player must never import.** The player is a streaming client; a
store build that could acquire is the thing PLAYER-007 exists to prevent. The rule is asserted
in `tests/test_player_store_safe.py`, in three forms: the assembled files may name none of the
five routes (derived from `api/v1.py`'s `ROUTES` table, not remembered here) nor this module; the
same holds for every file reachable from the player's entry documents; and the build refuses this
file outright when asked to include it. What that cannot prove is stated there and worth
repeating: a player script could rebuild a route by concatenation or fetch a `.js` by string at
runtime, and an edit to `player/assemble.mjs` itself is outside the assertion. What it does prove
is that no file the player serves, and no file those make it load, carries acquisition.

Whether any of it may be called is `/api/v1/capabilities`' question, and `sdk.capabilities` is
where it is answered. The document this server publishes names `accounts`, `library`, `playback`,
`party`, `social` and `discovery` (`search`, `sonic_analysis`) — and **no `repo` domain**, and
no acquisition flag anywhere. So a call is gated on the `repo` domain alone, exactly as
`sdk.social` gates a boost on `social` alone: there is no finer flag to ask for, and inventing
`repo.snapshot` in a client would be guessing at a contract the server never wrote. Against this
server every entry point therefore refuses by name, before the wire — the routes answer over
HTTP, but the capability document has not claimed them, and the client does not translate
`discovery`'s vocabulary into acquisition.

Reads are shared reads (`api/v1.py:_reader`): in hosted mode the request needs the member's
session, which is the `Session` value `sdk.social` already defines — reused here rather than
defined twice. It travels in a header only, and no message this module raises carries it.

Deliberately absent: every write (`subscribe`, `check`, `set_enabled`, `remove`, `release_seat`)
and the unversioned `/api/artist`, `/api/catalogue`, `/api/feed` routes. They belong to the cards
that own them, not to this one.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .capabilities import Capabilities
from .session import Session

#: The five versioned reads (`api/v1.py`, its `ROUTES` table).
SNAPSHOT_PATH = '/api/v1/repo/snapshot'
RELEASES_PATH = '/api/v1/repo/releases'
TRACKS_PATH = '/api/v1/repo/tracks'
ARTIST_PATH = '/api/v1/repo/artist'
SUBSCRIPTIONS_PATH = '/api/v1/repo/subscriptions'
API_VERSION = 'v1'
DEFAULT_TIMEOUT = 15


class RepoError(Exception):
    """The call did not produce an answer: a wire failure, no envelope, or a refusal."""


class ClientError(RepoError):
    """No usable answer at all: a bad status with no `{"error"}` body, HTML, or not a v1
    envelope.

    This is a broken server or proxy, not a decision about the request, so it carries no status
    and nothing here is retried.
    """


class Unavailable(RepoError):
    """The capability document names no `repo` domain. Do not retry, and offer no acquisition.

    `needed` is the domain that was missing (`repo`), so a caller can log the switch it should
    have read before asking, and hide the control that offered the call.
    """

    def __init__(self, message, needed=''):
        self.needed = str(needed or '')
        super().__init__(str(message or ''))


class Denied(RepoError):
    """The server answered `{"error": "..."}` with an HTTP status: it declined, and said why.

    `message` is the server's own sentence, `status` the code it came with, and `retry_after`
    the seconds it asked for when the bucket is spent.
    """

    def __init__(self, message, status=0, retry_after=0):
        self.message = str(message or '')
        self.status = int(status or 0)
        self.retry_after = int(retry_after or 0)
        super().__init__(f'{self.status}: {self.message}')


class SignInRequired(Denied):
    """401 — no session, or one that has expired. Sign in and ask again."""


class NotFound(Denied):
    """404 — no such follow, release, album page or route."""


class RateLimited(Denied):
    """429 — this caller's bucket for the minute is spent; `retry_after` says for how long."""


#: The statuses whose meaning a caller branches on. Everything else is a plain `Denied`, because
#: a 400 is a caller's mistake and a 405 is a client that asked the wrong method.
ERROR_STATUS = {401: SignInRequired, 404: NotFound, 429: RateLimited}


def error_for(status, message, retry_after=0):
    """The exception an `{"error": …}` answer with this status deserves."""
    mapper = ERROR_STATUS.get(int(status or 0), Denied)
    return mapper(message or 'the server declined the request', status, retry_after)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    """The objects in one list, filtered — never None, never a crash on a surprise shape."""
    return tuple(row for row in value if isinstance(row, dict)) if isinstance(value, list) else ()


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _number(value):
    """A duration or a timestamp, which the store keeps in a REAL column."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _text(value):
    return str(value or '')


def _flag(value, default):
    """A row's 0/1 flag as a bool; an absent one means the row never said otherwise."""
    if value is None:
        return default
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return default


# -- rows ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Subscription:
    """One followed artist, as `repo.service.subscriptions` decorates it.

    `mine` is the server's answer, not this client's: a follow made before ownership existed
    belongs to nobody, and with no member id every row is theirs to see.
    """

    id: int = 0
    provider: str = ''
    artist_id: str = ''
    artist_name: str = ''
    artist_url: str = ''
    member_id: str = ''
    enabled: bool = True
    added: float = 0.0
    last_checked: float = 0.0
    interval: float = 0.0
    new_items: int = 0
    mine: bool = False
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(id=_int(row.get('id')), provider=_text(row.get('provider')),
                   artist_id=_text(row.get('artist_id')),
                   artist_name=_text(row.get('artist_name')),
                   artist_url=_text(row.get('artist_url')),
                   member_id=_text(row.get('member_id')),
                   enabled=_flag(row.get('enabled'), True),
                   added=_number(row.get('added')),
                   last_checked=_number(row.get('last_checked')),
                   interval=_number(row.get('interval')),
                   new_items=_int(row.get('new_items')),
                   mine=_flag(row.get('mine'), False), raw=dict(row))


@dataclass(frozen=True)
class Release:
    """One release a followed artist published, in the store's `artist_releases` columns."""

    provider: str = ''
    release_id: str = ''
    title: str = ''
    release_type: str = ''
    release_date: str = ''
    track_count: int = 0
    artwork: str = ''
    source_url: str = ''
    fetched: float = 0.0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(provider=_text(row.get('provider')),
                   release_id=_text(row.get('release_id')), title=_text(row.get('title')),
                   release_type=_text(row.get('release_type')),
                   release_date=_text(row.get('release_date')),
                   track_count=_int(row.get('track_count')),
                   artwork=_text(row.get('artwork')), source_url=_text(row.get('source_url')),
                   fetched=_number(row.get('fetched')), raw=dict(row))


@dataclass(frozen=True)
class Track:
    """One song in a followed artist's catalogue.

    Named `Track` here and not exported from `sdk/__init__`: `sdk.social.Track` is a different
    shape (a feed row's id/title/artist), and one export name cannot carry two.
    """

    provider: str = ''
    track_id: str = ''
    release_id: str = ''
    title: str = ''
    artist: str = ''
    album: str = ''
    track_no: int = 0
    duration: float = 0.0
    artwork: str = ''
    source_url: str = ''
    fetched: float = 0.0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(provider=_text(row.get('provider')), track_id=_text(row.get('track_id')),
                   release_id=_text(row.get('release_id')), title=_text(row.get('title')),
                   artist=_text(row.get('artist')), album=_text(row.get('album')),
                   track_no=_int(row.get('track_no')),
                   duration=_number(row.get('duration')), artwork=_text(row.get('artwork')),
                   source_url=_text(row.get('source_url')),
                   fetched=_number(row.get('fetched')), raw=dict(row))


@dataclass(frozen=True)
class ReleasePage:
    """One page of a followed artist's page: every release, and `page` songs from `offset`.

    `track_total` and `fetched` are the server's numbers; `offset`/`page` echo the request it
    actually served.
    """

    artist: str = ''
    provider: str = ''
    releases: tuple = ()
    tracks: tuple = ()
    track_total: int = 0
    offset: int = 0
    page: int = 0
    fetched: float = 0.0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(artist=_text(row.get('artist')), provider=_text(row.get('provider')),
                   releases=tuple(Release.from_json(item) for item in _rows(row.get('releases'))),
                   tracks=tuple(Track.from_json(item) for item in _rows(row.get('tracks'))),
                   track_total=_int(row.get('track_total')), offset=_int(row.get('offset')),
                   page=_int(row.get('page')), fetched=_number(row.get('fetched')),
                   raw=dict(row))


@dataclass(frozen=True)
class Provider:
    """One provider identity an artist page knows: where the artist is, and their RSS feed."""

    provider: str = ''
    artist_id: str = ''
    url: str = ''
    rss: str = ''

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(provider=_text(row.get('provider')), artist_id=_text(row.get('artist_id')),
                   url=_text(row.get('url')), rss=_text(row.get('rss')))


@dataclass(frozen=True)
class FeedItem:
    """One release the Follow feed has seen for this artist, with the server's decision."""

    id: int = 0
    title: str = ''
    artist: str = ''
    release_type: str = ''
    release_date: str = ''
    source_url: str = ''
    artwork: str = ''
    first_seen: float = 0.0
    decision: str = ''
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(id=_int(row.get('id')), title=_text(row.get('title')),
                   artist=_text(row.get('artist')),
                   release_type=_text(row.get('release_type')),
                   release_date=_text(row.get('release_date')),
                   source_url=_text(row.get('source_url')), artwork=_text(row.get('artwork')),
                   first_seen=_number(row.get('first_seen')),
                   decision=_text(row.get('decision')), raw=dict(row))


@dataclass(frozen=True)
class ArtistPage:
    """One artist page: what the catalogue knows, and whether this server follows them.

    `tracks`/`downloaded` are the server's counts for the artist; `watches` are the follows the
    page matched, each already carrying `mine`.
    """

    name: str = ''
    providers: tuple = ()
    subscribed: bool = False
    watches: tuple = ()
    feed: tuple = ()
    tracks: int = 0
    downloaded: int = 0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(name=_text(row.get('name')),
                   providers=tuple(Provider.from_json(item) for item in _rows(row.get('providers'))),
                   subscribed=row.get('subscribed') is True,
                   watches=tuple(Subscription.from_json(item) for item in _rows(row.get('watches'))),
                   feed=tuple(FeedItem.from_json(item) for item in _rows(row.get('feed'))),
                   tracks=_int(row.get('tracks')), downloaded=_int(row.get('downloaded')),
                   raw=dict(row))


@dataclass(frozen=True)
class Snapshot:
    """The poll snapshot: the follows with ownership, the queue's numbers, the feed's counter.

    `stats`, `current`, `playlists`, `logs` and `settings` are carried as the store wrote them.
    Re-declaring the queue's own fields here would be a second definition of acquisition state,
    and the client's question is narrower: what is followed, what is moving, what is new.
    """

    subscriptions: tuple = ()
    stats: dict = field(default_factory=dict)
    current: dict | None = None
    feed_new: int = 0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        current = row.get('current')
        return cls(subscriptions=tuple(Subscription.from_json(item)
                                       for item in _rows(row.get('subscriptions'))),
                   stats=dict(_mapping(row.get('stats'))),
                   current=dict(current) if isinstance(current, dict) else None,
                   feed_new=_int(row.get('feed_new')), raw=dict(row))


# -- the client ----------------------------------------------------------------------------------

class RepoClient:
    """One server, one session. Calls are synchronous and nothing is cached."""

    def __init__(self, base_url, capabilities, session=None, *, timeout=DEFAULT_TIMEOUT,
                 opener=None):
        self.base_url = str(base_url).rstrip('/')
        self.capabilities = (capabilities if isinstance(capabilities, Capabilities)
                             else Capabilities.from_document(capabilities or {}))
        self.session = session if isinstance(session, Session) else Session()
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    # ---- capability gate -----------------------------------------------------

    def _require(self, what):
        """Refuse by name before the wire when the document names no `repo` domain.

        The gate is the domain alone, for the reason in the module docstring: the document has
        no acquisition flag anywhere, so there is no finer name to ask for and none is invented
        here. The reason quotes what the document does say — this server names `discovery`
        (`search`, `sonic_analysis`) — so a caller can see that `discovery` was not translated
        into acquisition.
        """
        if self.capabilities.feature('repo').available:
            return
        if self.capabilities.degraded:
            reason = ('the capability document could not be read: '
                      + (self.capabilities.reason or 'no reason given'))
        else:
            discovery = self.capabilities.features.discovery
            if discovery.available:
                flags = ', '.join(sorted(str(name) for name in discovery.details)) or 'no flags'
                reason = (f'its document names discovery ({flags}) and no repo domain, '
                          'so no acquisition was inferred from it')
            else:
                reason = 'its document names no repo domain'
        raise Unavailable(f'{what} is unavailable: this server does not advertise repo '
                          f'({reason})', 'repo')

    # ---- wire ----------------------------------------------------------------

    def _call(self, path, params, what):
        """One GET, one JSON object. An `{"error": …}` answer raises the typed refusal."""
        url = self.base_url + path
        if params:
            url += '?' + urlencode({key: str(value) for key, value in params.items()
                                    if value is not None})
        headers = {'Accept': 'application/json'}
        headers.update(self.session.header())
        request = urllib.request.Request(url, headers=headers)
        response = self._fetch(request, what)
        with response:
            raw = response.read()
        return self._document(raw, what)

    def _fetch(self, request, what):
        """The open response, or the typed error its status deserves.

        The status may enter a message; the headers never do, because one of them is the session
        cookie and an exception ends up in a log. The URL carries no credential at all — the
        session is a header on every route here.
        """
        try:
            return self._open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry_after = _int(exc.headers.get('Retry-After')) if exc.headers else 0
            try:
                raw = exc.read()
            except OSError:
                raw = b''
            finally:
                exc.close()
            document = self._salvage(raw)
            message = _text(document.get('error'))
            if not message:
                # A status with no refusal sentence is a broken server or a proxy, not a
                # decision about this request: there is nothing here a caller can act on.
                raise ClientError(f'{what}: HTTP {status}') from exc
            raise error_for(status, message,
                            _int(document.get('retry_after')) or retry_after) from exc
        except OSError as exc:
            raise ClientError(f'{what}: {exc}') from exc

    @staticmethod
    def _salvage(raw):
        """Best effort at an error envelope; a proxy's HTML simply has no message to keep."""
        try:
            document = json.loads(raw.decode('utf-8', 'replace') or '{}')
        except ValueError:
            return {}
        return document if isinstance(document, dict) else {}

    @staticmethod
    def _document(raw, what):
        """A 2xx body as a JSON object; anything else is a client error, not a refusal."""
        try:
            document = json.loads(raw.decode('utf-8') or '{}')
        except (ValueError, UnicodeError) as exc:
            raise ClientError(f'{what}: the answer was not JSON') from exc
        if not isinstance(document, dict):
            raise ClientError(f'{what}: the answer was not a JSON object')
        return document

    def _data(self, path, params, what):
        """The `data` of a v1 envelope, or a client error naming what was expected."""
        document = self._call(path, params, what)
        if document.get('api') != API_VERSION or 'data' not in document:
            raise ClientError(f'{what}: the answer is not a v1 envelope')
        return document['data']

    # ---- reads ---------------------------------------------------------------

    def snapshot(self):
        """Everything the UI polls for, in one read: follows, queue numbers, the feed counter."""
        self._require('the repository snapshot')
        return Snapshot.from_json(self._data(SNAPSHOT_PATH, None, 'the repository snapshot'))

    def releases(self, watch, offset=0, limit=60):
        """One followed artist's releases, and one page of their songs.

        `watch` is the subscription id. `limit` is the page size the server clamps (1–200) and
        `offset` its start; this client neither clamps nor sorts, and an unknown `watch` is the
        server's 400.
        """
        self._require("a followed artist's releases")
        data = self._data(RELEASES_PATH,
                          {'watch': int(watch), 'offset': _int(offset), 'limit': _int(limit)},
                          "a followed artist's releases")
        return ReleasePage.from_json(data)

    def tracks(self, url):
        """The songs inside one album page, as the provider listed them.

        `url` is the album's source page — the `source_url` a `Release` carries. It is sent as
        given; whether that page is readable is the provider's and the server's business.
        """
        self._require('the songs of one album')
        rows = self._data(TRACKS_PATH, {'url': str(url)}, 'the songs of one album')
        return tuple(Track.from_json(row) for row in _rows(rows))

    def artist(self, name):
        """One artist page: what the catalogue knows, and whether the server follows them."""
        self._require('an artist page')
        return ArtistPage.from_json(
            self._data(ARTIST_PATH, {'name': str(name)}, 'an artist page'))

    def subscriptions(self):
        """Every followed artist, each with the `mine` the server derived for this session."""
        self._require('the followed artists')
        rows = self._data(SUBSCRIPTIONS_PATH, None, 'the followed artists')
        return tuple(Subscription.from_json(row) for row in _rows(rows))