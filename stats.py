"""Listening numbers, computed from the dated play log.

`listen_events` (see db.py) is one row per play, forever. Everything here is a read
over that table joined to `tracks` for the display fields — no counters are stored, so
a number can never drift from the plays underneath it.

Most of these are per-member. The one that is not is `popular_tracks`, the community
chart: it takes no member and counts every play by everybody.

Profile windows are **rolling and relative to now**, which is what the profile offers:
last 7 days, last 30, last 365, all time. The one calendar window there is the yearly
top 100, because "my 2026" means a year, not a trailing twelve months. The chart is the
other way round: "today", "this week" and "this month" are calendar questions.

`listening_plays` is deliberately not read here: it is a lifetime total with no dates,
written only by Subsonic scrobbles, and cannot answer any window.
"""

from __future__ import annotations

import threading
import time

# `period` -> trailing seconds. `None` means everything ever recorded.
WINDOWS = {"week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400, "all": None}
PERIODS = tuple(WINDOWS)
# The chart's windows. The short ones are calendar windows in local time (see
# `calendar_window`): today, this week, this month. "year" is the same question one size up
# — this calendar year, from January 1st — and "all" is the lifetime of the log, which has
# no start date at all.
CALENDAR_PERIODS = ("day", "week", "month", "year", "all")
DEFAULT_LIMIT = 100
YEAR_CACHE_TTL = 24 * 60 * 60

_year_cache: dict[tuple[str, int], tuple[float, list[int]]] = {}
_year_lock = threading.Lock()


def period_seconds(period: str) -> int | None:
    if period not in WINDOWS:
        raise ValueError("unknown period %r" % (period,))
    return WINDOWS[period]


def window(period: str, now: float | None = None) -> tuple[float, float]:
    """(start, end) in epoch seconds; `end` is exclusive and is now."""
    now = time.time() if now is None else float(now)
    span = period_seconds(period)
    return (0.0 if span is None else now - span), now


def year_window(year: int) -> tuple[float, float]:
    """Calendar year in local time: Jan 1 00:00 to the next Jan 1 00:00."""
    start = time.mktime((int(year), 1, 1, 0, 0, 0, 0, 0, -1))
    end = time.mktime((int(year) + 1, 1, 1, 0, 0, 0, 0, 0, -1))
    return start, end


def calendar_window(period: str, now: float | None = None) -> tuple[float, float]:
    """(start, end) for today, this week, this month, this year or all time, in local time.

    A chart asks "what has been played today", so the windows start where the calendar
    does: local midnight, Monday for the week, the 1st for the month, January 1st for the
    year. Midnight is built with `mktime` on the local date, which is what keeps it correct
    across a DST change. "all" starts at 0, which `_span` reads as "no lower bound".
    """
    if period not in CALENDAR_PERIODS:
        raise ValueError("unknown period %r" % (period,))
    now = time.time() if now is None else float(now)
    if period == "all":
        return 0.0, now
    today = time.localtime(now)
    midnight = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
    if period == "day":
        start = midnight
    elif period == "week":
        start = midnight - today.tm_wday * 86400      # tm_wday is 0 on Monday
    elif period == "month":
        start = time.mktime((today.tm_year, today.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    else:
        start = time.mktime((today.tm_year, 1, 1, 0, 0, 0, 0, 0, -1))
    return start, now


def _span(start: float, end: float) -> tuple[str, list]:
    if start > 0:
        return " AND e.ts>=? AND e.ts<?", [start, end]
    return " AND e.ts<?", [end]


def totals(store, member_id: str, start: float, end: float) -> dict:
    """Plays, known listening seconds, and how many distinct things were played."""
    clause, params = _span(start, end)
    with store.lock:
        row = store.conn.execute(
            "SELECT COUNT(*) AS plays, COALESCE(SUM(MAX(t.duration,0)),0) AS seconds,"
            " COUNT(DISTINCT e.track_id) AS tracks, COUNT(DISTINCT t.artist_name) AS artists,"
            " COUNT(CASE WHEN t.duration>0 THEN 1 END) AS timed"
            " FROM listen_events e LEFT JOIN tracks t ON t.id=e.track_id"
            " WHERE e.member_id=?" + clause, [member_id, *params]).fetchone()
        albums = store.conn.execute(
            "SELECT COUNT(DISTINCT t.artist_name || char(31) || t.album)"
            " FROM listen_events e JOIN tracks t ON t.id=e.track_id"
            " WHERE e.member_id=? AND t.album<>''" + clause, [member_id, *params]).fetchone()[0]
    plays = row["plays"] or 0
    return {
        "plays": plays,
        "seconds": int(row["seconds"] or 0),
        # Durations are not guessed: a track with no duration contributes nothing and is
        # reported so the UI can say "at least".
        "untimed_plays": plays - (row["timed"] or 0),
        "tracks": row["tracks"] or 0,
        "artists": row["artists"] or 0,
        "albums": albums or 0,
    }


def top_tracks(store, member_id: str, start: float, end: float,
               limit: int = DEFAULT_LIMIT) -> list[dict]:
    clause, params = _span(start, end)
    with store.lock:
        rows = store.conn.execute(
            "SELECT e.track_id AS id, COUNT(*) AS plays, MAX(e.ts) AS last,"
            " t.title, t.artist_name, t.album, t.artwork, t.duration"
            " FROM listen_events e JOIN tracks t ON t.id=e.track_id"
            " WHERE e.member_id=?" + clause +
            " GROUP BY e.track_id ORDER BY plays DESC, last DESC, e.track_id LIMIT ?",
            [member_id, *params, int(limit)]).fetchall()
    return [{"id": r["id"], "title": r["title"], "artist_name": r["artist_name"],
             "album": r["album"] or "", "artwork": r["artwork"] or "",
             "duration": r["duration"] or 0, "plays": r["plays"], "last": r["last"]}
            for r in rows]


def popular_tracks(store, start: float, end: float, limit: int = 50) -> list[dict]:
    """The most played tracks across every member in a window — the community chart.

    The one read here with no member filter. `listeners` is how many different people it
    took to reach that count, which is what makes a chart a chart rather than one
    person's repeat plays.
    """
    clause, params = _span(start, end)
    with store.lock:
        rows = store.conn.execute(
            "SELECT e.track_id AS id, COUNT(*) AS plays,"
            " COUNT(DISTINCT e.member_id) AS listeners, MAX(e.ts) AS last,"
            " t.title, t.artist_name, t.album, t.artwork, t.duration"
            " FROM listen_events e JOIN tracks t ON t.id=e.track_id"
            " WHERE 1=1" + clause +
            " GROUP BY e.track_id ORDER BY plays DESC, last DESC, e.track_id LIMIT ?",
            [*params, int(limit)]).fetchall()
    return [{"id": r["id"], "title": r["title"], "artist_name": r["artist_name"],
             "album": r["album"] or "", "artwork": r["artwork"] or "",
             "duration": r["duration"] or 0, "plays": r["plays"],
             "listeners": r["listeners"], "last": r["last"]}
            for r in rows]


def top_artists(store, member_id: str, start: float, end: float, limit: int = 20) -> list[dict]:
    clause, params = _span(start, end)
    with store.lock:
        rows = store.conn.execute(
            "SELECT t.artist_name AS artist_name, COUNT(*) AS plays,"
            " COUNT(DISTINCT e.track_id) AS tracks, MAX(e.ts) AS last"
            " FROM listen_events e JOIN tracks t ON t.id=e.track_id"
            " WHERE e.member_id=? AND t.artist_name<>''" + clause +
            " GROUP BY t.artist_name ORDER BY plays DESC, last DESC, artist_name LIMIT ?",
            [member_id, *params, int(limit)]).fetchall()
    return [{"artist_name": r["artist_name"], "plays": r["plays"], "tracks": r["tracks"],
             "last": r["last"]} for r in rows]


def top_albums(store, member_id: str, start: float, end: float, limit: int = 20) -> list[dict]:
    clause, params = _span(start, end)
    with store.lock:
        rows = store.conn.execute(
            "SELECT t.artist_name AS artist_name, t.album AS album, COUNT(*) AS plays,"
            " COUNT(DISTINCT e.track_id) AS tracks, MAX(e.ts) AS last,"
            " MAX(t.artwork) AS artwork"
            " FROM listen_events e JOIN tracks t ON t.id=e.track_id"
            " WHERE e.member_id=? AND t.album<>''" + clause +
            " GROUP BY t.artist_name, t.album"
            " ORDER BY plays DESC, last DESC, album LIMIT ?",
            [member_id, *params, int(limit)]).fetchall()
    return [{"artist_name": r["artist_name"], "album": r["album"], "plays": r["plays"],
             "tracks": r["tracks"], "last": r["last"], "artwork": r["artwork"] or ""}
            for r in rows]


def day_histogram(store, member_id: str, start: float, end: float,
                  days: int = 30) -> list[dict]:
    """Plays per local day, oldest first, with empty days present as zero."""
    clause, params = _span(start, end)
    with store.lock:
        rows = store.conn.execute(
            "SELECT date(e.ts,'unixepoch','localtime') AS day, COUNT(*) AS plays"
            " FROM listen_events e WHERE e.member_id=?" + clause +
            " GROUP BY day", [member_id, *params]).fetchall()
    counts = {r["day"]: r["plays"] for r in rows}
    # Walk back from the end of the window so a quiet day is a zero, not a gap.
    out = []
    step = 86400
    today = time.localtime(end)
    anchor = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 12, 0, 0, 0, 0, -1))
    for offset in range(int(days) - 1, -1, -1):
        stamp = anchor - offset * step
        day = time.strftime("%Y-%m-%d", time.localtime(stamp))
        out.append({"day": day, "plays": counts.get(day, 0)})
    return out


def _days(store, member_id: str) -> list[str]:
    with store.lock:
        rows = store.conn.execute(
            "SELECT DISTINCT date(ts,'unixepoch','localtime') AS day FROM listen_events"
            " WHERE member_id=? ORDER BY day", (member_id,)).fetchall()
    return [r["day"] for r in rows]


def streak(store, member_id: str, now: float | None = None) -> dict:
    """Current run of consecutive days with a play, and the longest such run ever.

    The current streak survives a day that has not been listened to yet, so opening the
    page in the morning does not show the streak reset.
    """
    now = time.time() if now is None else float(now)
    days = _days(store, member_id)
    if not days:
        return {"current": 0, "longest": 0, "last_day": ""}
    import datetime
    as_dates = [datetime.date.fromisoformat(d) for d in days]
    longest = run = 1
    for previous, current in zip(as_dates, as_dates[1:]):
        run = run + 1 if (current - previous).days == 1 else 1
        longest = max(longest, run)
    today = datetime.date.fromtimestamp(now)
    latest = as_dates[-1]
    if (today - latest).days > 1:
        current_run = 0
    else:
        current_run = 1
        for index in range(len(as_dates) - 1, 0, -1):
            if (as_dates[index] - as_dates[index - 1]).days == 1:
                current_run += 1
            else:
                break
    return {"current": current_run, "longest": longest, "last_day": days[-1]}


def new_artists(store, member_id: str, start: float, end: float) -> list[dict]:
    """Artists whose *first ever* play by this member falls inside the window."""
    with store.lock:
        rows = store.conn.execute(
            "SELECT t.artist_name AS artist_name, MIN(e.ts) AS first"
            " FROM listen_events e JOIN tracks t ON t.id=e.track_id"
            " WHERE e.member_id=? AND t.artist_name<>''"
            " GROUP BY t.artist_name HAVING first>=? AND first<?"
            " ORDER BY first, artist_name", (member_id, start, end)).fetchall()
    return [{"artist_name": r["artist_name"], "first": r["first"]} for r in rows]


def first_play(store, member_id: str, start: float, end: float) -> dict | None:
    clause, params = _span(start, end)
    with store.lock:
        row = store.conn.execute(
            "SELECT e.ts, t.title, t.artist_name, t.id FROM listen_events e"
            " JOIN tracks t ON t.id=e.track_id"
            " WHERE e.member_id=?" + clause + " ORDER BY e.ts LIMIT 1",
            [member_id, *params]).fetchone()
    if not row:
        return None
    return {"ts": row["ts"], "id": row["id"], "title": row["title"],
            "artist_name": row["artist_name"]}


def busiest_day(store, member_id: str, start: float, end: float) -> dict | None:
    clause, params = _span(start, end)
    with store.lock:
        row = store.conn.execute(
            "SELECT date(e.ts,'unixepoch','localtime') AS day, COUNT(*) AS plays"
            " FROM listen_events e WHERE e.member_id=?" + clause +
            " GROUP BY day ORDER BY plays DESC, day LIMIT 1", [member_id, *params]).fetchone()
    return {"day": row["day"], "plays": row["plays"]} if row else None


def years(store, member_id: str) -> list[int]:
    """Every calendar year this member has a play in, newest first."""
    with store.lock:
        rows = store.conn.execute(
            "SELECT DISTINCT strftime('%Y', ts, 'unixepoch', 'localtime') AS year"
            " FROM listen_events WHERE member_id=? ORDER BY year DESC",
            (member_id,)).fetchall()
    return [int(r["year"]) for r in rows if r["year"]]


def year_tracks(store, member_id: str, year: int, limit: int = DEFAULT_LIMIT,
                now: float | None = None) -> list[int]:
    """The top tracks of one calendar year, cached for a day.

    This is the yearly top-100 playlist: recomputed at most once every 24 hours so a
    list you are listening to does not reshuffle under you mid-song, but never stored
    as membership, so it cannot go stale across a year boundary either.
    """
    now = time.time() if now is None else float(now)
    key = (member_id, int(year))
    with _year_lock:
        entry = _year_cache.get(key)
        if entry and now - entry[0] < YEAR_CACHE_TTL:
            return list(entry[1])
    start, end = year_window(year)
    ids = [row["id"] for row in top_tracks(store, member_id, start, end, limit)]
    with _year_lock:
        _year_cache[key] = (now, list(ids))
    return ids


def clear_year_cache() -> None:
    """Drop the yearly cache; `top-100:<year>` answers fresh on the next read."""
    with _year_lock:
        _year_cache.clear()


def year_cards(store, member_id: str, now: float | None = None) -> list[dict]:
    """One card per calendar year with plays: the count and up to four covers.

    The cover art comes from the year's own top tracks (the same cached list the
    `top-100:<year>` playlist serves), so the card cannot show a song the list does not
    hold.
    """
    now = time.time() if now is None else float(now)
    with store.lock:
        rows = store.conn.execute(
            "SELECT strftime('%Y', ts, 'unixepoch', 'localtime') AS year,"
            " COUNT(*) AS plays, COUNT(DISTINCT track_id) AS tracks"
            " FROM listen_events WHERE member_id=? GROUP BY year ORDER BY year DESC",
            (member_id,)).fetchall()
    cards = []
    for row in rows:
        year = int(row["year"])
        ids = year_tracks(store, member_id, year, now=now)
        art = []
        if ids:
            with store.lock:
                found = {r["id"]: r["artwork"] for r in store.conn.execute(
                    "SELECT id, artwork FROM tracks WHERE id IN ("
                    + ",".join("?" for _ in ids[:8]) + ")", ids[:8])}
            art = [found[i] for i in ids[:8] if found.get(i)]
        cards.append({"year": year, "plays": row["plays"], "tracks": row["tracks"],
                      "artwork": art[:4]})
    return cards


def summary(store, member_id: str, period: str = "week", *,
            now: float | None = None, year: int | None = None) -> dict:
    """Everything the profile's Listening section needs, in one read."""
    now = time.time() if now is None else float(now)
    if year is not None:
        start, end = year_window(year)
        label = str(int(year))
    else:
        start, end = window(period, now)
        label = period
    totals_row = totals(store, member_id, start, end)
    artists = top_artists(store, member_id, start, end)
    return {
        "period": label,
        "start": start,
        "end": end,
        "totals": totals_row,
        "top_tracks": top_tracks(store, member_id, start, end),
        "top_artists": artists,
        "top_albums": top_albums(store, member_id, start, end),
        "favourite_artist": artists[0] if artists else None,
        "day_histogram": day_histogram(store, member_id, start, end,
                                       days=30 if period != "all" else 90),
        "streak": streak(store, member_id, now),
        "new_artists": new_artists(store, member_id, start, end),
        "first_play": first_play(store, member_id, start, end),
        "busiest_day": busiest_day(store, member_id, start, end),
        "years": years(store, member_id),
        "empty": totals_row["plays"] == 0,
    }