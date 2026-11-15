# Which address a person can actually use (HOST-006)

Status: **detector and renderer built and tested; not yet wired into the window.** The decision
lives in `desktop/host/addresses.py`, the one line of truth in `desktop/ui/address.js`, and the
two are joined by the wiring listed at the end. Until that wiring exists the window shows the
address it polls (`127.0.0.1:8470`) and nothing about reachability — which is what it did before
this card.

The card is *"show a usable address when Tailscale already provides one; do not manage
Tailscale"*. Two thirds of the work is in the parts that read like a warning label:

> A machine has a LAN address whether or not anything is listening on it, and a Tailscale
> address whether or not the person wants one. Neither is an answer to "how do I open this from
> my phone?" unless the server is actually listening where that address points.

So the module answers one question — *what can be typed on another device to reach this
server?* — and it answers it with `None` more often than a feature request would like.

## What is read, and what is never done

| read | how | cost |
| --- | --- | --- |
| this machine's addresses | `socket.if_nameindex()` + `SIOCGIFADDR` per interface, plus a UDP `connect` to `203.0.113.1`/`2001:db8::1` (no packet is sent; the kernel only fills in the source address) | no privilege, no network |
| Tailscale's own view | `tailscale status --json`, read once | one process, 2 s ceiling |
| the bind | the host the resolver already produced (`resolve.Address.host`) | nothing |

Never: `tailscale up`, `tailscale down`, `tailscale login`, `tailscale set`, `tailscale serve`,
a config write, a preference, a route, a firewall rule, a listener. The command constant is
`('tailscale', 'status', '--json')`, the default reader refuses any other argv, and
`tests/test_host_addresses.py` asserts both. The test suite always injects a reader, so no test
and no CI machine runs the binary.

## States, not exceptions

"Fail soft and fast" is four reported states plus one deliberate silence:

| `tailscale` | means | what the window does |
| --- | --- | --- |
| `up` | daemon running, `BackendState: Running`, has a name and/or address | offers the name, then the literal |
| `down` | non-zero exit, or `NeedsLogin` / `Stopped` / `NoState` / `Starting` | falls through to the LAN address |
| `absent` | `FileNotFoundError` — no binary on this machine | falls through, note says so |
| `unknown` | timeout, output that is not JSON, or a payload with no state | falls through, note says *could not be asked* — not "down" |
| `unread` | the bind made the question pointless, so no command ran | nothing; the note explains reachability |

`unread` exists so that a window bound to `127.0.0.1` paints immediately on a machine where
Tailscale is installed but wedged, and so that the common case costs zero subprocesses.

## Precedence

For a server bound to every interface (`0.0.0.0`, `::`, `*`, or no host):

| # | offer | kind | why it is here |
| --- | --- | --- | --- |
| 1 | the bound address itself, when concrete | `bind` | it is what was asked for; nothing else can be true of it |
| 2 | the Tailscale MagicDNS name, when Tailscale is up | `magicdns` | survives the address changing, which is what a re-keyed device does |
| 3 | the Tailscale `100.x` address, when up but nameless | `tailscale` | reaches every device already signed in, on the same wifi too |
| 4 | this machine's own LAN address, IPv4 first | `lan` | the answer when there is no tailnet — and it must not need one |
| 5 | — | — | `primary is None`, plus the sentence that explains it |

Tailscale outranks the LAN address because it works from more places; the LAN address is the
fallback that must keep working on a machine that has never installed Tailscale.

## The bind decides, the interface list only supplies candidates

| bind | answer |
| --- | --- |
| `127.0.0.1`, `::1`, `localhost` | **no offer at all**, `note`: listening on this computer only. Not `127.0.0.1` dressed up as shareable, and not the LAN address the machine happens to have |
| `0.0.0.0`, `::`, `*`, empty | the precedence above |
| anything else | that address, `kind: bind` — and no probe is run, because a second address would belong to a server that is not listening on it |

An interface address is offered only when it is a route from another device: loopback,
link-local (`169.254/16`, `fe80::/10`), carrier NAT (`100.64/10` on a non-Tailscale interface)
and container/VPN interfaces (`docker0`, `br-*`, `veth*`, `virbr*`, `podman*`, `tun*`, `wg*` …)
are classified and skipped. A machine running Docker has a dozen private addresses that answer
`ip addr` exactly like a network card does; handing `172.17.0.1` to a phone is a bug, so the
classification exists even though nothing prints it.

## The window's half

