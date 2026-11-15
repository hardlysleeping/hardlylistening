#!/usr/bin/env python3
"""Cover-art store: report, run the sweep by hand, or dry-run one file.

The dedupe and the display-size embed are automatic — the server runs the sweep
at boot, every finished download is normalised in place, and `artwork.for_track`
registers whatever it finds. This tool exists to look at the result and to prove
a change on copies before trusting it:

    python3 tools/art_store.py report          # what the store holds
    python3 tools/art_store.py jxl             # convert the store to JPEG XL
    python3 tools/art_store.py sweep           # same sweep, in the foreground
    python3 tools/art_store.py check FILE...   # dry run: no writes at all
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db                                       # noqa: E402
from library import art_store, id3art           # noqa: E402


def report() -> int:
    store = db.Store()
    folder = art_store.folder(store)
    images = sorted(p for p in folder.glob("*") if p.is_file()) if folder.exists() else []
    total = sum(p.stat().st_size for p in images)
    by_suffix = {}
    for path in images:
        by_suffix.setdefault(path.suffix.lstrip("."), []).append(path.stat().st_size)
    print(f"store    {folder}")
    print(f"images   {len(images)}  {total / 1e6:.1f} MB")
    for suffix, sizes in sorted(by_suffix.items()):
        print(f"  {suffix:<6} {len(sizes)}  {sum(sizes) / 1e6:.1f} MB")
    rows = store.conn.execute(
        "SELECT COUNT(*) total, SUM(art_checked > 0) checked, "
        "SUM(art_hash != '') with_art FROM tracks WHERE file != ''").fetchone()
    print(f"tracks   {rows['total']} with a file, {rows['checked'] or 0} checked, "
          f"{rows['with_art'] or 0} linked to a cover")
    return 0


def jxl() -> int:
    """Convert the stored JPEG/PNG copies to JPEG XL, verifying each one first."""
    store = db.Store()
    folder = art_store.folder(store)
    size = lambda: sum(p.stat().st_size for p in folder.glob("*") if p.is_file())
    before = size()
    log = db.APP_DIR / "backups" / f"art-jxl-{time.strftime('%Y%m%d-%H%M%S')}.tsv"
    lines = []

    def progress(action, name, old, new):
        lines.append(f"{action}\t{name}\t{old}\t{new}")
        if len(lines) % 50 == 0:
            print(f"  {len(lines)}  {action} {name} {old // 1024} KB -> {new // 1024} KB",
                  flush=True)

    seen = art_store.migrate(store, progress)
    after = size()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(lines) + "\n")
    print(f"\nconverted {seen['converted']}  kept {seen['kept']}  "
          f"saved {seen['saved'] / 1e6:.1f} MB")
    print(f"store {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")
    print(f"log {log}")
    return 0


def sweep() -> int:
    store = db.Store()

    def progress(done, outcome):
        if done % 25 == 0 or outcome in ("slimmed", "failed"):
            print(f"  {done}  {outcome}", flush=True)

    seen = art_store.sweep(store, progress=progress)
    print("\n" + "  ".join(f"{name}: {count}" for name, count in seen.items()))
    return 0


def check(paths) -> int:
    """Dry run: what would happen, with the audio bytes compared but nothing written."""
    store = db.Store()
    for name in paths:
        path = Path(name).expanduser()
        if not path.is_file():
            print(f"{path}: missing")
            continue
        raw = path.read_bytes()
        try:
            pictures = id3art.covers(raw)
        except id3art.Unsupported as error:
            print(f"{path.name}: unsupported ({error})")
            continue
        if not pictures:
            print(f"{path.name}: no embedded cover")
            continue
        mime, data = pictures[0]
        key = art_store.digest(data)
        known = art_store.image(store, key)
        print(f"{path.name}\n  cover   {mime}  {len(data) / 1024:.0f} KB  "
              f"{'already in store' if known else 'new to store'}  {key[:12]}")
        jpeg = art_store.display(store, art_store.put(store, data))
        if jpeg is None:
            print("  display could not be generated")
            continue
        new = id3art.embed(raw, jpeg.read_bytes())
        if new is None:
            print(f"  file    already holds the {art_store.WIDTH} px cover")
            continue
        same = id3art.audio(new) == id3art.audio(raw)
        print(f"  file    {len(raw) / 1024:.0f} KB -> {len(new) / 1024:.0f} KB, "
              f"audio identical: {same}")
    return 0


if __name__ == "__main__":
    command = (sys.argv[1:2] or ["report"])[0]
    if command == "sweep":
        raise SystemExit(sweep())
    if command == "jxl":
        raise SystemExit(jxl())
    if command == "check":
        raise SystemExit(check(sys.argv[2:]))
    raise SystemExit(report())