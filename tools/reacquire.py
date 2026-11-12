#!/usr/bin/env python3
"""Re-acquire the back catalogue at source quality.

    python3 tools/reacquire.py plan            # what would run, and the size estimate
    python3 tools/reacquire.py status          # how many are still to do
    python3 tools/reacquire.py run [--limit N] # do it, one track at a time
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                # noqa: E402
import reacquire                         # noqa: E402
from engine.queue import Queue           # noqa: E402


def review(store) -> int:
    items = reacquire.candidates()
    work = reacquire.plan(store, items)
    by_class: dict[str, int] = {}
    seconds = 0.0
    for item in work:
        by_class[item["class"]] = by_class.get(item["class"], 0) + 1
        seconds += item["duration"]
    print(f"audit      {reacquire.audit_path()}")
    print(f"candidates {len(items)}  still to do {len(work)}")
    for name, count in sorted(by_class.items()):
        print(f"  {name:<12} {count}")
    print(f"audio      {seconds / 3600:.1f} hours of source stream to fetch")
    done = sum(1 for item in items if reacquire.done(store, item["track_id"], item["origin"]))
    print(f"already re-acquired {done}")
    return 0


def status(store) -> int:
    items = reacquire.candidates()
    left = reacquire.plan(store, items)
    print(f"still to do {len(left)} of {len(items)}")
    return 0


def run(store, limit: int) -> int:
    items = reacquire.candidates()
    work = reacquire.plan(store, items)
    if not work:
        print("nothing left to re-acquire")
        return 0
    print(f"{len(work)} track(s) to re-acquire" + (f", stopping after {limit}" if limit else ""))

    def progress(item, result):
        mark = "ok  " if result["status"] == "completed" else "FAIL"
        print(f"  {mark} {item['track_id']:4} {item['title'][:58]:60} {result['status']}",
              flush=True)

    queue = Queue(store)                 # no worker thread: this process downloads
    seen = reacquire.run(store, queue, work, limit=limit, progress=progress)
    print(f"\ndone {seen['done']}  failed {seen['failed']}  skipped {seen['skipped']}")
    if seen["stopped"]:
        print(f"stopped: {seen['stopped']}")
    return 0 if not seen["failed"] else 1


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "plan"
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    store = db.Store()
    if command == "plan":
        raise SystemExit(review(store))
    if command == "status":
        raise SystemExit(status(store))
    if command == "run":
        raise SystemExit(run(store, limit))
    raise SystemExit(__doc__)