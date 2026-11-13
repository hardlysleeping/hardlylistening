# Headless server packaging — canonical runtime, VPS layout, Docker

Status: **partly built — the process, its paths, its launcher and its backup exist; no packaged
install has been started on a host yet.** Owner decisions are recorded in `PLAN.md` § Platform
separation → *Runtime boundary*; this document is the operational detail for the
`SERVER-PKG-###` cards.

| done | card |
| --- | --- |
| ✓ | 002 `paths.py` — runtime writes leave the source tree when `DATA_DIR` is declared |
| ✓ | 004 graceful shutdown on `SIGTERM`/`SIGINT` |
| ✓ | 001 runtime config: `HOST`, `PORT`, `MUSIC_DIR` |
| ✓ | 003 `bin/hardlylistening-server` with `run`, `check`, `version` |
| ✓ | 010 `backup <directory>` — SQLite online-backup API, verified copy |
| ✓ | 011 migration test — a checkout's Repo, Library and accounts survive the move |
| ✓ | 007 Dockerfile — same runtime, non-root 10001, config from the environment |
| ✓ | 008 compose example — `/data` beside the file, media outside the project, loopback port |
| ~ | 005 systemd unit — `deploy/hardlylistening.{service,env.example}` committed, never installed |
| ✓ | 006 `deploy/install.sh` — user, directories, code copy, env file, `check`, unit, enable |
| ✓ | 009 storage half — `IDENTITY_DIR` declared, created `0700`, in the backup boundary; Docker health in the image |
| — | 009 key half — node-id/keypair generation and format are FED-001, not packaging |

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
├── identity/                node-id, private-key, public-key          (0700)
├── cache/                   artwork/, browser-downloads/, artist-art/,
│                            metadata/, federation/
├── podcasts/                one directory per member; episodes they chose to keep
├── tmp/
└── state/
```

Every one of those directories is declared in `paths.py` and created by `paths.ensure()`; the
layout in this document and the layout on disk are the same paths, not a diagram and an
approximation. `podcasts/` is state, not a cache: it is in the backup boundary, and each member's
budget (5 GB by default) is enforced against it. `identity/` is created `0700` because the files that will live in it are private.

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

**Done (SERVER-PKG-010).** `backup <directory>` copies the database with SQLite's online-backup
API, so the server keeps serving and the copy is a consistent snapshot of every committed write.
Copying the file by hand is wrong twice: with WAL on, `hardlylistening.db` alone is not a database,
and a byte copy taken mid-write can be torn.

Three deliberate properties:

* the source is opened `mode=ro`, so a backup can never write to the database it is protecting;
* the copy is written to `<name>.part`, set back to `journal_mode=DELETE`, integrity-checked, and
  only then renamed — a failed or torn copy never replaces the last good one, and `--force` is
  needed to replace an existing file at all;
* the journal mode matters for the artifact, not the process: the page-1 header carries the
  source's mode, so without that step the "backup" is a database in three files that a careless
  rsync can copy inconsistently.

Scope is the database only — the output says so. Media wants rsync or a filesystem snapshot, and
since SERVER-PKG-009 `identity/` is printed too, with its resolved path, precisely so a successful
`backup` is not read as a complete one. `tests/test_backup.py` keeps a
real writer open while the backup runs, then reads the copy back: settings and rows match, the copy
opens standalone, no `-wal` survives, the source file is byte-identical afterwards, a missing
database is a refusal rather than an empty backup, and an existing copy is not replaced without
`--force`.

**Done (SERVER-PKG-006).** `deploy/install.sh` creates the account, the directories, the code copy,
the environment file, probes with `check`, installs the unit and enables it. Two promises it keeps
literally:

* **It never deletes the database or the media.** `uninstall` removes `/opt` and the unit, and then
  prints what it kept. Removing a library is an operator's `rm`, never a side effect of uninstalling
  a service.
* **`--dry-run` prints every command and changes nothing**, because the honest way to review an
  installer is to read what it would do. The copy step is the same tar command either way.

The copy is where this can go quietly wrong, so `tests/test_install.py` reproduces it with the same
exclusions and asserts both directions: `web/`, `bin/`, `cli.py`, `VERSION` are present, while
`hardlylistening.db`, its `-wal`/`-shm`, `.cache`, `artist-art`, `backups`, `tests` and `docs` are
not — an exclusion one level too wide ships a live database into `/opt`, where the next upgrade
overwrites it. The copied tree then runs its own `check` and has to pass, and creates no database
beside itself.

That test also found a real inconsistency in `check`: with a data directory that does not exist yet
— the normal state before the first start — the database row said `fail` while the row above said
"will be created". Both now judge by the nearest existing ancestor (`_creatable`), which is the only
honest answer to "could this be made" without making it.

**Done (SERVER-PKG-009, storage half).** `paths.py` declares `IDENTITY_DIR`, `STATE_DIR`,
`TMP_DIR`, `METADATA_CACHE_DIR` and `FEDERATION_CACHE_DIR`, and `ensure()` creates all of them
under `DATA_DIR` — `identity/` with mode `0700`, because the day key material appears it must not
be group- or world-listable. Nothing writes to `state/`, `tmp/`, `cache/metadata/` or
`cache/federation/` yet; they are created so the documented layout is real rather than promised,
and so a volume that is missing one of them fails here rather than in a feature six months later.

Key generation is deliberately **not** part of this: node-id, keypair format and rotation belong
to FED-001, which is the code that would read them. Storage without a consumer is honest; invented
crypto is not.

`check` reports the identity directory only for a declared install (a checkout has none, and
`ensure()` creates nothing there), warns if it is group- or world-listable, and — like every other
row — creates nothing. `backup` now prints the resolved identity path in its "not copied" block,
so a database backup cannot be mistaken for a complete one.

**Done (SERVER-PKG-011).** The move is small enough to look like nothing — copy the database and
the caches, set `HARDLYLISTENING_DATA_DIR` — and that is the risk: `paths.py` resolves four paths
from the environment at import time, so one misspelled variable or a half-moved cache tree reads as
an empty library rather than as an error. `tests/test_migration.py` builds a checkout-shaped
instance with a Repo (artist, release, two tracks), a Library (playlist and its one membership), an
account with a credential, a settings root, an art row and a log row; moves it the way §11 says
to; and then compares the store opened through the packaged path against the store opened before
the move — row by row, not "the file exists". It also asserts the cache directories resolve under
the data directory afterwards, that a second start changes not one byte (idempotent migrations are
what make an update a file replacement), and that `HARDLYLISTENING_DB` still beats `DATA_DIR`.

Still required before the system service ships:

* **`migrate`** (see §5), and the migrations stay automatic and idempotent at startup as they are
  now.

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
hardlylistening-server backup <dir> [--force]  # online-backup API, verified, single file       ✓ built
```

