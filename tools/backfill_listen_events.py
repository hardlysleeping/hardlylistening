"""One-off: copy the dated listens still held in `activity` into `listen_events`.

`activity` is the feed's window (newest 4000 rows, a repeat inside ten minutes folded);
`listen_events` is the history the stats read. This copies every listen row it still has,
keeping each play's own timestamp and applying the same 30-second double-report guard as
`activity.log_play`, so a backfilled history never double-counts a report that the feed
already folded.

Idempotent: rows already present for the same member+track inside 30 s are skipped, so a
second run adds nothing.

    python3 tools/backfill_listen_events.py [--dry-run]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

import activity  # noqa: E402
import db  # noqa: E402


def main(dry_run: bool = False) -> int:
    store = db.Store()
    inserted = skipped = 0
    with store.lock:
        rows = store.conn.execute(
            "SELECT ts, member_id, track_id FROM activity"
            " WHERE kind='listen' AND member_id<>'' AND track_id>0"
            " ORDER BY ts, id").fetchall()
    known = {}
    for row in rows:
        member, track, ts = row["member_id"], row["track_id"], float(row["ts"])
        last = known.get((member, track))
        if last is not None and ts - last < activity.PLAY_DEDUPE:
            skipped += 1
            continue
        if not dry_run:
            with store.lock:
                exists = store.conn.execute(
                    "SELECT 1 FROM listen_events WHERE member_id=? AND track_id=?"
                    " AND abs(ts-?)<?", (member, track, ts, activity.PLAY_DEDUPE)).fetchone()
                if exists:
                    skipped += 1
                    known[(member, track)] = ts
                    continue
                store.conn.execute(
                    "INSERT INTO listen_events(ts,member_id,track_id,source) VALUES(?,?,?,'web')",
                    (ts, member, track))
                store.conn.commit()
        inserted += 1
        known[(member, track)] = ts
    with store.lock:
        total = store.conn.execute("SELECT COUNT(*) FROM listen_events").fetchone()[0]
    print(f"{'dry run: ' if dry_run else ''}backfilled {inserted}, skipped {skipped},"
          f" history now {total} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))