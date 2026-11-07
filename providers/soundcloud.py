"""SoundCloud provider.

Sets, single tracks, artist pages and search are all yt-dlp extractors, and every
resolved track carries its own audio URL — so this is the one provider where
"download" needs no matching step at all.
"""

from __future__ import annotations

import re

from downloaders import ytdlp
from providers.base import Provider, Release, TrackRef


class SoundcloudProvider(Provider):
    name = "soundcloud"
    direct_audio = True
    patterns = (r"^https?://(?:www\.|m\.)?soundcloud\.com/",)

    def resolve(self, url: str) -> Release:
        info = ytdlp.json_info(url)
        entries = info.get("entries") or [info]
        tracks: list[TrackRef] = []
        for entry in entries:
            if not entry:
                continue
            entry = dict(entry)
            title = (entry.get("title") or "").strip()
            # yt-dlp flat sets may expose only an API URL whose final path is
            # the numeric provider ID. That is an identifier, never a title.
            if not title or title == str(entry.get("id") or ""):
                source = entry.get("webpage_url") or entry.get("url") or ""
                title = _title_from_url(source)
                if not title and source:
                    detail = ytdlp.json_info(source, full=True)
                    entry.update({k: v for k, v in detail.items() if v is not None})
                    title = (entry.get("title") or "").strip()
                    if title == str(entry.get("id") or ""):
                        title = ""
                if not title:
                    raise RuntimeError("SoundCloud track metadata has no usable title; retry import")
            if not title or title == "[Private track]":
                continue
            source = entry.get("webpage_url") or entry.get("url") or ""
            tracks.append(TrackRef(
                provider=self.name,
                provider_track_id=str(entry.get("id") or ""),
                title=title,
                # A flat entry often carries no uploader at all. Reading the
                # uploader off the permalink beats stamping the account that
                # published the set on every track, which is what the person
                # who owns the set ends up starring at in the library.
                artist=(entry.get("artist") or entry.get("uploader")
                        or entry.get("channel") or _artist_from_url(source)).strip(),
                album=(info.get("title") or "").strip() if info.get("entries") else "",
                track_number=len(tracks) + 1,
                duration=entry.get("duration") or 0,
                artwork=entry.get("thumbnails", [{}])[-1].get("url", "") if entry.get("thumbnails") else "",
                source_url=source,
            ))
        title = (info.get("title") or "SoundCloud set").strip()
        # A flat listing has no per-track artist either, so the account that
        # published the set is the honest fallback (Monstercat, not "Unknown").
        fallback_artist = ((info.get("uploader") or "").strip()
                          or _user_from_url(url))
        for track in tracks:
            if not track.artist:
                track.artist = fallback_artist
        return Release(
            provider=self.name,
            release_id=_release_id(url),
            title=title,
            artist=fallback_artist,
            artist_url=_artist_url(url),
            release_type="playlist" if info.get("entries") else "track",
            track_count=len(tracks),
            artwork=info.get("thumbnail") or "",
            source_url=url,
            tracks=tracks,
        )

    # -- following --
    def supports_follow(self) -> bool:
        return True

    def describe_artist(self, url: str) -> dict | None:
        """SoundCloud's numeric user id is the stable handle, not the slug."""
        artist_url = _artist_url(url)
        if not artist_url:
            return None
        info = ytdlp.json_info(artist_url, limit=1)
        artist_id = str(info.get("id") or "").strip()
        if not artist_id:
            return None
        name = (info.get("title") or "").strip()
        for suffix in (" (All)", " (Tracks)", " (Sets)", " (Albums)", " (Reposts)"):
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()
        return {"artist_id": artist_id, "artist_name": name, "artist_url": artist_url}

    def new_releases(self, artist_url: str, watermark: float,
                     limit: int = 60) -> list[Release]:
        """The artist's newest uploads, one single-track release each.

        A flat SoundCloud listing gives id, title and permalink but **no publish
        date and no duration**, so this cannot filter by `watermark` — it returns
        the newest `limit` uploads and the caller drops the ids it has already
        seen. Resolving each track individually for a timestamp would mean one
        request per track on every check, which is exactly how a follow gets an
        account rate-limited.
        """
        info = ytdlp.json_info(artist_url, limit=limit)
        artist = (info.get("title") or "").strip()
        for suffix in (" (All)", " (Tracks)", " (Sets)", " (Albums)", " (Reposts)"):
            if artist.endswith(suffix):
                artist = artist[: -len(suffix)].strip()
        fresh: list[Release] = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            title = (entry.get("title") or "").strip()
            track_id = str(entry.get("id") or "").strip()
            url = (entry.get("url") or entry.get("webpage_url") or "").strip()
            if not title or not track_id or not url or title == "[Private track]":
                continue
            fresh.append(Release(
                provider=self.name, release_id=f"track:{track_id}", title=title,
                artist=artist, artist_url=artist_url, release_type="track",
                track_count=1, source_url=url,
                tracks=[TrackRef(provider=self.name, provider_track_id=track_id,
                                 title=title, artist=artist, source_url=url,
                                 track_number=1)]))
        return fresh


def _release_id(url: str) -> str:
    m = re.search(r"soundcloud\.com/([^/?#]+)(?:/([^/?#]+))?", url)
    if not m:
        return url
    return f"{m.group(1)}/{m.group(2)}" if m.group(2) else m.group(1)


def _user_from_url(url: str) -> str:
    m = re.search(r"soundcloud\.com/([^/?#]+)", url or "")
    return re.sub(r"[-_]+", " ", m.group(1)).title() if m else ""


def _title_from_url(url: str) -> str:
    from urllib.parse import urlparse, unquote
    parsed = urlparse(url or "")
    if parsed.hostname not in {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}:
        return ""
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[-1].isdigit():
        return ""
    slug = unquote(parts[-1])
    slug = re.sub(r"-\d+$", "", slug)          # SoundCloud's duplicate marker
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _artist_from_url(url: str) -> str:
    """The uploader from a track permalink — the path's first segment.

    Only for `/user/slug`; a bare `/user` is the artist page, whose own
    sentence in the set (`uploader`) is already the answer.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url or "")
    if parsed.hostname not in {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}:
        return ""
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or not parts[0]:
        return ""
    return re.sub(r"[-_]+", " ", parts[0]).strip().title()


def _artist_url(url: str) -> str:
    m = re.search(r"https?://(?:www\.|m\.)?soundcloud\.com/([^/?#]+)", url)
    return f"https://soundcloud.com/{m.group(1)}" if m else ""

