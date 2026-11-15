"""Give every artist in the library a profile page where one can be derived.

Only artists.url is written; track rows and identities are never touched.
SoundCloud pages come from the track URL with no network call; anything else costs
one bounded yt-dlp lookup per artist (--limit / --sleep).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from urllib.parse import urlsplit

import db
from downloaders import ytdlp


def slug_from_soundcloud(source_url):
    """https://soundcloud.com/someone/track-name → https://soundcloud.com/someone."""
    try:
        parts = urlsplit(source_url or "")
    except ValueError:
        return ""
    if "soundcloud.com" not in (parts.hostname or ""):
        return ""
    segments = [s for s in parts.path.split("/") if s]
    if not segments or segments[0] in {"you", "discover", "search", "stream", "charts", "tracks", "albums", "sets"}:
        return ""
    return f"https://soundcloud.com/{segments[0]}"


def artists_missing(store):
    return [dict(r) for r in store.conn.execute(
        "SELECT lower(t.artist_name) AS key, MIN(t.artist_name) AS name,"
        "       MIN(t.provider) AS provider, COUNT(*) AS tracks"
        " FROM tracks t"
        " WHERE COALESCE(t.artist_name,'')<>''"
        "   AND NOT EXISTS (SELECT 1 FROM artists a"
        "                   WHERE lower(a.name)=lower(t.artist_name)"
        "                     AND COALESCE(a.url,'')<>'')"
        " GROUP BY lower(t.artist_name) ORDER BY tracks DESC")]


def source_for(store, name):
    row = store.conn.execute(
        "SELECT source_url, provider FROM tracks WHERE lower(artist_name)=lower(?)"
        " AND COALESCE(source_url,'')<>'' ORDER BY file<>'' DESC, added DESC LIMIT 1",
        (name,)).fetchone()
    return (row["source_url"], row["provider"]) if row else ("", "")


def lookup(url):
    info = ytdlp.json_info(url, limit=1, timeout=25) or {}
    entries = info.get("entries") or [info]
    first = entries[0] if entries else {}
    return ((info.get("uploader_url") or info.get("channel_url")
             or first.get("uploader_url") or first.get("channel_url") or ""),
            str(info.get("uploader_id") or info.get("channel_id")
                or first.get("uploader_id") or first.get("channel_id") or ""))


def save(store, name, provider, url, artist_id=""):
    with store.lock:
        existing = store.conn.execute(
            "SELECT id, artist_id FROM artists WHERE lower(name)=lower(?) LIMIT 1", (name,)).fetchone()
        if existing:
            store.conn.execute(
                "UPDATE artists SET url=?, artist_id=CASE WHEN ?<>'' THEN ? ELSE artist_id END"
                " WHERE id=?", (url, artist_id, artist_id, existing["id"]))
        else:
            store.conn.execute(
                "INSERT INTO artists(provider,artist_id,name,url) VALUES(?,?,?,?)"
                " ON CONFLICT(provider,artist_id) DO UPDATE SET"
                " url=COALESCE(NULLIF(excluded.url,''),artists.url)", 
                (provider or "generic", artist_id or ("name:" + name.lower()), name, url))
        store.conn.commit()


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=40,
                        help="network lookups this run (SoundCloud URLs are free)")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    store = db.Store()
    pending = artists_missing(store)
    print(f"{len(pending)} artists have no profile page")
    free = network = none = failed = 0
    budget = args.limit
    for row in pending:
        name, provider = row["name"], row["provider"]
        source, track_provider = source_for(store, name)
        url = slug_from_soundcloud(source)
        artist_id = ""
        if url:
            free += 1
        elif budget > 0 and source:
            try:
                url, artist_id = lookup(source)
                budget -= 1
                network += 1
                time.sleep(args.sleep)
            except Exception as exc:
                failed += 1
                print(f"  ! {name} — {type(exc).__name__}: {exc}")
                continue
        if not url:
            none += 1
            if args.verbose:
                print(f"  · {name} ({track_provider}) — no derivable page")
            continue
        if args.verbose or free + network <= 25:
            print(f"  + {name} → {url}")
        if not args.dry_run:
            save(store, name, track_provider or provider, url, artist_id)
    print(f"derived {free + network} ({free} from SoundCloud URLs, {network} by lookup),"
          f" none {none}, failed {failed}"
          + (" — dry run, nothing written" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
