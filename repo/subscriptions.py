"""Subscribing to an artist (REPO-014) — tracking that never downloads anything.

The rule the whole feature rests on: a subscription produces **feed items**, and
only a click turns one into a download job. Nothing here calls the queue.

Detecting "new" needs a seen-set, not a timestamp: a flat SoundCloud listing
carries ids and titles but no publish dates, so a release is new when this
subscription has not looked at that id before (`watch_items`). A brand-new
subscription records the current uploads as already seen, so later checks only
report genuinely new ids — and lists that same newest batch (capped at
`FEED_CAP`) in the feed, because seeing the latest releases and picking one is
what subscribing to an artist is for.
"""

from __future__ import annotations

from providers import base as provider_base

#: Never dump an artist's whole history into the feed in one check.
FEED_CAP = 30

#: A new subscription lists only this many of the artist's latest releases.
SEED_CAP = 10


def provider_named(name: str):
    for provider in provider_base.registry():
        if provider.name == name:
            return provider
    raise ValueError(f"unknown provider {name}")


def subscribe(store, url: str, *, interval: float = 0, limit: int = 60,
              member_id: str = "") -> dict:
    """Subscribe to an artist page. Returns the row plus what was baselined."""
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
                               who["artist_name"], who["artist_url"], interval,
                               member_id=member_id)
    existed = bool(store.watch_seen(watch_id))

    baselined = 0
    listed = 0
    if not existed:
        try:
            current = provider.new_releases(who["artist_url"], 0, limit=limit)
            # The seen-set keeps future checks to genuinely new ids, but the
            # uploads are still listed in the feed: subscribing to an artist to
            # see their latest releases and pick one is the whole point.
            store.watch_remember(watch_id, [r.release_id for r in current])
            baselined = len(current)
            # Providers hand back newest first, and the feed lists highest id
            # first, so the batch is stored oldest first: read back, it reads
            # newest first, and later checks append above it.
            for release in reversed(current[:SEED_CAP]):
                if store.feed_add(watch_id, release.provider, release.release_id,
                                  release.title, artist=release.artist or who["artist_name"],
                                  release_type=release.release_type,
                                  track_count=release.track_count,
                                  release_date=release.release_date,
                                  artwork=release.artwork, source_url=release.source_url):
                    listed += 1
        except Exception as exc:                      # noqa: BLE001
            store.log(f"could not baseline “{who['artist_name']}”: {exc}", "warn")
        store.watch_mark(watch_id, checked=True)

    store.log(f"subscribed to {who['artist_name']} ({provider.name})"
              f"{f', {baselined} existing uploads marked seen' if baselined else ''}",
              "ok")
    return {"watch": store.watch_get(watch_id), "baselined": baselined,
            "listed": listed, "already": existed}


def subscribe_by_name(store, name: str, provider: str = "",
                      member_id: str = "") -> dict:
    """Subscribe to an artist whose page link is not known — resolved from the library.

    With a provider, only that provider's page is used: the artist page offers one
    button per provider, so the choice has to be honoured rather than guessed.
    """
    name = (name or "").strip()
    provider = (provider or "").strip().lower()
    if not name:
        raise ValueError("artist name required")
    if provider and provider not in ("soundcloud", "apple"):
        raise ValueError("unknown provider")
    with store.lock:
        row = store.conn.execute(
            "SELECT url FROM artists WHERE lower(name)=lower(?) AND url<>''"
            + (" AND provider=?" if provider else "")
            + " ORDER BY provider LIMIT 1",
            (name, provider) if provider else (name,)).fetchone()
    if row and row["url"]:
        return subscribe(store, row["url"], member_id=member_id)
    from library import artist_art
    order = [provider] if provider else ["soundcloud", "apple"]
    errors = []
    for candidate in order:
        url = artist_art.provider_url(store, name, candidate)
        if not url:
            errors.append(f"no {candidate} page found for this artist")
            continue
        try:
            return subscribe(store, url, member_id=member_id)
        except Exception as exc:                      # noqa: BLE001
            errors.append(str(exc))
    raise ValueError(errors[0] if errors else "no subscribable page found for this artist")


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
    """Check the subscriptions that are due, a couple at a time so one slow
    artist cannot hold the scheduler."""
    out = []
    for watch in store.watch_due()[:budget]:
        try:
            out.append(check(store, watch))
        except Exception as exc:                      # noqa: BLE001
            store.watch_mark(watch["id"], checked=True)   # do not hammer a broken one
            store.log(f"check failed for {watch['artist_name']}: {exc}", "error",
                      f"watch:{watch['id']}")
            out.append({"watch_id": watch["id"], "artist": watch["artist_name"],
                        "error": str(exc)})
    return out
