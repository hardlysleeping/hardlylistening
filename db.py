"""HardlyListening — database layer.

SQLite is the source of truth for application state; the filesystem only holds
media. One connection per process (the queue serialises writes with a lock), WAL
so a read from the API never blocks a download.

Table map
  artists / releases / tracks     the library, single copy per recording
  playlists / playlist_tracks     membership; a track can be in many playlists
  download_jobs                   the work queue (status lives HERE, not on tracks)
  provider_state                  per-provider pause + 429 cooldown deadline
  watches / feed_items            discovery (written by the follow worker)
  settings / logs
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
import paths  # noqa: E402  (same directory; kept next to APP_DIR for readability)
DB_PATH = paths.DB_PATH
VERSION = (APP_DIR / "VERSION").read_text().strip() if (APP_DIR / "VERSION").exists() else "0.2.0"

# Download-job states. A track's state is its job's state — there is no second
# copy of it on the track row.
JOB_STATES = ("queued", "resolving", "downloading", "retry_wait",
              "completed", "failed", "unavailable", "cancelled", "review")
OPEN_STATES = ("queued", "resolving", "downloading", "retry_wait")

DEFAULTS = {
    "root": str(Path.home() / "Music" / "HardlyListening"),
    "audio_format": "mp3",
    "ai_apple_enabled": "1",
    "ai_model": "google/gemini-2.5-flash-lite",
    "ai_base_url": "https://openrouter.ai/api/v1",
    "pacing": "5",
    "max_attempts": "4",
    "cooldown": "900",
    "embed_thumb": "1",
    "poll_interval": "21600",
    "min_free_gb": "2",          # stop starting jobs below this; never hit ENOSPC
    "disk_limit_pct": "95",      # never let the library disk pass this % used
    "member_quota_gb": "10",     # how much music each non-admin member may add; 0 = unlimited
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  artist_id TEXT NOT NULL,
  name TEXT NOT NULL,
  url TEXT DEFAULT '',
  artwork TEXT DEFAULT '',
  UNIQUE(provider, artist_id)
);

CREATE TABLE IF NOT EXISTS artist_art (
  name_key TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  artwork TEXT NOT NULL DEFAULT '',
  manual INTEGER NOT NULL DEFAULT 0,
  provider TEXT NOT NULL DEFAULT '',
  fetched REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artist_id INTEGER REFERENCES artists(id),
  provider TEXT NOT NULL,
  release_id TEXT NOT NULL,
  title TEXT NOT NULL,
  release_type TEXT DEFAULT '',       -- album | ep | single | track | mix
  track_count INTEGER DEFAULT 0,
  release_date TEXT DEFAULT '',
  artwork TEXT DEFAULT '',
  source_url TEXT DEFAULT '',
  first_seen REAL NOT NULL,
  UNIQUE(provider, release_id)
);

CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artist_id INTEGER REFERENCES artists(id),
  release_id INTEGER REFERENCES releases(id),
  provider TEXT NOT NULL,
  provider_track_id TEXT NOT NULL,
  title TEXT NOT NULL,
  artist_name TEXT DEFAULT '',
  album TEXT DEFAULT '',
  track_number INTEGER DEFAULT 0,
  duration REAL DEFAULT 0,
  isrc TEXT DEFAULT '',
  bpm REAL DEFAULT 0,
  bpm_conf REAL DEFAULT 0,
  bpm_phase REAL DEFAULT 0,
  bpm_source TEXT DEFAULT '',
  bpm_at REAL DEFAULT 0,
  artwork TEXT DEFAULT '',
  source_url TEXT DEFAULT '',
  file TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  added REAL NOT NULL,
  UNIQUE(provider, provider_track_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS tracks_unique_id
  ON tracks(provider, provider_track_id);

CREATE TABLE IF NOT EXISTS playlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  provider TEXT DEFAULT '',
  source_url TEXT DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'url',      -- url | feed | manual | upload
  artwork TEXT DEFAULT '',
  added REAL NOT NULL,
  position INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0,
  -- 1 = the recording was already in the library when this playlist was added,
  -- so it is a membership link to the existing file, not a second copy.
  reused INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (playlist_id, track_id)
);

CREATE TABLE IF NOT EXISTS download_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  source_url TEXT DEFAULT '',
  resolved_url TEXT DEFAULT '',
  resolved_title TEXT DEFAULT '',
  match REAL DEFAULT 0,
  pinned INTEGER DEFAULT 0,
  tried TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  priority INTEGER DEFAULT 0,
  attempts INTEGER DEFAULT 0,
  max_attempts INTEGER DEFAULT 4,
  next_retry_at REAL DEFAULT 0,
  progress REAL DEFAULT 0,
  bytes INTEGER DEFAULT 0,
  speed REAL DEFAULT 0,
  eta REAL DEFAULT 0,
  error_type TEXT DEFAULT '',
  error_message TEXT DEFAULT '',
  added REAL NOT NULL,
  started REAL DEFAULT 0,
  finished REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS job_queue ON download_jobs(status, priority, id);
CREATE INDEX IF NOT EXISTS job_track ON download_jobs(track_id);

CREATE TABLE IF NOT EXISTS provider_state (
  provider TEXT PRIMARY KEY,
  paused INTEGER DEFAULT 0,
  cooldown_until REAL DEFAULT 0,
  consecutive_limits INTEGER DEFAULT 0,
  last_error TEXT DEFAULT '',
  last_success REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  artist_id TEXT NOT NULL,
  artist_name TEXT DEFAULT '',
  artist_url TEXT DEFAULT '',
  -- Who followed them. Empty on follows made before the Feed knew about
  -- members, and on installs with sign-in turned off.
  member_id TEXT DEFAULT '',
  interval REAL DEFAULT 21600,
  last_checked REAL DEFAULT 0,
  watermark REAL NOT NULL,             -- a new follow never backfills
  enabled INTEGER DEFAULT 1,
  added REAL NOT NULL,
  UNIQUE(provider, artist_id)
);

CREATE TABLE IF NOT EXISTS feed_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  watch_id INTEGER REFERENCES watches(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  release_id TEXT NOT NULL,
  artist TEXT DEFAULT '',
  title TEXT NOT NULL,
  release_type TEXT DEFAULT '',
  track_count INTEGER DEFAULT 0,
  release_date TEXT DEFAULT '',
  artwork TEXT DEFAULT '',
  source_url TEXT DEFAULT '',
  first_seen REAL NOT NULL,
  decision TEXT NOT NULL DEFAULT 'new',   -- new | seen | ignored | queued
  outcome TEXT NOT NULL DEFAULT 'none',   -- none | running | complete | failed
  playlist_id INTEGER REFERENCES playlists(id),
  UNIQUE(provider, release_id)
);
CREATE INDEX IF NOT EXISTS feed_decisions ON feed_items(decision, first_seen DESC);

-- What a follow has already looked at. `watches.watermark` cannot carry this on
-- its own: a flat artist listing has ids and titles but no publish dates, so
-- "new" has to mean "an id this follow has not seen before".
CREATE TABLE IF NOT EXISTS watch_items (
  watch_id INTEGER REFERENCES watches(id) ON DELETE CASCADE,
  release_id TEXT NOT NULL,
  first_seen REAL NOT NULL,
  PRIMARY KEY (watch_id, release_id)
);

-- An artist's whole catalogue, not just what is new. The Feed lists unhandled
-- releases; this is the discography behind the artist page, so opening it can
-- show everything the provider will give us without a fresh request each time.
CREATE TABLE IF NOT EXISTS artist_releases (
  watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  release_id TEXT NOT NULL,
  title TEXT NOT NULL,
  release_type TEXT DEFAULT '',
  track_count INTEGER DEFAULT 0,
  release_date TEXT DEFAULT '',
  artwork TEXT DEFAULT '',
  source_url TEXT DEFAULT '',
  fetched REAL NOT NULL,
  PRIMARY KEY (watch_id, provider, release_id)
);
CREATE INDEX IF NOT EXISTS artist_releases_order
  ON artist_releases(watch_id, release_date DESC);

-- Every song the catalogue lists. One row per song per follow: a song that is on
-- two compilations is one row, and `album` keeps the name it was released under.
CREATE TABLE IF NOT EXISTS artist_tracks (
  watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  track_id TEXT NOT NULL,
  release_id TEXT DEFAULT '',
  title TEXT NOT NULL,
  artist TEXT DEFAULT '',
  album TEXT DEFAULT '',
  track_no INTEGER DEFAULT 0,
  duration REAL DEFAULT 0,
  artwork TEXT DEFAULT '',
  source_url TEXT DEFAULT '',
  fetched REAL NOT NULL,
  PRIMARY KEY (watch_id, provider, track_id)
);
CREATE INDEX IF NOT EXISTS artist_tracks_order
  ON artist_tracks(watch_id, album, track_no);

CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  level TEXT NOT NULL,
  msg TEXT NOT NULL,
  ref TEXT DEFAULT ''
);

-- What people did, for the Feed's Activity tab. One row per event; consecutive
-- listens are stacked when the feed is read, not written.
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  member_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  track_id INTEGER NOT NULL DEFAULT 0,
  playlist_id INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts DESC);

-- One rolling 24-hour boost slot per member; changing track keeps the original expiry.
CREATE TABLE IF NOT EXISTS song_boosts (
  member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  boosted_at REAL NOT NULL,
  expires_at REAL NOT NULL
);

-- Subsonic bookmarks: one resumable position per member and track.
CREATE TABLE IF NOT EXISTS listening_bookmarks (
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  position_ms INTEGER NOT NULL DEFAULT 0,
  comment TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  updated REAL NOT NULL,
  PRIMARY KEY (member_id, track_id)
);
CREATE INDEX IF NOT EXISTS listening_bookmarks_member ON listening_bookmarks(member_id, updated DESC);

-- Public share links. Only the SHA-256 of the bearer token is stored, so a
-- database leak cannot mint a working link. Every row expires.
CREATE TABLE IF NOT EXISTS listening_shares (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('track', 'album', 'playlist')),
  target TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  expires REAL NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS listening_shares_member ON listening_shares(member_id, created DESC);
"""


