"""Ranking that learns from what the owner actually picks.

The scoring formula is a guess about what "the same recording" means. Every time a
human uses a candidate, that is a fact instead of a guess, so it is recorded and
folded back into the ranking:

* the exact upload already chosen for this query goes first — he said yes to it;
* an uploader he has chosen for this artist before is lifted slightly, because the
  person who posts the right versions is usually the same person;
* how strictly versions are separated follows his own picks: if every pick so far
  named the requested version, a different named version is pushed down harder;
  if he keeps picking a differently-named version, that penalty is softened
  instead of fought.

Nothing here is a model. It is a handful of counted facts, and each one is
reversible (the table is the record).
"""
from __future__ import annotations

import json
import time

from downloaders.ytdlp import _version_tokens, hit_score, norm

EXACT = 1.0
UPLOADER_BOOST = 1.10
STRICT_MISMATCH = 0.5      # he has only ever picked the named version
DEFAULT_MISMATCH = 0.7     # copy of hit_score's own default
LOOSE_MISMATCH = 0.85      # he keeps accepting a different version


def schema(store) -> None:
    store.conn.execute("""CREATE TABLE IF NOT EXISTS source_picks (
        id INTEGER PRIMARY KEY, created REAL NOT NULL, query TEXT NOT NULL,
        track_title TEXT NOT NULL, artist TEXT NOT NULL, url TEXT NOT NULL,
        uploader TEXT NOT NULL, candidate_title TEXT NOT NULL,
        version_match INTEGER NOT NULL DEFAULT 0)""")
    store.conn.execute("CREATE INDEX IF NOT EXISTS source_picks_query ON source_picks(query)")
    store.conn.commit()


def seed_from_jobs(store) -> int:
    """Adopt the choices already made by hand before this table existed.

    A job with `pinned=1` and a resolved URL that appears in its stored candidates
    is a source someone pointed at deliberately — the same signal as pressing Use.
    """
    try:
        with store.lock:
            rows = [dict(r) for r in store.conn.execute(
                "SELECT * FROM download_jobs WHERE pinned=1 AND resolved_url<>''"
                " AND candidates<>''").fetchall()]
    except Exception:                             # noqa: BLE001
        return 0
    added = 0
    for job in rows:
        track = store.track_by_id(job["track_id"])
        if not track:
            continue
        try:
            pool = json.loads(job.get("candidates") or "[]")
        except ValueError:
            continue
        picked = next((c for c in pool if c.get("url") == job["resolved_url"]), None)
        if picked and record(store, track, picked):
            added += 1
    return added


def query_for(track: dict) -> str:
    """The key a pick is recorded under, and the query used for scoring.

    It keeps the full title on purpose: the version words in brackets are what
    tells a remix from the original, and scoring needs them. *Searching* uses the
    cleaned variants in ``ytdlp.query_variants`` — recall and scoring want
    different strings, and mixing them up broke version separation.
    """
    return " ".join(x for x in (track.get("artist_name"), track.get("title")) if x).strip()


def metadata_for(url: str) -> dict:
    """Ask yt-dlp what a pasted URL actually is, so a handbook pick is usable."""
    from downloaders import ytdlp
    try:
        info = ytdlp.json_info(url, timeout=90)
    except Exception:                             # noqa: BLE001 - best effort only
        return {}
    if not info or info.get("_type") == "playlist":
        return {}
    return {"url": info.get("webpage_url") or url,
            "title": info.get("title") or "",
            "artist": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration") or 0}


def record(store, track: dict, candidate: dict) -> bool:
    """Remember a candidate the owner picked. False when it is not a real pick."""
    url = (candidate or {}).get("url") or ""
    if not url:
        return False
    query = query_for(track)
    if not query:
        return False
    wanted = _version_tokens(query)
    offered = _version_tokens(f"{candidate.get('artist', '')} {candidate.get('title', '')}")
    matched = 1 if (wanted and offered and (wanted & offered)) else 0
    with store.lock:
        schema(store)
        exists = store.conn.execute(
            "SELECT id FROM source_picks WHERE query=? AND url=?", (query, url)).fetchone()
        if exists:
            return False
        store.conn.execute(
            "INSERT INTO source_picks (created,query,track_title,artist,url,uploader,"
            "candidate_title,version_match) VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), query, track.get("title", ""), track.get("artist_name", ""),
             url, candidate.get("artist", ""), candidate.get("title", ""), matched))
        store.conn.commit()
    return True


