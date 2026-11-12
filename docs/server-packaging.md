# Headless server packaging — canonical runtime, VPS layout, Docker

Status: **partly built — the process, the paths and the launcher exist; the service, image and
backup do not.** Owner decisions are recorded in `PLAN.md` § Platform separation → *Runtime
boundary*; this document is the operational detail for the `SERVER-PKG-###` cards.

| done | card |
| --- | --- |
| ✓ | 002 `paths.py` — runtime writes leave the source tree when `DATA_DIR` is declared |
| ✓ | 004 graceful shutdown on `SIGTERM`/`SIGINT` |
| ✓ | 001 runtime config: `HOST`, `PORT`, `MUSIC_DIR` |
| ✓ | 003 `bin/hardlylistening-server` with `run`, `check`, `version` |
| — | 005 systemd unit, 006 image, 007/008 restart state, 009 identity, 010 `backup`, 011 migration test |

The rule everything else follows from:

> `hardlylistening-server` is the canonical runtime. systemd, Docker and the Desktop GUI all
> launch that same backend. **No GUI dependency may exist inside the server package.**

```
                    hardlylistening-server
                             │
             ┌───────────────┼───────────────┐
             │               │               │
          systemd          Docker        Desktop GUI
            VPS             VPS           launches it
```

Zero functional difference between native, Docker and Desktop-hosted instances. Only process
management and filesystem paths differ.

---

## 1. What the runtime already is, and stays

One OS process, Python standard library HTTP server, SQLite, a queue worker thread, a scheduler
thread, `yt-dlp`, `ffmpeg`, the `web/` frontend and the OpenSubsonic surface. It already accepts
`--host` and `--port` (defaults `127.0.0.1:8470`) and already reads `HARDLYLISTENING_DB`,
`HARDLYLISTENING_AUTH_REQUIRED`, `HARDLYLISTENING_PUBLIC_ORIGIN` and `HARDLYLISTENING_AI_API_KEY`.

Measured 2026-11-11: that is what the live instance runs. Packaging **wraps** it.

Do not add Electron, Tauri, a Node runtime, Redis, Postgres, Celery or a separate worker daemon to
the headless server. Do not split workers into separate services before there is a measured reason.

```
hardlylistening-server
├── HTTP server threads
├── download Queue worker
├── Scheduler
├── Repo services
├── Library / Playback
├── Social
├── OpenSubsonic
└── Federation worker (only when enabled)
```

---

## 2. Filesystem boundaries

### Application (read-only at runtime)

```
/opt/hardlylistening/
├── server.py  db.py  security.py  subsonic.py  listening.py  …
├── core/  repo/  library/  social/  federation/  engine/  providers/
├── web/                     index.html, js/, styles/, …
├── bin/hardlylistening-server
└── VERSION
```

Application code is treated as read-only. **Never store user data inside it.**

### Mutable state

```
/var/lib/hardlylistening/
├── hardlylistening.db
├── identity/                node-id, private-key, public-key
├── cache/                   artwork/, metadata/, federation/
├── tmp/
└── state/
```

Owner `hardlylistening:hardlylistening`. Directories `750`, secrets and private keys `600`,
database `640` where appropriate.

### Media (never inside either of the above)

```
/srv/hardlylistening/music/
├── Artist A/Album/01 - Song.opus
└── Artist B/…
```

Separating it keeps the FHS meaning intact (`/opt` application, `/etc` configuration, `/var/lib`
state, `/srv` served media), and makes both Docker volumes and VPS migration honest.

### Configuration

```
/etc/hardlylistening/hardlylistening.env      root:hardlylistening 640
```

```bash
HARDLYLISTENING_HOST=127.0.0.1
HARDLYLISTENING_PORT=8470

HARDLYLISTENING_DATA_DIR=/var/lib/hardlylistening
HARDLYLISTENING_DB=/var/lib/hardlylistening/hardlylistening.db
HARDLYLISTENING_MUSIC_DIR=/srv/hardlylistening/music

HARDLYLISTENING_AUTH_REQUIRED=1
HARDLYLISTENING_PUBLIC_ORIGIN=https://music.example.com

HARDLYLISTENING_FEDERATION_ENABLED=0
# HARDLYLISTENING_AI_API_KEY=…
```

