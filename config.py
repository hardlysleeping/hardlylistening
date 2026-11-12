"""Runtime settings that are not filesystem paths: where to listen, what a new library seeds.

`paths.py` answers "where may this process write". This module answers the other half of the
packaged install: the two socket options and the media root, all with the same rule — an
environment variable is a default, an explicit command-line flag still wins, and an existing
install with nothing declared behaves exactly as it always has.

    HARDLYLISTENING_HOST=127.0.0.1        loopback, as today
    HARDLYLISTENING_PORT=8470             8470, as today
    HARDLYLISTENING_MUSIC_DIR=/srv/...    only seeds a library nobody has chosen a root for

`MUSIC_DIR` is deliberately *not* an override. The media root is a database setting the admin
owns (they can move it in the UI), so this only supplies a value while the settings table still
holds the built-in default; after that the database wins and the variable is inert. Anything else
would silently re-point a library on every start.
"""
import os
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8470


def _text(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def host() -> str:
    return _text("HARDLYLISTENING_HOST") or DEFAULT_HOST


def port() -> int:
    """The listen port, or `ValueError` — a typo must not silently become 8470.

    Falling back on a bad value would start the server somewhere the operator did not ask for,
    which is worse than refusing to start.
    """
    raw = _text("HARDLYLISTENING_PORT")
    if not raw:
        return DEFAULT_PORT
    try:
        value = int(raw, 10)
    except ValueError:
        raise ValueError(f"HARDLYLISTENING_PORT is not a number: {raw!r}") from None
    if not 0 < value < 65536:
        raise ValueError(f"HARDLYLISTENING_PORT is out of range: {value}")
    return value


def music_dir() -> str:
    """The declared media root, or "" — never a guess, never the home directory."""
    return _text("HARDLYLISTENING_MUSIC_DIR")


def seed_music_root(store) -> bool:
    """Give an un-chosen database its first root; leave every other database alone.

    "Un-chosen" is not "no row": `db.connect()` inserts every `DEFAULTS` key on creation, so a
    fresh database already holds `root = ~/Music/HardlyListening`. The declaration applies while
    the stored value is still that default (or absent), and stops applying the moment the admin
    has chosen something — which is what keeps this from re-pointing a library on every start.

    Returns True only when it wrote, so the caller can log the one start that did.
    """
    declared = music_dir()
    if not declared:
        return False
    from db import DEFAULTS                     # local: config is imported before db by some paths
    current = store.setting("root")
    if current and current != DEFAULTS["root"]:
        return False
    store.set_settings({"root": str(Path(declared).expanduser())})
    return True