def forget(store, query: str, url: str) -> bool:
    """Undo a pick: an accidental click must not keep teaching the ranking."""
    try:
        with store.lock:
            schema(store)
            cur = store.conn.execute(
                "DELETE FROM source_picks WHERE query=? AND url=?", (query, url))
            store.conn.commit()
            return cur.rowcount > 0
    except Exception:                             # noqa: BLE001
        return False


def history(store, query: str | None = None) -> list[dict]:
    try:
        with store.lock:
            schema(store)
            if query:
                rows = store.conn.execute(
                    "SELECT * FROM source_picks WHERE query=?", (query,)).fetchall()
            else:
                rows = store.conn.execute(
                    "SELECT * FROM source_picks ORDER BY id DESC LIMIT 500").fetchall()
    except Exception:                              # noqa: BLE001 - no table, no history
        return []
    return [dict(r) for r in rows]


def has_history(store, query: str) -> bool:
    return bool(history(store, query))


def _signals(store, query: str) -> dict:
    rows = history(store, query)
    artist = ""
    title = ""
    if rows:
        artist, title = rows[0].get("artist", ""), rows[0].get("track_title", "")
    # Uploader affinity is per artist, not per song: the same channel posts the
    # right versions across a discography.
    trusted = {r["uploader"] for r in history(store) if artist and r.get("artist") == artist
               and r.get("uploader")}
    wanted = _version_tokens(query)
    matched = [r for r in rows if r.get("version_match")]
    mismatched = [r for r in rows if wanted and not r.get("version_match")]
    if mismatched and not matched:
        mismatch_factor = LOOSE_MISMATCH
    elif matched and not mismatched:
        mismatch_factor = STRICT_MISMATCH
    else:
        mismatch_factor = DEFAULT_MISMATCH
    return {
        "chosen": {r["url"] for r in rows},
        "uploaders": trusted,
        "mismatch_factor": mismatch_factor,
        "picks": len(rows),
        "loose": bool(mismatched and not matched),
    }


def score(store, query: str, candidate: dict, signals: dict | None = None) -> float:
    base = hit_score(query, candidate)
    sig = signals if signals is not None else _signals(store, query)
    if candidate.get("url") in sig["chosen"]:
        return EXACT                                # already confirmed by hand
    score = base
    wanted = _version_tokens(query)
    offered = _version_tokens(f"{candidate.get('artist', '')} {candidate.get('title', '')}")
    if wanted and offered and not (wanted & offered):
        # hit_score applied 0.7; swap in what his own picks say instead.
        score = score / DEFAULT_MISMATCH * sig["mismatch_factor"]
    if candidate.get("artist") and candidate["artist"] in sig["uploaders"]:
        score *= UPLOADER_BOOST
    return round(min(1.0, score), 3)


def rank(store, query: str, candidates: list[dict]) -> list[dict]:
    """Candidates sorted best-first, with the learned score written onto each one."""
    if not candidates:
        return []
    sig = _signals(store, query)
    out = []
    for candidate in candidates:
        item = dict(candidate)
        item["match"] = score(store, query, item, sig)
        item["picked"] = item.get("url") in sig["chosen"]
        out.append(item)
    # A tie at 1.0 must still put the upload he chose first.
    out.sort(key=lambda c: (-(c.get("match") or 0), 0 if c.get("url") in sig["chosen"] else 1))
    return out


def taste(store, track: dict, limit: int = 5) -> list[dict]:
    """Recent picks by this artist, for the judging model to compare against."""
    artist = (track or {}).get("artist_name") or ""
    if not artist:
        return []
    return [r for r in history(store) if r.get("artist") == artist][:limit]


def explain(store, query: str) -> dict:
    """What the ranking currently believes about this query, for the UI."""
    sig = _signals(store, query)
    return {"picks": sig["picks"], "trusted_uploaders": sorted(sig["uploaders"]),
            "mismatch_factor": sig["mismatch_factor"], "loose_on_versions": sig["loose"],
            "query": query, "normalised": norm(query), "chosen": sorted(sig["chosen"])}


def dump(store) -> str:
    return json.dumps(history(store), indent=2)[:20000]
