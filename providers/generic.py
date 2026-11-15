"""Fallback provider: anything else yt-dlp can read.

Bandcamp albums, YouTube playlists, a bare track URL. Same contract, no service
knowledge — if yt-dlp resolves it, it is one release with a direct audio URL.
"""

from __future__ import annotations

import re

from downloaders import ytdlp
from providers.base import Provider, Release, TrackRef


class GenericProvider(Provider):
    name = "generic"
    direct_audio = True
    patterns = ()

    def identify(self, url: str) -> bool:
        # Last in the registry: claimed by default, for any http(s) URL.
        return bool(re.match(r"^https?://", url, re.I))

    def provider_name(self, url: str) -> str:
        host = (re.search(r"https?://([^/]+)", url) or [None, ""])[1].lower()
        for key, name in (("bandcamp", "bandcamp"), ("youtube", "youtube"),
                          ("youtu.be", "youtube")):
            if key in host:
                return name
        return "generic"

    def resolve(self, url: str) -> Release:
        info = ytdlp.json_info(url, timeout=90)
        if not info:
            raise RuntimeError("yt-dlp could not read that link — it may be private, "
                               "removed, or not a page it supports")
        entries = info.get("entries") or [info]
        name = self.provider_name(url)
        tracks: list[TrackRef] = []
        for entry in entries:
            if not entry or not entry.get("title"):
                continue
            tracks.append(TrackRef(
                provider=name,
                provider_track_id=str(entry.get("id") or ""),
                title=entry["title"].strip(),
                artist=(entry.get("uploader") or entry.get("artist") or "").strip(),
                album=(entry.get("album") or "").strip(),
                track_number=len(tracks) + 1,
                duration=entry.get("duration") or 0,
                artwork=entry.get("thumbnail") or "",
                source_url=entry.get("webpage_url") or entry.get("url") or url,
            ))
        # The uploader's own page, so an artist pulled from a plain link still has a
        # profile to follow. yt-dlp names it differently per site.
        artist_url = (info.get("uploader_url") or info.get("channel_url")
                      or entries[0].get("uploader_url") or entries[0].get("channel_url") or "")
        artist_id = (info.get("uploader_id") or info.get("channel_id")
                     or entries[0].get("uploader_id") or entries[0].get("channel_id") or "")
        return Release(provider=name, release_id=str(info.get("id") or url),
                       title=(info.get("title") or "Playlist").strip(),
                       artist=(info.get("uploader") or "").strip(),
                       artist_id=str(artist_id),
                       artist_url=artist_url,
                       release_type="playlist" if info.get("entries") else "track",
                       track_count=len(tracks), artwork=info.get("thumbnail") or "",
                       source_url=url, tracks=tracks)
