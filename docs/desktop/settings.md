# Desktop settings (HOST-005)

The window's settings panel, the rules behind it, and — since SERVER-006 — the two switches that
used to be rows with a reason attached. Every setting in the panel is now real.

The panel is four questions plus the port:

| setting | control | server-side reality | effect |
| --- | --- | --- | --- |
| Local only vs the LAN | two buttons | `--host` / `HARDLYLISTENING_HOST` | the address the server binds |
| Port | shown, not edited here | `--port` / `HARDLYLISTENING_PORT` | the port the server binds |
| Web UI on/off | two buttons | `--no-web-ui` / `HARDLYLISTENING_WEB_UI` | off: every page and asset in `web/` is a 404 with a sentence |
| OpenSubsonic on/off | two buttons | `--no-subsonic` / `HARDLYLISTENING_SUBSONIC` | off: `/rest/*` answers the protocol's own error envelope |
| Start at sign-in | two buttons | XDG autostart entry | runs the server launcher at login |

`desktop/host/settings.py` is the specification and the tested copy. `desktop/ui/settings.js` is
the same decisions in the panel, and `desktop/src-tauri/src/settings.rs` only reads and writes:
the crate cannot be compiled on this machine (no `webkit2gtk-4.1`), so `rustfmt --check` is the
whole proof of the Rust half.

## Local only vs the LAN

There is exactly one bind control in the server, so there is exactly one setting here.

* `config.host()` returns `HARDLYLISTENING_HOST`, defaulting to `127.0.0.1`.
* `server.py --host` beats that variable (`main()`: "`--host`/`--port` beat the environment").
* `main()` passes the result straight to `ThreadingHTTPServer((args.host, args.port))`.

So the two named exposures are spellings of that one value:

| choice | bind address | who can connect |
| --- | --- | --- |
| This computer only | `127.0.0.1` | only this machine |
| Anyone on this network | `0.0.0.0` | every IPv4 interface |

A LAN address typed into the config file by hand (`192.168.1.50`, say) is accepted and reported
as the LAN exposure; the panel's two buttons are the two usual values, not an allow-list.

Two limits worth naming:

* `0.0.0.0` is IPv4 only. An IPv6 LAN needs `--host ::`, which this window does not offer —
  `HARDLYLISTENING_HOST` still does.
* Binding the LAN is **exposure, not authentication**. Docs §7 of `docs/server-packaging.md` is
  the reference: put a proxy in front, or set `HARDLYLISTENING_PUBLIC_ORIGIN` and accounts
  (`HARDLYLISTENING_AUTH_REQUIRED`) before leaving it open. The panel says that in the exposure
  hint rather than implying the bind is a security boundary.

## The web UI switch

The server used to serve `web/` on every start. Since SERVER-006 it serves it unless told not to:

* `config.web_ui()` reads `HARDLYLISTENING_WEB_UI` (on unless `0`/`false`/`off`/`no`), and
  `server.py --no-web-ui` overrides it. `main()` writes the answer into `server.FEATURES` before
  it binds, so a running server is never half-gated.
* **Off gates the directory, not the API.** `Handler.static()` is the one function that serves
  pages, scripts, styles and PWA assets from `web/`; it answers `404` with
  `The web UI is disabled on this server.` The `/api/*` routes, `/rest/*`, `/api/health` and the
  rendered `/s/<token>` share pages do not pass through it and keep working.
* Off also skips the sign-in redirect for `/`: sending a person to `/login` would only land them
  on a 404, so they get the sentence directly.

The panel's row is a real two-state control, painted from `resolve.features()`. A stored value is
`true` or `false` — a string such as `"false"` is refused rather than guessed, because string
truthiness is how a `"false"` becomes on.

## The OpenSubsonic switch

`/rest/*` was dispatched unconditionally. Now:

* `config.subsonic()` reads `HARDLYLISTENING_SUBSONIC`, and `server.py --no-subsonic` overrides it.
* Off answers the **protocol's** error shape — `subsonic-response` with `status: failed`, error
  code `0`, and "OpenSubsonic is disabled on this server" — in the format the request asked for
  (`f=json` or XML). A plain Subsonic client gets a refusal it can report, not a bare 404 it would
  read as a broken network.
* `/api/v1/capabilities` now reports `subsonic.enabled` from `FEATURES` instead of the constant
  it used to send, and publishes the web UI's state beside it as `web_ui.enabled`. The endpoint
  cannot claim more than the server does, which is what makes the document worth trusting.

## Autostart: the server, at sign-in, only when chosen

The entry is written to:

```
<XDG_CONFIG_HOME>/autostart/hardlylistening-server.desktop
```

which is `~/.config/autostart/…` unless `XDG_CONFIG_HOME` says otherwise.
`HARDLYLISTENING_DESKTOP_AUTOSTART` overrides the whole path — the same escape hatch
`HARDLYLISTENING_DESKTOP_CONFIG` gives the address, so a test (or a portable install) never
reaches into a person's real config.

The body runs the launcher, never the window:

```ini
[Desktop Entry]
Type=Application
Version=1.0
Name=HardlyListening server
Comment=Starts the HardlyListening server at sign-in, not the desktop window
Exec="/path/to/bin/hardlylistening-server" run --host 127.0.0.1 --port 8470 --no-subsonic
Terminal=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
```

