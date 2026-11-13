# The local server's lifecycle (HOST-003)

HOST-001 established the one rule everything here rests on: the window holds **only a server it
spawned**, and a server found answering `/api/health` that is not in that slot belongs to systemd,
Docker or a terminal. HOST-003 builds the rest of the lifecycle on that rule — start, watch, stop,
close — without loosening it.

Three files carry it, and the split is deliberate:

| file | what it decides | what it does |
| --- | --- | --- |
| `desktop/host/lifecycle.py` | everything: the state machine, the deadline, the port probe, the handoff file, argv/env | nothing it spawns in production; it is the tested spec |
| `desktop/src-tauri/src/lifecycle.rs` | nothing | spawn, TERM, kill, wait, read/write the state file |
| `desktop/ui/lifecycle.js` | what each transition says and which button is live | paints its own nodes; no process, no file |

The Rust crate cannot be compiled on the machine this was written on (no `webkit2gtk-4.1`, no
`gtk+-3.0`, no `libsoup-3.0`), so the same split as HOST-001 applies: the decisions are Python,
the OS calls are Rust, and `tests/test_host_lifecycle.py` compares the constants the two copies
cannot avoid repeating.

The window calls **one** command, `lifecycle_step`, with an `action` — `state`, `start`, `stop`,
`close` or `close_behaviour` (the stored preference) — and `lib.rs`'s window-close event calls
`lifecycle::on_close`. One command rather than five keeps the wiring in a single place; the
actions are dispatched in `lifecycle::handle`, not in the WebView.

## The state machine

`phase` and `ownership` are separate, because "failed" and "not mine" are different facts.

| phase | ownership | means |
| --- | --- | --- |
| `idle` | `none` | nothing is running here, or the last thing that ran was stopped |
| `starting` | `ours` | a child was spawned and has not answered yet |
| `up` | `ours` | the child answers `/api/health` |
| `up` | `external` | something answers that this window did not spawn |
| `failed` | `ours` | the start was declared failed but the process is still alive (a timeout) |
| `failed` | `none` | it exited on its own, or could not be spawned at all |
| `failed` | `external` | the port is held by another program that does not answer |
| `stopping` | `ours` | SIGTERM sent, waiting out the grace period |

Transitions, in the order they can happen:

```
idle ──start──▶ starting ──healthy──▶ up ──stop──▶ stopping ──exit──▶ idle
                    │                    │                              ▲
                    ├─child exited──▶ failed/none ──start──┐            │
                    └─deadline────▶ failed/ours ──stop─────┴────────────┘
idle ──start (something answers)──▶ up/external        (stop refused)
idle ──start (port bound, no HTTP)──▶ failed/external   (stop refused)
up/ours ──close──▶ idle/none, child keeps running, handoff record written
```

`failed/ours` is not an error state to hide: a start that timed out leaves a real process, and
Stop must still be available for it. That is why the deadline does not kill the child — killing
it would erase the thing the window is trying to explain.

## The start, and the five ways it ends

`Lifecycle.start()` checks, in this order:

1. **Already ours** → `already_started`. No second child.
2. **Something answers** `/api/health` → `already_running`, ownership `external`. Nothing is
   spawned: a second server on one port is either a bind failure or a server nobody polls.
3. **The port is bound but does not answer** → `port_in_use`, ownership `external`. Something
   else owns the port; starting here would silently serve somewhere else.
4. **Spawn** the launcher. A failure to exec is `spawn_failed` with the OS text.
5. **Watch.** Poll `/api/health` every 100 ms until it answers (`up`), the child exits (`exited`,
   with the status code), or `START_DEADLINE` = 20 s passes (`timeout`).

The deadline is 20 s in three places — `START_DEADLINE` in `lifecycle.py`, `START_DEADLINE` in
`lifecycle.rs`, and `STATUS_AFTER_START_MS` in `state.js` — because it is one fact the window
shows and the host enforces. The Python and Rust constants are compared by a test; the JS one is
the client-side guard for a host that stops answering.

Every ending carries a `reason` and a `detail` sentence naming the address, the port or the exit
status. "It did not start" with no reason is indistinguishable from a broken button.

## What stop may touch

`stop()` signals **only the child in this window's slot**:

* `SIGTERM` first. The server shuts its queue worker down on it (`SERVER-PKG-004`); `SIGKILL`
  would skip that, so it is the fallback.
* 3 s of grace (`STOP_GRACE`), then `SIGKILL`. The result says `forced: true` so the window can
  say "it was killed" rather than pretending it was graceful.
* An external server is refused with `not_started_here`, and the detail names who probably owns
  it. That refusal is a warning in the window, never silence.
