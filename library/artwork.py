"""Cover art for a track, from the deduplicated store.

SoundCloud downloads often have no artwork URL in the database, but the mp3
usually carries the cover as an attached picture. The library keeps one copy of
each distinct image in `<library>-artwork/<hash>.<ext>` and a 640 px JPEG inside
every track, so a row is answered from `tracks.art_hash` without touching the
audio file at all — and twenty tracks sharing a cover share one image and one
640 px cache file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from library import art_store

CACHE = art_store.CACHE
# Wide enough for the album page's cover and the fullscreen disc, small enough that a
# locally extracted fallback is not a 3000 px file per track.
WIDTH = art_store.WIDTH


def for_track(store, track_id: int) -> Path | None:
    """Path to a cached JPEG for this track, or None when the file has no cover."""
    CACHE.mkdir(parents=True, exist_ok=True)
    row = store.conn.execute("SELECT file, art_hash, art_checked FROM tracks WHERE id=?",
                             (track_id,)).fetchone()
    if row is None:
        return None
    key = row["art_hash"] or ""
    if key:
        jpeg = art_store.display(store, key)
        if jpeg is not None:
            return jpeg
    elif row["art_checked"]:
        return None                       # the sweep already looked: there is no cover
    source = Path(row["file"] or "")
    if not source.is_file():
        return None

    # Not in the store yet: take the picture out of the file once, register it, and
    # answer from the store from then on. The sweep slims the file itself.
    junk = CACHE / f"{track_id}@640.none"
    if junk.exists():
        return None
    temp = CACHE / f"{track_id}.part.jpg"
    try:
        # Embedded covers are often 3000 px (600 KB); 640 keeps the album hero and
        # the fullscreen disc sharp and still shrinks to a row thumbnail fine.
        subprocess.run(
            # Embedded covers are often 3000 px (600 KB); 640 keeps the album hero and
            # the fullscreen disc sharp and still shrinks to a row thumbnail fine.
            ["ffmpeg", "-v", "error", "-y", "-i", str(source),
             "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale=min({WIDTH}\\,iw):-2",
             "-q:v", "4", "-f", "image2", str(temp)],
            capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if not (temp.is_file() and art_store._is_image(temp)):
        temp.unlink(missing_ok=True)
        junk.write_text("no embedded cover\n")
        return None
    jpeg = temp.read_bytes()
    temp.unlink(missing_ok=True)
    # The store keeps the picture that is actually in the file, at full size: the
    # cache copy above is a scaled-down JPEG and would be a poor master.
    try:
        pictures = art_store.id3art.covers(source.read_bytes())
    except (OSError, art_store.id3art.Unsupported):
        pictures = []
    key = art_store.put(store, pictures[0][1]) if pictures else art_store.put(store, jpeg)
    if pictures:
        with store.lock:
            store.conn.execute("UPDATE tracks SET art_hash=? WHERE id=?", (key, track_id))
            store.conn.commit()
    shared = art_store.display(store, key)
    if shared is not None:
        return shared
    cached = CACHE / f"{track_id}@{WIDTH}.jpg"
    cached.write_bytes(jpeg)
    return cached