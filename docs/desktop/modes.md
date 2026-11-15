# Two choices, four combinations

Status: **model and window module built; not yet painted by `index.html`/`app.js`.** The two
axes, their four combinations and the remote-address rule live in `desktop/host/mode.py` (the
tested model) and `desktop/ui/mode.js` (the same rules for the window), driven by
`tests/test_host_mode.py` and `tests/test_host_mode.cjs`. The anchors that put the controls on
the page are in *Wiring it into the page* below; until that lands, `mode.js` is loaded by the
Node test and by nothing else.

## The two axes

A desktop window answers two questions, and they are separate questions:

| axis | values | question |
| --- | --- | --- |
| **local server** | on / off | does *this window* run a server on this computer? |
| **target** | local / remote | which address does the window talk to? |

They are not one mode switch. A person can run a local server for other devices and still have
this window follow a VPS. A person can run nothing here and use a remote server. Turning one
control must not move the other.

## The matrix

`Start`/`Stop` below are the *choice-level* permissions: with the local server off there is
nothing for them to act on, and the switch is the action. The runtime gate on top stays in
`desktop/ui/state.js` — a server that is already running, or one systemd/Docker started, still
narrows Start and refuses Stop. Ownership is never overridden by this model.

| local server | target | state id | window talks to | Start / Stop | when the address does not answer |
| --- | --- | --- | --- | --- | --- |
| on | local | `local` | the local address | live / live | "The local server at … is not answering. Start it from this window." |
| on | remote | `local-behind-remote` | the remote origin | live / live | "… The local server on this computer keeps running; point the window at local to use it instead." |
| off | local | `local-off-pointed-local` | the local address | off / off | "Nothing answers …: this window is not running a local server. Switch the local server on, or point the window at a remote server." |
| off | remote | `remote-only` | the remote origin | off / off | "… This window runs no local server, and it does not need one: retry when the remote is back, or switch the local server on." |

**Remote-only is a supported state, not a degraded one.** A server that lives on a VPS, a
tailnet node or a home box does not need a second copy on the machine the window runs on. No
combination carries a "degraded" flag, and `tests/test_host_mode.py` and
`tests/test_host_mode.cjs` both assert that, so the claim cannot quietly become a comment.

The one state that asks for attention is `local-off-pointed-local`: the window is pointed at an
address nobody is serving. It says so and offers both exits; it does **not** re-point itself.

## The non-couplings

Both are asserted from both directions, in both languages:

* **turning the local server off does not rewrite the target.** `with_local_server(mode, off)` /
  `Mode.setLocalServer(mode, false)` copy `target` and `remote_origin` unchanged. A local target
  stays local, so the dead end above is reachable rather than papered over.
* **switching the target to remote does not start or stop the server.** `with_target(...)` /
  `Mode.setTarget(...)` copy `run_local_server` unchanged. Neither mutator has a process to
  signal: they return a value.

A remembered remote origin also survives a switch back to local, so going back and forth does not
mean retyping the address.

## The remote-address rule

`parse_origin` (Python) and `Mode.parseOrigin` (JS) apply the same four rules, in the order a
person hits them:

1. **A scheme is required.** `music.example.org` is refused with the fix written out
   (`https://music.example.org`). A bare host does not say whether to speak TLS.
2. **https is preferred; http is accepted and flagged.** The origin carries `secure` and, when
   false, a `warning` the window can show. A LAN or tailnet address often has no certificate;
   refusing it would be a policy this card did not ask for.
3. **Exactly one trailing slash.** Accepted with none, one or several; normalised to one, so
   `https://host`, `https://host/` and `https://host///` all become `https://host/`.
4. **An address that is this computer is refused.** Loopback (`127.0.0.0/8`, `localhost`, `::1`),
   a listen-anywhere bind (`0.0.0.0`, `::`), the local server's own resolved host, or this
   machine's own name via `gethostname`/`getfqdn` (no DNS lookup, no socket). A "remote" address
   that resolves to the machine the window runs on is the local server with extra steps.

