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
    # snd.sc is SoundCloud's other short domain; it resolves to the same pages.
    patterns = (r"^https?://(?:www\.|m\.|on\.)?soundcloud\.com/",
                r"^https?://(?:www\.)?snd\.sc/")

    def resolve(self, url: str) -> Release:
        # A clipboard/social share link (on.soundcloud.com/XXXX) is an opaque
        # token, not a permalink: its path says nothing about the set. Follow
        # the redirect first, so release ids, the artist and the per-track
        # permalinks all come from the real page.
        url = canonical_url(url)
        # 90 s, not the 300 s yt-dlp default: a stalled source must fail while the
        # person who just pasted the link is still looking at the page.
        info = ytdlp.json_info(url, timeout=90)
        if not info:
            raise RuntimeError("SoundCloud could not read that link — it may be private, "
                               "removed, or not a public track, set or profile")
        entries = info.get("entries") or [info]
        # A flat listing carries no title or duration for any track, and for
        # large or reshuffled sets it sometimes holds only api-v2 URLs instead
        # of permalinks — which yt-dlp cannot extract at all. Every untitled
        # entry is resolved by its numeric id up front, in batched API calls,
        # rather than one yt-dlp run per track.
        api_meta = _api_tracks([str(e.get("id") or "") for e in entries
                                if e and not str(e.get("title") or "").strip()])
        tracks: list[TrackRef] = []
        for entry in entries:
            if not entry:
                continue
            entry = dict(entry)
            meta = api_meta.get(str(entry.get("id") or ""))
            if meta:
                entry.update({
                    "title": meta.get("title") or "",
                    "uploader": (meta.get("user") or {}).get("username") or "",
                    "duration": (meta.get("duration") or 0) / 1000.0,
                    "webpage_url": meta.get("permalink_url") or "",
                    "thumbnail": meta.get("artwork_url") or "",
                })
            title = (entry.get("title") or "").strip()
            # yt-dlp flat sets may expose only an API URL whose final path is
            # the numeric provider ID. That is an identifier, never a title.
            if not title or title == str(entry.get("id") or ""):
                source = entry.get("webpage_url") or entry.get("url") or ""
                # yt-dlp's flat set entries often hold an api-v2 track URL;
                # resolving it as-is returns no metadata. Resolve the numeric ID
                # through the regular SoundCloud permalink instead.
                if re.match(r"https?://api-v2\.soundcloud\.com/tracks/\d+", source):
                    # Prefer the track id's real permalink from yt-dlp's own
                    # extraction; constructing one from the set owner is wrong
                    # for shared playlists containing other artists.
                    source = f"https://soundcloud.com/{entry.get('id')}"
                    detail = ytdlp.json_info(source, full=True)
                    if detail:
                        entry.update({k: v for k, v in detail.items() if v is not None})
                        source = entry.get("webpage_url") or source
                        title = (entry.get("title") or "").strip()
                if not title:
                    title = _title_from_url(source)
                if not title and source:
                    detail = ytdlp.json_info(source, full=True)
                    if detail:
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
                artwork=(((entry.get("thumbnails") or [{}])[-1].get("url") or "")
                         or entry.get("thumbnail") or ""),
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
    def tracks(self, url: str) -> list[TrackRef]:
        """A set's songs, or the single song behind a track link.

        This is the expensive one: yt-dlp has to read the set page, so it only
        ever runs when somebody opens that row.
        """
        target = canonical_url(url)
        info = ytdlp.json_info(target, timeout=120)
        entries = [e for e in (info.get("entries") or []) if e] or [info]
        if not any((e.get("title") or "").strip() for e in entries):
            # A flat listing sometimes drops the titles (short sets especially),
            # leaving nothing to show. The full extraction reads each track and
            # costs a few seconds, so it only happens when the cheap pass failed.
            info = ytdlp.json_info(target, timeout=180, full=True)
            entries = [e for e in (info.get("entries") or []) if e] or [info]
        out: list[TrackRef] = []
        for entry in entries:
            title = (entry.get("title") or "").strip()
            target = (entry.get("webpage_url") or entry.get("url") or "").strip()
            if not title or not target or title == "[Private track]":
                continue
            out.append(TrackRef(
                provider="soundcloud",
                provider_track_id=str(entry.get("id") or ""),
                title=title,
                artist=(entry.get("uploader") or "").strip(),
                duration=float(entry.get("duration") or 0),
                source_url=target))
        return out

    def supports_follow(self) -> bool:
        return True

    def describe_artist(self, url: str) -> dict | None:
        """SoundCloud's numeric user id is the stable handle, not the slug."""
        artist_url = _artist_url(canonical_url(url))
        if not artist_url:
            return None
        info = ytdlp.json_info(artist_url, limit=1)
        if not info:
            return None
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
        if not info:
            return []
        entries = [e for e in (info.get("entries") or []) if e]
        artist = (info.get("title") or "").strip()
        for suffix in (" (All)", " (Tracks)", " (Sets)", " (Albums)", " (Reposts)"):
            if artist.endswith(suffix):
                artist = artist[: -len(suffix)].strip()
        # The flat listing has no dates, so the feed could not say which upload
        # was newest. One batched API call per 50 ids carries the real publish
        # date and the artwork, which is what the feed sorts and draws on.
        meta_by_id = _api_tracks([str(e.get("id") or "") for e in entries])
        fresh: list[Release] = []
        for entry in entries:
            title = (entry.get("title") or "").strip()
            track_id = str(entry.get("id") or "").strip()
            url = (entry.get("url") or entry.get("webpage_url") or "").strip()
            if not title or not track_id or not url or title == "[Private track]":
                continue
            meta = meta_by_id.get(track_id) or {}
            published = str(meta.get("display_date") or meta.get("created_at") or "")
            art = str(meta.get("artwork_url") or "")
            fresh.append(Release(
                provider=self.name, release_id=f"track:{track_id}", title=title,
                artist=artist, artist_url=artist_url, release_type="track",
                release_date=published[:10], artwork=art.replace("-large", "-t500x500"),
                track_count=1, source_url=url,
                tracks=[TrackRef(provider=self.name, provider_track_id=track_id,
                                 title=title, artist=artist, source_url=url,
                                 track_number=1)]))
        fresh.sort(key=lambda r: r.release_date or "", reverse=True)
        return fresh