`desktop/ui/address.js` renders the dictionary `addresses.py` produces — it does not compute
precedence, so the rule exists once. It formats the URL (IPv6 brackets, zone ids dropped, the
shell's port), labels the kind of address, carries the reason, and counts the alternates.

Its rule is suspicion: no primary, an unrecognised `kind`, a host with whitespace, a loopback or
listen-anywhere host wearing a `lan` label, or no answer at all → **silence**. Those are the
states a half-finished boot actually produces (the command failed, the server restarted, the
detector was not bundled), and a window that invents a plausible address is worse than one that
says nothing.

## What is proven, and how

```sh
python3 -m unittest tests.test_host_addresses     # 39 tests: precedence, states, silence
node tests/test_host_address.cjs                  # 44 checks: labels, URLs, silence
python3 -m host.addresses 0.0.0.0                 # what this machine would show (from desktop/)
```

Fixtures cover `tailscale status --json` with and without `Self`, `NeedsLogin`, a daemon that is
down, a binary that is absent, a timeout, non-JSON output, IPv6-only machines, multiple
interfaces, and the same address on two interfaces. No fixture is a real subprocess.

Unverified, plainly:

* **The wired window.** Nothing in `desktop/ui/index.html`, `app.js` or the crate calls either
  file yet — see *WIRING*. Until then the code is proven but the window is unchanged.
* **A bundled build.** The detector is Python, and the crate cannot import it. A bundle only
  gets an answer if `desktop/host/addresses.py` is shipped as a resource and `python3` is present
  (it must be anyway: the launcher execs it). That packaging step is not done, and the resource
  path mapping (`_up_` and friends) has not been measured on a built bundle.
* **Tailscale's JSON shape.** `BackendState`, `Self.DNSName` and `Self.TailscaleIPs` are used as
  documented and as observed once on a live machine; a future `tailscale` version that renames
  one of them lands in `unknown`, which is silent rather than wrong.
* **Non-Linux interface probing.** `SIOCGIFADDR` with `0x8915` is the Linux request; on macOS
  and Windows that ioctl fails and is skipped, leaving the routed address alone (with an empty
  interface name). A multi-homed macOS or Windows machine therefore shows one address, not all of
  them. Neither was measured.

## WIRING

The shared files need these edits; each is anchored to the text it replaces. Nothing in the
delivered files assumes them, so the window stays as it is until they are applied.

**1. `desktop/ui/index.html`** — load the renderer before `app.js`:

```html
 <script src="state.js"></script>
+<script src="address.js"></script>
 <script src="app.js"></script>
```

In the server card, after `<p class="hint" id="address-hint"></p>`, add the line and its reason:

```html
 <p class="hint" id="address-hint"></p>
+<p class="url" id="share-address" hidden></p>
+<p class="hint" id="share-hint" hidden></p>
```

In the existing `.actions` row, after the `#open` button:

```html
 <button id="open" type="button">Open in browser</button>
+<button id="copy-address" type="button" disabled>Copy address</button>
```

No CSS change: `.url`, `.hint` and `button` already exist, and `hidden` works on both elements
because neither has a `display` rule of its own.

**2. `desktop/ui/app.js`** — element references, after `config: $('fact-config')`:

```js
     config: $('fact-config'),
+    share: $('share-address'),
+    shareHint: $('share-hint'),
+    copy: $('copy-address')
```

A module-level answer beside `var polling = false;`:

```js
+  /* HOST-006: silence until the shell says otherwise. `address.js` refuses to render an
+   * answer it cannot place, so "nothing" is the correct initial state, not a guess. */
+  var share = { silent: true, url: '', copy: '', label: '', why: '', note: '', others: 0 };
```

A renderer beside `render()`:

```js
+  function renderShare() {
+    var shown = !share.silent && !!share.url;
+    els.share.hidden = !shown;
+    els.share.textContent = shown ? share.url : '';
+    els.shareHint.hidden = !shown && !share.note;
+    els.shareHint.textContent = shown
+      ? share.label + ' · ' + share.why + (share.others ? ' (' + share.others + ' more)' : '')
+      : share.note;
+    els.copy.disabled = !shown;
+    els.copy.title = shown ? 'Copy ' + share.url : 'No reachable address to copy';
+  }
```

At the end of `render()`, after `text(els.members, …)`:

```js
+    renderShare();
```

In `boot()`'s `host_env` handler, after `if (env.address) { state.address = … }`:

```js
+      /* The shell's detector decides; this only paints. `env.addresses` is the dictionary from
+       * `desktop/host/addresses.py`, and `env.address.port` is the port it is served on. */
+      share = HostAddress.describe(env.addresses, env.address && env.address.port);
```

A copy handler, beside the other `addEventListener` calls:

```js
+  els.copy.addEventListener('click', function () {
+    if (!share.copy) { return; }
+    var value = share.copy;
+    if (navigator.clipboard && navigator.clipboard.writeText) {
+      navigator.clipboard.writeText(value).then(function () {
+        dispatch({ type: 'notice', text: 'Copied ' + value + '.' });
+      }).catch(function (error) { explain(null, 'The address could not be copied: ' + error); });
+      return;
+    }
+    dispatch({ type: 'notice', text: value, kind: 'warn' });
+  });
```

**3. `desktop/src-tauri/src/lib.rs`** — one command that carries the detector's JSON. Anchor:
after `fn host_env(...) -> Value { … }` and before `/// One poll: are we holding a child…`:

```rust
+/// HOST-006: the address a person could type on another device, or the reason there is none.
+///
+/// The decision is `desktop/host/addresses.py`, which `tests/test_host_addresses.py` drives.
+/// This shell cannot import Python, but it must not grow a second precedence either: it runs
+/// that file and passes the JSON through. A bundle that did not ship the detector reports a
+/// miss, and the window then shows no address — the card's rule, not a failure.
+#[tauri::command]
+fn host_addresses(app: tauri::AppHandle) -> Value {
+    let script = match host::detector(&app) {
+        Some(path) => path,
+        None => {
+            return json!({
+                "primary": Value::Null,
+                "others": [],
+                "note": "The address detector was not bundled, so no reachable address can be shown.",
+            })
+        }
+    };
+    let address = host::address().map(|value| value.host).unwrap_or_default();
+    let output = Command::new("python3")
+        .arg(&script)
+        .arg(&address)
+        .stdin(Stdio::null())
+        .stderr(Stdio::null())
+        .output();
+    match output {
+        Ok(done) if done.status.success() => {
+            serde_json::from_slice(&done.stdout).unwrap_or_else(|error| {
+                json!({ "primary": Value::Null, "others": [],
+                        "note": format!("The address detector printed nothing usable: {error}") })
+            })
+        }
+        Ok(done) => json!({ "primary": Value::Null, "others": [],
+                            "note": format!("The address detector exited {}", done.status) }),
+        Err(error) => json!({ "primary": Value::Null, "others": [],
+                              "note": format!("The address detector could not run: {error}") }),
+    }
+}
```

and register it in the handler list, after `host_env,`:

```rust
             host_env,
+            host_addresses,
             server_status,
```

**4. `desktop/src-tauri/src/host.rs`** — the detector's path, in the same spirit as
`launcher()`: an environment override, the bundle, then the checkout. Anchor: after
`pub fn launcher(app: &tauri::AppHandle) -> Option<PathBuf> { … }`:

```rust
+/// Where `addresses.py` lives: an override, the bundle's resources, then a development
+/// checkout. A miss is reported rather than guessed at — the window then says nothing.
+pub fn detector(app: &tauri::AppHandle) -> Option<PathBuf> {
+    use tauri::Manager;
+
+    if let Some(declared) = env_text("HARDLYLISTENING_DESKTOP_HOST") {
+        return Some(PathBuf::from(declared));
+    }
+    let mut roots = Vec::new();
+    if let Ok(dir) = app.path().resource_dir() {
+        roots.push(dir.join("host"));
+        roots.push(dir.join("desktop").join("host"));
+    }
+    if let Ok(exe) = std::env::current_exe() {
+        let mut dir = exe.parent().map(Path::to_path_buf);
+        for _ in 0..6 {
+            let Some(current) = dir else { break };
+            roots.push(current.join("desktop").join("host"));
+            roots.push(current.join("host"));
+            dir = current.parent().map(Path::to_path_buf);
+        }
+    }
+    roots
+        .into_iter()
+        .map(|root| root.join("addresses.py"))
+        .find(|candidate| candidate.is_file())
+}
```

**5. `desktop/src-tauri/tauri.conf.json`** — ship the detector so a bundle can answer at all.
Anchor: `"bundle": {`:

```json
     "externalBin": ["bin/hardlylistening-server"],
+    "resources": { "../host/addresses.py": "host/addresses.py" },
```

The mapped path is what `host::detector` looks for as `resource_dir()/host/addresses.py`; the
resource mapping has not been measured on a built bundle (see the top of this document).

**6. `desktop/host/resolve.py`** — no change. `address()` already returns the bind host, and
`detect(host)` takes exactly that string; the two modules answer different questions on purpose.

**7. `desktop/README.md`** — the "LAN / Tailscale exposure" bullet under *What is deliberately
not here* becomes a pointer, and the test list gains the two new files:

```markdown
-* **LAN / Tailscale exposure** — HOST-005 and HOST-006. The window can point at a LAN address,
-  and that is all: it neither binds one nor manages a tailnet.
+* **LAN / Tailscale exposure** — HOST-005 binds one; HOST-006 *reports* one. The window reads
+  `tailscale status --json` and the interfaces to say what is reachable, and never runs `up`,
+  `down`, `login` or `set`: the tailnet is not its to manage. See `docs/desktop/addresses.md`.
```

```markdown
 python3 -m unittest tests.test_desktop_host     # config, capabilities, imports, sidecar, resolver
+python3 -m unittest tests.test_host_addresses   # which address is reachable, and silence
 node tests/test_desktop_state.cjs               # ownership, health states, address formatting
+node tests/test_host_address.cjs                # the window's one line of truth
```