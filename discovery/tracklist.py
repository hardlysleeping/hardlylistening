"""Tracklists and full catalogues behind a follow.

Both are user-initiated reads — somebody opened an album, or an artist's page —
and never run during a poll. Results are cached in memory so re-opening a row
costs nothing; the process forgets them on restart, which is fine for data that
comes from a public catalogue.
"""

from __future__ import annotations

import threading
import time

from providers import base as provider_base

CACHE: dict[str, tuple[float, list[dict]]] = {}
CATALOGUE: dict[int, tuple[float, list[dict]]] = {}
LOCK = threading.Lock()
TTL = 900


def _release_dict(release) -> dict:
    return {
        "release_id": release.release_id,
        "title": release.title,
        "release_type": release.release_type,
        "release_date": release.release_date,
        "track_count": release.track_count,
        "artwork": release.artwork,
        "source_url": release.source_url,
    }


def tracks(url: str) -> list[dict]:
    """The songs on one release page, in album order."""
    url = (url or "").strip()
    if not url:
        return []
    now = time.time()
    with LOCK:
        hit = CACHE.get(url)
        if hit and hit[0] > now:
            return hit[1]
    provider = provider_base.for_url(url)
    if provider.name not in ("apple", "soundcloud"):
        raise ValueError("that link has no tracklist")
    found = []
    for index, track in enumerate(provider.tracks(url)):
        if not track.source_url:
            continue                       # nothing to download, so nothing to show
        found.append({
            "n": track.track_number or index + 1,
            "title": track.title,
            "artist": track.artist,
            "duration": track.duration,
            "url": track.source_url,
        })
    with LOCK:
        CACHE[url] = (now + TTL, found)
    return found


def catalogue(store, watch_id: int) -> dict:
    """Everything a followed artist has published, newest first."""
    watch = store.watch_get(int(watch_id))
    if not watch:
        raise ValueError("no such follow")
    if not watch.get("artist_url"):
        raise ValueError("this follow has no artist page to read")
    now = time.time()
    with LOCK:
        hit = CATALOGUE.get(int(watch_id))
        if hit and hit[0] > now:
            return {"artist": watch["artist_name"], "provider": watch["provider"],
                    "releases": hit[1]}
    provider = provider_base.for_url(watch["artist_url"])
    if not provider.supports_follow():
        raise ValueError(f"{provider.name} artist pages are not supported")
    # Watermark 0 and a wide limit: a catalogue is not "what is new".
    releases = [_release_dict(r) for r in
                provider.new_releases(watch["artist_url"], 0, limit=200)]
    with LOCK:
        CATALOGUE[int(watch_id)] = (now + TTL, releases)
    return {"artist": watch["artist_name"], "provider": watch["provider"],
            "releases": releases}