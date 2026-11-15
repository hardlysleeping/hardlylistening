"""Paths and sentences the HTTP layer owns.

They are here rather than in `server.py` because only the adapters use them, and because
`APP_DIR` has to be the repository root — one level above this package, not this file.
"""
from pathlib import Path

import paths

APP_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = APP_DIR / "web"
ARTIST_ART_DIR = paths.ARTIST_ART_DIR

#: The one reply for a browser surface that was switched off at boot. A sentence, not an empty
#: 404: a person who reaches a headless server from a browser should be able to tell a disabled
#: UI from a typo in the URL.
UI_OFF = b"The web UI is disabled on this server.\n"

#: Playback telemetry is one bounded line per interesting event. Past this size the log is
#: rotated once (`.1`), because the point is the next drive, not a growing archive.
PLAYBACK_LOG_MAX = 256 * 1024
