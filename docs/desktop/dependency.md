# Desktop depends on Server — the dependency direction, and the build assertion

Card: **HOST-007**. Authority for the rule: [`docs/server-domains.md`](../server-domains.md) §11.

> **Desktop depends on Server; Server never depends on Desktop.** No import, no shared module, no
> build step in either direction that makes the core need a GUI to start.

This document says what that rule means at the level of files, what enforces it, and what would
break it. `tests/test_host_dependency.py` is the enforcement; nothing here asks you to trust a
diagram.

---

## 1. The rule, split into four claims

A direction is not one fact. Four separate claims make it up, and each one has its own way of being
quietly violated:

| # | claim | how it fails silently |
| --- | --- | --- |
| 1 | Nothing in the server's runtime path names the shell or its toolchain | a comment, a docstring, a string in `web/`, or an `import` inside a function that never runs |
| 2 | The server boots and answers with `desktop/` absent | it only *seems* to: the file was found on the developer's machine |
| 3 | The only edge is the launcher the shell bundles, byte for byte | the sidecar becomes a fork, or the shell writes generated files into the core |
| 4 | The packaged installs (Docker, systemd) carry no shell | the image or the unit grows a build step nobody reads |

**Runtime path** here means: every module the entry point can import (computed from
`bin/hardlylistening-server` → `cli.py` → …, including `from package import submodule` and
literal `__import__`/`importlib.import_module` names), plus `web/`, `bin/`, `deploy/`, `Dockerfile`,
`.dockerignore`, `install-service.sh` and `start.sh`. That is the surface a packaged install ships
and runs. `docs/` and `tests/` are not in it — they are not loaded by the process.

The forbidden names are: a `desktop/` path, `src-tauri`, `tauri`, `cargo`, `npm`, `pnpm`, `npx`,
`yarn`, `package.json`, `node_modules`, and `node` in the shapes that mean the toolchain (a
`node script.js` invocation, a node shebang). A bare `\bnode\b` is deliberately **not** scanned:
this server has federation *nodes* and XML *nodes*, and `web/js/library.js` iterates DOM `node`s —
a test that failed on those would be switched off rather than fixed.

Two files name `node_modules` and are allowed to: `.dockerignore` and `deploy/install.sh`'s
`--exclude=` list. Both are *keeping it out*, which is the opposite of depending on it. The test
asserts that every occurrence is an exclusion, so if one ever becomes a requirement, it fails there.

---

## 2. How it is enforced

`tests/test_host_dependency.py`, four classes, each matching a claim above.

### 2.1 `NoHostInTheRuntimePath` — the scan

The import closure is *walked*, not assumed: `cli` is parsed, every import that resolves inside the
tree is parsed in turn, and the walk repeats until nothing new appears (73 modules on
2026-11-12). A module three imports away is scanned; a nested import is scanned; the entry point is
the file the launcher actually execs. `web/`, `bin/`, `deploy/` and the start scripts are read as
text, because a string in `web/` is shipped and a shell command in `bin/` is executed.

The closure test also asserts the closure is not empty and contains known modules (`server`,
`security`, `subsonic`, `listening`, `paths`). Without that, a walker broken by a refactor would
pass every other test in the class by scanning nothing.

### 2.2 `BootsWithTheShellAbsent` — the simulation

The repository is copied to a temporary directory with **exactly one directory removed** — `desktop/`
— and the copy's own `bin/hardlylistening-server` is started there against a temporary data
directory. Then: `/api/health` answers `200 {"ok": true}`, `/` serves the web UI, the database
appears in the temporary data directory (so the process is the one the test started), the process
is still alive, and the original `desktop/` is byte-identical afterwards.

Why this is a faithful simulation and not a weaker one:

* **Not** `sys.modules.pop('desktop')` or a trimmed `sys.path`. That proves something about the
  import system and nothing about the two ways a server really reaches a shell — a
  `Path.exists()`/`open()` probe, and a `subprocess` exec, which never touches the import system.
  In the copy a probe is answered exactly as a desktop-free install answers it: `False`.
* **Not** a guard over a tree where `desktop/` is still present. That tests a hypothetical and
  cannot see code that changes behaviour depending on whether the directory is there.
* The copy is the real tree, and the process is the real launcher — the same file systemd's
  `ExecStart` and the Dockerfile's `ENTRYPOINT` run. `web/`, `tests/`, `docs/`, `player/` and every
  core package are present; `desktop/` is the only absence.
* An audit hook, installed through a `sitecustomize` on `PYTHONPATH`, fails on any `open`,
  directory listing or creation under either `desktop/` directory — the checkout's and the copy's.
  That covers what absence cannot: a hard-coded absolute path back into the machine that ran the
  test. `os.stat` is not an audited event in CPython, so a bare `exists()` is invisible to the hook;
  absence answers it instead. `assert_tripwire_is_armed` opens `desktop/README.md` in the same
  environment and requires the hook to fire, so the test cannot pass because the guard failed to
  load.

