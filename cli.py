"""The packaged entry point: `run`, `check`, `version`.

`bin/hardlylistening-server` is a two-line shell wrapper around this file, so the logic lives
where it can be tested. Nothing here daemonises, forks or writes a PID file: systemd, Docker or
the desktop shell owns the process, and this only decides what to start.

`check` is the installer's and systemd's `ExecStartPre` probe. It reports, it never repairs —
`ExecStartPre` must not be where a broken install discovers destructive ambition — and it writes
to nothing: a missing directory is reported as missing, not created, and the database is opened in
a transaction that is rolled back.
"""
import argparse
import os
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

import config
import paths

APP_DIR = Path(__file__).resolve().parent
USAGE = """usage: hardlylistening-server [command] [--host HOST] [--port PORT]

commands:
  run       serve (the default when no command is given)
  check     config, directories, database, disk, yt-dlp, ffmpeg, listen port
  version   print the installed version
"""


def version() -> str:
    marker = APP_DIR / "VERSION"
    return marker.read_text().strip() if marker.is_file() else "unknown"


def _disk_free_gb(path: Path) -> float:
    stat = shutil.disk_usage(path)
    return stat.free / (1024 ** 3)


def _disk_used_pct(path: Path) -> int:
    stat = shutil.disk_usage(path)
    return round(stat.used / stat.total * 100)


def _tool(name: str) -> str:
    """Where a required binary is, using the same fallbacks the downloader uses."""
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/home/bazzite/.local/bin/{name}", f"/usr/local/bin/{name}",
                      f"/usr/bin/{name}", f"/home/linuxbrew/.linuxbrew/bin/{name}"):
        if Path(candidate).is_file():
            return candidate
    return ""


def _database_probe(path: Path) -> tuple[bool, str]:
    """Can this database be written, without writing to it?

    A missing file is not opened: sqlite3 would create it, and `check` must not be the thing
    that puts a database somewhere. Its parent being writable is the right answer there.
    """
    if not path.exists():
        parent = path.parent
        if not parent.is_dir():
            return False, f"{path} is missing and {parent} does not exist"
        if not os.access(parent, os.W_OK):
            return False, f"{path} is missing and {parent} is not writable"
        return True, f"{path} (will be created)"
    if not os.access(path, os.W_OK):
        return False, f"{path} is not writable"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
        try:
            conn.execute("BEGIN IMMEDIATE")      # proves the write lock is obtainable
            conn.execute("ROLLBACK")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"{path}: {exc}"
    return True, str(path)


def _stored_root(path: Path) -> str:
    """The media root the database itself holds, or "" — read only, never created."""
    if not path.exists():
        return ""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='root'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return ""
    return row[0] if row else ""


def check() -> int:
    rows: list[tuple[str, str, str]] = []      # (name, state, detail)
    failures = 0

    def add(name: str, ok: bool, detail: str, warn: bool = False):
        nonlocal failures
        state = "ok" if ok else ("warn" if warn else "fail")
        if not ok and not warn:
            failures += 1
        rows.append((name, state, detail))

    # -- configuration ------------------------------------------------------------
    try:
        host, port = config.host(), config.port()
        add("config", True, f"{host}:{port}  data {paths.DATA_DIR}"
            f"{'' if paths.DECLARED else ' (checkout, undeclared)'}")
    except ValueError as exc:
        add("config", False, str(exc))
        return _report(rows, failures)

    # -- application tree ---------------------------------------------------------
    add("app", (APP_DIR / "server.py").is_file(), str(APP_DIR))

    # -- writable data ------------------------------------------------------------
    data_ok = paths.DATA_DIR.is_dir() and os.access(paths.DATA_DIR, os.W_OK)
    if not data_ok and not paths.DATA_DIR.exists():
        parent = paths.DATA_DIR.parent
        data_ok = parent.is_dir() and os.access(parent, os.W_OK)
        add("data dir", data_ok, f"{paths.DATA_DIR} (will be created)")
    else:
        add("data dir", data_ok, str(paths.DATA_DIR))

    db_ok, db_detail = _database_probe(paths.DB_PATH)
    add("database", db_ok, db_detail)

    # -- media --------------------------------------------------------------------
    # The declared variable only seeds a new database, so the database is what decides
    # where the library is once it has one.
    root = _stored_root(paths.DB_PATH) or config.music_dir()
    if not root:
        add("music", False, "no root: set HARDLYLISTENING_MUSIC_DIR or choose one in the UI")
    elif not Path(root).is_dir():
        add("music", False, f"{root} does not exist")
    else:
        add("music", os.access(root, os.W_OK), root + ("" if os.access(root, os.W_OK)
                                                       else " (not writable)"))

    # -- disk ---------------------------------------------------------------------
    for label, target in (("disk data", paths.DATA_DIR), ("disk music", Path(root) if root else None)):
        if not target or not target.is_dir():
            continue
        try:
            free, used = _disk_free_gb(target), _disk_used_pct(target)
        except OSError as exc:
            add(label, False, str(exc), warn=True)
            continue
        add(label, True, f"{free:.1f} GB free, {used}% used  ({target})")

    # -- tools --------------------------------------------------------------------
    for name in ("yt-dlp", "ffmpeg"):
        found = _tool(name)
        add(name, bool(found), found or "not found on PATH")

    # -- listen port --------------------------------------------------------------
    # Not a failure: a running instance holding its own port is the normal case for a
    # manual `check`, and systemd's ExecStartPre runs before anything else has it.
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            add("port", True, f"{port} is free")
        except OSError as exc:
            add("port", False, f"{port} is in use ({exc.strerror})", warn=True)

    return _report(rows, failures)


def _report(rows, failures: int) -> int:
    print(f"hardlylistening {version()}")
    width = max(len(name) for name, _, _ in rows)
    for name, state, detail in rows:
        print(f"  {name.ljust(width)}  {state:<4}  {detail}")
    warnings = sum(1 for _, state, _ in rows if state == "warn")
    if failures:
        print(f"result: {failures} failed" + (f", {warnings} warning(s)" if warnings else ""))
    elif warnings:
        print(f"result: ok, {warnings} warning(s)")
    else:
        print("result: ok")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = "run"
    if argv and not argv[0].startswith("-"):
        command = argv.pop(0)
    if command in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0
    if command == "version":
        print(version())
        return 0
    if command == "check":
        return check()
    if command != "run":
        print(f"hardlylistening-server: unknown command {command!r}\n", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2
    import server
    return server.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())