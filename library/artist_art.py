"""Real artist pictures, fetched once and stored by artist name.

The UI falls back to an album cover when there is no picture. This fills the gap
for SoundCloud (profile avatar) and Apple Music (artist page image), keyed by the
normalised display name the library actually groups by. A manual upload always
wins and is never overwritten by the automatic fetch.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def name_key(name: str) -> str:
    return " ".join((name or "").casefold().split())


def _page(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(900_000).decode("utf-8", "replace")


def _og_image(html: str) -> str:
    match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    return match.group(1) if match else ""


def soundcloud_avatar(url: str) -> str:
    """The profile avatar from a SoundCloud user page, or ''."""
    if not url:
        return ""
    try:
        html = _page(url)
    except Exception:
        return ""
    image = _og_image(html)
    if image:
        return image
    match = re.search(r'"avatar_url":"([^"]+)"', html)
    return match.group(1).replace("\\/", "/") if match else ""


def apple_avatar(name: str) -> str:
    """Apple Music artist image, found through the public iTunes search API."""
    if not name:
        return ""
    try:
        query = urllib.parse.quote(name)
        raw = _page(f"https://itunes.apple.com/search?term={query}&entity=musicArtist&limit=1")
        results = (json.loads(raw) or {}).get("results") or []
        link = (results[0].get("artistLinkUrl") or "") if results else ""
        if not link:
            return ""
        html = _page(link)
    except Exception:
        return ""
    image = _og_image(html)
    # Apple returns a 1200x630 crop; ask for a square so a round avatar is not
    # a thin horizontal slice of the face.
    return re.sub(r"/1200x630[A-Za-z]*\.(png|jpg|jpeg)$", r"/600x600bb.\1", image)


def fetch(store, name: str, provider: str, url: str = "") -> str:
    """Fetch once, store, and return the picture (or '')."""
    key = name_key(name)
    if not key:
        return ""
    with store.lock:
        row = store.conn.execute(
            "SELECT artwork, manual FROM artist_art WHERE name_key=?", (key,)).fetchone()
    if row and (row["manual"] or row["artwork"]):
        return row["artwork"] if not row["manual"] else ""
    art = ""
    if provider == "soundcloud":
        art = soundcloud_avatar(url)
    elif provider == "apple":
        art = apple_avatar(name)
    if art:
        store.set_artist_art(name, art, provider=provider)
    return art


_warming = False


def warm(store, limit: int = 25) -> None:
    """Fetch pictures for artists that have none, a few at a time.

    Called from the library endpoint in a daemon thread: a page load fills a batch,
    the next load fills the next. A stored picture means the artist is never
    requested again, so this converges instead of repeating.
    """
    global _warming
    if _warming:
        return
    _warming = True
    try:
        import threading
        threading.Thread(target=_warm_batch, args=(store, limit), daemon=True).start()
    except Exception:
        _warming = False


def _warm_batch(store, limit: int) -> None:
    global _warming
    try:
        import time
        from urllib.parse import urlparse
        with store.lock:
            rows = [dict(r) for r in store.conn.execute(
                "SELECT artist_name, provider, MAX(source_url) AS source_url FROM tracks"
                " WHERE file<>'' AND artist_name<>'' GROUP BY lower(trim(artist_name))")]
        known = store.artist_art_map()
        batch = [r for r in rows if name_key(r["artist_name"]) not in known][:limit]
        for row in batch:
            provider = row.get("provider") or ""
            source = row.get("source_url") or ""
            url = ""
            if provider == "soundcloud":
                match = re.search(r"soundcloud\.com/([^/?#]+)", source)
                slug = match.group(1) if match else ""
                if slug and slug not in ("tracks", "playlists", "sets", "discover", "you"):
                    url = f"https://soundcloud.com/{slug}"
                elif "api-v2.soundcloud.com/tracks/" in source:
                    # Many rows store the API track URL, which carries no uploader
                    # page. yt-dlp resolves it to the real uploader.
                    try:
                        from downloaders import ytdlp
                        info = ytdlp.json_info(source, limit=1)
                        url = info.get("uploader_url") or info.get("webpage_url") or ""
                    except Exception:
                        url = ""
            art = soundcloud_avatar(url) if provider == "soundcloud" else \
                  apple_avatar(row["artist_name"]) if provider == "apple" else ""
            if art:
                store.set_artist_art(row["artist_name"], art, provider=provider)
            time.sleep(0.6)
    except Exception:
        pass
    finally:
        _warming = False


def apple_artist_url(name: str) -> str:
    """The Apple Music artist page for a name, from the public search API."""
    if not name:
        return ""
    try:
        query = urllib.parse.quote(name)
        raw = _page(f"https://itunes.apple.com/search?term={query}&entity=musicArtist&limit=1")
        results = (json.loads(raw) or {}).get("results") or []
        return (results[0].get("artistLinkUrl") or "") if results else ""
    except Exception:
        return ""


def provider_url(store, name: str, provider: str) -> str:
    """One followable artist page, resolved only when asked for."""
    if provider == "apple":
        return apple_artist_url(name)
    if provider != "soundcloud":
        return ""
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT source_url FROM tracks WHERE provider='soundcloud'"
            " AND lower(trim(artist_name))=lower(trim(?)) AND file<>''"
            " ORDER BY added DESC LIMIT 20", (name,))]
    for row in rows:
        source = row["source_url"] or ""
        match = re.search(r"soundcloud\.com/([^/?#]+)", source)
        slug = match.group(1) if match else ""
        if slug and slug not in ("tracks", "playlists", "sets", "discover", "you"):
            return f"https://soundcloud.com/{slug}"
        if "api-v2.soundcloud.com/tracks/" in source:
            try:
                from downloaders import ytdlp
                info = ytdlp.json_info(source, limit=1)
            except Exception:
                continue
            url = (info.get("uploader_url") or "").strip()
            if url:
                return url
    return ""


def provider_urls(store, name: str) -> dict:
    """Followable artist pages for a library artist, resolved only when asked.

    Track artists usually have no `artists` row (that table holds the set's
    uploader), so an artist page has no link to follow. This finds one from the
    artist's own tracks instead: the SoundCloud uploader behind the newest track,
    and the Apple artist page matching the name.
    """
    return {"soundcloud": provider_url(store, name, "soundcloud"),
            "apple": provider_url(store, name, "apple")}


def candidates(store, name: str) -> dict:
    """Both provider pictures for one artist, for the admin picker."""
    sc = ""
    with store.lock:
        row = store.conn.execute(
            "SELECT source_url FROM tracks WHERE provider='soundcloud'"
            " AND lower(trim(artist_name))=lower(trim(?)) AND file<>''"
            " ORDER BY added DESC LIMIT 1", (name,)).fetchone()
    if row:
        source = row["source_url"] or ""
        match = re.search(r"soundcloud\.com/([^/?#]+)", source)
        slug = match.group(1) if match else ""
        url = ""
        if slug and slug not in ("tracks", "playlists", "sets", "discover", "you"):
            url = f"https://soundcloud.com/{slug}"
        elif "api-v2.soundcloud.com/tracks/" in source:
            try:
                from downloaders import ytdlp
                info = ytdlp.json_info(source, limit=1)
                url = info.get("uploader_url") or info.get("webpage_url") or ""
            except Exception:
                url = ""
        if url:
            sc = soundcloud_avatar(url)
    return {"soundcloud": sc, "apple": apple_avatar(name)}