### 2.3 `TheOnlyEdgeIsTheLauncher` — the one direction that exists

`desktop/tools/stage_sidecar.py` stages a **copy** of `bin/hardlylistening-server` under Tauri's
target-triple name. The test runs it into a temporary directory and compares SHA-256 with the
launcher: a bundle that ships a different runtime under the same name would fail.

Then a hash manifest of the whole core (everything except `desktop/` and `.git`) is taken before and
after staging. No file added, removed or rewritten. That is the empirical half of "nothing in
`desktop/` writes into the core's files".

The static half covers what cannot be run on a machine without `webkit2gtk-4.1`. It is a **write
target** scan, not a ban on write calls: HOST-003 writes the handoff record `lifecycle.json` into the
config directory, HOST-005 writes `host.json` and the autostart entry, and HOST-002 writes the
wizard's `setup.json` beside them — all of those are the shell's own state. The earlier blanket rule
— no `fs::write` anywhere in `desktop/` — was narrowed for exactly that reason, after HOST-003
landed, and the scan now makes two separate assertions over every write target in every `.py` and
`.rs` file under `desktop/`:

* **Nothing it targets may be reachable from the server's tree.** The target expression is expanded
  into a *cone*: the expression itself, every local it is built from, and — for a parameter — every
  argument it is handed at its call sites in the same file, to a depth of three frames. A cone
  containing `app_dir`, `APP_DIR`, `parents[...]`, `resource_dir`, `current_exe`, `launcher`,
  `LAUNCHER_NAME`, the launcher's `bin/` path, `server.py`, `cli.py`, `web/`, `index.html`,
  `HARDLYLISTENING_DB`/`_DATA_DIR`/`_MUSIC_DIR`/`_SERVER`, `music_dir` or `.db` fails. This half was
  not widened for any of the writes below: a validator that passes a server path through still fails
  it, and a control proves that.
* **Everything it targets must be a location the shell owns, or one the person chose.** The shell
  owns `config_path()`, `state_path()`, `setup_path()`, `autostart_path()`, `HANDOFF_FILE` and
  `desktop/`'s own build directory. The person owns two: `stage_sidecar.py --out`, and the wizard's
  music/data answers (`validate_data_dir(`, `data_dir_of(`, the `data_dir` key), which the wizard
  creates on first run. A write naming neither fails even when it would be harmless — that is what
  keeps the exception reviewable.

Two writes are pinned by name rather than by the generic rule. HOST-003's: in both `lifecycle.rs` and
`host/lifecycle.py` every write target resolves to `state_path(config)`, every `let config = …` in the
crate is `host::config_path()`, and that resolver reads `XDG_STATE_HOME` or
`HARDLYLISTENING_DESKTOP_CONFIG` and never `app_dir`, `resource_dir`, `current_exe` or `launcher`.
HOST-002's: the wizard writes `setup.json` beside `host.json`, and since HOST-008 that is not a
second resolver — `setup_path()` is `resolve.state_dir()` plus its own file name, and `mode_path()`
is the same call plus `mode.json`. `host.json`, `setup.json` and `mode.json` therefore cannot land
in different directories, and the override that moves one moves all three. The test asserts the
stronger thing now: the expressions that decode `XDG_STATE_HOME` and `XDG_DATA_HOME` exist in
exactly one module, `resolve.py`, and the install defaults are aliases (`MUSIC_SUFFIX =
resolve.MUSIC_SUFFIX`) rather than copies. `settings.py` keeps `XDG_CONFIG_HOME`, which is the
autostart entry's own location — a different variable, not an exception.

The Rust cannot import Python, so `wizard.rs` still decodes `XDG_STATE_HOME` itself; that copy is
pinned to the Python constants by `tests/test_host_wizard.py` and to the override names here, which
is the honest limit of a mirror. `setup.py`'s `write_state` — a library function whose caller is the
Rust command, not another Python module — is exempted only because the test also proves it has no
caller inside `desktop/` and that the Rust twin binds `let path = state_path()` before writing. Add
a Python caller and the exemption fails first.

A negative control feeds synthetic writes into the app root, into `bin/…/server.py`, into
`web/index.html` and into the database path, and requires each to trip the scan; another requires a
write to `/var/tmp/scratch.json` to fail the ownership rule; a third launders a server path through
`validate_data_dir(` and still requires it to trip. If a later edit loosens the classifier, those
controls stop failing.

