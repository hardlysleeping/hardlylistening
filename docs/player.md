# The player

Purpose: a player for music you already have on a server. It is a **Tauri 2** application —
Rust core, the operating system's own WebView, no bundled Chromium and no Electron — that
speaks **Subsonic / OpenSubsonic** to whatever server a person points it at. It is a client
and only a client: it never runs this app's Python backend, it ships no acquisition code, and
it does not need the desktop host in `desktop/`. The two shells are separately distributable
and share only the UI they assemble from `web/`.

Card: **PLAYER-001** — the shell and its connect screen. Everything past the handshake has a
card of its own, listed at the end.

## Layout

```
player/
  assemble.mjs          the build: an explicit allow-list, copied byte for byte
  shell/
    connect.html        the window's first page
    connect.css         the page frame; colours and controls come from web/styles
    connect.js          address + envelope + Subsonic error codes, and the in-memory session
  src-tauri/
    Cargo.toml          tauri 2, one library crate for desktop/iOS/Android
    build.rs            tauri_build
    tauri.conf.json     the window, ../www as the front end, desktop + iOS + Android bundle
    capabilities/default.json   core permissions only: no fs, shell or HTTP plugin
    src/lib.rs          the builder, with the mobile entry point
    src/main.rs         the desktop binary
  www/                  ASSEMBLED, GITIGNORED. Do not edit; it is rebuilt every time
```

`www/` is not a second source tree. It is what `assemble.mjs` made, and a build starts by
replacing it.

## What the player bundles

`player/assemble.mjs` holds two lists — `WEB_ALLOW` (files taken from `web/`) and
`SHELL_ALLOW` (the three files above from `player/shell/`) — and those lists are the single
statement of what a player contains. Today that is:

| from `web/` | why |
| --- | --- |
| `index.html` | the app's markup, unchanged |
| `preferences.js`, `genres.js`, `lyrics.js`, `lifecycle.js` | the classic scripts that markup loads |
| `js/library.js`, `js/playback-queue.js`, `js/social-feed.js` | the library, the queue, the activity feed |
| `styles/app.css` and every sheet it imports (`tokens`, `base`, `layout`, `motion`, `utilities`, `components/*`) | the app's visual language, so the gate looks like the app |

Three consequences worth stating plainly:

* **Acquisition is excluded.** `web/js/repo-discovery.js` is not in `WEB_ALLOW`, and
  `EXCLUDED` names it with the reason, so the absence is a decision a reader can check.
  PLAYER-007 turns that into a store-safe assertion over the built tree — see
  [player/store-safe.md](player/store-safe.md) for what it proves and what it cannot.
* **The player is not a copy.** Files are copied and then hashed against their source; if a
  byte changed on the way in, the script fails. There is nothing to keep in sync by hand.
* **The entry document is the one file rewritten**, and only to drop references to `EXCLUDED`
  files — the acquisition module, the web-app manifest and icon the host serves, and the
  links to the Party, Mixer and Swipe pages the player does not carry. Each drop is recorded
  in the receipt with the exact text removed, so the difference from `web/index.html` is
  auditable rather than implied. `web/` itself is never edited.

Everything else under `web/` is deliberately outside: the mixer (`dj.html`, `party.*`), the
swipe page, `login.html`, `signup.html`, the offline page and `sw.js` (a service worker is a
browser-tab idea; the shell serves its own assets), and the web-only `app.webmanifest` /
icons that live at the repository root and are served by the Python host, not from `web/`.

### How a build decides, and how it fails

`node player/assemble.mjs` writes `player/www/`. Before it writes anything it refuses:

1. a listed file that is missing (`... are not there`);
2. a plan that would ship a forbidden path — `repo-discovery.js`, `src-tauri`,
   `node_modules`, `electron`, or any `.py` (`... must not contain`);
3. a document that references a file that is neither assembled nor excluded
   (`... point at files it does not contain`) — a reference to an excluded file is dropped and
   recorded, a reference to an unaccounted one is a mistake in the allow-list and stops the
   build;
4. an output directory that already holds files the script did not assemble (a `.assembly.json`
   receipt is written on every run, so a stray directory is never deleted);
5. an output path inside `web/`, or one that contains `web/`.

References are read from markup only: inline `<script>` bodies are code, not markup, and a
template in them that writes an `<a href>` is not a request the window makes. A route the host
serves from a file is read as that file (`/party` → `party.html`), which is how the excluded
pages are recognised in the navigation.

After copying it walks the finished tree once more for forbidden names, so the guard is about
bytes on disk rather than about the plan. `--out <dir>` writes elsewhere, `--list` prints the
plan as JSON (the tests read it), and `--include-web <rel>` / `--forget-web <rel>` exist only so
a test can prove the guards fire. No build uses them.

## Running it