New variables: `HARDLYLISTENING_DATA_DIR`, `HARDLYLISTENING_MUSIC_DIR`, `HARDLYLISTENING_HOST`,
`HARDLYLISTENING_PORT`. All four are read by `paths.py` and `config.py`; nothing else in the process
looks at the environment for them.

* `DATA_DIR` — default keeps today's behaviour for existing installs; the packaged default is
  `/var/lib/hardlylistening`.
* `MUSIC_DIR` — the initial `root` setting. "Initial" is narrower than it sounds: `db.connect()`
  inserts every default on creation, so a fresh database already holds
  `~/Music/HardlyListening`. The variable applies only while the stored root is still that
  default (or absent) and stops applying the moment an admin chooses a location in the UI. It is a
  seed, never an override — anything else would re-point the library on every start.
* `HOST` / `PORT` — defaults for the listen socket. An explicit `--host`/`--port` still wins, so a
  unit file can set both and a one-off run can override either. A malformed `PORT` refuses to start
  (exit 2) instead of falling back to 8470: starting somewhere the operator did not ask for is
  worse than not starting.

---

## 3. What must change in the code (measured, not guessed)

**Done (SERVER-PKG-002).** `paths.py` is now the single answer to "where may this process write",
and the four sites that used to sit in the source tree resolve through it:

| write site | before | declared `DATA_DIR` | undeclared |
| --- | --- | --- | --- |
| database | `<app>/hardlylistening.db` | `DATA_DIR/hardlylistening.db` | unchanged |
| artwork cache | `<app>/.cache/artwork` | `DATA_DIR/cache/artwork` | unchanged |
| browser-download cache | `<app>/.cache/browser-downloads` | `DATA_DIR/cache/browser-downloads` | unchanged |
| artist pictures | `<app>/artist-art/` | `DATA_DIR/cache/artist-art` | unchanged |

`HARDLYLISTENING_DB` still wins over all of it. With `DATA_DIR` unset every path is byte-identical
to what it was, which is why the live install needed no move and no migration: the two layouts are
the same code, one environment variable apart. `paths.ensure()` creates the declared directories and
deliberately does nothing when they were not declared — creating them "just in case" would be the
write to the source tree this module exists to prevent.

Acceptance is literal: **`/opt/hardlylistening` can be mounted read-only and the server still
runs.** `tests/test_paths.py` proves the mechanism (resolution both ways, plus a run that opens the
store, writes artwork, artist art and a browser archive, and leaves the app tree untouched); the
mount itself is an operator check.

**Done (SERVER-PKG-004).** `SIGTERM` and `SIGINT` stop the process in the documented order. The
handler only sets a flag; the main thread does the stopping, because `shutdown()` may not be called
from the thread running `serve_forever`.

```
HTTP: stop accepting, close the socket        httpd.shutdown(), server_close()
Scheduler: stop and join                      SCHEDULER.stop(); join(5)
Queue: stop, grace 5 s, else kill the child   QUEUE.stop(grace=5.0)
SQLite: commit and close                      STORE.close()
```

A download interrupted at shutdown is **not** marked cancelled: the child is killed, its partial
file removed, and the row goes back to `queued` so the next start resumes it. That is the same rule
the constructor already applies to rows left behind by a hard kill. `tests/test_shutdown.py` runs
the real entry point in a subprocess, signals it, and asserts exit 0, the log line, a released port
and a checkpointed WAL.

**Done (SERVER-PKG-001).** `config.py` owns the three settings that are not filesystem paths —
`host()`, `port()`, `music_dir()` — plus `seed_music_root(store)`, and `server.main(argv)` takes the
environment as its argument defaults. With nothing declared the server binds `127.0.0.1:8470` and
seeds no root, byte-identical to before. `tests/test_config.py` runs the module and the launcher in
subprocesses against a temporary data directory: nothing declared is today's behaviour, the
environment supplies all three, a bad port refuses, a new database takes the declared root, and a
chosen root survives the variable.

