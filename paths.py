"""Where this process is allowed to write.

The rule: code and data are separate. A server installed under `/opt` (or any checkout someone
else owns) may be read-only at runtime, so nothing created at runtime is placed next to the
modules that create it once a data directory is declared.

    HARDLYLISTENING_DATA_DIR=/var/lib/hardlylistening
        database            /var/lib/hardlylistening/hardlylistening.db
        artwork             /var/lib/hardlylistening/cache/artwork
        browser downloads   /var/lib/hardlylistening/cache/browser-downloads
        artist art          /var/lib/hardlylistening/cache/artist-art

With `HARDLYLISTENING_DATA_DIR` unset every path is exactly what it was before this module
existed — `<app>/hardlylistening.db`, `<app>/.cache/...`, `<app>/artist-art` — so an existing
checkout keeps its database and its caches where they already are. Moving an install is therefore
a copy of files plus one environment variable, not a migration.

Not covered here on purpose: the media root, which is a database setting the admin owns, and the
per-user XDG cache a provider uses for its own cookies.
"""
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

#: Declared by the packaged layouts (VPS, Docker, Desktop). Absence means "someone's checkout".
DECLARED = bool(os.environ.get("HARDLYLISTENING_DATA_DIR"))

DATA_DIR = Path(os.environ["HARDLYLISTENING_DATA_DIR"]).expanduser() if DECLARED else APP_DIR

#: Cache lives beside the database when declared, and where it always has when not.
CACHE_DIR = (DATA_DIR / "cache") if DECLARED else (APP_DIR / ".cache")
ARTIST_ART_DIR = (DATA_DIR / "cache" / "artist-art") if DECLARED else (APP_DIR / "artist-art")

#: The database file: an explicit path wins, otherwise it sits in the data directory.
DB_PATH = Path(os.environ.get("HARDLYLISTENING_DB") or (DATA_DIR / "hardlylistening.db"))


def ensure() -> None:
    """Create the declared directories, and only when they were declared.

    A checkout is not modified: its directories already exist, and creating them on every boot
    would be a write to the source tree in the one case this module exists to prevent.
    """
    if not DECLARED:
        return
    for directory in (DATA_DIR, CACHE_DIR, ARTIST_ART_DIR):
        directory.mkdir(parents=True, exist_ok=True)