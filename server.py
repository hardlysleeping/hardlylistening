#!/usr/bin/env python3
"""HardlyListening — HTTP surface.

Thin on purpose: routes translate JSON into Store/Queue calls. No download logic,
no provider knowledge, no SQL in the handlers.

  python3 server.py [--port 8470] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import config
import db
import paths
import security
from engine.queue import Queue
from httpd import constants

APP_DIR = Path(__file__).resolve().parent

#: Published on the root module on purpose. The adapters read these back through
#: `httpd.runtime` at call time, so `patch.object(server, ...)` in the tests — and a switch
#: thrown in `main()` — is seen by every request.
SECURITY = security.SECURITY
PLAYBACK_LOG_MAX = constants.PLAYBACK_LOG_MAX

#: What this process actually serves. Decided once, in `main()`, from `config` and the flags —
#: never inside a request, so a running server cannot end up half-gated. Tests set it directly;
#: nothing else writes it after boot.
FEATURES = {"web_ui": True, "subsonic": True}

STORE: db.Store | None = None
QUEUE: Queue | None = None
SCHEDULER = None
SEEDED = False
LOCK = threading.Lock()


def boot() -> tuple[db.Store, Queue]:
    global STORE, QUEUE, SCHEDULER
    with LOCK:
        if STORE is None:
            paths.ensure()
            STORE = db.Store()
            # `MUSIC_DIR` supplies the first root only: once the settings table has one, the
            # admin's choice wins and the variable is inert (see config.seed_music_root).
            if config.seed_music_root(STORE):
                print(f"library root seeded from HARDLYLISTENING_MUSIC_DIR: {STORE.setting('root')}",
                      flush=True)
            # Choices made by hand before the ranking kept a record still count.
            import ranking
            ranking.seed_from_jobs(STORE)
            QUEUE = Queue(STORE)
            QUEUE.start()
            from engine.scheduler import Scheduler
            SCHEDULER = Scheduler(STORE)
            SCHEDULER.start()
            # Cover art dedupe is a standing job, not a chore: it picks up whatever
            # the download path did not already do, then keeps watching.
            from library import art_store
            art_store.start(STORE)
            # Same standing-job idea for files the queue could not link: a file
            # with no row is invisible, so a retry downloads it again.
            from library import reconcile
            reconcile.start(STORE)
            # The bucket mirror: a persistent queue of finished files, drained while the server
            # runs. Nothing is queued until something downloads, and local mode queues nothing.
            from library import uploads
            uploads.start(STORE)
            # A USB export that was mid-copy when the process stopped is not running now:
            # it is marked interrupted rather than left claiming to be copying (HL-228).
            from library import usb_export
            usb_export.startup(STORE)
            # Previous media kept for a reverse is collected once its window passes; an
            # interrupted swap's scratch folder goes too (HL-229).
            from replacements import start as start_retention
            start_retention(STORE)
            # Core publishes domain events (a member joining or renaming); Social subscribes
            # Activity to them here, so `security.py` never reaches into the Feed. A server
            # running without Social still boots — those events simply go nowhere (ARCH-007).
            try:
                from social import service as social_service
                social_service.subscribe_events()
            except Exception:
                pass
    assert STORE and QUEUE
    return STORE, QUEUE

# The HTTP layer is a package: `httpd/` holds the adapters, `server.py` is the composition root.
# `runtime.bind` is what lets those adapters read `boot`, `FEATURES` and `QUEUE` from *this*
# module — including when this file runs as `__main__` — without importing it twice.
from httpd import Handler
from httpd import runtime as _runtime

_runtime.bind(sys.modules[__name__])

def shutdown(httpd: ThreadingHTTPServer) -> None:
    """Stop in the order the systemd unit and Docker both depend on.

    HTTP first, so no new request can touch a half-closed database; then the scheduler, which
    would otherwise start a follow check mid-shutdown; then the queue worker, which is given a
    short grace period and otherwise hands its job back to the queue; then SQLite. A bare kill
    skips all of this, which is why the handler exists at all.
    """
    httpd.shutdown()
    httpd.server_close()
    if SCHEDULER is not None:
        SCHEDULER.stop()
        SCHEDULER.join(5)
    if QUEUE is not None:
        QUEUE.stop(grace=5.0)
    if STORE is not None:
        STORE.close()

def argument_parser(default_host: str, default_port: int, web_ui: bool = True,
                    subsonic: bool = True) -> argparse.ArgumentParser:
    """The `run` flags, with the environment's answers as their defaults.

    `--no-web-ui` and `--no-subsonic` are spelled as the *off* switch because on is the default
    everywhere: a unit file or a window says nothing and gets today's behaviour. `dest` is the
    positive name, so the parser itself returns what the server should serve — no second
    negation in `main()` to get wrong.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--no-web-ui", dest="web_ui", action="store_false", default=web_ui,
                        help="serve the API, not the pages in web/")
    parser.add_argument("--no-subsonic", dest="subsonic", action="store_false", default=subsonic,
                        help="answer /api, not /rest/*")
    return parser

def main(argv: list[str] | None = None) -> int:
    """Serve until a signal arrives.

    `--host`/`--port`/`--no-web-ui`/`--no-subsonic` beat the environment, which beats today's
    defaults, so a unit file can set all four and a one-off run can still override any of them.
    """
    try:
        default_host, default_port = config.host(), config.port()
        default_web_ui, default_subsonic = config.web_ui(), config.subsonic()
    except ValueError as exc:
        print(f"config: {exc}", flush=True)
        return 2
    args = argument_parser(default_host, default_port, default_web_ui,
                           default_subsonic).parse_args(argv)
    # Decided once, before anything can be served: a running server is never half-gated.
    FEATURES.update(web_ui=args.web_ui, subsonic=args.subsonic)

    boot()      # fail fast if the DB or yt-dlp is broken
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"HardlyListening on http://{args.host}:{args.port}  (db: {db.DB_PATH})",
          flush=True)
    if not (args.web_ui and args.subsonic):
        # Say it once, in the journal: "the page 404s" is a confusing way to learn a switch is on.
        off = [name for name, on in (("web UI", args.web_ui), ("OpenSubsonic", args.subsonic))
               if not on]
        print(f"not served: {', '.join(off)}", flush=True)

    # `serve_forever` blocks, and `shutdown()` must be called from another thread, so the
    # signal handler's only job is to set a flag and the main thread does the stopping.
    stop = threading.Event()

    def received(signum, _frame):
        print(f"\n{signal.Signals(signum).name}: shutting down", flush=True)
        stop.set()

    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), received)

    serving = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    serving.start()
    try:
        while not stop.wait(0.5):
            pass
    finally:
        shutdown(httpd)
        print("bye", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
