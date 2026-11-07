"""Where a track file lives.

    <root>/<Artist>/<Collection>/NN - Title.ext

`Collection` is the album when the provider knows one, otherwise the playlist
that first brought the track in ("Singles" if there is neither). One recording is
one file, no matter how many playlists reference it — membership lives in
`playlist_tracks`, not in the path.

That is the deliberate difference from a per-playlist downloader: adding the same
track to a second playlist must not copy the audio.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def safe(text: str, limit: int = 90) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[/\\:*?\"<>|\x00-\x1f]", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text[:limit].rstrip(" .") or "untitled")


def collection_for(album: str, playlist_title: str) -> str:
    return safe(album or playlist_title or "Singles", 70)


def path_for(root: Path, *, artist: str, album: str, playlist_title: str,
             number: int, title: str, extension: str = "mp3") -> Path:
    artist_dir = safe(artist or "Unknown Artist", 60)
    collection = collection_for(album, playlist_title)
    stem = f"{number:02d} - {safe(title)}" if number else safe(title)
    return Path(root) / artist_dir / collection / f"{stem}.{extension.lstrip('.')}"


def unique(path: Path, taken: set[str]) -> Path:
    """Keep two different recordings with the same name from colliding."""
    if str(path) not in taken and not path.exists():
        return path
    stem, suffix, index = path.stem, path.suffix, 2
    while True:
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if str(candidate) not in taken and not candidate.exists():
            return candidate
        index += 1


def sidecar_ok(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0
