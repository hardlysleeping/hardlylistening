"""List the latest releases for subscriptions made before the feed listed them.

A follow used to record the artist's current uploads as already seen and leave the
feed empty, so the artist page showed "nothing since subscribing". This adds that
newest batch (capped like a normal check) to the feed for subscriptions whose feed is
still empty. Safe to re-run: a follow that already has feed items is skipped.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db import Store              # noqa: E402
from discovery import subscriptions  # noqa: E402

#: Optional path so the script can be tried against a copy of the database first.
store = Store(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
listed = {(item["watch_id"], item["release_id"]) for item in store.feed_list(2000)}

for watch in store.watches():
    if any(watch_id == watch["id"] for watch_id, _ in listed):
        print(f"{watch['artist_name']}: already listed", flush=True)
        continue
    try:
        provider = subscriptions.provider_named(watch["provider"])
        releases = provider.new_releases(watch["artist_url"],
                                         watch.get("watermark") or 0, limit=60)
    except Exception as exc:                       # noqa: BLE001
        print(f"{watch['artist_name']}: skipped ({exc})", flush=True)
        continue
    added = 0
    # Newest first from the provider, stored oldest first so the feed reads the
    # same way round as a normal follow.
    for release in reversed(releases[:subscriptions.SEED_CAP]):
        if store.feed_add(watch["id"], release.provider, release.release_id,
                          release.title, artist=release.artist or watch["artist_name"],
                          release_type=release.release_type,
                          track_count=release.track_count,
                          release_date=release.release_date,
                          artwork=release.artwork, source_url=release.source_url):
            added += 1
    print(f"{watch['artist_name']}: listed {added}", flush=True)
    time.sleep(0.5)

print("done", flush=True)