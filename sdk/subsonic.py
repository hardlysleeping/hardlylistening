"""An OpenSubsonic client for this server, written against what `subsonic.py` really answers.

The server's adapter is the authority, so this client does not chase the whole protocol: it
speaks the endpoints that surface implements — ping, the library, search, stars, bookmarks,
streams — and raises `NotSupported` for the ones it answers with "not implemented" instead of
returning an empty result that looks like success. Every response is the JSON envelope
(`f=json`); a second XML parser would be a second set of bugs for no caller.

Authentication is whatever a device gets: the username/password pair `/api/listening/devices`
hands out, or the Subsonic MD5 token form built from that same password. Nothing is stored —
credentials live in the `Credentials` value the caller passes in, and nowhere else.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlencode

#: What a client states it speaks. The server accepts this without negotiating.
PROTOCOL_VERSION = '1.16.1'
DEFAULT_CLIENT = 'hardlylistening-sdk'
DEFAULT_TIMEOUT = 30
CHUNK = 65536


class ClientError(Exception):
    """The call did not produce a usable Subsonic envelope: a bad status, HTML, no JSON."""


class SubsonicError(Exception):
    """An envelope with `status="failed"`, carrying the protocol's numeric code."""

    def __init__(self, code, message):
        self.code, self.message = int(code), str(message or '')
        super().__init__(f'{self.code}: {self.message}')


class GenericError(SubsonicError):
    """0 — the server declined for a reason of its own."""


class MissingParameter(SubsonicError):
    """10 — a required parameter was absent or invalid."""


class IncompatibleClient(SubsonicError):
    """20 — this client's version is not usable against that server."""


class NotSupported(SubsonicError):
    """30 — the server does not implement the endpoint; do not retry, hide the feature."""


class WrongCredentials(SubsonicError):
    """40 — the device id or its password/token is wrong, or the device was revoked."""


class NotAuthorized(SubsonicError):
    """50 — the device is known but may not do this."""


class TrialExpired(SubsonicError):
    """60 — a paid server's trial is over."""


class NotFound(SubsonicError):
    """70 — the id is not in this device's catalogue, or the media is gone."""


ERROR_CODES = {0: GenericError, 10: MissingParameter, 20: IncompatibleClient, 30: NotSupported,
               40: WrongCredentials, 50: NotAuthorized, 60: TrialExpired, 70: NotFound}

#: `listening.rest()` flattens a 404 into code 70 and only the message says which 404 it was.
#: The two phrases below are how `subsonic.py` refuses an endpoint it does not implement, so a
#: caller gets `NotSupported` (hide the control) rather than `NotFound` (retry with another id).
UNSUPPORTED = ('unsupported endpoint', 'does not implement')


def error_for(code, message):
    """The exception an envelope's error tuple deserves."""
    text = str(message or '')
    if any(marker in text.lower() for marker in UNSUPPORTED):
        return NotSupported(code, text or 'not supported by this server')
    mapper = ERROR_CODES.get(int(code), GenericError)
    return mapper(code, text)


@dataclass(frozen=True)
class Credentials:
    """One device's credentials, in the two forms this server accepts.

    `username` is a device id (`hl_…`), never an email: the Subsonic surface authenticates a
    device, and that device carries its own scope. `password` is the device password; `token`
    and `salt` are the MD5 pair (`md5(password + salt)`) a client may send instead, so the
    password itself is not in every URL.
    """

    username: str
    password: str = ''
    token: str = ''
    salt: str = ''

    @classmethod
    def from_password(cls, username, password):
        """The pair `POST /api/listening/devices` prints once, for a device that keeps it."""
        return cls(username=str(username), password=str(password))

    @classmethod
    def from_token(cls, username, token, salt):
        """The token form: `t = md5(password + s)`, lower or upper case for `t`."""
        return cls(username=str(username), token=str(token), salt=str(salt))

    @classmethod
    def from_device(cls, device):
        """From the body that created the device: `{username, password, token_auth}`."""
        device = device if isinstance(device, dict) else {}
        return cls(username=str(device.get('username') or ''),
                   password=str(device.get('password') or ''))

    def params(self):
        """The auth query parameters, or `ValueError` when there is nothing to send."""
        if self.token and self.salt:
            return {'u': self.username, 't': self.token, 's': self.salt}
        if self.password:
            return {'u': self.username, 'p': self.password}
        raise ValueError('credentials need a password, or a token and a salt')


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(container, key):
    """The rows under one container key, filtered to objects — never None, never a crash."""
    rows = _mapping(container).get(key) or ()
    return tuple(row for row in rows if isinstance(row, dict))


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class Song:
    """One track, in the fields a player needs. `raw` keeps the rest of the envelope row."""

    id: str
    title: str
    artist: str
    album: str
    album_id: str
    artist_id: str
    duration: int
    track: int
    suffix: str
    size: int
    starred: bool
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(id=str(row.get('id') or ''), title=str(row.get('title') or ''),
                   artist=str(row.get('artist') or ''), album=str(row.get('album') or ''),
                   album_id=str(row.get('albumId') or ''), artist_id=str(row.get('artistId') or ''),
                   duration=_int(row.get('duration')), track=_int(row.get('track')),
                   suffix=str(row.get('suffix') or ''), size=_int(row.get('size')),
                   starred=bool(row.get('starred')), raw=dict(row))


