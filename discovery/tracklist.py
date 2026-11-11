"""Tracklists and full catalogues behind a follow.

Both are user-initiated reads — somebody opened an album, or an artist's page —
and never run during a poll. A tracklist is cached in memory; a catalogue is
stored per follow, because it is the whole discography and re-reading it on every
open would hammer the provider for data that changes once a week.
"""

from __future__ import annotations

import threading
import time

from providers import base as provider_base

CACHE: dict[str, tuple[float, list[dict]]] = {}
LOCK = threading.Lock()
TTL = 900

#: A stored catalogue is re-read from the provider after this long. Opening the
#: artist page serves the stored rows in between.
CATALOGUE_TTL = 6 * 3600

#: How much of the provider's list to ask for. Apple's own lookup stops at 200
#: releases; SoundCloud pages. The store keeps whatever comes back.
RELEASES_CAP = 500
TRACKS_CAP = 2000

#: An artist-level song lookup is one request but iTunes caps it at 200 songs. The
#: albums it does not cover are read one album at a time, and an album's tracklist
#: does not change, so those rows are reused for this long before being re-read.
TRACK_TTL = 7 * 24 * 3600

#: A bound on the one-off per-album pass, so a huge discography cannot turn one
#: page open into hundreds of provider requests.
ALBUM_FETCH_CAP = 120

#: One page of songs on the artist page.
PAGE = 60


def _release_dict(release) -> dict:
    return {
        "provider": release.provider,
        "release_id": release.release_id,
        "title": release.title,
        "release_type": release.release_type,
        "release_date": release.release_date,
        "track_count": release.track_count,
        "artwork": release.artwork,
        "source_url": release.source_url,
    }


def _track_dict(track, fetched: float | None = None) -> dict:
    return {
        "fetched": fetched if fetched is not None else time.time(),
        "provider": track.provider,
        "track_id": str(track.provider_track_id or ""),
        "release_id": getattr(track, "release_id", "") or "",
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "track_no": track.track_number,
        "duration": track.duration,
        "artwork": track.artwork,
        "source_url": track.source_url,
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


def _refresh(store, watch: dict) -> None:
    """Read the whole catalogue back into the store, replacing what was there."""
    provider = provider_base.for_url(watch["artist_url"])
    if not provider.supports_follow():
        raise ValueError(f"{provider.name} artist pages are not supported")
    # Watermark 0 and a wide limit: a catalogue is not "what is new".
    releases = provider.new_releases(watch["artist_url"], 0, limit=RELEASES_CAP)
    songs = [_track_dict(t) for t in
             provider.catalogue_tracks(watch["artist_url"], limit=TRACKS_CAP,
                                       releases=releases) if t.title]
    # An artist-level Apple lookup stops at 200 songs, so the older albums are read
    # one at a time. Their rows are kept for TRACK_TTL: a tracklist is stable, and
    # this is the only part of the catalogue that costs a request per release.
    if provider.name == "apple":
        cutoff = time.time() - TRACK_TTL
        live = {r.release_id for r in releases}
        stored: dict[str, list[dict]] = {}
        for row in store.artist_tracks_all(int(watch["id"])):
            if row["release_id"] in live and float(row.get("fetched") or 0) >= cutoff:
                stored.setdefault(row["release_id"], []).append(row)
        have: dict[str, int] = {}
        for song in songs:
            have[song["release_id"]] = have.get(song["release_id"], 0) + 1
        fetched = 0
        for release in releases:
            want = int(release.track_count or 0)
            got = have.get(release.release_id, 0)
            kept = stored.get(release.release_id) or []
            # "Any song from this album" is not the album. The artist-level lookup
            # gives a handful of tracks off a big compilation and nothing for some
            # singles, so completeness is judged against the release's own count.
            if (got >= want) if want else bool(got):
                continue
            if ((len(kept) >= want) if want else bool(kept)):
                songs.extend(kept)                     # a fresh album read, reused
                have[release.release_id] = len(kept)
                continue
            if fetched >= ALBUM_FETCH_CAP:
                store.log(f"“{watch['artist_name']}” has more albums than one read "
                          f"covers; the rest load when opened", "info")
                break
            fetched += 1
            try:
                found = provider.tracks(release.source_url)
            except Exception:                          # noqa: BLE001
                found = []
            if not found:
                songs.extend(kept)                     # better than nothing for this album
                continue                               # one bad album must not stop the rest
            for track in found:
                track.release_id = release.release_id
                songs.append(_track_dict(track))
            have[release.release_id] = len(found)
    store.artist_catalogue_replace(int(watch["id"]),
                                   [_release_dict(r) for r in releases], songs)


def catalogue(store, watch_id: int, offset: int = 0, limit: int = PAGE) -> dict:
    """Everything a followed artist has published: every release, and a page of songs.

    The first call after `CATALOGUE_TTL` reads the provider; every call in
    between is served from the store, so opening the page twice costs nothing.
    """
    watch = store.watch_get(int(watch_id))
    if not watch:
        raise ValueError("no such follow")
    if not watch.get("artist_url"):
        raise ValueError("this follow has no artist page to read")
    now = time.time()
    stamp = store.artist_catalogue_stamp(int(watch_id))
    if not stamp or now - stamp > CATALOGUE_TTL:
        try:
            _refresh(store, watch)
        except Exception as error:                     # noqa: BLE001
            # A provider that is down must not blank a page that was already read:
            # stale rows beat an error when there is something to show.
            if store.artist_catalogue_stamp(int(watch_id)):
                store.log(f"could not refresh “{watch['artist_name']}”: {error}", "warn")
            else:
                raise
    releases = store.artist_releases(int(watch_id))
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or PAGE), 200))
    songs, total = store.artist_tracks(int(watch_id), offset, limit)
    return {"artist": watch["artist_name"], "provider": watch["provider"],
            "releases": releases, "tracks": songs, "track_total": total,
            "offset": offset, "page": limit,
            "fetched": store.artist_catalogue_stamp(int(watch_id))}