**Done (SERVER-PKG-003).** `bin/hardlylistening-server` is a `sh` wrapper that resolves its own
directory and `exec`s `cli.py`; `cli.py` owns the command dispatch and `check`. `check` reports and
repairs nothing — a missing music directory is a `fail` and is still missing afterwards, a missing
database is reported as "will be created" and is not created, and the database itself is opened
`mode=rw` inside `BEGIN IMMEDIATE` … `ROLLBACK` to prove the write lock without writing. A busy
port is a warning, because a manual `check` against a running instance is normal.

Still required before the system service ships:

* **`backup` and `migrate` subcommands** (see §5), and the migrations stay automatic and idempotent
  at startup as they are now.

---

## 4. Launcher, process model, users

`/opt/hardlylistening/bin/hardlylistening-server` does almost nothing: resolve its own directory,
then `exec` the Python entry point and let that read the environment. No daemonisation, no PID
files — one external supervisor owns the process.

```sh
#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "${HARDLYLISTENING_PYTHON:-python3}" "$APP_DIR/cli.py" "$@"
```

That is the whole script. `HARDLYLISTENING_PYTHON` exists for installs whose interpreter is not on
`PATH` (a venv under `/opt`); everything else is `cli.py`'s job, where it can be tested without
spawning a shell.

Dedicated account `hardlylistening:hardlylistening`, no interactive login, home
`/var/lib/hardlylistening`. It owns the data and media trees, **not** `/opt` or `/etc`.

Supervision is never nested: `systemd → hardlylistening-server`, or `Docker → hardlylistening-server`,
or `Desktop → hardlylistening-server`. Never systemd → Docker → another supervisor → server.

---

## 5. CLI contract

```
hardlylistening-server run                 # default; --host/--port override the environment   ✓ built
hardlylistening-server check               # config, app, data, DB read/write, music, disk, yt-dlp, ffmpeg, port   ✓ built
hardlylistening-server version             # prints VERSION                                    ✓ built
hardlylistening-server migrate             # explicit upgrade path                             — not built
hardlylistening-server backup <path>       # SQLite online-backup API, consistent snapshot      — not built
```

Exit codes: `0` success (warnings included), `1` a failed check, `2` a usage error — an unknown
command, or a malformed `HARDLYLISTENING_PORT`. No command means `run`, so the unit file needs no
argument.

`check` is used by the installer and by `ExecStartPre`. Never put destructive work in `ExecStartPre`,
which is why `check` creates nothing, migrates nothing and opens the database read-only or inside a
rolled-back transaction.

**Backup boundary.** Required: the database, `/srv/hardlylistening/music/`, and `identity/`.
Optional and rebuildable: `cache/`. Do not copy a live SQLite file by hand — `backup` uses the
backup API. Media is rsynced or snapshotted separately.

**Update boundary.** Replacing `/opt/hardlylistening` must never touch `/etc`, `/var/lib` or `/srv`;
then migrations run and the service restarts.

---

## 6. systemd (native VPS)

```ini
[Unit]
Description=HardlyListening Server
Documentation=https://github.com/hardlysleeping/hardlylistening
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=hardlylistening
Group=hardlylistening
WorkingDirectory=/opt/hardlylistening
EnvironmentFile=/etc/hardlylistening/hardlylistening.env
Environment=PYTHONUNBUFFERED=1
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStartPre=/opt/hardlylistening/bin/hardlylistening-server check
ExecStart=/opt/hardlylistening/bin/hardlylistening-server
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGTERM
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/hardlylistening
ReadWritePaths=/srv/hardlylistening/music
PrivateDevices=false

[Install]
WantedBy=multi-user.target
```

`PrivateDevices=false` on purpose: FFmpeg and media tooling may need normal device access, and
sandboxing that before it is tested is how a working install becomes a puzzle. The protection that
matters is `ProtectSystem=strict` plus exactly two writable paths.

```
daemon-reload → enable --now → status → journalctl -u hardlylistening -f
```

Logs go to journald. No second application log file by default.

---

## 7. Reverse proxy

