"""Social for the client SDK: the Activity feed, comments, votes, likes and boosts.

Reads are the versioned ones — `api/v1.py` registers `social/activity`, `social/comments` and
`social/summary`, and nothing else — so this client calls those three and takes `data` out of the
`{"api": "v1", …}` envelope. The writes were never versioned: a vote, a comment, a like and a
boost travel on the unversioned routes `social/api.py` and `server.py` own, and each method below
says which one it builds.

Nothing here decides anything. The feed is `social.service.feed`'s answer, already collapsed into
day cards; whether a member may edit a comment arrives as `can_edit`; which `kind`/`target` pairs
exist is `social.discussion.validate_target`'s judgement, and the server answers 400 or 404.
A client that filtered members, recomputed a score or second-guessed visibility would be a second
implementation, and this module is not one.

Whether any of it may be called at all is `/api/v1/capabilities`' question, and `sdk.capabilities`
is where it is answered: a server that does not advertise the flag an entry point needs raises
`Unavailable` naming that flag, never an empty tuple that reads as "nothing happened". The boost
slot is the one exception the document itself makes — it names no `boosts` flag, because the flag
would always be true wherever `social` is — so a boost is gated on the domain alone.

A write needs the browser session `security.py` mints: the `__Host-hl_session` cookie, and the
`Origin` header its CSRF check compares against `Host`. Both travel in headers, never in a URL,
and no message this module raises carries either. Nothing is stored.

Deliberately absent: member profiles (`/api/member/<id>`), reporting a play
(`/api/activity/listen`), the unversioned discussion reads, and any acquisition. They belong to
the cards that own them, not to this one.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode, urlsplit

from .capabilities import Capabilities
from .session import SESSION_COOKIE, Session

#: Where the three versioned reads live (`api/v1.py`, its `ROUTES` table).
ACTIVITY_PATH = '/api/v1/social/activity'
COMMENTS_PATH = '/api/v1/social/comments'
SUMMARY_PATH = '/api/v1/social/summary'
#: Where the member's own 24-hour slot is read and set (`server.py`).
BOOST_PATH = '/api/boost'
#: The prefix the unversioned discussion adapter matches (`social/api.py`).
DISCUSSION_PATH = '/api/social'
API_VERSION = 'v1'
DEFAULT_TIMEOUT = 15


class SocialError(Exception):
    """The call did not produce an answer: a wire failure, no envelope, or a refusal."""


class ClientError(SocialError):
    """No usable answer at all: a bad status with no `{"error"}` body, HTML, or not a v1 envelope.

    This is a broken server or proxy, not a decision about the request, so it carries no status
    and nothing here is retried.
    """


class Unavailable(SocialError):
    """The capability document does not advertise the flag this call needs. Do not retry.

    `needed` is the flag that was missing (`social.activity`, or just `social`), so a caller can
    log the switch it should have read before asking, and hide the control that offered the call.
    """

    def __init__(self, message, needed=''):
        self.needed = str(needed or '')
        super().__init__(str(message or ''))


class Denied(SocialError):
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
    """404 — no such route, track, playlist, album key or comment."""


class RateLimited(Denied):
    """429 — this member's bucket for the minute is spent; `retry_after` says for how long."""


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
    """A timestamp, which the feed writes as seconds and could write as a float string."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _text(value):
    return str(value or '')


# -- rows ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Member:
    """Who did it. `name` and `icon` are the server's answer, already substituted when empty."""

    id: str = ''
    name: str = ''
    icon: str = ''

    @classmethod
    def from_comment(cls, row):
        row = _mapping(row)
        return cls(id=_text(row.get('member_id')), name=_text(row.get('name')),
                   icon=_text(row.get('icon')))

    @classmethod
    def from_voter(cls, row):
        row = _mapping(row)
        return cls(id=_text(row.get('id')), name=_text(row.get('name')),
                   icon=_text(row.get('icon')))

    @classmethod
    def from_activity(cls, row):
        row = _mapping(row)
        return cls(id=_text(row.get('member_id')), name=_text(row.get('member')),
                   icon=_text(row.get('member_icon')))


