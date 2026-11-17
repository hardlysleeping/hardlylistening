# The desktop host shell (HOST-001)

The optional window that starts, stops and points at a HardlyListening server. It is a **client**
of the server's HTTP API, sitting beside `web/` rather than in front of it. Delete this directory
and the headless install is unaffected — `tests/test_desktop_host.py` asserts that by inspecting
imports, not by asking you to trust it.

```
desktop/
├── host/                  the decisions, in Python, where they can be tested
│   ├── resolve.py         path + address resolution (the spec the Rust mirrors)
│   ├── addresses.py       which address another device could actually type
│   ├── mode.py            two independent choices, four combinations
│   ├── lifecycle.py       start deadline, stop ownership, close handoff
│   ├── settings.py        the settings model and what has no server-side switch
│   └── setup.py           the first run's four answers
├── tools/stage_sidecar.py copy bin/hardlylistening-server to the sidecar name
├── ui/                    plain HTML/CSS/JS — no framework, no bundler, no node_modules
│   ├── state.js           status, health and the button state (tested)
│   ├── mode.js            the two controls
│   ├── address.js         the one address worth copying, or nothing
│   ├── lifecycle.js       transition wording and button enablement
│   ├── settings.js        the settings panel, self-mounting
│   ├── wizard.js          the first-run steps
│   └── app.js             painting + the Tauri bridge, nothing else
└── src-tauri/             Rust: window, sidecar spawn, HTTP probe, the OS halves
```

Every `.js` under `ui/` is loaded by `ui/index.html` and the test asserts that: a module written
and never wired would be a feature that silently is not there.

Rules this shell obeys, in the order they constrain the code:

1. **Tauri 2 only** — the OS WebView, not Chromium. No Electron, no bundled browser, no second
   HTTP server.
2. **Tauri is the interface, not the foundation.** systemd and Docker must keep starting the same
   runtime with no desktop, no display and no Node. `tests/test_headless.py` is that promise;
   this directory must never be a prerequisite for it.
3. **The GUI holds no domain rules.** It shows what `/api/health` and `/api/v1/capabilities`
   answer. No track, queue, member or library logic exists here — not even a version number,
   which `/api/health` already carries.
4. **The server is the canonical launcher.** The sidecar is `bin/hardlylistening-server`, the
   same file systemd's `ExecStart` and the Dockerfile's `CMD` point at, copied under Tauri's
   naming convention. Not a fork, not a shell script with extra logic.

## Running it in development

Prerequisites (Linux): `webkit2gtk-4.1`, `gtk+-3.0`, `libsoup-3.0` development packages,
`rustc`/`cargo`, and the Tauri CLI:

```sh
cargo install tauri-cli --version '^2' --locked   # provides `cargo tauri`
```

Then, from the repository root:

```sh
cd desktop/src-tauri
cargo tauri dev            # window + the served UI, no bundle
```

`cargo tauri dev` does **not** need a staged sidecar: in a development run the launcher is found
by walking up from the built binary to the checkout's `bin/`. To point the window somewhere else
entirely (a different checkout, a wrapper, an interpreter):

```sh
HARDLYLISTENING_SERVER=/path/to/bin/hardlylistening-server cargo tauri dev
```

The server it starts is an ordinary local server on `127.0.0.1:8470`; anything else already
answering that address is treated as somebody else's and left running (see *Ownership*).

## Building a bundle

```sh
cd desktop/src-tauri
cargo tauri build
```

`beforeBuildCommand` runs `python3 tools/stage_sidecar.py` first, which asks `rustc -vV` for
the target triple and copies `bin/hardlylistening-server` to
`src-tauri/bin/hardlylistening-server-<triple>` (byte-identical, `--check` verifies). Tauri
resolves `externalBin` by that suffixed name; a copy without the suffix builds and then ships
without a server.

What the bundle contains:

| part | what it is |
| --- | --- |
| the window binary | this crate: twelve Rust commands and a WebView |
| `ui/` assets | embedded at build time, exactly the files in `desktop/ui` |
| the sidecar | a copy of `bin/hardlylistening-server` |
| icons | generated from the repository's `icon.svg` |

### What the bundle does *not* contain, and why that matters

**No server core.** The package is client-only by decision: containers and systemd own the
server's lifecycle, and a second copy of the tree inside a `.deb` would be a second distribution
channel for the same code. The launcher is a `sh` script whose only job is to
`exec python3 cli.py "$@"`, so it can only start a server when the tree beside it is there.

The window therefore searches for a launcher, in this order, and accepts one only when both the
launcher and the `cli.py` beside it exist:

1. `HARDLYLISTENING_SERVER` — an explicit path, honoured even when it turns out not to be usable,
   so the failure can be reported instead of hidden;
2. a system install at `/opt/hardlylistening/bin/hardlylistening-server`;
3. the bundle's own sidecar locations;
4. a development checkout, walked up from the binary.