* Stopping clears the handoff record, because there is no longer anything to hand off.

## Closing the window: handoff by default

Closing (or Quit) **does not stop a server this window started**. It records that the server is
being left running and forgets it:

* the child is not signalled;
* a record is written naming its pid, its address and when it was left;
* the window stops supervising it, so the next window sees a server answering that it did not
  start — `external`, stop refused.

That is the rule the card asks for: a server started by this window survives a close unless the
person chose otherwise. The choice is `stop_on_close`, **off** unless set; when it is on, close is
a normal graceful stop.

### The state file: `lifecycle.json`

`<XDG_STATE_HOME>/hardlylistening-desktop/lifecycle.json`, beside `host.json`. The same directory,
a different file, and `HARDLYLISTENING_DESKTOP_CONFIG` still points both at a test directory
because the path is derived from `resolve.config_path()`.

```json
{
  "handoff": {
    "pid": 48221,
    "listen": "127.0.0.1:8470",
    "launcher": "/opt/hardlylistening/bin/hardlylistening-server",
    "recorded_at": 1763000000.5
  },
  "stop_on_close": false
}
```

Two facts, one file. The justification is the alternative: putting both into `host.json` would
mean two writers on the one file a person's address lives in, and HOST-005 is the other writer.
A preference silently dropped by a second writer is a bug that only appears months later.

Rules the file obeys:

* **Written atomically** — a temporary file beside the target and a rename, so a crash leaves the
  previous record rather than half of one.
* **Only this module writes it.** Nothing else in the window touches `lifecycle.json`.
* **A stale record is removed, not reported.** If the pid is gone (a reboot, a crash, a server
  someone stopped by hand), the record is deleted and `handoff` reads as empty: the question the
  window asks is "is a server of mine running", and a dead pid is a wrong answer. Pid reuse is
  the residual risk; the record is advice, not authority, and the address is what the window
  actually polls.
* **Malformed is ignored**, not fatal — the same rule `resolve.read_config` follows.

The record does not grant stop rights. A server a previous window left running is `external` to
the next one; the record explains what it is, it does not authorise a signal.

## The command that is spawned

`build_launch()` reuses `resolve.py` rather than repeating it: `resolve.address()` chooses the
address (environment, then `host.json`, then the documented defaults) and
`resolve.launcher()` chooses the entry point (`HARDLYLISTENING_SERVER`, else `bin/…`).

* argv: the launcher, then `--host`/`--port` **explicitly**, so the server cannot resolve a
  different address than the window polls.
* environment: the inherited environment, plus `HARDLYLISTENING_HOST` and
  `HARDLYLISTENING_PORT` set to the same values (the flags and the environment agree), plus
  `HARDLYLISTENING_PYTHON` when the shell declares one.
* working directory: the application root (`<app>/bin/…` → `<app>`), so the launcher's relative
  lookup is stable regardless of where the window was started.
* stdio: `/dev/null` for all three. The server's logs belong to whoever owns the process; a
  window that buffered them would be a second log sink.
* a **new process group** (POSIX), so a terminal `Ctrl+C` aimed at the window cannot take down a
  server the window is about to hand off.

A missing launcher is `no_launcher` with the fix in the detail, and a bad port is `bad_address`
rather than a silent fall back to the default — the same rule `config.port()` follows.

## What is verified, and what is not

Verified here, with a stub `http.server` on port 0 and stub python children — never the real
server, never the real address, never the live database:

* every start ending: up, `exited`, `timeout`, `already_running`, `port_in_use`, `no_launcher`,
  `spawn_failed`;
* graceful stop, and the grace-then-kill path for a child that ignores `SIGTERM`;
* the refused stop of an external server, with the server still answering afterwards;
* a child that dies on its own being noticed by a poll and released;
* close handing off (child still running, record written, stale record removed) and
  `stop_on_close` stopping instead;
* the argv/env/cwd that a real spawn receives, proven by a launcher that writes them down.

**Not verified, stated plainly:**

* **The Rust has never seen a compiler.** `rustfmt --check` proves it parses. `wait_ready`,
  `terminate`, `process_group(0)`, `Command::spawn` and the `serde_json` round-trip are used as
  documented, not as observed. Expect the first `cargo build` to correct details.
* **The WebView has never run `lifecycle.js`.** The Node test executes the module in a `vm`, not
  in a WebView, and the painting is against a fake document.
* **`SIGTERM` on Windows.** The grace path is `#[cfg(unix)]`; Windows goes straight to
  `Child::kill`. The state file and the deadline are portable; the signal is not.
* **Handoff across a real reboot.** The stale-record path is tested with a pid that has exited,
  which is the same evidence the code uses, but no test reboots a machine.