def disk_free_gb(path: str | Path) -> float:
    """Free space at `path`, walking up to the nearest directory that exists.

    The library root may not exist yet (fresh install, a root that was just
    moved), and statvfs on a missing path raises instead of answering.
    """
    try:
        probe = Path(path).expanduser()
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        # Bazzite's "/" is a 42 MB read-only composefs image and always reads as
        # 0 GB free — never take that as the answer for a path that does not
        # exist yet, or a fresh install would refuse to download anything.
        if not probe.exists() or str(probe) == "/":
            probe = Path.home()
        return shutil.disk_usage(probe).free / (1024 ** 3)
    except OSError:
        return float("inf")          # unknown is not a reason to stop downloading


def disk_usage(path: str | Path) -> tuple[float, float, float]:
    """(total GB, free GB, used %) for the filesystem holding `path`.

    The volume is the one the library actually lives on — resolving through the
    nearest existing parent so a root that does not exist yet still answers.
    """
    try:
        probe = Path(path).expanduser()
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        if not probe.exists() or str(probe) == "/":
            probe = Path.home()
        usage = shutil.disk_usage(probe)
    except OSError:
        return (0.0, float("inf"), 0.0)      # unknown must not stop the queue
    total = usage.total / (1024 ** 3)
    free = usage.free / (1024 ** 3)
    used_pct = (100.0 * usage.used / usage.total) if usage.total else 0.0
    return (round(total, 2), round(free, 2), round(used_pct, 1))