Bind `127.0.0.1:8470`, put Caddy or nginx in front, set
`HARDLYLISTENING_PUBLIC_ORIGIN=https://music.example.com`. Do not expose 8470 publicly when a proxy
is used.

Tailnet/LAN-only installs instead bind the private interface (or `0.0.0.0` with a firewall) — no
architectural difference.

---

## 8. Docker

Same runtime, no special build. Non-root (`uid 10001`), small Python base plus `ffmpeg`/`yt-dlp`.

```
/config   optional config/secrets
/data     database, identity, cache, state, tmp
/music    Repository media
```

```dockerfile
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg yt-dlp \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN useradd --system --uid 10001 --home /data hardlylistening
USER 10001
ENV HARDLYLISTENING_DATA_DIR=/data \
    HARDLYLISTENING_DB=/data/hardlylistening.db \
    HARDLYLISTENING_MUSIC_DIR=/music \
    HARDLYLISTENING_HOST=0.0.0.0 \
    HARDLYLISTENING_PORT=8470 \
    PYTHONUNBUFFERED=1
EXPOSE 8470
ENTRYPOINT ["/app/bin/hardlylistening-server"]
```

```yaml
services:
  hardlylistening:
    image: ghcr.io/hardlysleeping/hardlylistening:latest
    container_name: hardlylistening
    restart: unless-stopped
    environment:
      HARDLYLISTENING_HOST: 0.0.0.0
      HARDLYLISTENING_PORT: 8470
      HARDLYLISTENING_AUTH_REQUIRED: 1
      HARDLYLISTENING_PUBLIC_ORIGIN: https://music.example.com
    volumes:
      - ./data:/data
      - /srv/hardlylistening/music:/music
    ports:
      - "127.0.0.1:8470:8470"
    healthcheck:
      test: ["CMD", "python3", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8470/api/health', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
```

Keep media outside the Compose project directory so replacing the deployment folder cannot delete
the library. `chown 10001:10001` the data and music trees; `PUID`/`PGID` only if a NAS forces it.

Persistent: `/data/hardlylistening.db`, `/data/identity`, `/music`. Recreatable: `/data/cache`,
`/data/tmp`. The container filesystem itself holds no unique state — destroying and recreating the
container must not lose the server.

**Health means the process is alive**, never "the provider is reachable" and never "peers respond".

---

## 9. Identity and federation

Node identity lives in `IDENTITY_DIR` (`/var/lib/hardlylistening/identity` or `/data/identity`) and
is never baked into the image. A rebuilt container that keeps `/data` remains the same federated
node. This is why identity is in the backup boundary even though federation is not implemented.

---

## 10. Desktop compatibility

The Desktop shell launches the same executable with its own environment (data folder, music folder,
port, bind address). An instance created by Desktop can later move to a VPS by copying the database,
the music tree and `identity/` — no architecture conversion.

---

## 11. Grading a move to a VPS

Generic shape of a migration from an existing install:

1. `check` on the old host, then `backup <path>`.
2. Copy the database to `DATA_DIR/`, the media to `/srv/hardlylistening/music`, `identity/` if present.
3. Point the Repository root at the new music path (the `root` setting, once — not every boot).
4. Start under systemd or Docker, verify `/api/health`, the web UI, `/rest/ping.view`.
5. Verify a download, a playback with Range, and one Subsonic client.

The database schema is unchanged by any of this: packaging moves files, it does not reshape data.

---

## 12. Release gate

Not "VPS packaging complete" until all hold:

```
✓ runs with no graphical environment
✓ no Tauri/Electron/Node dependency required
✓ /opt can be read-only
✓ database lives outside the source tree
✓ music lives outside the source tree
✓ identity survives application updates
✓ SIGTERM exits cleanly
✓ systemd restart preserves queue and database state
✓ Docker restart preserves queue and database state
✓ container recreation preserves all persistent state
✓ full web UI, Repo/downloads, Library, Social and OpenSubsonic all work
✓ node identity has persistent storage for future federation
✓ native and Docker use the same server code
✓ the current lightweight foreground mode still works
```