@dataclass(frozen=True)
class Album:
    """One album, with its songs when it was fetched by id."""

    id: str
    name: str
    artist: str
    artist_id: str
    song_count: int
    duration: int
    songs: tuple = ()
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row, songs=()):
        row = _mapping(row)
        return cls(id=str(row.get('id') or ''), name=str(row.get('name') or ''),
                   artist=str(row.get('artist') or ''), artist_id=str(row.get('artistId') or ''),
                   song_count=_int(row.get('songCount')), duration=_int(row.get('duration')),
                   songs=tuple(songs), raw=dict(row))


@dataclass(frozen=True)
class Artist:
    """One artist, with albums when it was fetched by id."""

    id: str
    name: str
    album_count: int
    albums: tuple = ()
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row, albums=()):
        row = _mapping(row)
        return cls(id=str(row.get('id') or ''), name=str(row.get('name') or ''),
                   album_count=_int(row.get('albumCount')), albums=tuple(albums), raw=dict(row))


@dataclass(frozen=True)
class Playlist:
    """One playlist; `songs` is filled by `playlist(id)`, empty in a listing."""

    id: str
    name: str
    song_count: int
    duration: int
    songs: tuple = ()
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row, songs=()):
        row = _mapping(row)
        return cls(id=str(row.get('id') or ''), name=str(row.get('name') or ''),
                   song_count=_int(row.get('songCount')), duration=_int(row.get('duration')),
                   songs=tuple(songs), raw=dict(row))


@dataclass(frozen=True)
class Bookmark:
    """Where playback stopped, in milliseconds, for one track."""

    song: Song
    position: int
    comment: str
    created: str
    changed: str
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        return cls(song=Song.from_json(row.get('entry')), position=_int(row.get('position')),
                   comment=str(row.get('comment') or ''), created=str(row.get('created') or ''),
                   changed=str(row.get('changed') or ''), raw=dict(row))


@dataclass(frozen=True)
class SearchResult:
    """One answer to `search3`, split the way the protocol splits it."""

    songs: tuple = ()
    albums: tuple = ()
    artists: tuple = ()


@dataclass(frozen=True)
class Extension:
    """One OpenSubsonic extension the server names, and the versions it serves."""

    name: str
    versions: tuple = ()

    @classmethod
    def from_json(cls, row):
        row = _mapping(row)
        versions = row.get('versions') or ()
        return cls(name=str(row.get('name') or ''), versions=tuple(_int(v) for v in versions))