#: Columns added after the first release. `CREATE TABLE IF NOT EXISTS` never
#: touches an existing table, so every new column has to be listed here or an
#: upgraded install silently loses it.
ADDED_COLUMNS = {
    "watches": {
        # Who followed this artist, so "My" can mean this member's own follows.
        "member_id": "TEXT DEFAULT ''",
    },
    "artists": {
        # A real profile picture (SoundCloud avatar), fetched once. Empty falls
        # back to an album cover in the UI.
        "artwork": "TEXT DEFAULT ''",
    },
    "listening_pins": {
        # Pins are dragged into an order the member chose; the number is that order.
        "position": "INTEGER NOT NULL DEFAULT 0",
    },
    "playlist_tracks": {
        # 1 = already in the library when this playlist was added: the row is a
        # link to the existing file, and it counts as grabbed straight away.
        "reused": "INTEGER NOT NULL DEFAULT 0",
    },
    "download_jobs": {
        "progress": "REAL DEFAULT 0",
        "bytes": "INTEGER DEFAULT 0",
        "speed": "REAL DEFAULT 0",
        "eta": "REAL DEFAULT 0",
        "tried": "TEXT DEFAULT ''",
        "pinned": "INTEGER DEFAULT 0",
        "candidates": "TEXT DEFAULT ''",
    },
    "tracks": {
        "isrc": "TEXT DEFAULT ''",
        # Whether the source still lists this track: '' unknown, 'present',
        # 'gone' (absent from a fresh read of the playlist it came from),
        # 'missing' (a catalog lookup says the store no longer has it).
        "source_status": "TEXT DEFAULT ''",
        "source_checked": "REAL DEFAULT 0",
        # Tempo grid: bpm 0 = never analysed. source is '' | 'auto' | 'manual',
        # so a corrected value is never overwritten by a re-analysis.
        "bpm": "REAL DEFAULT 0",
        "bpm_conf": "REAL DEFAULT 0",
        "bpm_phase": "REAL DEFAULT 0",
        "bpm_source": "TEXT DEFAULT ''",
        "bpm_at": "REAL DEFAULT 0",
    },
}


def migrate(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not present:
            continue                      # table itself does not exist yet
        for name, ddl in columns.items():
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


class ProjectionConnection(sqlite3.Connection):
    projection_callback = None
    projection_owner = None

    def close(self):
        owner = self.projection_owner
        if owner:
            with owner.lock:
                owner._projection_closed = True
                if owner._projection_timer:
                    owner._projection_timer.cancel()
                self.projection_callback = None
                super().close()
        else:
            super().close()

    def commit(self):
        super().commit()
        if self.projection_callback:
            self.projection_callback()


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=False, timeout=30, factory=ProjectionConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    migrate(conn)
    for key, value in DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (key, value))
    for provider in ("soundcloud", "apple", "youtube", "bandcamp"):
        conn.execute("INSERT OR IGNORE INTO provider_state (provider) VALUES (?)", (provider,))
    conn.commit()
    return conn


