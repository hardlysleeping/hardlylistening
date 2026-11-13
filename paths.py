"""Where this process is allowed to write.

The rule: code and data are separate. A server installed under `/opt` (or any checkout someone
else owns) may be read-only at runtime, so nothing created at runtime is placed next to the
modules that create it once a data directory is declared.

    HARDLYLISTENING_DATA_DIR=/var/lib/hardlylistening
        database            /var/lib/hardlylistening/hardlylistening.db
        artwork             /var/lib/hardlylistening/cache/artwork
        transcode cache     /var/lib/hardlylistening/cache/transcode
        browser downloads   /var/lib/hardlylistening/cache/browser-downloads
        artist art          /var/lib/hardlylistening/cache/artist-art
        metadata cache      /var/lib/hardlylistening/cache/metadata
        federation cache    /var/lib/hardlylistening/cache/federation
        podcast downloads   /var/lib/hardlylistening/podcasts           (per member, capped)
        identity            /var/lib/hardlylistening/identity          (0700)
        state               /var/lib/hardlylistening/state
        tmp                 /var/lib/hardlylistening/tmp

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
METADATA_CACHE_DIR = CACHE_DIR / "metadata"
FEDERATION_CACHE_DIR = CACHE_DIR / "federation"

#: Podcast episodes a member downloaded (HL-209). Not a cache: it survives a cache clear and
#: belongs in a backup, which is exactly why it sits beside the database rather than in
#: `cache/`. One directory per member; the 5 GB budget is enforced per member by podcasts.py.
PODCAST_DIR = DATA_DIR / "podcasts"

#: Persistent state that is not the database. `state/` and `tmp/` are part of the documented
#: layout (`docs/server-packaging.md` §2) and are created now so the layout is real rather than
#: promised; nothing writes to them yet.
STATE_DIR = DATA_DIR / "state"
TMP_DIR = DATA_DIR / "tmp"

#: Node identity for federation. **Storage**: `repo/federation.py` generates `node-id` and
#: `node-secret` here on first use, never at boot, and never rewrites a file that already exists
#: (FED-001; `docs/federation.md` §3). The directory is in the backup boundary and has to survive
#: an application replacement. Private: 0700, and `node-secret` is 0600.
IDENTITY_DIR = DATA_DIR / "identity"

#: The database file: an explicit path wins, otherwise it sits in the data directory.
DB_PATH = Path(os.environ.get("HARDLYLISTENING_DB") or (DATA_DIR / "hardlylistening.db"))


def ensure() -> None:
    """Create the declared directories, and only when they were declared.

    A checkout is not modified: its directories already exist, and creating them on every boot
    would be a write to the source tree in the one case this module exists to prevent.
    """
    if not DECLARED:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # `mode=` applies to the leaf and is unaffected by a 022/002 umask, which is exactly why
    # identity is created separately rather than in the loop below.
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in (CACHE_DIR, ARTIST_ART_DIR, METADATA_CACHE_DIR, FEDERATION_CACHE_DIR,
                      PODCAST_DIR, STATE_DIR, TMP_DIR):
        directory.mkdir(parents=True, exist_ok=True)