class SubsonicClient:
    """One server, one device. Calls are synchronous and nothing is cached."""

    def __init__(self, base_url, credentials, *, client=DEFAULT_CLIENT,
                 version=PROTOCOL_VERSION, timeout=DEFAULT_TIMEOUT, opener=None):
        self.base_url = str(base_url).rstrip('/')
        self.credentials = credentials
        self.client_name = client
        self.version = version
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    # ---- wire -------------------------------------------------------------

    def _pairs(self, params):
        pairs = list(self.credentials.params().items())
        pairs += [('v', self.version), ('c', self.client_name), ('f', 'json')]
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                pairs.extend((key, str(item)) for item in value)
            else:
                pairs.append((key, str(value)))
        return pairs

    def url(self, endpoint, **params):
        """A complete `…/rest/<endpoint>.view` URL, credentials included."""
        return f'{self.base_url}/rest/{endpoint}.view?' + urlencode(self._pairs(params))

    def _fetch(self, url, headers=None):
        request = urllib.request.Request(url, headers=headers or {})
        try:
            return self._open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ClientError(f'HTTP {exc.code} from {self.base_url}') from exc
        except OSError as exc:
            raise ClientError(f'{self.base_url}: {exc}') from exc

    def request(self, endpoint, **params):
        """One call, one envelope. Returns the whole `subsonic-response` mapping.

        A failed envelope raises the matching `SubsonicError` subclass; a reply that is not an
        envelope at all raises `ClientError`, because that is a broken server or proxy rather
        than a protocol answer.
        """
        response = self._fetch(self.url(endpoint, **params))
        with response:
            raw = response.read()
        try:
            document = json.loads(raw.decode('utf-8') or '{}')
        except (ValueError, UnicodeError) as exc:
            raise ClientError(f'{endpoint}: the answer was not JSON') from exc
        envelope = _mapping(document).get('subsonic-response')
        if not isinstance(envelope, dict):
            raise ClientError(f'{endpoint}: no subsonic-response in the answer')
        error = envelope.get('error')
        if isinstance(error, dict):
            raise error_for(error.get('code'), error.get('message'))
        if str(envelope.get('status') or 'ok') != 'ok':
            raise ClientError(f'{endpoint}: status {envelope.get("status")}')
        return envelope

    # ---- server -----------------------------------------------------------

    def ping(self):
        """True when the credentials work; raises `WrongCredentials` or `ClientError` otherwise."""
        self.request('ping')
        return True

    def extensions(self):
        """The OpenSubsonic extensions the server names for itself."""
        rows = self.request('getOpenSubsonicExtensions').get('openSubsonicExtensions') or ()
        return tuple(Extension.from_json(row) for row in rows if isinstance(row, dict))

    # ---- library ----------------------------------------------------------

    def artists(self):
        """Every artist in this device's catalogue, flattened out of the index groups."""
        payload = self.request('getArtists').get('artists')
        return tuple(Artist.from_json(row) for index in _rows(payload, 'index')
                     for row in _rows(index, 'artist'))

    def artist(self, artist_id):
        """One artist with their albums."""
        row = self.request('getArtist', id=artist_id).get('artist')
        albums = tuple(Album.from_json(item) for item in _rows(row, 'album'))
        return Artist.from_json(row, albums=albums)

    def album(self, album_id):
        """One album with its songs."""
        row = self.request('getAlbum', id=album_id).get('album')
        songs = tuple(Song.from_json(item) for item in _rows(row, 'song'))
        return Album.from_json(row, songs=songs)

    def song(self, song_id):
        """One track."""
        return Song.from_json(self.request('getSong', id=song_id).get('song'))

    def search(self, term, *, songs=20, albums=20, artists=20, offset=0):
        """`search3`, with the same offset applied to each of the three result kinds."""
        payload = self.request('search3', query=term, songCount=songs, songOffset=offset,
                               albumCount=albums, albumOffset=offset,
                               artistCount=artists, artistOffset=offset).get('searchResult3')
        return SearchResult(songs=tuple(Song.from_json(row) for row in _rows(payload, 'song')),
                            albums=tuple(Album.from_json(row) for row in _rows(payload, 'album')),
                            artists=tuple(Artist.from_json(row) for row in _rows(payload, 'artist')))

    # ---- playlists --------------------------------------------------------
    # This server answers the three writes with "Unsupported endpoint", which becomes
    # `NotSupported`. They are still sent: the client is not HardlyListening-only, and a server
    # that does implement them should work from the same call.

    def playlists(self):
        payload = self.request('getPlaylists').get('playlists')
        return tuple(Playlist.from_json(row) for row in _rows(payload, 'playlist'))

    def playlist(self, playlist_id):
        row = self.request('getPlaylist', id=playlist_id).get('playlist')
        songs = tuple(Song.from_json(item) for item in _rows(row, 'entry'))
        return Playlist.from_json(row, songs=songs)

    def create_playlist(self, name, song_ids=()):
        """Make a playlist; raises `NotSupported` on this server, which has no createPlaylist."""
        self.request('createPlaylist', name=name, songId=tuple(song_ids))

    def update_playlist(self, playlist_id, *, name=None, comment=None, song_ids=None,
                        add=(), remove=()):
        """Rename, replace the songs, or add/remove some; `song_ids=None` leaves them alone."""
        self.request('updatePlaylist', playlistId=playlist_id, name=name, comment=comment,
                     songId=song_ids, songIdToAdd=tuple(add), songIdToRemove=tuple(remove))

    def delete_playlist(self, playlist_id):
        """Delete a playlist; raises `NotSupported` here."""
        self.request('deletePlaylist', id=playlist_id)

    # ---- stars and bookmarks ----------------------------------------------

    def star(self, song_ids=(), album_ids=()):
        """Mark tracks and/or albums as starred. At least one id is required."""
        self._mark('star', song_ids, album_ids)

    def unstar(self, song_ids=(), album_ids=()):
        """The reverse of `star`, with the same requirement."""
        self._mark('unstar', song_ids, album_ids)

    def _mark(self, endpoint, song_ids, album_ids):
        songs, albums = tuple(song_ids), tuple(album_ids)
        if not songs and not albums:
            raise ValueError('star/unstar needs a song id or an album id')
        self.request(endpoint, id=songs or None, albumId=albums or None)

    def bookmarks(self):
        """Every resume point this device has saved."""
        payload = self.request('getBookmarks').get('bookmarks')
        return tuple(Bookmark.from_json(row) for row in _rows(payload, 'bookmark'))

    def create_bookmark(self, song_id, position, comment=''):
        """Save a resume point. `position` is milliseconds, the unit the protocol uses."""
        self.request('createBookmark', id=song_id, position=int(position), comment=comment or None)

    def delete_bookmark(self, song_id):
        """Forget the resume point for one track."""
        self.request('deleteBookmark', id=song_id)

    # ---- streams ----------------------------------------------------------

    def stream_url(self, song_id, *, max_bitrate=None, format=None):
        """A URL a player can open directly, credentials included.

        `max_bitrate` is the protocol's `maxBitRate` in kbps, `format` (mp3, opus, aac) asks
        this server for a re-encode because the stored file is not playable as it is. Both are
        omitted when None: an unasked-for transcode is a wasted encode.
        """
        return self.url('stream', id=song_id, maxBitRate=max_bitrate, format=format)

    def download_url(self, song_id):
        """The stored file itself: this endpoint never transcodes."""
        return self.url('download', id=song_id)

    def cover_art_url(self, cover_id, size=None):
        """Artwork for an album, artist or track id; a miss is a 404, not a blank image."""
        return self.url('getCoverArt', id=cover_id, size=size)

    def read_range(self, song_id=None, *, url=None, offset=0, length=None):
        """The bytes of one range, in one request. A short answer is a `ClientError`."""
        return b''.join(self.iter_range(song_id, url=url, offset=offset, length=length, retries=0))

    def iter_range(self, song_id=None, *, url=None, offset=0, length=None,
                   chunk_size=CHUNK, retries=2):
        """Yield bytes from `offset`, resuming where a dropped connection stopped.

        Seeking is one `Range` header on the stream, never an `offset` parameter: the server's
        Range support is what a player uses. One header per request — the first from `offset`,
        a retry from the byte after the last one handed out — so a caller writing chunks to
        disk ends up with a continuous file rather than a gap. A server that ignores the header
        is an error, not a silent download of the wrong bytes.
        """
        if url is None:
            if song_id is None:
                raise ValueError('a song id or a URL is required')
            url = self.stream_url(song_id)
        remaining = None if length is None else int(length)
        if remaining is not None and remaining <= 0:
            return
        attempts = 0
        while True:
            start = offset
            end = '' if remaining is None else str(start + remaining - 1)
            response = self._fetch(url, {'Range': f'bytes={start}-{end}'})
            if start and getattr(response, 'status', 206) != 206:
                response.close()
                raise ClientError('the server ignored the Range request')
            expected = _int(response.headers.get('Content-Length'))
            received = 0
            try:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    received += len(chunk)
                    offset += len(chunk)
                    if remaining is not None:
                        remaining -= len(chunk)
                    yield chunk
                    if remaining == 0:
                        return
            finally:
                response.close()
            if not expected or received >= expected:
                return
            if attempts >= retries:
                raise ClientError(f'the stream stopped at byte {offset} and could not resume')
            attempts += 1