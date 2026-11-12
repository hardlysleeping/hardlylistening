#!/usr/bin/env python3
"""Adopt audio files that no track row points at.

    python3 tools/adopt.py check     # what would be adopted, nothing written
    python3 tools/adopt.py sweep     # do it
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                    # noqa: E402
from library import reconcile                # noqa: E402


def check() -> int:
    store = db.Store()
    found = reconcile.orphans(store)
    print(f"{len(found)} file(s) with no track row")
    for path in found[:40]:
        print(f"  {path.relative_to(reconcile.root(store))}")
    if len(found) > 40:
        print(f"  … {len(found) - 40} more")
    return 0


def sweep() -> int:
    store = db.Store()

    def progress(action, path, detail):
        print(f"  {action:<9} {path.name[:64]:66} {detail}")

    seen = reconcile.sweep(store, progress)
    print()
    for action, count in sorted(seen.items()):
        print(f"  {action:<9} {count}")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "check":
        raise SystemExit(check())
    if command == "sweep":
        raise SystemExit(sweep())
    raise SystemExit(__doc__)