# Desktop settings (HOST-005)

The window's settings panel, the rules behind it, and — stated as plainly as the README states
what a bundle still needs — **the two settings this card cannot honestly offer**.

The panel is four questions. Two of them are real settings the server acts on, one is a desktop
entry, and two are facts with a reason attached:

| setting | control | server-side reality | effect |
| --- | --- | --- | --- |
| Local only vs the LAN | two buttons | `--host` / `HARDLYLISTENING_HOST` | the address the server binds |
| Port | shown, not edited here | `--port` / `HARDLYLISTENING_PORT` | the port the server binds |
| Web UI on/off | **unsupported** | none exists | nothing; the UI is always served |
| OpenSubsonic on/off | **unsupported** | none exists | nothing; `/rest/` is always routed |
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

## The web UI: no switch exists

`server.py` serves `web/` on every start: `static()` reads from `WEB_DIR`, `/` maps to
`index.html`, and no variable turns it off. There is no `HARDLYLISTENING_WEB_UI` and inventing one
would mean editing the server — which this card does not.

So the setting is **marked unsupported**, with that reason in the panel, and a patch that asks for
it is refused rather than stored. Hiding the UI is a reverse-proxy rule (`deny /` in Caddy or
nginx), not a server setting; the panel says so instead of pretending.

## OpenSubsonic: same shape

`server.py` dispatches every `/rest/` request to `listening.rest()` unconditionally, and
`/api/v1/capabilities` answers `subsonic.enabled: true` as a constant. Nothing gates it: no
variable, no flag, no database setting.

`subsonic.py` is a protocol adapter, not a feature-flagged surface, so there is nowhere to put a
switch. Marked unsupported, refused rather than stored — a stored `subsonic: false` would be a
promise the next start breaks.

If a switch is ever added server-side, this module is the one place to change: add the variable to
`config.py`, remove the entry from `UNSUPPORTED`, and give the row a control.

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
Exec="/path/to/bin/hardlylistening-server" run --host 127.0.0.1 --port 8470
Terminal=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
```

* `Exec` is the same `argv()` the window spawns with, so an autostarted server binds the address
  the window polls. The path is quoted for the `Desktop Entry` spec when it contains spaces, and
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
{ "host": "0.0.0.0", "port": 8470 }
```

Rules:

* **Only what the server understands is stored.** No `web_ui`, no `subsonic`, no `autostart` —
  keys the server would ignore are not written, and keys the shell does not own are preserved.
* **Written atomically** (temp file beside it, then rename): a window that dies mid-write must not
  leave half a JSON file where the next start reads its address.
* **An unparsable file is kept, not destroyed.** `resolve.read_config` reports and ignores a
  malformed file, which is right for opening a window and wrong for overwriting one; the old
  bytes are copied to `host.json.bak` first.
* **Precedence is unchanged**: environment → this file → the server's defaults, via
  `resolve.address()`. Nothing here re-implements that.

### The environment wins, and the panel says so

If `HARDLYLISTENING_HOST` or `HARDLYLISTENING_PORT` is set for the window, the file is inert for
that field. Writing it would change nothing, so the write is **refused with the variable named**
rather than performed and silently ignored, and the panel's buttons for that field are disabled
with the same sentence. The other field still saves.

## Restart semantics

The server reads `--host`/`--port` once, in `main()`. A saved address is therefore true for the
*next* start: the window's polling address changes immediately, the running server does not.

So every address change is reported as a restart:

> Saved. Restart the server to listen on 0.0.0.0:8470 — it keeps answering on 127.0.0.1:8470
> until then.

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

## WIRING

Nothing in this card edits the shared files; these are the exact additions that turn the delivered
files into a working panel. Each is an insertion, not a rewrite.

### 1. `desktop/ui/index.html` — load the panel

Anchor (the end of the file, lines 60–61):

```html
<script src="state.js"></script>
<script src="app.js"></script>
```

becomes:

```html
<script src="state.js"></script>
<script src="app.js"></script>
<script src="settings.js"></script>
```

`settings.js` mounts itself: on load it finds `.shell`, builds its card in the shell's own classes
(`card`, `eyebrow`, `facts`, `actions`, `button.primary`, `hint`, `foot`, `notice`), and inserts
it directly after the `.card.server` section. No other HTML change is needed, and removing the
script removes the panel cleanly.

### 2. `desktop/src-tauri/src/lib.rs` — register the two commands

Anchor, below the crate doc comment:

```rust
mod host;
mod probe;
```

becomes:

```rust
mod host;
mod probe;
mod settings;
```

Anchor, in `run()`:

```rust
        .invoke_handler(tauri::generate_handler![
            host_env,
            server_status,
            server_start,
            server_stop,
            open_web_ui,
            quit
        ])
```

becomes:

```rust
        .invoke_handler(tauri::generate_handler![
            host_env,
            server_status,
            server_start,
            server_stop,
            open_web_ui,
            settings::settings_read,
            settings::settings_write,
            quit
        ])
```

Both are Rust functions, so `capabilities/default.json` needs no change: capabilities gate plugin
commands, and the shell calls neither plugin.

### 3. `desktop/README.md` — stop saying the window does not write settings

Anchor (in *What is deliberately not here*):

```markdown
* **Settings** — HOST-005. The window shows the address and the config path; it does not write
  them.
```

becomes:

```markdown
* **Settings** — HOST-005 wrote `desktop/host/settings.py`, the panel in `desktop/ui/settings.js`
  and the two commands in `src-tauri/src/settings.rs`. The window edits the exposure
  (local vs LAN) and the autostart entry; the web UI and OpenSubsonic are reported as having no
  server-side switch, because they have none. See `docs/desktop/settings.md`.
```

Also anchor (the address table):

```markdown
| `<XDG_STATE_HOME>/hardlylistening-desktop/host.json` | a person chose an address in the window (HOST-005 writes it) |
```

becomes:

```markdown
| `<XDG_STATE_HOME>/hardlylistening-desktop/host.json` | a person chose an address in the window (HOST-005 writes it) |
```

### 4. Nothing else

`tauri.conf.json` already embeds `desktop/ui` (`frontendDist`), so `settings.js` ships without a
config change; `web/`, `config.py` and every core module are untouched, and the headless install
is unaffected.