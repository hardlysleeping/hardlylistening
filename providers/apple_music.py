"""Apple Music — discovery and metadata only.

There is no yt-dlp extractor for Apple Music, so this provider never downloads.
It reads the public tracklist out of the page's own serialized-server-data (no
auth, no developer token) and leaves acquiring the audio to the resolver.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import urllib.request

from providers.base import Provider, Release, TrackRef

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
STOREFRONT = re.compile(r"music\.apple\.com/([a-z]{2})/", re.I)
_TOKEN: str | None = None


def _get(url: str, timeout: int = 40) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def artwork(raw) -> str:
    """Apple hands artwork flat or wrapped in {"dictionary": {...}}."""
    if not raw:
        return ""
    art = raw.get("dictionary") or raw
    url = art.get("url") or ""
    return (url.replace("{w}", "600").replace("{h}", "600")
            .replace("{f}", "jpg").replace("{c}", "bb"))


def page_data(url: str) -> tuple[dict, str]:
    page = _get(url)
    match = re.search(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>',
                      page, re.S)
    if not match:
        raise RuntimeError("could not read the Apple Music page")
    return json.loads(html_mod.unescape(match.group(1)))["data"][0]["data"], page


def api_token() -> str | None:
    """The public MusicKit token the web player itself uses.

    Necessary only for playlists whose tracklist does not fit on the page.
    """
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    try:
        page = _get("https://music.apple.com/us/browse")
        match = re.search(r'src="(/assets/index[^"]+\.js)"', page)
        if not match:
            return None
        bundle = _get("https://music.apple.com" + match.group(1), timeout=60)
        tokens = re.findall(r"eyJ[A-Za-z0-9_.-]{200,}", bundle)
        if tokens:
            _TOKEN = tokens[0]
    except Exception:
        return None
    return _TOKEN


def catalog(path: str, timeout: int = 40) -> dict | None:
    token = api_token()
    if not token:
        return None
    req = urllib.request.Request(
        "https://amp-api.music.apple.com" + path,
        headers={"Authorization": f"Bearer {token}",
                 "Origin": "https://music.apple.com", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _lookup(artist_id: str, store: str = "us", *, entity: str = "",
            limit: int = 200) -> list[dict]:
    """Public iTunes lookup — no auth, no token, dates included."""
    url = (f"https://itunes.apple.com/lookup?id={artist_id}&country={store}"
           f"&limit={limit}")
    if entity:
        url += f"&entity={entity}"
    try:
        payload = json.loads(_get(url))
    except Exception:
        return []
    return payload.get("results") or []


def _track_from_lockup(item: dict) -> TrackRef | None:
    title = (item.get("title") or "").strip()
    if not title:
        return None
    links = item.get("subtitleLinks") or []
    artist = (links[0].get("title") if links else "") or ""
    store_id = re.search(r"(\d{4,})$", item.get("id") or "")
    return TrackRef(
        provider="apple",
        provider_track_id=store_id.group(1) if store_id else "",
        title=title,
        artist=artist.strip(),
        duration=round((item.get("duration") or 0) / 1000.0, 1),
        artwork=artwork(item.get("artwork")),
        source_url="",          # resolved by search, never downloaded from Apple
    )


class AppleMusicProvider(Provider):
    name = "apple"
    direct_audio = False          # every track must be matched to a real source
    patterns = (r"^https?://music\.apple\.com/", r"^https?://itunes\.apple\.com/")

    def resolve(self, url: str) -> Release:
        data, page = page_data(url)
        sections = data.get("sections") or []

        if "/song/" in url:
            head = next((s["items"][0] for s in sections
                         if s.get("itemKind") == "songDetailHeader" and s.get("items")), None)
            if not head:
                raise RuntimeError("could not read that Apple Music song")
            store_id = (re.search(r"/(\d+)(?:\?|$)", url) or [None, ""])[1]
            track = TrackRef(provider=self.name, provider_track_id=store_id,
                             title=(head.get("title") or "").strip(),
                             artist=(head.get("artists") or "").strip(),
                             artwork=artwork(head.get("artwork")),
                             track_number=1)
            return Release(provider=self.name, release_id=store_id or url,
                           title=track.title, artist=track.artist,
                           release_type="track", release_date="",
                           track_count=1, artwork=track.artwork,
                           source_url=url, tracks=[track])

        title = "Apple Music playlist"
        art = ""
        release_artist = ""
        tracks: list[TrackRef] = []
        for section in sections:
            kind = section.get("itemKind")
            items = section.get("items") or []
            if kind in ("containerDetailHeaderLockup", "albumDetailHeaderLockup") and items:
                head = items[0]
                title = (head.get("title") or title).strip()
                art = artwork(head.get("artwork")) or art
                # Album pages carry the artist on the header only; their track
                # lockups have no subtitleLinks, so every track would otherwise
                # be filed under "Unknown Artist".
                links = head.get("subtitleLinks") or []
                release_artist = (links[0].get("title") if links else "") or release_artist
            elif kind in ("trackLockup", "albumTrackLockup"):
                for item in items:
                    track = _track_from_lockup(item)
                    if track:
                        track.track_number = len(tracks) + 1
                        track.album = title
                        tracks.append(track)

        # Pasting one song off an album (…?i=<id>) must not queue the album.
        only = re.search(r"[?&]i=(\d+)", url)
        if only and any(t.provider_track_id == only.group(1) for t in tracks):
            tracks = [t for t in tracks if t.provider_track_id == only.group(1)]
        for index, track in enumerate(tracks):
            track.track_number = index + 1
            if not track.artist:
                track.artist = release_artist

        declared = len(tracks)
        found = re.search(r"(\d+)\s+songs?\b", page)
        if found and not only:
            declared = int(found.group(1))
        if declared and len(tracks) < declared:
            tracks.extend(self._from_api(url, {t.provider_track_id for t in tracks},
                                         declared))

        release_id = (re.search(r"/(pl\.[0-9a-fA-F]+|album/[^/]+/\d+|\d+)", url)
                      or [None, url])[1]
        return Release(provider=self.name, release_id=str(release_id), title=title,
                       artist=release_artist,
                       release_type="playlist" if "/playlist/" in url else "album",
                       track_count=len(tracks), artwork=art, source_url=url,
                       tracks=tracks)

    def _from_api(self, url: str, seen: set[str], declared: int) -> list[TrackRef]:
        """Only for playlists too long to fit on the page."""
        store = (STOREFRONT.search(url) or [None, "us"])[1]
        playlist = re.search(r"/pl\.[A-Za-z0-9._-]+", url)
        if not playlist:
            return []
        out: list[TrackRef] = []
        base = f"/v1/catalog/{store}/playlists/{playlist.group(0)[1:]}?include=tracks"
        for offset in range(0, declared + 50, 50):
            result = catalog(f"{base}&offset={offset}")
            if not result or not result.get("data"):
                break
            related = (result["data"][0].get("relationships") or {}).get("tracks") or {}
            added = 0
            for item in related.get("data") or []:
                attrs = item.get("attributes") or {}
                store_id = item.get("id") or ""
                if store_id in seen:
                    continue
                seen.add(store_id)
                out.append(TrackRef(provider=self.name, provider_track_id=store_id,
                                    title=attrs.get("name") or "",
                                    artist=attrs.get("artistName") or "",
                                    duration=round((attrs.get("durationInMillis") or 0) / 1000),
                                    track_number=len(seen)))
                added += 1
            if not added:
                break
        return out

    # -- following --
    def tracks(self, url: str) -> list[TrackRef]:
        """An album's songs, from the same public lookup the feed already uses.

        One request, measured at ~0.4 s for a 15-track album; a single song link
        comes back as its own one-entry list.
        """
        album = re.search(r"/album/(?:[^/]+/)?(\d{4,})", url)
        song = re.search(r"/song/(?:[^/]+/)?(\d{4,})", url)
        target = album or song
        if not target:
            return []
        store = (STOREFRONT.search(url) or [None, "us"])[1].lower()
        rows = _lookup(target.group(1), store,
                       entity="" if song else "song", limit=200)
        out: list[TrackRef] = []
        for row in rows:
            if row.get("wrapperType") != "track":
                continue
            title = (row.get("trackName") or "").strip()
            if not title:
                continue
            art = str(row.get("artworkUrl100") or "")
            out.append(TrackRef(
                provider="apple",
                provider_track_id=str(row.get("trackId") or ""),
                title=title,
                artist=(row.get("artistName") or "").strip(),
                album=(row.get("collectionName") or "").strip(),
                track_number=int(row.get("trackNumber") or 0),
                duration=round((row.get("trackTimeMillis") or 0) / 1000.0, 1),
                artwork=(art.replace("100x100", "600x600") if art else ""),
                # The song's own page: queueing it downloads that one song.
                source_url=str(row.get("trackViewUrl") or "")))
        out.sort(key=lambda t: t.track_number)
        return out

    def supports_follow(self) -> bool:
        return True

    def describe_artist(self, url: str) -> dict | None:
        """Apple's numeric artist id, read from the URL and confirmed by lookup."""
        # `[^/]*/?` let the greedy prefix eat the id and capture only its last
        # four digits, which silently produced an empty lookup.
        match = (re.search(r"/artist/(?:[^/]+/)?(\d{4,})(?:[/?#]|$)", url)
                 or re.search(r"[?&]id=(\d{4,})", url)
                 or re.search(r"artistId=(\d{4,})", url))
        if not match:
            return None
        artist_id = match.group(1)
        store = (STOREFRONT.search(url) or [None, "us"])[1].lower()
        rows = _lookup(artist_id, store, entity="", limit=1)
        name = ""
        if rows:
            name = (rows[0].get("artistName") or "").strip()
        if not name:
            return None
        return {"artist_id": artist_id, "artist_name": name,
                "artist_url": f"https://music.apple.com/{store}/artist/{artist_id}"}

    def new_releases(self, artist_url: str, watermark: float,
                     limit: int = 60) -> list[Release]:
        """Albums, EPs and singles from the public iTunes lookup.

        Unlike SoundCloud, Apple hands us real release dates — but the feed still
        keys on the seen-set, so a date is used for ordering and display only.
        """
        match = (re.search(r"/artist/(?:[^/]+/)?(\d{4,})(?:[/?#]|$)", artist_url)
                 or re.search(r"artistId=(\d{4,})", artist_url))
        if not match:
            return []
        artist_id = match.group(1)
        store = (STOREFRONT.search(artist_url) or [None, "us"])[1].lower()
        rows = _lookup(artist_id, store, entity="album", limit=200)
        if not rows:
            return []
        artist = ""
        collections = []
        for row in rows:
            if row.get("wrapperType") == "artist":
                artist = (row.get("artistName") or "").strip()
                continue
            if row.get("wrapperType") != "collection" or not row.get("collectionId"):
                continue
            collections.append(row)
        collections.sort(key=lambda r: r.get("releaseDate") or "", reverse=True)

        fresh: list[Release] = []
        seen: set[tuple] = set()
        for row in collections:
            # Apple appends the type to the name ("… - Single"); the feed row
            # already carries release_type, so drop the suffix.
            title = re.sub(r"\s*-\s*(Single|EP)\s*$", "",
                           (row.get("collectionName") or "").strip(), flags=re.I)
            date = (row.get("releaseDate") or "")[:10]
            # Re-uploads and storefront duplicates share a title and a date; one
            # row in the feed is enough, and clicking it queues the fuller one.
            key = (title.lower(), date)
            if not title or key in seen:
                continue
            seen.add(key)
            count = int(row.get("trackCount") or 0)
            kind = "album"
            if count <= 1:
                kind = "single"
            elif count <= 6:
                kind = "ep"
            fresh.append(Release(
                provider=self.name,
                release_id=f"album:{row['collectionId']}",
                title=title,
                artist=artist,
                artist_url=artist_url,
                release_type=kind,
                release_date=date,
                track_count=count,
                artwork=(row.get("artworkUrl100") or "").replace("100x100bb", "600x600bb"),
                source_url=(row.get("collectionViewUrl") or "").split("?")[0],
                tracks=[]))
            if len(fresh) >= limit:
                break
        return fresh
