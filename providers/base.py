"""Provider contract.

A provider answers three questions and nothing else:

    identify(url)              -> is this mine?
    resolve(url)               -> a Release plus its TrackRefs
    new_releases(watch)        -> what has this artist published since `watermark`

It never touches the queue. Expansion produces plain data; the queue decides
what to do with it. That is what keeps `if provider == ...` out of the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TrackRef:
    provider: str
    provider_track_id: str
    title: str
    artist: str = ""
    album: str = ""
    track_number: int = 0
    duration: float = 0
    isrc: str = ""
    artwork: str = ""
    source_url: str = ""      # empty means "resolve by search"


@dataclass
class Release:
    provider: str
    release_id: str
    title: str
    artist: str = ""
    artist_id: str = ""
    artist_url: str = ""
    release_type: str = "playlist"   # album | ep | single | track | playlist | mix
    release_date: str = ""
    track_count: int = 0
    artwork: str = ""
    source_url: str = ""
    tracks: list[TrackRef] = field(default_factory=list)


class Provider:
    name = "generic"
    #: URL patterns this provider claims
    patterns: tuple[str, ...] = ()
    #: whether a resolved track carries its own audio URL (no matching needed)
    direct_audio = True

    def identify(self, url: str) -> bool:
        return bool(self.pattern(url)) if self.patterns else False

    def pattern(self, url: str) -> re.Match | None:
        for pat in self.patterns:
            m = re.search(pat, url, re.I)
            if m:
                return m
        return None

    def resolve(self, url: str) -> Release:            # pragma: no cover - abstract
        raise NotImplementedError

    def supports_follow(self) -> bool:
        return False

    def describe_artist(self, url: str) -> dict | None:
        """Who is this page? {artist_id, artist_name, artist_url} or None.

        Called once when a follow is created; `artist_id` is what makes the
        follow unique, so it must be stable across runs.
        """
        return None

    def new_releases(self, artist_url: str, watermark: float,
                     limit: int = 60) -> list[Release]:
        """What this artist has published, newest first.

        `watermark` is advisory — providers whose listings carry no publish
        dates rely on the caller's seen-set instead.
        """
        return []


def registry() -> list[Provider]:
    # Imported here so a provider module can import this one without a cycle.
    from providers.apple_music import AppleMusicProvider
    from providers.generic import GenericProvider
    from providers.soundcloud import SoundcloudProvider
    return [SoundcloudProvider(), AppleMusicProvider(), GenericProvider()]


def needs_metadata_approval(name: str) -> bool:
    """Metadata-only providers require a separately approved audio candidate."""
    return any(p.name == name and not p.direct_audio for p in registry())


def for_url(url: str) -> Provider:
    for provider in registry():
        if provider.identify(url):
            return provider
    return registry()[-1]      # generic yt-dlp fallback