Requirements beyond this repository: Rust (stable), the Tauri 2 CLI
(`cargo install tauri-cli --version "^2"`), and the WebView development packages for the
platform — on Linux `webkit2gtk-4.1`, `gtk+-3.0` and `libsoup-3.0`, per Tauri's prerequisites
page.

```sh
# development: the assembly runs first, then the window opens
cd player/src-tauri
cargo tauri dev

# a desktop build (appimage/deb on Linux, .app on macOS, .msi on Windows)
cargo tauri build
```

`beforeDevCommand`/`beforeBuildCommand` in `tauri.conf.json` call `node assemble.mjs`, so the
assembly is not a step a person can forget (Tauri runs them from `player/`). To look at the
connect screen without Rust, run the assembly and open `player/www/connect.html` in a browser
— it is plain HTML, CSS and one classic script, and it pings whatever address is typed.

`security.csp` is left at `null` on purpose. The assembled `index.html` is the app's own markup,
inline scripts included, so a CSP strict enough to be worth having would have to allow
`'unsafe-inline'` for it; scoping that belongs with PLAYER-003, when the shell knows which of
the app's pages it actually loads.

### iOS and Android

Tauri generates the platform projects; they are **not** committed (`gen/apple`,
`gen/android` are gitignored, and `src-tauri/gen/` is what the CLI produces):

```sh
cd player/src-tauri
cargo tauri icon path/to/1024.png     # icons are not in the tree yet
cargo tauri ios init                  # once: writes gen/apple
cargo tauri android init              # once: writes gen/android
cargo tauri ios dev                   # or: cargo tauri android dev
```

`bundle.iOS.minimumSystemVersion` is 13.0 and `bundle.android.minSdkVersion` is 24; those are
declared in `tauri.conf.json` and unverified here (see below). The window URL is
`connect.html`, which is the same relative page on all three platforms, so no platform gets
its own front end.

## The connect screen

When no server is configured the window shows one card: server address, username, password.
It calls `<server>/rest/ping.view?u=…&p=…&v=1.16.1&c=hardlylistening-player&f=json` and reads
the reply as the protocol defines it:

* a **`subsonic-response` with `status: "ok"`** is a success, and the screen reports the
  server's `type`, `serverVersion` and whether it is `openSubsonic`;
* an **`error` object** is reported by its code — 40 is wrong credentials, 30 is a protocol
  version the server will not speak, 10 a missing parameter — with the server's own message
  appended when it adds something;
* **anything else** (an HTML login page, a proxy error, JSON without the envelope) is refused
  as "answered, but not as a Subsonic server", because a person needs to know which of those
  happened.

The address is normalised only where normalising cannot be wrong: trailing slashes are
dropped, and a pasted `/rest` suffix is named rather than doubled. A missing scheme is
refused rather than guessed (`http` versus `https` is the person's choice).

**Credentials are held in memory for the session and nowhere else** — no `localStorage`, no
IndexedDB, no cookie, no file. After a restart the screen asks again. That is PLAYER-002
(keychain on each platform), and until it exists a plaintext-on-disk shortcut is explicitly
not taken. The password goes to the server as Subsonic's plain `p=` parameter, so the address
should be `https://`; token auth (`t`/`s`, md5) is not built.

## Not here yet

| card | what it adds |
| --- | --- |
| PLAYER-002 | accounts: remembered credentials in each platform's keychain |
| PLAYER-003 | library UI: the assembled `index.html` routed to once a server is chosen |
| PLAYER-004 | playback surface: transport, queue, background audio |
| PLAYER-005 | offline: downloads and a cache |
| PLAYER-006 | social: the activity feed against a server that offers it |
| PLAYER-007 | the store-safe assertion, enforced over the built tree |

### What is unverified, and why

This machine is Bazzite (immutable) with **no `webkit2gtk-4.1`, `gtk+-3.0` or `libsoup-3.0`
development packages and no `cargo-tauri`**, so the Rust crate and the Tauri configuration
have not been compiled, and no window has been opened. What is proven here is what can be
proven without them: the JSON files parse and describe the shapes Tauri 2 reads,
`rustfmt --check` accepts the three `.rs` files, the assembly's output is real (the tests run
it into a temporary directory and walk it), and the connect screen's logic runs under node
with `fetch` stubbed. Compilation, `gen/apple`/`gen/android` generation, and a real WebView
therefore need a machine with the WebView packages or a CI runner.

Two known gaps that only a real build will settle: the shell fetches a server from the
WebView's own origin, so a Subsonic server that sends no CORS headers may refuse the ping
(the `tauri-plugin-http` route is the fallback, and it is PLAYER-002's decision along with
the keychain), and iOS ATS needs an exception before a plain `http://` LAN server answers.
The entry document's references are now resolved (nothing it asks for is absent), but its
inline scripts still call the Python host's `/api/…` endpoints; inside the player those
requests have no server until PLAYER-003 points the library at the Subsonic endpoints, and
PLAYER-007 must assert the store-safe exclusion over the built tree rather than trust the
allow-list alone.