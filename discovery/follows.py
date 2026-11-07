"""Following an artist — discovery that never downloads anything.

The rule the whole feature rests on: a follow produces **feed items**, and only a
click turns one into a download job. Nothing in this module calls the queue.

Detecting "new" needs a seen-set, not a timestamp: a flat SoundCloud listing
carries ids and titles but no publish dates, so a release is new when this follow
has not looked at that id before (`watch_items`). A brand-new follow therefore
records the current uploads as already seen — it never backfills the feed with an
artist's entire back catalogue.
"""

from __future__ import annotations

from providers import base as provider_base

#: Never dump an artist's whole history into the feed in one check.
FEED_CAP = 30


def provider_named(name: str):
    for provider in provider_base.registry():
        if provider.name == name:
            return provider
    raise ValueError(f"unknown provider {name}")


def follow(store, url: str, *, interval: float = 0, limit: int = 60) -> dict:
    """Follow an artist page. Returns the watch row plus what was baselined."""
    url = (url or "").strip()
    if not url:
        raise ValueError("paste an artist link")
    provider = provider_base.for_url(url)
    if not provider.supports_follow():
        raise ValueError(f"{provider.name} artist pages are not supported yet")

    who = provider.describe_artist(url)
    if not who:
        raise ValueError("that does not look like an artist page")

    watch_id = store.watch_add(provider.name, who["artist_id"],
                               who["artist_name"], who["artist_url"], interval)
    existed = bool(store.watch_seen(watch_id))

    baselined = 0
    if not existed:
        try:
            current = provider.new_releases(who["artist_url"], 0, limit=limit)
            store.watch_remember(watch_id, [r.release_id for r in current])
            baselined = len(current)
        except Exception as exc:                      # noqa: BLE001
            store.log(f"could not baseline “{who['artist_name']}”: {exc}", "warn")
        store.watch_mark(watch_id, checked=True)

    store.log(f"following {who['artist_name']} ({provider.name})"
              f"{f', {baselined} existing uploads marked seen' if baselined else ''}",
              "ok")
    return {"watch": store.watch_get(watch_id), "baselined": baselined,
            "already": existed}


def check(store, watch: dict, *, limit: int = 60) -> dict:
    """One artist, one look. Adds whatever is genuinely new to the feed."""
    provider = provider_named(watch["provider"])
    known = store.watch_seen(watch["id"])
    releases = provider.new_releases(watch["artist_url"],
                                     watch.get("watermark") or 0, limit=limit)
    fresh = [r for r in releases if r.release_id not in known]

    added = 0
    for release in fresh[:FEED_CAP]:
        if store.feed_add(watch["id"], release.provider, release.release_id,
                          release.title, artist=release.artist or watch["artist_name"],
                          release_type=release.release_type,
                          track_count=release.track_count,
                          release_date=release.release_date, artwork=release.artwork,
                          source_url=release.source_url):
            added += 1
    store.watch_remember(watch["id"], [r.release_id for r in releases])
    store.watch_mark(watch["id"], checked=True)
    if added:
        store.log(f"{added} new from {watch['artist_name']}", "ok", f"watch:{watch['id']}")
    return {"watch_id": watch["id"], "artist": watch["artist_name"],
            "found": len(releases), "new": added}


def check_all(store, *, budget: int = 2) -> list[dict]:
    """Check the follows that are due, a couple at a time so one slow artist
    cannot hold the scheduler."""
    out = []
    for watch in store.watch_due()[:budget]:
        try:
            out.append(check(store, watch))
        except Exception as exc:                      # noqa: BLE001
            store.watch_mark(watch["id"], checked=True)   # do not hammer a broken follow
            store.log(f"check failed for {watch['artist_name']}: {exc}", "error",
                      f"watch:{watch['id']}")
            out.append({"watch_id": watch["id"], "artist": watch["artist_name"],
                        "error": str(exc)})
    return out