A miss is a supported state, not an error: the window drives a server it does not own — the VPS
container, one started by systemd, or one started by Docker — and Start is disabled with the
reason shown. `desktop/host/resolve.py` is the tested statement of this rule and
`src-tauri/src/host.rs` mirrors it; `spawnable()` in both is what prevents a Start button that
spawns a launcher and immediately dies on a `cli.py` that is not there.

It is **not a self-contained application**: even once a launcher resolves, the server it starts
still needs a Python 3 on the target machine, plus whatever the server needs to be useful
(`yt-dlp`, `ffmpeg`, a media root).

A self-contained build would have to freeze the interpreter — PyInstaller/Nuitka one-file output,
or an embedded CPython — and ship the tree that launcher execs. That is a packaging change with a
real cost (startup time, size, `ffmpeg`/`yt-dlp` still external) and it is not HOST-001.

## Address and configuration

The window asks the same questions the packaged installs answer:

| source | wins when |
| --- | --- |
| `HARDLYLISTENING_HOST`, `HARDLYLISTENING_PORT` | the environment declares them |
| `<XDG_STATE_HOME>/hardlylistening-desktop/host.json` | a person chose an address in the window (HOST-005 writes it) |
| the server's defaults (`127.0.0.1:8470`) | neither |

Whatever is resolved is passed to the launcher as explicit `--host`/`--port`, so one address is
polled and one address is served. `HARDLYLISTENING_DESKTOP_CONFIG` overrides the config file
path. A typo in the port is an error shown in the window, never a silent fall back to 8470.

## Ownership, and what closing the window does

The lifecycle's child slot holds **only a server this window spawned**. A server found answering the health
check that is not in that slot belongs to systemd, Docker or a terminal: Start is disabled, Stop
is disabled, and an attempted stop reports "that server was running before this window opened…
left alone". This is the rule that keeps a desktop window from stopping the VPS's service
because it happened to be open.

**HOST-003.** Closing the window (or pressing Quit) hands the server off rather than killing it, unless the
person asked otherwise; the preference and the handoff record live in `lifecycle.json` beside
`host.json`. A server this window did not start is never signalled. See
[lifecycle.md](../docs/desktop/lifecycle.md).

## The first run, and the two choices

**HOST-002.** On a launch with no usable `setup.json` the window asks four questions — music folder, data
folder, server name, admin account — and writes its answers as it goes, so a second launch resumes
instead of starting over. The admin account is created by the server's own one-shot account path,
never by writing the database from the window. See [wizard.md](../docs/desktop/wizard.md).

**HOST-004.** Beside the server buttons are two separate choices: whether this window runs a local server, and
which server the window talks to. They are two axes, not one mode switch, and neither rewrites
the other. Remote-only — a remote server and no local one — is a supported state, not a degraded
one. See [modes.md](../docs/desktop/modes.md).

## What is deliberately not here

* **Settings beyond the server's own** — HOST-005 wrote `desktop/host/settings.py`, the panel in
  `desktop/ui/settings.js` and the two commands in `src-tauri/src/settings.rs`. The window edits
  the exposure (local vs LAN), the web UI and OpenSubsonic switches, and the autostart entry —
  every one of them a flag or entry the server reads. See
  [settings.md](../docs/desktop/settings.md).
* **Managing a tailnet** — HOST-006 *reports* a reachable address, it does not manage one. The
  window reads `tailscale status --json` and the interfaces to say what another device could type,
  never runs `up`, `down`, `login` or `set`, and says nothing when nothing is reachable. See
  [addresses.md](../docs/desktop/addresses.md).
