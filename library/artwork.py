"""Cover art extracted from the audio file itself.

SoundCloud downloads often have no artwork URL in the database, but the mp3
usually carries the cover as an attached picture. Extracting it locally keeps
thumbnails working without a network call and without depending on a provider
that may have deleted the track.

Extracted images are cached under `.cache/artwork/<track id>.jpg`; a track with
no embedded picture writes `<id>.none` so it is not re-probed on every scroll.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / ".cache" / "artwork"


def for_track(store, track_id: int) -> Path | None:
    """Path to a cached JPEG for this track, or None when the file has no cover."""
    CACHE.mkdir(parents=True, exist_ok=True)
    jpeg = CACHE / f"{track_id}.jpg"
    if jpeg.is_file() and jpeg.stat().st_size > 0:
        return jpeg
    if (CACHE / f"{track_id}.none").exists():
        return None
    row = store.conn.execute("SELECT file FROM tracks WHERE id = ?", (track_id,)).fetchone()
    source = Path((row["file"] if row else "") or "")
    if not source.is_file():
        return None
    temp = CACHE / f"{track_id}.part.jpg"
    try:
        subprocess.run(
            # Downscale to a thumbnail: embedded covers are often 3000 px, which
            # is 600 KB per row and pointless at 34 px.
            ["ffmpeg", "-v", "error", "-y", "-i", str(source),
             "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=160:-2",
             "-q:v", "5", "-f", "image2", str(temp)],
            capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if temp.is_file() and temp.stat().st_size > 512:
        temp.replace(jpeg)
        return jpeg
    temp.unlink(missing_ok=True)
    (CACHE / f"{track_id}.none").write_text("no embedded cover\n")
    return None