The honest limit: this is text, not a compiler. Python is parsed with `ast`; Rust is scanned, because
the standard library has no Rust parser. Locals are followed through single-name assignments and
parameters through same-file call sites only, so a path aliased three functions away in another file
is not traced. The two person-chosen locations are exactly that — a person may point `--out` at the
core or answer the data step with the application root, and the scan cannot bound either. Staging into
the core is caught, if at all, by the before/after manifest above; a data folder inside the core is a
configuration, not a dependency.

### 2.4 `ThePackagedInstallsCarryNoShell` — the artefacts

The Dockerfile is parsed into instructions (comments and continuations joined), and every
instruction is scanned. `ENTRYPOINT ["/app/bin/hardlylistening-server"]` and `CMD ["run"]` are
asserted literally, and the entrypoint path is resolved back into the tree — the image cannot name a
runtime that is not there. The single `COPY` is `COPY . /app`, and no `RUN` fetches a toolchain.
`.dockerignore` is parsed too, with comments stripped: it must list `desktop`, because that one line
is the only thing keeping the shell out of an image built from `COPY . /app`.

`deploy/hardlylistening.service` is parsed into `Key=Value` settings: `ExecStart` is
`/opt/hardlylistening/bin/hardlylistening-server run`, and every path it runs or enters
(`ExecStart`, `ExecStartPre`, `WorkingDirectory`) is mapped back onto this tree and must exist. Its
`ReadWritePaths` are state and media only, never the application tree — where a shell would have to
live to be written into.

`install-service.sh` writes its unit in a heredoc, so the heredoc body is extracted and checked the
same way: `ExecStart` runs `server.py` from the checkout, `WorkingDirectory=$HERE`.

---

## 3. What would break it

Concrete, in the order they are likely to happen:

1. **An import in the core.** `import desktop.host.resolve` — for a constant, an address rule, a port
   — from `config.py`, `server.py`, `cli.py` or anything in the closure. This is the actual failure
   mode; the closure scan catches it at any depth, and the boot test catches it too because the copy
   has no `desktop/` to import.
2. **A shared module.** Moving a rule "so both can use it" into a third place the core imports. If
   the shell needs a value, repeat it (as `desktop/host/resolve.py` repeats `DEFAULT_HOST`/
   `DEFAULT_PORT`) and let a test compare the two — `tests/test_desktop_host.py` does exactly that.
3. **A build step.** A `package.json`, a `web/dist/`, a bundler, a `beforeBuildCommand` in the
   core's path, or a `RUN npm install`/`cargo build` in the Dockerfile.
4. **A sidecar that is not a copy.** Editing the staged file, adding wrapper logic to the sidecar
   path, or staging anything but `bin/hardlylistening-server`. The byte comparison and the
   refusal in `stage_sidecar.py` (`refusing to stage …`) are the two guards.
5. **The shell writing into the core.** A staging script that regenerates `VERSION`, rewrites `bin/`,
   or drops a cache next to `server.py`. The manifest comparison fails on added, removed and
   rewritten files alike.
6. **A path in `web/` or `bin/`.** The web UI is served as written; a string pointing at
   `desktop/` in it is a server-side dependency in everything but name.
7. **A unit or image that starts the shell.** `ExecStart=… desktop/src-tauri/target/…` or a
   `HEALTHCHECK` that needs a window. Both are parsed, and both would fail on the token scan and on
   the path resolution.

---

## 4. What this does not prove

Stated plainly, because a green suite that overstates itself is worse than a red one:

* **Nothing is compiled.** The machine this was written on has no `webkit2gtk-4.1`, `gtk+-3.0` or
  `libsoup-3.0` development packages, so `cargo build` has never run. The Rust claims here are
  text-level (no write whose target reaches the server, no server import) plus `rustfmt --check`
  elsewhere; "the crate builds" is untested.
* **No bundle has been produced.** The sidecar's location inside a `.deb`/AppImage is inferred, not
  measured. The byte comparison pins what would be shipped, not where Tauri puts it.
* **`COPY . /app` carries the build context.** `.dockerignore` now lists `desktop`, so an image
  built from this checkout contains no shell, and the test asserts that line is present with no
  negation. It has not built an image: the exclusion is checked as text, not by inspecting a layer.
  No instruction uses the shell either way, and the server never reads it.
* **`os.stat` is not audited.** A `Path.exists()` probe under `desktop/` is invisible to the
  tripwire. In the copy it is answered `False`, which is the correct desktop-free behaviour; the
  hook covers opens, listings and creation.
* **The web UI's JS is not executed.** A string scan of `web/` cannot prove no script reaches for a
  host; it proves no shipped file names one.

---

## 5. Re-running it

```sh
TMPDIR=/tmp/opencode PYTHONPATH=.:tests python3 -m unittest tests.test_host_dependency tests.test_headless
```

The boot test copies the tree and starts one server on a loopback port with a temporary data
directory; it writes nothing into the checkout and touches no real install. It takes about a second
on this machine, most of it the boot wait.