* **A log viewer.** The server's stdout/stderr are deliberately not captured: its logs belong to
  whoever owns it (the journal, a terminal, Docker's log driver). A failed start shows up as
  "down" plus the probe's error, not as a log pane.
* **An HTTP shutdown endpoint.** Stopping uses the OS signal the server already handles. Adding
  a route would put a destructive control in the server's API surface; that is a separate
  decision and not one this card makes.
* **A tray icon and update checks.** Autostart is HOST-005's; a tray icon and update checks are
  still absent.
* **The served UI.** `web/` is untouched. "Open in browser" points the OS browser at the
  address the server already serves; the window does not reimplement a player.

## Tests, and what they do not prove

The host has no `webkit2gtk-4.1`, `gtk+-3.0` or `libsoup-3.0` dev packages, and adding them to an
immutable system is a layering change plus a reboot. The `huibuild` distrobox (Ubuntu 24.04) has
them, so the crate is compiled there and linked against the host's own WebKit at run time:

```sh
distrobox enter huibuild -- bash -lc 'cd ~/apps/hardlylistening/desktop/src-tauri && \
  CARGO_TARGET_DIR=$HOME/.cache/hl-desktop-release cargo build --release'
```

`cargo tauri build` is not used — there is no `cargo-tauri` CLI in that box, and plain
`cargo build` is enough to produce the window binary. To produce the *package*, the CLI comes from
the Node toolchain instead, which also runs `beforeBuildCommand` (the sidecar staging):

```sh
distrobox enter huibuild -- bash -lc 'cd ~/apps/hardlylistening/desktop && npx --no-install tauri build --bundles deb'
```

The crate's own tests are in the library, not the binary — `cargo test --release --lib` runs them
(there is no `cargo-tauri` here, so `tauri test` does not exist either). The proof is still split:

```sh
python3 -m unittest tests.test_desktop_host     # config, capabilities, imports, sidecar, resolver
python3 -m unittest tests.test_host_wizard      # the first run's four answers
python3 -m unittest tests.test_host_mode        # two choices, four combinations
python3 -m unittest tests.test_host_addresses   # which address is reachable, and silence
python3 -m unittest tests.test_host_settings    # the settings model and its refusals
python3 -m unittest tests.test_host_lifecycle   # start, stop, handoff, deadline
node tests/test_desktop_state.cjs               # ownership, health states, address formatting
node tests/test_host_wizard.cjs                 # the wizard's steps and resumption
node tests/test_host_mode.cjs                   # the two controls never couple
node tests/test_host_address.cjs                # the window's one line of truth
node tests/test_host_settings.cjs               # the panel's decisions
node tests/test_host_lifecycle.cjs              # the lifecycle's transitions
rustfmt --check desktop/src-tauri/src/*.rs      # parses; formatting only
distrobox enter huibuild -- bash -lc 'cd ~/apps/hardlylistening/desktop/src-tauri && \
  cargo test --release --lib'                   # the crate's own unit tests (wizard defaults)
```

`tests/test_desktop_host.py` parses `tauri.conf.json`, every capability file, the sidecar name
against the real launcher and the icon list against the files on disk; it also asserts the
desktop imports no server module and that no server module so much as names `desktop/`.

What that does **not** cover, stated plainly:

* **Compilation and the live window.** The crate compiles, and a release build was walked under
  Xvfb on X11: the four first-run answers, the account creation, Start/Stop, the settings panel,
  Quit-with-handoff and a reopen with the handoff record. That run corrected five things the Node
  tests could not see: `icons/32x32.png` and `icons/128x128.png` were palette PNGs, which Tauri
  refuses (RGBA is required); `Lifecycle::start` had an unused parameter; `setup_admin` was being
  invoked with `data_dir`, which Tauri renames to `dataDir` on the way in, so the account step
  always failed; an exit warning stayed on screen after the server was up again; and a deliberate
  Stop was reported as "The local server exited." because only the `lifecycle` module was told
  about it. The last three now have guards that fail if the fix is removed.
* **Bundling.** A `.deb` is produced (`tauri build --bundles deb`, 1.50 MiB,
  `Depends: libwebkit2gtk-4.1-0, libgtk-3-0`) and its contents were checked: the window binary,
  the launcher byte-identical to `bin/hardlylistening-server`, the `.desktop` entry, the icons and
  `/usr/lib/HardlyListening/host/`. It ships no server core, so on an installed system the window
  is a client until a launcher resolves; that is the decision above, not a gap. The build only
  worked after `beforeBuildCommand` was corrected (it pointed one directory above the tree).
* **The packaged window, rendered.** The extracted `.deb` was run under Xvfb with
  `GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_COMPOSITING_MODE=1
  WEBKIT_DISABLE_DMABUF_RENDERER=1` — without the last two, WebKitGTK creates no mapped window
  and the root capture is blank, which is what earlier runs saw. The shell renders with
  **Start local server disabled** and the notice "No usable local server was found to start…",
  exactly the client-only state described above. **Not proven:** the sidecar's in-bundle location
  is still inferred rather than exercised (the package has no core for it to find), and Stop on a
  server this window spawned is untested from the package.
* **The full served UI on a loopback install.** This was the desktop's last open decision, and
  it was made on the server side (2026-11-16): a deployment with **no** `PUBLIC_ORIGIN` now
  authenticates a request that is *provably* local — loopback socket peer, loopback `Host`
  header, and no proxy or forwarding header — and signs it in with a plain `hl_session` cookie.
  A tunnel or reverse proxy fails those checks together (it connects from loopback but forwards
  the public `Host` and adds `X-Forwarded-*`), so an exposed server never looks local, and a
  configured `https` origin keeps `__Host-`+`Secure` everywhere. Proven live: invite →
  `hl_session` cookie → `/api/state` 200, and the same cookie with `X-Forwarded-For` → 503.
  Still true and unchanged: a *remote* device needs a real https origin, and invite links minted
  on a loopback install point at `127.0.0.1` — useless to anyone else.
* **WebView behaviour beyond that walk.** CSP, `withGlobalTauri` and the `xdg-open` handoff were
  exercised only as far as the screenshots show; `tests/test_desktop_state.cjs` still runs the
  state logic in Node, not in a WebView.
* **SIGTERM on Windows.** The grace path is `#[cfg(unix)]`; Windows goes straight to
  `Child::kill`, which is not the server's graceful shutdown.

## Packaging note

The direction is asserted, not intended: `tests/test_host_dependency.py` walks the server's real
import closure, boots a copy with exactly `desktop/` removed, and pins the staged sidecar to the
launcher by SHA-256. `.dockerignore` excludes `desktop`, so `COPY . /app` cannot carry these files
into the image as inert data. See [dependency.md](../docs/desktop/dependency.md).