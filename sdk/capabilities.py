"""What this server says it offers, so a client can hide what is not there.

`GET /api/v1/capabilities` is public and derived from the modules that implement each feature,
which makes it the one place a client should ask before drawing a tab, a menu item or a social
button. Reading it is therefore allowed to fail: `probe` never raises, and a missing, malformed
or merely old document degrades to the OpenSubsonic core — what a plain Subsonic client would
assume — rather than starting with a lie in either direction.

A domain is `available` only when the document names it with at least one flag. Nothing is
inferred from this app's source, and no optional domain is invented for a server that stayed
quiet: a client that guesses "surely it has that" is the thing this module exists to prevent.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field

#: Where a server that has a capability document publishes it. Short on purpose: a caller
#: passes a base URL, not the whole path.
CAPABILITIES_PATH = '/api/v1/capabilities'
DEFAULT_TIMEOUT = 15

#: Every optional domain a client may want to gate. `repo` is here because acquisition is a
#: real domain of this app, not because this server advertises it — see `Features.from_document`.
DOMAINS = ('accounts', 'library', 'playback', 'party', 'social', 'repo', 'discovery')


def _mapping(value):
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ServerInfo:
    """Who answered, and whether it expects members to sign in."""

    name: str = ''
    version: str = ''
    api: str = 'v1'
    requires_members: bool = False

    @classmethod
    def from_document(cls, raw):
        raw = _mapping(raw)
        return cls(name=str(raw.get('name') or ''), version=str(raw.get('version') or ''),
                   api=str(raw.get('api') or 'v1'),
                   requires_members=raw.get('requires_members') is True)


@dataclass(frozen=True)
class SubsonicInfo:
    """The protocol facts a client needs before its first request.

    An absent document leaves `enabled` True and everything else empty: the Subsonic core is
    the one thing assumed, and no extension or transcode target is claimed without a sentence
    saying so.
    """

    enabled: bool = True
    version: str = ''
    server_version: str = ''
    open_subsonic: bool = False
    extensions: tuple = ()
    transcode_targets: tuple = ()
    default_transcode: str = ''

    @classmethod
    def from_document(cls, raw):
        raw = _mapping(raw)
        extensions = tuple(dict(row) for row in raw.get('extensions') or ()
                           if isinstance(row, dict))
        targets = tuple(str(name) for name in raw.get('transcode_targets') or ())
        return cls(enabled=raw.get('enabled') is not False,
                   version=str(raw.get('version') or ''),
                   server_version=str(raw.get('server_version') or ''),
                   open_subsonic=raw.get('open_subsonic') is True,
                   extensions=extensions, transcode_targets=targets,
                   default_transcode=str(raw.get('default_transcode') or ''))


@dataclass(frozen=True)
class Feature:
    """One optional domain: available or not, plus the flags the document gave it.

    `available` is the switch a UI binds to — hide the tab, don't probe the endpoint. Inside an
    available domain `get('comments')` answers the finer question; inside an unavailable one the
    details are empty, so a caller never reads a flag out of a domain the server did not name.
    """

    name: str
    available: bool = False
    details: dict = field(default_factory=dict)

    def get(self, key, default=False):
        """One flag from the document, or `default` when the domain or the key is absent."""
        return self.details.get(key, default)

    @classmethod
    def from_document(cls, name, raw):
        details = _mapping(raw)
        return cls(name=name, available=bool(details), details=dict(details))


@dataclass(frozen=True)
class Features:
    """The optional domains, each one answering `available`.

    `repo` is deliberately the odd one out on this server: the document has no `repo` section,
    because `server.py` advertises `discovery` (search, sonic analysis) and leaves acquisition
    unnamed. A client must not translate "I know the app has a Repo" into an available feature,
    so the missing section stays `available=False` and a caller offers no acquisition.
    """

    accounts: Feature
    library: Feature
    playback: Feature
    party: Feature
    social: Feature
    repo: Feature
    discovery: Feature

    @classmethod
    def from_document(cls, raw):
        raw = _mapping(raw)
        return cls(**{name: Feature.from_document(name, raw.get(name)) for name in DOMAINS})

    @classmethod
    def empty(cls):
        """No domain claimed at all — what an unreadable document degrades to."""
        return cls(**{name: Feature(name) for name in DOMAINS})


def _federation(document):
    """Whether the document advertises federation, and nothing more.

    This server does not yet advertise it: `/api/v1/capabilities` has no `federation` flag,
    because cross-instance federation is not implemented, so a client must answer False rather
    than infer it from the app's plans or from `social` existing. A flag, when one appears, is
    taken at its word — a bool, or an object with `enabled`.
    """
    for holder in (document, _mapping(document.get('features'))):
        value = holder.get('federation')
        if isinstance(value, bool):
            return value
        if isinstance(value, dict) and isinstance(value.get('enabled'), bool):
            return value['enabled']
    return False


@dataclass(frozen=True)
class Capabilities:
    """One server's capability document, read into values a caller can branch on."""

    server: ServerInfo
    subsonic: SubsonicInfo
    features: Features
    federation: bool
    raw: dict = field(default_factory=dict)
    degraded: bool = False
    reason: str = ''

    @classmethod
    def from_document(cls, document):
        """Read a document tolerantly: absent keys are defaults, never an exception."""
        doc = _mapping(document)
        return cls(server=ServerInfo.from_document(doc.get('server')),
                   subsonic=SubsonicInfo.from_document(doc.get('subsonic')),
                   features=Features.from_document(doc.get('features')),
                   federation=_federation(doc), raw=dict(doc))

    @classmethod
    def assume_core(cls, reason=''):
        """No usable document: the OpenSubsonic core, and no optional domain claimed."""
        return cls(server=ServerInfo(), subsonic=SubsonicInfo(), features=Features.empty(),
                   federation=False, degraded=True, reason=str(reason or ''))

    def feature(self, name):
        """One domain by name; an unknown name is unavailable rather than an error."""
        found = getattr(self.features, name, None)
        return found if isinstance(found, Feature) else Feature(str(name))


def probe(base_url, *, timeout=DEFAULT_TIMEOUT, opener=None):
    """The server's capabilities, or the core-only default when the document is unusable.

    This is the whole degrade rule: a client asks before it can do anything else, so a failed
    capability call must not stop it from starting. No exception leaves here; `degraded` and
    `reason` say what happened for a caller that wants to log it.
    """
    fetch = opener or urllib.request.urlopen
    url = str(base_url).rstrip('/') + CAPABILITIES_PATH
    try:
        response = fetch(url, timeout=timeout)
        with response:
            raw = response.read()
        document = json.loads(raw.decode('utf-8') or '{}')
    except (OSError, ValueError, UnicodeError) as exc:
        close = getattr(exc, 'close', None)
        if callable(close):
            close()
        return Capabilities.assume_core(str(exc) or exc.__class__.__name__)
    if not isinstance(document, dict) or not document:
        return Capabilities.assume_core('the capability document was empty')
    return Capabilities.from_document(document)