@dataclass(frozen=True)
class Track:
    """The one track a boost or a feed entry names, in the feed's own flat shape."""

    id: int = 0
    title: str = ''
    artist: str = ''
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(id=_int(row.get('id')), title=_text(row.get('title')),
                   artist=_text(row.get('artist_name')), raw=dict(row))


@dataclass(frozen=True)
class Boost:
    """One member's 24-hour slot, in whichever of its three shapes the server answered.

    The feed row carries `member` and `track`, `current_boost` carries `boosted_at` and the track
    but no member, and the write carries `swapped`/`already_active` but no track. Every field is
    optional for that reason; `expires_at` is always the server's, never computed here.
    """

    track_id: int = 0
    boosted_at: float = 0.0
    expires_at: float = 0.0
    swapped: bool = False
    already_active: bool = False
    track: Track | None = None
    member: Member | None = None
    raw: dict = field(default_factory=dict)

    @property
    def kind(self):
        return 'boost'

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        track = row.get('track')
        return cls(track_id=_int(row.get('track_id') or _mapping(track).get('id')),
                   boosted_at=_number(row.get('boosted_at') or row.get('ts')),
                   expires_at=_number(row.get('expires_at')),
                   swapped=row.get('swapped') is True,
                   already_active=row.get('already_active') is True,
                   track=Track.from_json(track) if isinstance(track, dict) else None,
                   member=Member.from_activity(row) if row.get('member_id') else None,
                   raw=dict(row))


@dataclass(frozen=True)
class Entry:
    """One thing inside a day card: a play, an addition, a playlist edit, a join or a rename."""

    kind: str = ''
    ts: float = 0.0
    track: Track | None = None
    playlist_id: int = 0
    title: str = ''
    detail: str = ''
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, group, row):
        row = _mapping(row)
        if group in ('listen', 'add'):
            track = Track.from_json(row)
            return cls(kind=group, ts=_number(row.get('ts')), track=track, title=track.title,
                       raw=dict(row))
        if group == 'playlist':
            return cls(kind=_text(row.get('kind')), ts=_number(row.get('ts')),
                       playlist_id=_int(row.get('id')), title=_text(row.get('title')),
                       raw=dict(row))
        return cls(kind=_text(row.get('kind')), ts=_number(row.get('ts')),
                   detail=_text(row.get('detail')), raw=dict(row))


@dataclass(frozen=True)
class DayCard:
    """One member's day: `counts` per group (`listen`, `add`, `playlist`, `other`) and the entries."""

    member: Member = field(default_factory=Member)
    day: str = ''
    ts: float = 0.0
    first_ts: float = 0.0
    counts: dict = field(default_factory=dict)
    groups: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def kind(self):
        return 'day'

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        groups = _mapping(row.get('groups'))
        return cls(member=Member.from_activity(row), day=_text(row.get('day')),
                   ts=_number(row.get('ts')), first_ts=_number(row.get('first_ts')),
                   counts={str(name): _int(count)
                           for name, count in _mapping(row.get('counts')).items()},
                   groups={str(name): tuple(Entry.from_json(str(name), item)
                                            for item in _rows(items))
                           for name, items in groups.items()},
                   raw=dict(row))


@dataclass(frozen=True)
class Voter:
    """One named voter: votes are never anonymous, so the score has faces behind it."""

    member: Member = field(default_factory=Member)
    value: int = 0

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(member=Member.from_voter(row), value=_int(row.get('value')))


@dataclass(frozen=True)
class Comment:
    """One comment, as the page shaped it: the body is plain text, never markup."""

    id: int = 0
    member: Member = field(default_factory=Member)
    body: str = ''
    created_at: str = ''
    updated_at: str = ''
    likes: int = 0
    liked: bool = False
    can_edit: bool = False
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(id=_int(row.get('id')), member=Member.from_comment(row),
                   body=_text(row.get('body')), created_at=_text(row.get('created_at')),
                   updated_at=_text(row.get('updated_at')), likes=_int(row.get('likes')),
                   liked=row.get('liked') is True, can_edit=row.get('can_edit') is True,
                   raw=dict(row))


@dataclass(frozen=True)
class Summary:
    """One target's votes and comment count: the score, the split, the caller's own vote."""

    score: int = 0
    up: int = 0
    down: int = 0
    vote: int = 0
    comments: int = 0
    voters: tuple = ()
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(score=_int(row.get('score')), up=_int(row.get('up')),
                   down=_int(row.get('down')), vote=_int(row.get('vote')),
                   comments=_int(row.get('comments')),
                   voters=tuple(Voter.from_json(item) for item in _rows(row.get('voters'))),
                   raw=dict(row))