class Store:
    """All SQL in one place, guarded by one lock.

    The queue worker, the HTTP threads and the (future) poller all talk through
    this object, so "who may write" is a single `with store.lock:`.
    """

    def __init__(self, path: Path | None = None):
        import threading
        self.lock = threading.RLock()
        self.conn = connect(path)
        self.conn.row_factory = sqlite3.Row
        self._projection_closed = False
        self.conn.projection_owner = self
        self._projection_revision = -1
        self._projection_timer = None
        self.projection_error = ''
        self.conn.executescript("CREATE TEMP TABLE projection_revision (n INTEGER); INSERT INTO projection_revision VALUES (0);")
        for table, operations in {
                'playlists': ('INSERT', 'UPDATE', 'DELETE'),
                'playlist_tracks': ('INSERT', 'UPDATE', 'DELETE'),
                'tracks': ('INSERT', 'UPDATE OF file,title,artist_name,duration', 'DELETE'),
                'download_jobs': ('INSERT', 'UPDATE OF status', 'DELETE'),
                'settings': ('INSERT', 'UPDATE OF value', 'DELETE')}.items():
            for index, operation in enumerate(operations):
                self.conn.execute(f"CREATE TEMP TRIGGER projection_{table}_{index} AFTER {operation} ON main.{table} BEGIN UPDATE projection_revision SET n=n+1; END")
        self.conn.projection_callback = self._schedule_projection
        self._schedule_projection()

    def _schedule_projection(self):
        import threading
        with self.lock:
            revision = self.conn.execute('SELECT n FROM projection_revision').fetchone()[0]
            if revision == self._projection_revision:
                return
            if self._projection_timer and self._projection_timer.is_alive():
                return
            self._projection_timer = threading.Timer(0.25, self.sync_playlists)
            self._projection_timer.daemon = True
            self._projection_timer.start()

    def sync_playlists(self):
        from library.playlists import sync
        with self.lock:
            if self._projection_closed:
                return
            try:
                sync(self)
                self._projection_revision = self.conn.execute('SELECT n FROM projection_revision').fetchone()[0]
                self.projection_error = ''
            except (OSError, ValueError) as exc:
                self.projection_error = str(exc)

    # -- lifecycle --
    def close(self) -> None:
        """Commit what is pending and close the connection. Called once, at shutdown.

        The lock is the same one every other thread uses, so this waits for an in-flight
        statement rather than closing the handle underneath it.
        """
        with self.lock:
            try:
                self.conn.commit()
            finally:
                self.conn.close()

    # -- settings --
    def settings(self) -> dict[str, str]:
        with self.lock:
            return {r["key"]: r["value"]
                    for r in self.conn.execute("SELECT key,value FROM settings")}

    def setting(self, key: str, default: str = "") -> str:
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key=?",
                                    (key,)).fetchone()
        return row["value"] if row else DEFAULTS.get(key, default)

    def setting_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.setting(key, str(default)))
        except ValueError:
            return default

    def set_settings(self, values: dict) -> dict:
        allowed = set(DEFAULTS)
        with self.lock:
            for key, value in values.items():
                if key in allowed:
                    self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                                      (key, str(value)))
            self.conn.commit()
        return self.settings()

    # -- logs --
    def log(self, msg: str, level: str = "info", ref: str = "") -> None:
        with self.lock:
            self.conn.execute("INSERT INTO logs (ts,level,msg,ref) VALUES (?,?,?,?)",
                              (time.time(), level, msg[:600], ref))
            self.conn.execute("DELETE FROM logs WHERE id < (SELECT MAX(id)-400 FROM logs)")
            self.conn.commit()

    def logs(self, limit: int = 80) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT ts,level,msg FROM logs ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # -- provider state --
    def provider(self, name: str) -> dict:
        with self.lock:
            row = self.conn.execute("SELECT * FROM provider_state WHERE provider=?",
                                    (name,)).fetchone()
            if not row:
                self.conn.execute("INSERT INTO provider_state (provider) VALUES (?)", (name,))
                self.conn.commit()
                row = self.conn.execute("SELECT * FROM provider_state WHERE provider=?",
                                        (name,)).fetchone()
        return dict(row)

    def set_provider(self, name: str, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE provider_state SET {cols} WHERE provider=?",
                              (*fields.values(), name))
            self.conn.commit()

    def cooldown(self) -> tuple[float, str]:
        """Latest cooldown across providers: (deadline, provider)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT provider, cooldown_until, last_error FROM provider_state"
                " ORDER BY cooldown_until DESC LIMIT 1").fetchone()
        if row and row["cooldown_until"] > time.time():
            return row["cooldown_until"], row["provider"]
        return 0.0, ""

    # -- artists / releases / tracks --
    def artist(self, provider: str, artist_id: str, name: str, url: str = "",
               artwork: str = "") -> int:
        with self.lock:
            self.conn.execute(
                "INSERT INTO artists (provider,artist_id,name,url,artwork) VALUES (?,?,?,?,?)"
                " ON CONFLICT(provider,artist_id) DO UPDATE SET"
                " name=excluded.name, url=COALESCE(NULLIF(excluded.url,''),artists.url),"
                " artwork=COALESCE(NULLIF(excluded.artwork,''),artists.artwork)",
                (provider, artist_id or "", name or "", url or "", artwork or ""))
            self.conn.commit()
            return self.conn.execute(
                "SELECT id FROM artists WHERE provider=? AND artist_id=?",
                (provider, artist_id or "")).fetchone()["id"]

    def set_artist_artwork(self, artist_id: int, artwork: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE artists SET artwork=? WHERE id=?", (artwork or "", artist_id))
            self.conn.commit()

    @staticmethod
    def _artist_key(name: str) -> str:
        return " ".join((name or "").casefold().split())

    def artist_art_map(self) -> dict[str, str]:
        with self.lock:
            return {row["name_key"]: row["artwork"] for row in self.conn.execute(
                "SELECT name_key, artwork FROM artist_art WHERE artwork<>''")}

    def set_artist_art(self, name: str, artwork: str, *, manual: bool = False,
                       provider: str = "") -> None:
        key = self._artist_key(name)
        if not key or not artwork:
            return
        with self.lock:
            if manual:
                self.conn.execute(
                    "INSERT INTO artist_art(name_key,name,artwork,manual,provider,fetched)"
                    " VALUES(?,?,?,1,?,?) ON CONFLICT(name_key) DO UPDATE SET"
                    " artwork=excluded.artwork, manual=1, provider=excluded.provider,"
                    " fetched=excluded.fetched",
                    (key, name or "", artwork, provider or "", time.time()))
            else:
                # Never overwrite a manual upload, and fetch only when empty.
                self.conn.execute(
                    "INSERT INTO artist_art(name_key,name,artwork,manual,provider,fetched)"
                    " VALUES(?,?,?,0,?,?) ON CONFLICT(name_key) DO UPDATE SET"
                    " artwork=excluded.artwork, provider=excluded.provider,"
                    " fetched=excluded.fetched"
                    " WHERE artist_art.manual=0 AND artist_art.artwork=''",
                    (key, name or "", artwork, provider or "", time.time()))
            self.conn.commit()

    def fill_release_artwork(self, track_id: int) -> str:
        """Put the release's cover on a track whose artwork is still empty.

        Only kinds that are a real release: a playlist is a set, and its cover is
        the set's own art, never the album of the songs inside it. An artwork
        already on the row is left alone.
        """
        with self.lock:
            self.conn.execute(
                "UPDATE tracks SET artwork=COALESCE(NULLIF(artwork,''),"
                " (SELECT r.artwork FROM releases r WHERE r.id=tracks.release_id"
                "  AND r.release_type IN ('album','single','ep','track')))"
                " WHERE id=?", (track_id,))
            self.conn.commit()
            row = self.conn.execute("SELECT artwork FROM tracks WHERE id=?",
                                    (track_id,)).fetchone()
        return (row["artwork"] if row else "") or ""

    def release(self, provider: str, release_id: str, title: str, *,
                artist_id: int | None = None, release_type: str = "",
                track_count: int = 0, release_date: str = "", artwork: str = "",
                source_url: str = "") -> int:
        with self.lock:
            self.conn.execute(
                "INSERT INTO releases (artist_id,provider,release_id,title,release_type,"
                "track_count,release_date,artwork,source_url,first_seen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(provider,release_id) DO UPDATE SET"
                " title=excluded.title, track_count=MAX(releases.track_count,excluded.track_count),"
                " artwork=COALESCE(NULLIF(excluded.artwork,''),releases.artwork)",
                (artist_id, provider, release_id, title, release_type, track_count,
                 release_date, artwork, source_url, time.time()))
            self.conn.commit()
            return self.conn.execute(
                "SELECT id FROM releases WHERE provider=? AND release_id=?",
                (provider, release_id)).fetchone()["id"]

    def track(self, provider: str, provider_track_id: str, title: str, *,
              artist_name: str = "", album: str = "", track_number: int = 0,
              duration: float = 0, artwork: str = "", source_url: str = "",
              release_id: int | None = None, artist_id: int | None = None,
              isrc: str = "") -> int:
        """Upsert by provider identity. The same recording is one row forever."""
        with self.lock:
            self.conn.execute(
                "INSERT INTO tracks (artist_id,release_id,provider,provider_track_id,title,"
                "artist_name,album,track_number,duration,isrc,artwork,source_url,added)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(provider,provider_track_id) DO UPDATE SET"
                " title=excluded.title,"
                " artist_name=COALESCE(NULLIF(excluded.artist_name,''),tracks.artist_name),"
                " album=COALESCE(NULLIF(excluded.album,''),tracks.album),"
                " artwork=COALESCE(NULLIF(excluded.artwork,''),tracks.artwork),"
                " duration=CASE WHEN excluded.duration>0 THEN excluded.duration ELSE tracks.duration END,"
                " release_id=COALESCE(excluded.release_id, tracks.release_id)",
                (artist_id, release_id, provider, provider_track_id, title,
                 artist_name, album, track_number, duration, isrc, artwork,
                 source_url, time.time()))
            self.conn.commit()
            return self.conn.execute(
                "SELECT id FROM tracks WHERE provider=? AND provider_track_id=?",
                (provider, provider_track_id)).fetchone()["id"]

    def track_by_id(self, track_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM tracks WHERE id=?",
                                    (track_id,)).fetchone()
        return dict(row) if row else None

    # -- playlists --
    def playlist(self, title: str, provider: str, source_url: str, *,
                 kind: str = "url", artwork: str = "") -> int:
        with self.lock:
            exists = self.conn.execute(
                "SELECT id FROM playlists WHERE source_url=? AND source_url<>''",
                (source_url,)).fetchone()
            if exists:
                return exists["id"]
            pos = self.conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 p FROM playlists").fetchone()["p"]
            cur = self.conn.execute(
                "INSERT INTO playlists (title,provider,source_url,kind,artwork,added,position)"
                " VALUES (?,?,?,?,?,?,?)",
                (title, provider, source_url, kind, artwork, time.time(), pos))
            self.conn.commit()
            return cur.lastrowid

    def add_to_playlist(self, playlist_id: int, track_id: int, position: int,
                        reused: bool = False) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO playlist_tracks"
                " (playlist_id,track_id,position,reused) VALUES (?,?,?,?)",
                (playlist_id, track_id, position, 1 if reused else 0))
            self.conn.commit()

    # -- jobs --
    def enqueue(self, track_id: int, provider: str, source_url: str = "",
                priority: int = 0) -> int | None:
        """One job per track while a job is still open; re-queueing is a no-op."""
        with self.lock:
            open_job = self.conn.execute(
                "SELECT id FROM download_jobs WHERE track_id=? AND status IN"
                " ('queued','resolving','downloading','retry_wait')", (track_id,)).fetchone()
            if open_job:
                return None
            cur = self.conn.execute(
                "INSERT INTO download_jobs (track_id,provider,source_url,status,"
                "priority,max_attempts,added) VALUES (?,?,?,'queued',?,?,?)",
                (track_id, provider, source_url, priority,
                 int(self.setting_float("max_attempts", 4)), time.time()))
            self.conn.commit()
            return cur.lastrowid

    def job(self, job_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM download_jobs WHERE id=?",
                                    (job_id,)).fetchone()
        return dict(row) if row else None

    def update_job(self, job_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE download_jobs SET {cols} WHERE id=?",
                              (*fields.values(), job_id))
            self.conn.commit()

    def next_job(self) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM download_jobs WHERE status IN ('queued','retry_wait')"
                " AND next_retry_at <= ? ORDER BY priority DESC, id LIMIT 1",
                (time.time(),)).fetchone()
        return dict(row) if row else None

    def job_for_track(self, track_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM download_jobs WHERE track_id=? ORDER BY id DESC LIMIT 1",
                (track_id,)).fetchone()
        return dict(row) if row else None

    def open_jobs(self) -> list[dict]:
        """Every job still waiting for the worker, in the order it will run."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM download_jobs WHERE status IN ('queued','retry_wait')"
                " ORDER BY priority DESC, id").fetchall()
        return [dict(r) for r in rows]

    def move_job(self, job_id: int, delta: int) -> bool:
        """Move a waiting job up (-1) or down (+1) in the real run order.

        Priorities are rewritten to 1..n on every move. Swapping two equal
        priorities would otherwise do nothing, which looks like a broken arrow.
        """
        jobs = self.open_jobs()
        ids = [job["id"] for job in jobs]
        if job_id not in ids:
            return False
        index = ids.index(job_id)
        target = index + delta
        if target < 0 or target >= len(ids):
            return False
        ids[index], ids[target] = ids[target], ids[index]
        with self.lock:
            for position, jid in enumerate(ids):
                self.conn.execute("UPDATE download_jobs SET priority=? WHERE id=?",
                                  (len(ids) - position, jid))
            self.conn.commit()
        return True

    def stats(self) -> dict:
        with self.lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) n FROM download_jobs GROUP BY status").fetchall()
            sizes = self.conn.execute("SELECT COALESCE(SUM(size),0) s FROM tracks").fetchone()["s"]
            tracks = self.conn.execute("SELECT COUNT(*) n FROM tracks").fetchone()["n"]
        counts = {r["status"]: r["n"] for r in rows}
        free = disk_free_gb(self.setting("root"))
        floor = self.setting_float("min_free_gb", 2.0)
        total_gb, _free, used_pct = disk_usage(self.setting("root"))
        limit_pct = self.setting_float("disk_limit_pct", 95.0)
        # Two guards, either one stops new downloads: a free-space floor for small
        # volumes, and a percentage so a big drive cannot be filled to the brim.
        return {
            "disk_free_gb": round(free, 2),
            "disk_total_gb": total_gb,
            "disk_used_pct": used_pct,
            "disk_limit_pct": limit_pct,
            "disk_floor_gb": floor,
            "disk_blocked": free < floor or used_pct >= limit_pct,
            "tracks": tracks,
            "done": counts.get("completed", 0),
            "queued": counts.get("queued", 0),
            "running": counts.get("downloading", 0) + counts.get("resolving", 0),
            "waiting": counts.get("retry_wait", 0),
            "failed": counts.get("failed", 0),
            "unavailable": counts.get("unavailable", 0),
            "review": counts.get("review", 0),
            "bytes": sizes,
        }

    # -- following / discovery --
    def watch_add(self, provider: str, artist_id: str, artist_name: str,
                  artist_url: str, interval: float = 0, member_id: str = "") -> int:
        with self.lock:
            # A follow is shared: the second person to follow an artist joins the
            # existing watch, and the first one keeps the credit for it.
            self.conn.execute(
                "INSERT INTO watches (provider,artist_id,artist_name,artist_url,"
                "member_id,interval,last_checked,watermark,enabled,added)"
                " VALUES (?,?,?,?,?,?,0,?,1,?)"
                " ON CONFLICT(provider,artist_id) DO UPDATE SET"
                " artist_name=excluded.artist_name,"
                " artist_url=excluded.artist_url, enabled=1,"
                # A follow made before ownership existed belongs to nobody, so
                # the first person to ask for it again claims it.
                " member_id=CASE WHEN COALESCE(watches.member_id,'')=''"
                " THEN excluded.member_id ELSE watches.member_id END",
                (provider, artist_id, artist_name, artist_url, member_id or "",
                 interval or self.setting_float("poll_interval", 21600),
                 time.time(), time.time()))
            self.conn.commit()
            return self.conn.execute(
                "SELECT id FROM watches WHERE provider=? AND artist_id=?",
                (provider, artist_id)).fetchone()["id"]

    def watch_get(self, watch_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM watches WHERE id=?",
                                    (watch_id,)).fetchone()
        return dict(row) if row else None

    def watches(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT w.*, (SELECT COUNT(*) FROM feed_items f WHERE f.watch_id=w.id"
                " AND f.decision='new') AS new_items FROM watches w ORDER BY w.added"
            ).fetchall()
        return [dict(r) for r in rows]

    def watch_release(self, watch_id: int, member_id: str = "") -> bool:
        """Drop this member's claim on a follow. The follow itself stays, so the
        artist is still tracked and still listed for everybody else."""
        with self.lock:
            if member_id:
                cur = self.conn.execute(
                    "UPDATE watches SET member_id='' WHERE id=? AND member_id=?",
                    (int(watch_id), member_id))
            else:
                cur = self.conn.execute(
                    "UPDATE watches SET member_id='' WHERE id=?", (int(watch_id),))
            self.conn.commit()
            return cur.rowcount > 0

    def watch_due(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM watches WHERE enabled=1 AND last_checked + interval <= ?"
                " ORDER BY last_checked LIMIT 5", (now,)).fetchall()
        return [dict(r) for r in rows]

    def watch_mark(self, watch_id: int, *, checked: bool = False,
                   enabled: bool | None = None) -> None:
        with self.lock:
            if checked:
                self.conn.execute("UPDATE watches SET last_checked=?, watermark=? WHERE id=?",
                                  (time.time(), time.time(), watch_id))
            if enabled is not None:
                self.conn.execute("UPDATE watches SET enabled=? WHERE id=?",
                                  (1 if enabled else 0, watch_id))
            self.conn.commit()

    def watch_remove(self, watch_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM watches WHERE id=?", (watch_id,))
            self.conn.commit()

    # ---- an artist's stored catalogue -------------------------------------
    # Written as a whole on each refresh: the provider's list is the truth, so a
    # release that disappeared upstream disappears here too.
    def artist_catalogue_replace(self, watch_id: int, releases: list[dict],
                                 tracks: list[dict]) -> None:
        now = time.time()
        with self.lock:
            self.conn.execute("DELETE FROM artist_releases WHERE watch_id=?", (watch_id,))
            self.conn.execute("DELETE FROM artist_tracks WHERE watch_id=?", (watch_id,))
            self.conn.executemany(
                "INSERT OR REPLACE INTO artist_releases (watch_id,provider,release_id,title,"
                "release_type,track_count,release_date,artwork,source_url,fetched)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(watch_id, r.get("provider", ""), r.get("release_id", ""), r.get("title", ""),
                  r.get("release_type", ""), int(r.get("track_count") or 0),
                  r.get("release_date", ""), r.get("artwork", ""), r.get("source_url", ""), now)
                 for r in releases])
            self.conn.executemany(
                "INSERT OR REPLACE INTO artist_tracks (watch_id,provider,track_id,release_id,"
                "title,artist,album,track_no,duration,artwork,source_url,fetched)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(watch_id, t.get("provider", ""), t.get("track_id", ""), t.get("release_id", ""),
                  t.get("title", ""), t.get("artist", ""), t.get("album", ""),
                  int(t.get("track_no") or 0), float(t.get("duration") or 0),
                  t.get("artwork", ""), t.get("source_url", ""),
                  float(t.get("fetched") or now))
                 for t in tracks])
            self.conn.commit()

    def artist_catalogue_stamp(self, watch_id: int) -> float:
        with self.lock:
            row = self.conn.execute(
                "SELECT MAX(fetched) FROM artist_releases WHERE watch_id=?",
                (watch_id,)).fetchone()
        return float(row[0] or 0) if row else 0.0

    def artist_releases(self, watch_id: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM artist_releases WHERE watch_id=?"
                " ORDER BY release_date DESC, title COLLATE NOCASE", (watch_id,)).fetchall()
        return [dict(r) for r in rows]

    def artist_tracks(self, watch_id: int, offset: int = 0,
                      limit: int = 60) -> tuple[list[dict], int]:
        """One page of the catalogue's songs, A→Z by album then track number."""
        with self.lock:
            total = self.conn.execute(
                "SELECT COUNT(*) FROM artist_tracks WHERE watch_id=?", (watch_id,)).fetchone()[0]
            rows = self.conn.execute(
                "SELECT * FROM artist_tracks WHERE watch_id=?"
                " ORDER BY album COLLATE NOCASE, track_no, title COLLATE NOCASE"
                " LIMIT ? OFFSET ?", (watch_id, limit, offset)).fetchall()
        return [dict(r) for r in rows], int(total)

    def artist_tracks_all(self, watch_id: int) -> list[dict]:
        """Every stored song, for deciding which album tracklists can be reused."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM artist_tracks WHERE watch_id=?", (watch_id,)).fetchall()
        return [dict(r) for r in rows]

    def watch_seen(self, watch_id: int) -> set[str]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT release_id FROM watch_items WHERE watch_id=?",
                (watch_id,)).fetchall()
        return {r["release_id"] for r in rows}

    def watch_remember(self, watch_id: int, release_ids: list[str]) -> None:
        if not release_ids:
            return
        now = time.time()
        with self.lock:
            self.conn.executemany(
                "INSERT OR IGNORE INTO watch_items (watch_id,release_id,first_seen)"
                " VALUES (?,?,?)", [(watch_id, rid, now) for rid in release_ids])
            self.conn.commit()

    def feed_add(self, watch_id: int, provider: str, release_id: str, title: str, *,
                 artist: str = "", release_type: str = "", track_count: int = 0,
                 release_date: str = "", artwork: str = "", source_url: str = "") -> int | None:
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO feed_items (watch_id,provider,release_id,artist,"
                "title,release_type,track_count,release_date,artwork,source_url,first_seen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (watch_id, provider, release_id, artist, title, release_type,
                 track_count, release_date, artwork, source_url, time.time()))
            self.conn.commit()
            return cur.lastrowid if cur.rowcount else None

    def feed_item(self, item_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM feed_items WHERE id=?",
                                    (item_id,)).fetchone()
        return dict(row) if row else None

    def feed_list(self, limit: int = 200, decision: str = "") -> list[dict]:
        sql = ("SELECT f.*, w.artist_name AS follow_name FROM feed_items f"
               " LEFT JOIN watches w ON w.id=f.watch_id")
        args: list = []
        if decision:
            sql += " WHERE f.decision=?"
            args.append(decision)
        sql += " ORDER BY f.first_seen DESC, f.id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def feed_mark(self, item_id: int, *, decision: str = "", outcome: str = "",
                  playlist_id: int | None = None) -> None:
        with self.lock:
            if decision:
                self.conn.execute("UPDATE feed_items SET decision=? WHERE id=?",
                                  (decision, item_id))
            if outcome:
                self.conn.execute("UPDATE feed_items SET outcome=? WHERE id=?",
                                  (outcome, item_id))
            if playlist_id:
                self.conn.execute("UPDATE feed_items SET playlist_id=? WHERE id=?",
                                  (playlist_id, item_id))
            self.conn.commit()

    def library(self, include_pending: bool = False) -> list[dict]:
        """Every track on disk, each carrying every playlist that references it.

        Membership is a join, never a folder: one recording can be in five sets.
        """
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT t.*, r.release_type AS release_type, r.artwork AS release_artwork, j.status, j.resolved_url FROM tracks t LEFT JOIN releases r ON r.id=t.release_id LEFT JOIN (SELECT track_id, MAX(id) AS latest FROM download_jobs GROUP BY track_id) latest ON latest.track_id=t.id LEFT JOIN download_jobs j ON j.id=latest.latest "
                + ("" if include_pending else "WHERE t.file<>'' ") +
                "ORDER BY t.artist_name, t.album, t.track_number").fetchall()]
            links = self.conn.execute(
                "SELECT pt.track_id AS track_id, p.id AS pid, p.title AS ptitle,"
                " p.provider AS pprovider, p.kind AS pkind, pt.position AS position"
                " FROM playlist_tracks pt JOIN playlists p ON p.id=pt.playlist_id"
                " ORDER BY p.position").fetchall()
        sets: dict[int, list[dict]] = {}
        for link in links:
            sets.setdefault(link["track_id"], []).append({
                "id": link["pid"], "title": link["ptitle"],
                "provider": link["pprovider"], "kind": link["pkind"], "position": link["position"]})
        for row in rows:
            row["playlists"] = sets.get(row["id"], [])
            row["source_status"] = row.get("source_status") or ""
            row["source_checked"] = row.get("source_checked") or 0
            row['playable'] = bool(row['file'] and Path(row['file']).is_file())
            # One cover per release. A song imported from the Feed used to keep only its
            # own embedded frame, so tracks off the same album showed different pictures
            # (and a grabbed album had none at all). A real release's artwork is the
            # album's cover; a playlist is a set, not an album, so its cover is never
            # stamped onto the songs inside it.
            release_art = (row.get('release_artwork') or '').strip()
            if release_art and (row.get('release_type') or '') in ('album', 'single', 'ep', 'track'):
                row['artwork'] = release_art
            # SoundCloud imports carry no artwork URL, but the mp3 has the cover
            # embedded — the local extractor has it. point at it so the grid and
            # the rows are not full of empty tiles.
            elif not row.get('artwork') and row['playable']:
                row['artwork'] = f"/api/track/{row['id']}/artwork"
            row['status'] = row['status'] or ('completed' if row['playable'] else 'missing')
            if not row['playable'] and (row['file'] or row['status'] == 'completed'):
                row['status'] = 'missing' 
        from genres import annotate as annotate_genres
        return annotate_genres(self, rows)

    def state(self) -> dict:
        """Everything the UI polls for, in one snapshot."""
        with self.lock:
            jobs = {}
            for row in self.conn.execute(
                    "SELECT * FROM download_jobs ORDER BY id DESC").fetchall():
                jobs.setdefault(row["track_id"], dict(row))
            playlists = [dict(r) for r in self.conn.execute(
                "SELECT * FROM playlists ORDER BY position, id").fetchall()]
            out = []
            for playlist in playlists:
                rows = self.conn.execute(
                    "SELECT t.*, pt.position AS list_position, pt.reused AS list_reused,"
                    " r.release_type AS release_type, r.artwork AS release_artwork"
                    " FROM playlist_tracks pt"
                    " JOIN tracks t ON t.id = pt.track_id"
                    " LEFT JOIN releases r ON r.id = t.release_id WHERE pt.playlist_id=?"
                    " ORDER BY pt.position, t.id", (playlist["id"],)).fetchall()
                tracks = []
                for row in rows:
                    track = dict(row)
                    # Same rule as the library: a real release's artwork is the album's
                    # cover, and a playlist's cover never stamps the songs inside it.
                    # Without this the Download queue showed one cover per track.
                    release_art = (track.get("release_artwork") or "").strip()
                    if release_art and (track.get("release_type") or "") in (
                            "album", "single", "ep", "track"):
                        track["artwork"] = release_art
                    job = jobs.get(track["id"]) or {}
                    candidates = job.get("candidates") or ""
                    # Stored review candidates may predate the current ranking, so
                    # they are re-scored on the way out: a percentage on screen has
                    # to mean what the current formula means.
                    if job.get("status") == "review" and candidates:
                        try:
                            pool = json.loads(candidates)
                        except ValueError:
                            pool = []
                        if pool:
                            import ranking
                            query = ranking.query_for(track)
                            if query:
                                pool = ranking.rank(self, query, pool)
                                candidates = json.dumps(pool)
                                job = dict(job, match=max(c["match"] for c in pool))
                    tracks.append({
                        "id": track["id"],
                        "position": track["list_position"],
                        "title": track["title"],
                        "artist": track["artist_name"],
                        "duration": track["duration"],
                        # Already in the library when this playlist was added: a
                        # link to the existing file, so it was never downloaded
                        # again but it still counts as grabbed.
                        "reused": 1 if track["list_reused"] else 0,
                        # No job left (finished jobs may be cleared) but the file
                        # is on disk: that track is done, not queued.
                        "status": job.get("status") or ("completed" if track["file"] else "queued"),
                        "attempts": job.get("attempts", 0),
                        "error": job.get("error_message", ""),
                        "kind": job.get("error_type", ""),
                        "file": track["file"],
                        "size": track["size"],
                        # Same rule as the library grid: no provider artwork URL
                        # means the cover from the downloaded file.
                        "artwork": track.get("artwork") or (
                            f"/api/track/{track['id']}/artwork" if track["file"] else ""),
                        "match": job.get("match", 0),
                        "candidates": candidates,
                        "priority": job.get("priority", 0),
                        "resolved_title": job.get("resolved_title", ""),
                        "next_try": job.get("next_retry_at", 0),
                        "max_attempts": job.get("max_attempts", 4),
                        "started": job.get("started", 0),
                        "progress": job.get("progress", 0),
                        "bytes": job.get("bytes", 0),
                        "speed": job.get("speed", 0),
                        "eta": job.get("eta", 0),
                        "source_url": job.get("source_url", ""),
                        "resolved_url": job.get("resolved_url", ""),
                        # A hand-picked source can be undone; an automatic one only
                        # needs a better search, so the UI must tell them apart.
                        "pinned": 1 if job.get("pinned") else 0,
                    })
                done = sum(1 for t in tracks if t["status"] == "completed")
                # Of those, how many were already on disk when the playlist was
                # added. They count as grabbed (they are in `done`); this number
                # exists so the card can say why they did not download.
                reused = sum(1 for t in tracks
                             if t["reused"] and t["status"] == "completed")
                # Waiting tracks first, in the order the worker will actually run
                # them; finished ones stay in playlist order underneath. Without
                # this the reorder arrows change the run order but the list never
                # moves, which reads as a broken button.
                tracks.sort(key=lambda t: (-(t.get("priority") or 0), t["position"]))
                for index, track in enumerate(tracks, start=1):
                    track["position"] = index      # the row number is the list's
                # A set the provider gave no cover for shows its first track's, not
                # an empty tile.
                playlist_art = playlist["artwork"] or next(
                    (t["artwork"] for t in tracks if t.get("artwork")), "")
                out.append({
                    "id": playlist["id"], "url": playlist["source_url"],
                    "platform": playlist["provider"], "title": playlist["title"],
                    "artwork": playlist_art, "status": "ready", "error": "",
                    "declared": len(tracks), "added": playlist["added"],
                    "folder": "", "done": done, "reused": reused,
                    "queued": sum(1 for t in tracks if t["status"] == "queued"),
                    "failed": sum(1 for t in tracks
                                  if t["status"] in ("failed", "unavailable")),
                    "review": sum(1 for t in tracks if t["status"] == "review"),
                    "count": len(tracks), "tracks": tracks,
                })
            import ranking as _ranking
            try:
                ranking_picks = len(_ranking.history(self))
            except Exception:                 # noqa: BLE001 - no table, no picks
                ranking_picks = 0
            # Counted from the jobs themselves, not from whatever a playlist
            # happens to list: a review row is a review row. Keep 0.90 in step
            # with engine.queue.AUTO_ACCEPT.
            review_jobs = {tid: job for tid, job in jobs.items()
                           if job.get("status") == "review"}
            strong_review = 0
            if review_jobs:
                import ranking
                placeholders = ",".join("?" * len(review_jobs))
                meta = {r["id"]: dict(r) for r in self.conn.execute(
                    f"SELECT id, title, artist_name FROM tracks WHERE id IN ({placeholders})",
                    tuple(review_jobs)).fetchall()}
                for tid, job in review_jobs.items():
                    try:
                        pool = json.loads(job.get("candidates") or "[]")
                    except ValueError:
                        continue
                    track = meta.get(tid) or {}
                    query = ranking.query_for(track)
                    if not query or not pool:
                        continue
                    if max(c["match"] for c in ranking.rank(self, query, pool)) >= 0.90:
                        strong_review += 1
            # What the worker is on right now, so the queue can say what is
            # actually moving instead of just labelling rows "downloading".
            live = [j for j in jobs.values()
                    if j.get("status") in ("downloading", "resolving")]
            current = None
            if live:
                job = max(live, key=lambda j: j.get("started") or 0)
                row = self.conn.execute(
                    "SELECT title, artist_name FROM tracks WHERE id=?",
                    (job["track_id"],)).fetchone()
                # The queue's headline is useless without saying which playlist it is
                # filling: the track belongs to a collection, and that is what a person
                # pasted and is waiting for.
                owner = self.conn.execute(
                    "SELECT p.id, p.title FROM playlist_tracks pt JOIN playlists p"
                    " ON p.id=pt.playlist_id WHERE pt.track_id=?"
                    " ORDER BY pt.playlist_id, pt.position LIMIT 1",
                    (job["track_id"],)).fetchone()
                current = {
                    "track_id": job["track_id"],
                    "title": (row["title"] if row else "") or job.get("resolved_title", ""),
                    "artist": row["artist_name"] if row else "",
                    "status": job["status"],
                    "progress": job.get("progress", 0),
                    "bytes": job.get("bytes", 0),
                    "speed": job.get("speed", 0),
                    "eta": job.get("eta", 0),
                    "attempts": job.get("attempts", 0),
                    "max_attempts": job.get("max_attempts", 4),
                    "started": job.get("started", 0),
                    "provider": job.get("provider", ""),
                    "playlist": (owner["title"] if owner else "") or "",
                    "playlist_id": (owner["id"] if owner else 0),
                }
        deadline, provider = self.cooldown()
        stats = self.stats()
        stats.update({
            "total": stats["tracks"], "downloading": stats["running"],
            "paused": any(self.provider(p)["paused"] for p in ("soundcloud", "apple")),
            "cooldown_until": deadline, "cooldown_provider": provider,
            "cooldown_reason": self.provider(provider)["last_error"] if provider else "",
            "ai_key_configured": bool(os.environ.get(
                "OPENROUTER_API_KEY" if self.setting("ai_base_url", "https://openrouter.ai/api/v1").rstrip("/") == "https://openrouter.ai/api/v1"
                else "HARDLYLISTENING_AI_API_KEY", "").strip()),
            "ai_metadata_only": True, "playlist_projection_error": self.projection_error,
            "now": time.time(), "version": VERSION,
            # Shared restore Shortcut (iCloud link). Empty when unset, and the UI then
            # hides the button instead of offering a dead one.
            "apple_restore_shortcut": os.environ.get(
                "HARDLYLISTENING_APPLE_RESTORE_SHORTCUT_URL", "").strip(),
            "yt_dlp": __import__("downloaders.ytdlp", fromlist=["x"]).YTDLP,
            "review_strong": strong_review,
            "ranking_picks": ranking_picks,
        })
        return {"playlists": out, "stats": stats, "logs": self.logs(),
                "settings": self.settings(), "current": current,
                "follows": self.watches(),
                "feed_new": self.conn.execute(
                    "SELECT COUNT(*) n FROM feed_items WHERE decision='new'"
                ).fetchone()["n"]}
