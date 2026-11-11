"""One-time fill of artist pictures for the artists already in the library.

Safe to re-run: artists that already have a picture (automatic or manual) are
skipped, so nothing is fetched twice.
"""
import sys
import time

sys.path.insert(0, "/var~/apps/hardlylistening")
from db import Store              # noqa: E402
from library import artist_art    # noqa: E402

store = Store()
total = 0
while True:
    before = len(store.artist_art_map())
    artist_art._warm_batch(store, 200)
    after = len(store.artist_art_map())
    total += max(0, after - before)
    print(f"artists with art: {after} (+{after - before})", flush=True)
    if after == before:
        break
    time.sleep(1)

print(f"done, {total} added", flush=True)