@dataclass(frozen=True)
class CommentPage:
    """One page of comments, with the target's summary and the cursor for the next page."""

    summary: Summary
    comments: tuple = ()
    next_after: int | None = None

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        after = row.get('next_after')
        return cls(summary=Summary.from_json(row),
                   comments=tuple(Comment.from_json(item) for item in _rows(row.get('items'))),
                   next_after=int(after) if isinstance(after, int) else None)


def activity_item(row):
    """One feed row as its typed value; an unknown kind is named rather than silently dropped."""
    row = _mapping(row)
    kind = _text(row.get('kind'))
    if kind == 'boost':
        return Boost.from_json(row)
    if kind == 'day':
        return DayCard.from_json(row)
    raise ClientError('the activity feed named a kind this client does not know: '
                      f'{kind or "none"}')


# -- the client ----------------------------------------------------------------------------------

class SocialClient:
    """One server, one session. Calls are synchronous and nothing is cached."""

    def __init__(self, base_url, capabilities, session=None, *, timeout=DEFAULT_TIMEOUT,
                 opener=None):
        self.base_url = str(base_url).rstrip('/')
        self.capabilities = (capabilities if isinstance(capabilities, Capabilities)
                             else Capabilities.from_document(capabilities or {}))
        self.session = session if isinstance(session, Session) else Session()
        self.timeout = timeout
        parts = urlsplit(self.base_url)
        # The CSRF check compares Origin against `Host` (`security.csrf`), so the client states
        # the origin it is actually talking to rather than a configured one.
        self.origin = f'{parts.scheme}://{parts.netloc}' if parts.scheme else ''
        self._open = opener or urllib.request.urlopen

    # ---- capability gate -----------------------------------------------------

    def _require(self, what, flag=''):
        """Refuse by name before the wire when the document does not offer what the call needs."""
        feature = self.capabilities.feature('social')
        if feature.available and (not flag or feature.get(flag, False)):
            return
        needed = f'social.{flag}' if flag else 'social'
        if not feature.available:
            if self.capabilities.degraded:
                reason = ('the capability document could not be read: '
                          + (self.capabilities.reason or 'no reason given'))
            else:
                reason = 'its document names no social domain'
        else:
            reason = f'its social domain does not name {flag}'
        raise Unavailable(f'{what} is unavailable: this server does not advertise {needed} '
                          f'({reason})', needed)

    # ---- wire ----------------------------------------------------------------

    def _call(self, method, path, *, params=None, body=None, label=''):
        """One request, one JSON object. An `{"error": …}` answer raises the typed refusal."""
        what = label or f'{method} {path}'
        url = self.base_url + path
        if params:
            url += '?' + urlencode({key: str(value) for key, value in params.items()})
        data = None if body is None else json.dumps(body).encode('utf-8')
        headers = {'Accept': 'application/json'}
        headers.update(self.session.header())
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if method != 'GET' and self.origin:
            headers['Origin'] = self.origin
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        response = self._fetch(request, what)
        with response:
            raw = response.read()
        return self._document(raw, what)

    def _fetch(self, request, what):
        """The open response, or the typed error its status deserves.

        The URL and the status may enter a message; the headers never do, because one of them is
        the session cookie and an exception ends up in a log.
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
        document = self._call('GET', path, params=params, label=what)
        if document.get('api') != API_VERSION or 'data' not in document:
            raise ClientError(f'{what}: the answer is not a v1 envelope')
        return document['data']

    def _discussion(self, kind, target, action, payload, what):
        """A write on `/api/social/<kind>/<target>/<action>` (`social/api.py`'s regex)."""
        path = (f'{DISCUSSION_PATH}/{quote(str(kind), safe="")}'
                f'/{quote(str(target), safe="")}/{action}')
        return self._call('POST', path, body=payload, label=what)

    # ---- reads: the versioned surface ----------------------------------------

    def activity(self, limit=80):
        """The Activity feed: live boosts first, then `social.service.feed`'s day cards.

        `limit` is the row count the server clamps (1–200); this client neither clamps nor sorts.
        """
        self._require('the activity feed', 'activity')
        rows = self._data(ACTIVITY_PATH, {'limit': int(limit)}, 'the activity feed')
        return tuple(activity_item(row) for row in _rows(rows))

    def comments(self, kind, target, after=0):
        """One page of comments for a Repo object, with its likes and the target's summary.

        `kind` is `track`, `playlist` or `album`; `target` is that object's id, and for an album
        the 64-character key `social.album_id` produces. The server validates both.
        """
        self._require('a comment page', 'comments')
        data = self._data(COMMENTS_PATH, {'kind': kind, 'target': target, 'after': _int(after)},
                          'a comment page')
        return CommentPage.from_json(data)

    def summary(self, kind, target):
        """One target's score, split, the caller's own vote and its comment count."""
        self._require('a vote summary', 'votes')
        return Summary.from_json(
            self._data(SUMMARY_PATH, {'kind': kind, 'target': target}, 'a vote summary'))

    def summaries(self, kind, targets):
        """The same counts for many targets at once, keyed by target.

        An empty sequence asks the server nothing and answers `{}`: that is the batch's own
        answer for no targets, and a request carrying `targets=` would read as one empty target.
        """
        self._require('vote summaries', 'votes')
        wanted = [str(target) for target in targets]
        if not wanted:
            return {}
        rows = self._data(SUMMARY_PATH, {'kind': kind, 'targets': ','.join(wanted)},
                          'vote summaries')
        return {str(key): Summary.from_json(row) for key, row in _mapping(rows).items()}

    def current_boost(self):
        """This session's live boost, or `None` when the 24-hour slot has expired."""
        self._require('a member boost')
        answer = self._call('GET', BOOST_PATH, label='a member boost')
        row = answer.get('boost')
        return Boost.from_json(row) if isinstance(row, dict) else None

    # ---- writes: the unversioned routes --------------------------------------

    def boost(self, track_id):
        """Put a track in this member's 24-hour slot; switching it never extends the expiry.

        `POST /api/boost` answers `{ok, boost}` with `swapped` and `already_active`, which are
        carried as the server said them. The document names no `boosts` flag, so this is gated on
        the `social` domain alone.
        """
        self._require('a boost')
        answer = self._call('POST', BOOST_PATH, body={'track_id': int(track_id)},
                            label='a boost')
        return Boost.from_json(answer.get('boost'))

    def comment(self, kind, target, body):
        """Post one plain-text comment. The 1–2000 character rule is the server's to enforce."""
        self._require('a comment', 'comments')
        return Summary.from_json(
            self._discussion(kind, target, 'comments', {'body': str(body)}, 'a comment'))

    def edit_comment(self, kind, target, comment_id, body):
        """Rewrite one comment; the server allows the author and an administrator, nobody else."""
        self._require('a comment edit', 'comments')
        return Summary.from_json(
            self._discussion(kind, target, f'comments/{int(comment_id)}', {'body': str(body)},
                             'a comment edit'))

    def delete_comment(self, kind, target, comment_id):
        """Delete one comment. Deletion is a flag on the edit route, not a `DELETE` request."""
        self._require('a comment delete', 'comments')
        return Summary.from_json(
            self._discussion(kind, target, f'comments/{int(comment_id)}', {'delete': True},
                             'a comment delete'))

    def vote(self, kind, target, value):
        """Set, switch or retract one vote: `1`, `-1`, or `0` to clear it.

        The server takes a JSON integer (`social.discussion.vote` checks `type(value) is int`),
        which is why this converts; the range check is the server's, and a bad value is its 400.
        """
        self._require('a vote', 'votes')
        return Summary.from_json(
            self._discussion(kind, target, 'vote', {'value': int(value)}, 'a vote'))

    def like(self, kind, target, comment_id):
        """Toggle this member's like on one comment; asking twice is how a like is taken back.

        The route is `/like/<comment_id>` with an empty body, and it toggles: there is no separate
        unlike endpoint in `social/api.py`, so callers must not send a second call expecting a
        second like.
        """
        self._require('a like', 'likes')
        return Summary.from_json(
            self._discussion(kind, target, f'like/{int(comment_id)}', {}, 'a like'))
