"""Song search — SoundCloud and YouTube results, and one result into the queue.

A search is a read: it asks yt-dlp for uploads matching the words and returns them as
plain data. "Download" turns one hit into a normal single-track playlist entry with the
upload already pinned as its source, so the queue downloads exactly the file that was
chosen instead of searching for it again.
"""

from __future__ import annotations

import re
from pathlib import Path

from downloaders import ytdlp

FROM_SEARCH = "From search"
SEARCH_URL = "hl-search://from-search"
PROVIDERS = {"scsearch": "soundcloud", "ytsearch": "youtube"}


def find(query: str, limit: int = 20) -> list[dict]:
    query = (query or "").strip()
    if len(query) < 2:
        raise ValueError("type at least two characters")
    hits = ytdlp.search_all(query)
    out = []
    for hit in hits[:max(1, min(int(limit or 20), 40))]:
        out.append({
            "url": hit["url"],
            "title": hit["title"],
            "artist": hit["artist"],
            "duration": hit["duration"],
            "artwork": hit.get("artwork") or "",
            "engine": hit.get("engine") or "",
            "provider": provider_for(hit.get("engine"), hit["url"]),
            "match": round(float(hit.get("match") or 0), 3),
        })
    return out


def provider_for(engine: str | None, url: str) -> str:
    name = PROVIDERS.get(re.sub(r"\d+$", "", engine or ""))
    if name:
        return name
    return "soundcloud" if "soundcloud.com" in (url or "") else "youtube"


def queue_hit(store, queue, item: dict) -> dict:
    """Put one chosen search result into the queue, pinned to that exact upload."""
    url = (item.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("that result has no usable link")
    title = (item.get("title") or "").strip()[:300] or "Untitled"
    provider = provider_for(item.get("engine"), url)
    source_id = str(item.get("source_id") or "").strip() or url
    try:
        duration = float(item.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    playlist_id = store.playlist(FROM_SEARCH, "search", SEARCH_URL, kind="search")
    track_id = store.track(
        provider, source_id, title,
        artist_name=(item.get("artist") or "").strip()[:200],
        album=FROM_SEARCH, duration=duration,
        artwork=(item.get("artwork") or "").strip(), source_url=url)
    row = store.track_by_id(track_id) or {}
    already = bool(row.get("file") and Path(row["file"]).is_file())
    position = _next_position(store, playlist_id)
    store.add_to_playlist(playlist_id, track_id, position, reused=already)
    if already:
        return {"track_id": track_id, "title": title, "queued": False, "already": True}
    job = store.enqueue(track_id, provider, url)
    queue.wake()
    store.log(f"search: queued “{title}” from {provider}", "ok", f"playlist:{playlist_id}")
    return {"track_id": track_id, "title": title, "queued": bool(job),
            "already": False, "provider": provider}


def _next_position(store, playlist_id: int) -> int:
    with store.lock:
        row = store.conn.execute(
            "SELECT COALESCE(MAX(position),0)+1 p FROM playlist_tracks WHERE playlist_id=?",
            (playlist_id,)).fetchone()
    return row["p"] if row else 1