def _client_id() -> str:
    """SoundCloud's public client id, as yt-dlp already cached it.

    yt-dlp fetches one on any SoundCloud request and keeps it in its cache
    directory, so by the time a set is being resolved the file exists.
    """
    import json
    import os
    from pathlib import Path
    cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    try:
        data = json.loads((cache / "yt-dlp/soundcloud/client_id.json").read_text())
        return str(data.get("data") or "")
    except Exception:
        return ""


def _api_tracks(ids: list[str]) -> dict[str, dict]:
    """Metadata for numeric track ids, in one batched API call per 50.

    The flat listing gives an id and nothing else; the API answers with title,
    uploader, duration and the real permalink — which is what the downloader
    needs as a source URL.
    """
    import json
    import urllib.request

    ids = [i for i in dict.fromkeys(ids) if i.isdigit()]
    if not ids:
        return {}
    client_id = _client_id()
    if not client_id:
        return {}
    out: dict[str, dict] = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        api = ("https://api-v2.soundcloud.com/tracks?ids=" + ",".join(chunk)
               + f"&client_id={client_id}")
        req = urllib.request.Request(api, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            continue
        for track in data if isinstance(data, list) else []:
            if isinstance(track, dict) and track.get("id"):
                out[str(track["id"])] = track
    return out


def canonical_url(url: str) -> str:
    """Follow a SoundCloud share link to the real permalink.

    `on.soundcloud.com/<token>` (the clipboard / "copy link" button in the app
    and the website) is a redirector: only the Location header knows whether
    the token is a track, a set or an artist page. Resolving the token directly
    leaves yt-dlp with an empty, untitled playlist, so the redirect is followed
    here, once, before anything else looks at the URL.
    """
    if not url:
        return url
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname not in {"on.soundcloud.com", "snd.sc"}:
        return url
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        url, method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
    try:
        with opener.open(req, timeout=20) as resp:
            target = resp.headers.get("Location") or ""
    except urllib.error.HTTPError as exc:      # 3xx arrives here without a redirect handler
        target = exc.headers.get("Location") or "" if exc.code in (301, 302, 303, 307, 308) else ""
    except Exception:
        target = ""
    if not target:
        return url
    # Drop the sharing chrome the redirect carries: ref/utm/si say nothing
    # about the track and only make the stored source URL noisy.
    from urllib.parse import parse_qsl, urlencode, urlunparse
    target_parts = urlparse(target)
    clean = [(k, v) for k, v in parse_qsl(target_parts.query)
             if k not in {"ref", "si", "p", "c"} and not k.startswith("utm_")]
    return urlunparse(target_parts._replace(query=urlencode(clean), fragment=""))


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
    # A share-link token is not a slug; canonical_url() must have run first.
    from urllib.parse import urlparse
    if urlparse(url or "").hostname in {"on.soundcloud.com", "snd.sc"}:
        return ""
    m = re.search(r"https?://(?:www\.|m\.)?soundcloud\.com/([^/?#]+)", url)
    return f"https://soundcloud.com/{m.group(1)}" if m else ""