* `Exec` is the same `argv()` the window spawns with — the address, plus `--no-web-ui` /
  `--no-subsonic` when a switch is off — so an autostarted server binds the address the window
  polls and serves what the panel shows. (The example above shows one switch off.) The path is quoted for the `Desktop Entry` spec when it contains spaces, and
  `%` is escaped as `%%` (it is a field code there).
* `Terminal=false` is honest about the cost: an autostarted server has no journal, no terminal and
  therefore **no visible log**. If logs matter, run it under systemd (`docs/server-packaging.md`)
  and leave this off.
* The entry is XDG, i.e. Linux (and the BSDs that follow it). On macOS and Windows
  `settings_read` reports `autostart_supported: false` and the panel explains instead of
  offering a button that writes a file nothing reads.

**Never written without an explicit choice.** That is structural, not a comment:

* `save(loaded, patch, autostart=None)` is the default, and `None` means "no choice was made" —
  the entry is neither written nor removed, so an entry installed by hand survives a save.
* A patch cannot carry `autostart`; it is refused with "autostart is only changed by an explicit
  choice, never by a patch".
* The panel's draft carries `autostartTouched`, set only by a click, and sends `null` otherwise.

Turning it off removes only `hardlylistening-server.desktop`; any other file in
`~/.config/autostart` is left alone.

## The file it writes

`resolve.py` already reads `<XDG_STATE_HOME>/hardlylistening-desktop/host.json`, and HOST-005 is
the card that writes it. The shape stays the one the resolver understands:

```json
{ "host": "0.0.0.0", "port": 8470, "web_ui": true, "subsonic": false }
```

Rules:

* **Only what the server understands is stored.** All four keys are read at boot (`--host`,
  `--port`, `--no-web-ui`, `--no-subsonic`); `autostart` is never written, because the server has
  no autostart flag — that one is the desktop entry. Keys the shell does not own are preserved.
* **Written atomically** (temp file beside it, then rename): a window that dies mid-write must not
  leave half a JSON file where the next start reads its address.
* **An unparsable file is kept, not destroyed.** `resolve.read_config` reports and ignores a
  malformed file, which is right for opening a window and wrong for overwriting one; the old
  bytes are copied to `host.json.bak` first.
* **Precedence is unchanged**: environment → this file → the server's defaults, via
  `resolve.address()` and `resolve.features()`. Nothing here re-implements either.

### The environment wins, and the panel says so

If `HARDLYLISTENING_HOST`, `_PORT`, `_WEB_UI` or `_SUBSONIC` is set for the window, the file is
inert for that field. Writing it would change nothing, so the write is **refused with the variable
named** rather than performed and silently ignored, and the panel's control for that field is
disabled with the same sentence. The other fields still save.

## Restart semantics

The server reads `--host`, `--port` and both switches once, in `main()`. A saved change is
therefore true for the *next* start: the window's polling address changes immediately, the running
server does not.

So every address change is reported as a restart:

> Saved. Restart the server to listen on 0.0.0.0:8470 — it keeps answering on 127.0.0.1:8470
> until then.

A switch alone gets its own sentence, because there is no address to name:

> Saved. Restart the server for the new switches to take effect.

The panel does not restart anything itself: HOST-001 made stopping an owned server a button, and
a save is not a stop. The diff (`restart`, `restart_required`, `summary`) is what lets the window
say this instead of leaving a person polling an address nothing listens on.

An autostart change is *not* a restart — it takes effect at the next sign-in.

## What is not verified, stated plainly

* **The Rust has never been compiled.** `rustfmt --check` proves it parses and is formatted;
  nothing proves `#[tauri::command]`, `set_autostart`'s permission call or the JSON shapes are
  right. Expect the first `cargo build` to correct details.
* **The panel has never run in a WebView.** `tests/test_host_settings.cjs` drives the decisions in
  Node with plain objects; the DOM code (`panel()`, `mount()`) is unexecuted, and so is the
  `invoke` bridge.
* **No autostart file is executed.** The tests never write to a real `~/.config/autostart`; they
  check the entry's text and its location in a temp directory. Whether a session actually starts
  it is untested here.
* **No test touches a real config.** Every write in `tests/test_host_settings.py` lands in a
  temporary directory via `HARDLYLISTENING_DESKTOP_CONFIG`/`HARDLYLISTENING_DESKTOP_AUTOSTART`.

## Tests

```sh
python3 -m unittest tests.test_host_settings     # the model, the file, the entry, the diff
node tests/test_host_settings.cjs                # the panel's decisions
rustfmt --check desktop/src-tauri/src/*.rs       # parses; formatting only
```

## Wiring (in place)

HOST-005 delivered the panel as files and spelled out how to attach it; that wiring has since been
applied, so these are facts about the tree rather than instructions:

* `desktop/ui/index.html` loads `settings.js` after `app.js`. The panel mounts itself into
  `.shell`, directly after the `.card.server` section.
* `desktop/src-tauri/src/lib.rs` declares `mod settings;` and registers `settings::settings_read`
  and `settings::settings_write` in `invoke_handler`.
* Both are Rust functions the crate defines, so `capabilities/default.json` needs no entry:
  capabilities gate plugin commands.
* `tauri.conf.json` embeds `desktop/ui` as `frontendDist`, so the panel ships without a config
  change. `web/`, `config.py` and every core module are untouched by the panel itself; the server
  half of the two switches lives in `server.py` and `config.py`.