The origin is an origin: no path (`https://host/library` is refused), no query string, no
`#fragment`, no credentials. Ports are optional and must be 1–65535. IPv6 hosts are bracketed in
the URL.

## The persistence format

`mode.json`, beside `host.json` in the same XDG state directory
(`$XDG_STATE_HOME/hardlylistening-desktop/mode.json`), overridden by
`HARDLYLISTENING_DESKTOP_MODE`. It is a separate file on purpose: `host.json` holds the address
the local server lives at and belongs to HOST-005, so a settings write cannot lose these choices
and this file cannot lose that address.

```json
{
  "version": 1,
  "run_local_server": true,
  "target": "remote",
  "remote_origin": "https://music.example.org:8443/"
}
```

Reading is forgiving in the same way `resolve.read_config` is: a missing file is the defaults, a
malformed file is reported and ignored, an unknown `target` or a non-boolean
`run_local_server` is reported and replaced by its default, and a file written for another
`version` falls back to the defaults with a note. The one asymmetry that is *not* a fallback: an
unusable stored `remote_origin` leaves `target` at `remote` with no address and reports why. The
window then has nowhere to talk to and says so, instead of quietly talking to the local server
the person did not choose.

## Wiring it into the page

Four insertions, none of which changes a decision — the page paints, `mode.js` decides:

| file | anchor | what goes there |
| --- | --- | --- |
| `desktop/ui/index.html` | `<script src="state.js"></script>` | `<script src="mode.js"></script>` after it, before `app.js` |
| `desktop/ui/index.html` | before `<div class="columns">` | one `<section class="card mode">` holding `#mode-local` (checkbox, `role="switch"`), the two `name="mode-target"` radios (`#mode-target-local`, `#mode-target-remote`), `#mode-origin` (`type="url"`), `#mode-local-hint`, `#mode-target-hint`, `#mode-problem` |
| `desktop/ui/app.js` | `els`, `render`, `boot` and one handler per control | `Mode.controls(mode)` painted; `Mode.setLocalServer` / `Mode.setTarget` on change; `Mode.fromPayload` / `toPayload` around the store |
| `desktop/ui/style.css` | end of file | the `.card.mode` rules: the switch and the radios are two groups, not one segmented control |

Three rules the handlers must keep, because they are the card:

* the local switch's handler calls `Mode.setLocalServer(mode, checked)` and nothing else;
* the radios' handler calls `Mode.setTarget(mode, 'local' | 'remote')` and nothing else;
* typing an address calls `Mode.setTarget(mode, mode.target, origin)` — the target it already
  has — so entering a remote address does not switch the window to it, and clearing the field
  passes `null`.

`app.js` currently holds the choices in `window.localStorage` under
`hardlylistening.desktop.mode`, with exactly the payload above. A Tauri command that writes
`mode.json` would replace those two calls; **nothing in `desktop/src-tauri` was touched by this
card**, and `server_status` still probes the local address only, so a remote target is a choice
the window records and displays before it is a server the window polls. That gap is a change to
the crate, not to this model.

## Running the proof

```sh
python3 -m unittest tests.test_host_mode      # 43 tests: matrix, non-couplings, origin rule, file
node tests/test_host_mode.cjs                 # 94 checks: the same, in the shipped window module
```

## What this does not prove

* **The window does not paint these controls yet.** `index.html`, `app.js` and `style.css` are
  untouched by this card; the insertion points are in *Wiring it into the page* above.
  `mode.js` is loaded by the Node test and by nothing else. The snippet from that section was
  run against a stub DOM (boot, click each control, type an address) before being written down,
  but that stub is not in the repository and proves the wiring, not the page.
* **No WebView ran.** The module is driven in a `vm` context, not in the OS WebView, and CSP,
  `withGlobalTauri` and the `invoke` bridge remain unexecuted, as they were for HOST-001.
* **Nothing writes `mode.json` yet.** The format, its round trip and its read rules are tested;
  the Tauri command that would save it is not part of this card, and the Rust side cannot be
  compiled here.
* **`gethostname`/`getfqdn` were exercised on one machine.** The own-name rule is tested with
  injected names; a name served by mDNS that neither reports is not detected.