`backup` takes a directory, not a file: it writes `<dir>/hardlylistening.db`, creates the directory
if it is missing, refuses to replace an existing copy without `--force`, and prints what it did not
copy.

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

Committed as `deploy/hardlylistening.service` and `deploy/hardlylistening.env.example` — read those,
not a copy in this document. **Not yet installed on a host** (SERVER-PKG-005 stays open until it
has been started somewhere and stopped cleanly, per the board's own rule).

```
deploy/hardlylistening.service        → /etc/systemd/system/hardlylistening.service
deploy/hardlylistening.env.example    → /etc/hardlylistening/hardlylistening.env   (root:hardlylistening 640)
```

Two choices in the unit are worth stating, because a "hardened" unit is where an install quietly
stops being able to download:

* `ExecStartPre=… check` — safe only because `check` creates, migrates and repairs nothing. A
  probe with ambition turns a bad config into a half-broken install.
* `PrivateDevices=false` on purpose. FFmpeg and the media tooling want normal device access, and
  sandboxing that before it has been tested is how a working install becomes a puzzle. The
  protection that matters is `ProtectSystem=strict` plus exactly two writable paths —
  `/var/lib/hardlylistening` and the library root.

```
daemon-reload → enable --now → status → journalctl -u hardlylistening -f
```

Logs go to journald. No second application log file by default.

The existing per-user unit (`install-service.sh`, `~/.config/systemd/user/hardlylistening.service`)
is untouched by any of this: it is how the development checkout runs, and the system unit is a
second, independent way to run the same code.

---

## 7. Reverse proxy

Bind `127.0.0.1:8470`, put Caddy or nginx in front, set
`HARDLYLISTENING_PUBLIC_ORIGIN=https://music.example.com`. Do not expose 8470 publicly when a proxy
is used.

Tailnet/LAN-only installs instead bind the private interface (or `0.0.0.0` with a firewall) — no
architectural difference.

---

## 8. Docker

Same runtime, no special build: the same `server.py`, the same `web/`, the same SQLite file. What
changes is where the files live and who runs the process. Committed as `Dockerfile`,
`.dockerignore` and `deploy/docker-compose.yml`.

```
/data     database, identity, cache, state, tmp        (volume)
/music    Repository media                             (volume)
/app      the application, read-only at runtime
```

Four choices in the image are load-bearing:

* **`yt-dlp` comes from pip, not Debian.** The archive's copy is a year behind the sites it has to
  parse, and a downloader that cannot read SoundCloud is the whole app.
* **`HOME=/data`.** yt-dlp and any provider that caches cookies writes under `$HOME`; left at the
  default, that cache lands on the container filesystem and vanishes with the container.
* **`.dockerignore` excludes `hardlylistening.db`, its `-wal`/`-shm` siblings, `.cache`,
  `artist-art` and `backups`.** A checkout holds a live database, and `COPY . /app` would bake
  somebody's library metadata into an image that is meant to be reproducible.
* **`stop_grace_period: 30s`, and `exec` in the wrapper.** SIGTERM has to reach the Python process,
  or the documented shutdown order never runs and every restart is a timeout kill.

The health check lives in the image rather than in Compose, so native and container agree on what
"healthy" means. **Health means the process answered**, never "the provider is reachable" and never
"peers respond". `check` is deliberately not the health probe: it wants a writable music mount and
reports tool state, which is an installer's question.

Keep media outside the Compose project directory so replacing the deployment folder cannot delete
the library. `chown 10001:10001` the data and music trees; `PUID`/`PGID` only if a NAS forces it.

Persistent: `/data/hardlylistening.db`, `/data/identity`, `/music`. Recreatable: `/data/cache`,
`/data/tmp`. The container filesystem itself holds no unique state — destroying and recreating the
container must not lose the server.

```
docker compose -f /srv/hardlylistening/docker-compose.yml up -d
docker compose -f /srv/hardlylistening/docker-compose.yml exec hardlylistening \
    /app/bin/hardlylistening-server check
```

---

## 9. Identity and federation

Node identity lives in `IDENTITY_DIR` (`/var/lib/hardlylistening/identity` or `/data/identity`) and
is never baked into the image. A rebuilt container that keeps `/data` remains the same federated
node. This is why identity is in the backup boundary even though federation is not implemented.

**Built (SERVER-PKG-009, storage half):** `IDENTITY_DIR = DATA_DIR/identity`, created by
`ensure()` with mode `0700`, reported by `check`, named by `backup`, and carried across a move by
`tests/test_migration.py` — which seeds a `node-id` file, migrates the instance the way §11 says
to, and asserts the file arrived under the data directory unchanged. **Not built:** the key
material itself. `node-id`, `private-key` and `public-key` in the diagram above are the intended
contents, not files that exist.

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
3. Point the Repository root at the new music path (the `root` setting, once — not every boot), or
   let `HARDLYLISTENING_MUSIC_DIR` seed it on a database that never had a chosen root.
4. Start under systemd or Docker, verify `/api/health`, the web UI, `/rest/ping.view`.
5. Verify a download, a playback with Range, and one Subsonic client.

The database schema is unchanged by any of this: packaging moves files, it does not reshape data.
`tests/test_migration.py` is that sentence as a test — Repo, Library and accounts compared before
and after the move.

---

## 12. Release gate

Not "VPS packaging complete" until all hold:

```
✓ runs with no graphical environment        (asserted: tests/test_headless.py boots the real
                                             entry point with no DISPLAY, and parses every core
                                             module for a GUI toolkit import)
✓ no Tauri/Electron/Node dependency required (asserted in the same file: no src-tauri,
                                             package.json or web/dist in the core, and the
                                             launcher is the plain interpreter)
✓ /opt can be read-only
✓ database lives outside the source tree
✓ music lives outside the source tree
✓ identity survives application updates   (IDENTITY_DIR under DATA_DIR, never /opt; 0700; carried by the migration test)
✓ SIGTERM exits cleanly
✓ systemd restart preserves queue and database state
✓ Docker restart preserves queue and database state
✓ container recreation preserves all persistent state
✓ full web UI, Repo/downloads, Library, Social and OpenSubsonic all work
✓ node identity has persistent storage for future federation
✓ native and Docker use the same server code
✓ the current lightweight foreground mode still works
```