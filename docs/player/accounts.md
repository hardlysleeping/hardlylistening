# Accounts: many servers, one active, and where the secret goes

> **Partly retired in P4 (2026-11-14).** `player/shell/accounts.js` is deleted; the account model
> now lives in `player/ui/src/lib/accounts/vault.ts` (the seam the page holds) and
> `player/ui/src/lib/app/session.svelte.ts` (the active server), against the same
> `player/src-tauri/src/accounts.rs` described here — the keychain half is unchanged and still
> correct. The record shape, the "the list is public, the secret is not" split, and the
> keep-a-saved-secret-on-a-failed-retry rule all still hold; only the file that holds the list has
> moved. `tests/test_player_accounts.cjs` was the shell's node half and is deleted as well: its
> rules are covered by `player/ui/src/lib/app/session.test.ts` (a failed sign-in keeps a saved
> account's secret, binding never reads the secret back) and `subsonic/auth.test.ts` (md5, token
> form). Nothing tests the old file any more, and nothing should.

Card: **PLAYER-002**. PLAYER-001 held one session in the connect page's memory, asked again
after every restart, and named this as the gap. This is the gap closed: an account list that
survives, an active server that can be switched without losing the others, and a secret that
goes to the operating system's keychain instead of to a file.

Two files, and the split between them is the point:

| file | what it owns | what it must never hold |
| --- | --- | --- |
| `player/shell/accounts.js` | the account list, the active server, the auth form, migration, redaction | a secret, once the vault has taken it |
| `player/src-tauri/src/accounts.rs` | one secret per account id, in the OS keychain | the list, the active server, anything it would have to write to a file |

`player/shell/` is the whole allow-list (`assemble.mjs` reads the directory), so `accounts.js`
is in the built tree the moment it exists — asserted by the tests, not by a line to remember.

## The account record

An account is public data: it says nothing a person's own window does not already show.

```js
{ id, server, username, label, form, added }
```

* `server` is the **origin**: scheme, host and an optional path — the part before `/rest`.
  Normalising drops trailing slashes and refuses a pasted `/rest`. An address that carries
  credentials (`https://user:pass@host`) is **refused, not stripped**: that URL is exactly what
  this card keeps out of the tree, the log and the config file, and quietly deleting the
  userinfo would hide that somebody pasted it.
* `id` is derived from `server` + `username` (two FNV-1a passes, `acct_<hex>`), not a counter.
  Signing in again updates that account rather than adding a copy, and the keychain entry is the
  same one. A real collision — same id, different pair — is refused rather than overwritten.
* `label` is what the list shows. It defaults to the address without its scheme.
* `form` is **recorded, never chosen**: `add()` demands the form the server accepted and refuses
  an account that claims any other. A credential stored in the wrong shape is a login that
  fails later, somewhere that explains nothing.
* `added` is the first time that pair was added; signing in again keeps it.

Because the record holds no secret, a record outlives a sign-out: `signOut()` unbinds the native
transport, detaches the library and clears the active id, and the record stays. The connect
screen therefore lists the saved servers as rows and reconnects to one with `activate(id)` and
no typing — the keychain still holds the secret. It also prefills its form from the newest
record, for the case where the keychain does *not* have it (a re-signed build, a platform with no
backend): `activate` answers "That account's password is gone. Enter it again to reconnect"
rather than failing silently. Switching between two saved servers was walked against the stub —
two rows, a tap on either lands on that server's library.

`forget(id)` is the other direction, and it used to have no caller: a saved server could be added
and never removed, so a typo that once pinged stayed in the list for good. The row's `×` now calls
it — `vault.forget`, `unbind`, the record dropped, and the session cleared if it was the active
one — behind the same two-tap ask as sign-out. Walked at 1200×860 and 390×844: the confirming
label fits the row's fixed width without reflowing, and the forgotten row is still gone after a
restart, because the list write is the same one every other change goes through.

`list()` and `serialise()` return copies of these fields and nothing else. `render()` is the
text the UI shows, built from the same record, and `restore()` reads a saved list through an
allow-list — a `password` key that found its way into a config file is dropped and *named* in
the result, never echoed.

## The three auth forms

A server accepts one of three things, and the player records which:

| form | what the server accepted | what goes on the request |
| --- | --- | --- |
| `password` | the username and password of an account it knows | `u=`, `p=` |
| `token` | `t = md5(password + salt)`, for a server that kept the secret (`token_secret`) | `u=`, `t=`, `s=` |
| `device-invite` | a credential issued once by `POST /api/listening/devices` (`{username, password}`), revocable by the server | `u=`, `p=` |

`authParams(account, secret, salt)` builds the throwaway parameter object for the recorded form
and refuses a form it does not know. Token auth is why `accounts.js` carries an md5: the OS
WebView offers SHA-1/SHA-2 through `crypto.subtle` and no md5 at all, so the implementation is
in the file (RFC 1321 over the UTF-8 bytes) and is checked against the RFC's own vectors in
`player/ui/src/lib/subsonic/auth.test.ts` rather than against itself.

A device invite's username is the identity the server printed (`hl_…`) and its password cannot
be typed from memory, so it is the account that most needs the keychain.

## The secret

The vault is a dependency the page is given, never one it creates:

```js
const store = PlayerAccounts.open({ vault: PlayerAccounts.memoryVault() });
```

Two implementations ship:

* `keychainVault(invoke)` — the Tauri commands `accounts_put`, `accounts_secret`,
  `accounts_forget`, `accounts_mode`. Nothing is cached on the page side: every read crosses
  the bridge, so a secret does not outlive the request in this page's state. `mode()` starts at
  `"pending"` and only says `"keychain"` after the Rust side has answered; a probe that throws
  degrades to the in-memory vault instead of failing the window.
* `memoryVault()` — the honest fallback. `mode()` is `"memory"` and `persists` is `false`, so a
  person can be told the password is forgotten when the window closes, instead of finding out
  at the next launch. Nothing in it touches the filesystem.

`add()` stores the secret first and adds the account only if that worked: a list entry whose
secret went nowhere is a login that fails with nothing to explain it. `remove()` forgets the
secret first and keeps the entry if the vault refuses, so the list never claims to have
forgotten something a keychain still holds.

### P2: the secret's journey changed

The page (now `player/ui`, `src/lib/accounts/vault.ts` + `src/lib/app/session.svelte.ts`) still
owns the list and the vault, but on a shell that has the `subsonic_*` commands it no longer reads
the secret back: `subsonic_bind` hands Rust the public record, and Rust reads the secret from the
same keychain by account id. `accounts_secret` remains for the `fetch` fallback that a browser or
an older build uses, and that fallback is the only path on which the page asks for it.

Two things this does not yet include, stated plainly rather than implied: **Android has no
Keystore backend** — the same JNI-shaped gap as before, deferred to P3 where a device build can
verify it, so an Android build still lands on the in-memory vault — and a stream URL still carries
a request-scoped credential to the page until P3's native audio engine plays from Rust.

The memory fallback is why a machine without a keyring still runs — a headless Linux session
with no `gnome-keyring` or `kwalletd`, a container, a plain SSH session. It is also the reason
`accounts_mode` exists at all: the shell has to be able to say *which* store is in use.

What neither store does, and this is the whole card: **no plaintext password is written to
disk.** The list may live in a config file because it holds no secret; the secret may live in
the keychain because that is a store built for one; and the only remaining option, a file the
player wrote itself, is refused outright. `add()` fails rather than fall back to it.

### What the rule means in practice

* **not in the list** — `list()`, `serialise()` and `render()` are built from the public record;
  the tests assert the fixture secret is absent from all three.
* **not in a log line** — `store.log()` routes every line through `redact()`, which removes the
  values this page has handled (longest first) and then the protocol's own parameter values
  (`p`, `enc`, `t`, `s`, `password`, `token`, `secret`, `api_key`). `lock()` drops the copy kept
  for that purpose; the vault is untouched.
* **not in a URL** — an account's address refuses credentials, and a credential-bearing URL that
  is logged is scrubbed to `p=***` / `t=***` even when this page never held the value. The
  protocol itself puts the password on the request (there is no other way to say it), which is
  why the address should be `https://`.

## Migration from PLAYER-001

`store.migrate(session)` takes the shape `connect.js` already produces —
`{server: {url, …}, credentials: {username, password}}` — and turns it into an ordinary
`password` account (that is what the ping used), storing the password in the vault and returning
`migratedFrom: "PLAYER-001"`. A page that has a session when this card lands therefore ends up
with one account, and nothing about it is special afterwards. An empty or partial session is
refused with a message rather than creating half an account.

`connect.js` is untouched by this card: it still keeps the session in memory for the page's
lifetime. Pointing the form at `store.add()` (and the saved list at `store.restore()`) is the
wiring below.

## WIRING

`Cargo.toml` and `lib.rs` are shared files, so nothing here edits them. In order:

**1. `player/src-tauri/Cargo.toml`** — after the `tauri = { version = "2", features = [] }`
line, add:

```toml
# The keychain, per platform: the Keychain on macOS and iOS, the Credential Manager on Windows,
# the Secret Service (through dbus) on Linux. Android has no backend in this crate, so it lands
# on the in-memory fallback that `accounts.rs` documents.
keyring = { version = "3", default-features = false, features = ["apple-native", "windows-native", "sync-secret-service"] }
```

**2. `player/src-tauri/src/lib.rs`** — after the module doc comment, before `pub fn run()`:

```rust
mod accounts;
```

**3. `player/src-tauri/src/lib.rs`** — the builder becomes:

```rust
    tauri::Builder::default()
        .manage(accounts::Vault::open())
        .invoke_handler(tauri::generate_handler![
            accounts::accounts_mode,
            accounts::accounts_put,
            accounts::accounts_secret,
            accounts::accounts_forget,
            accounts::accounts_present,
        ])
        .run(tauri::generate_context!())
        .expect("the player shell could not start");
```

**4. `player/shell/connect.html`** — one line beside the screen's own script, so the model is
there before anyone presses Connect:

```html
<script src="./accounts.js"></script>
```

**5. `player/shell/connect.js`** — on a successful `connect()` (where `session` is set), the
session becomes an account. `invoke` only exists once the capability allows the command, so the
call is guarded and a browser tab simply gets the memory vault:

```js
  const invoke = root.__TAURI__ && root.__TAURI__.core && root.__TAURI__.core.invoke;
  const accounts = PlayerAccounts.open({ vault: PlayerAccounts.keychainVault(invoke) });
```

and in the success branch of the submit handler:

```js
      const moved = await accounts.migrate(session);
      if (!moved.ok) say(moved.error, "err");
```

The saved list is a file, so **step 6** is the card that adds the fs permission and a config
path. Until then the store starts with `memoryVault()` in a browser and with the keychain (or
its honest memory fallback) in the shell, and it says which — which is what PLAYER-001 did,
minus the promise.

**6. `player/src-tauri/capabilities/default.json`** — nothing to add for the keychain, because a
Tauri command is not a plugin permission. The *list* needs either an `fs` permission for one
config path or a small `accounts_config` command beside the four above; that is the piece this
card leaves to the shell's own storage card, and it is why the list is written by
`serialise()`/`restore()` rather than by the model itself.

## What is verified, and what is still a claim

The host is Bazzite (immutable) with no `webkit2gtk-4.1`, `gtk+-3.0` or `libsoup-3.0`
development packages and no `cargo-tauri`, so nothing is built on the host itself. It is built
and run in the `huibuild` distrobox, which has those packages, against a stub server.

Measured there:

* **The crate links.** `cargo build --release --features tauri/custom-protocol` resolves
  `keyring = "3"` with `default-features = false` and `apple-native`, `windows-native`,
  `sync-secret-service` exactly as written — no `crypto-*` feature was needed for the session
  collection, and `rustfmt --check` accepts `accounts.rs`.
* **Both stores, by running them.** With the container's normal D-Bus (it shares the host bus)
  the connect screen prints "The password goes to the system keychain, and only there." With
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent-bus` the same build prints "This machine has
  no keychain this build can reach, so the password is kept in memory for this session only — it
  is never written to disk." That sentence is not a guess about the platform: `accounts_mode`
  asks `Vault::mode()`, and `accounts_put` answers with the mode the write actually used, so the
  screen follows the store rather than the hope.
* The session rules run under vitest against a stubbed vault
  (`player/ui/src/lib/app/session.test.ts`, 7 tests: transport choice, the failed sign-in that
  keeps a saved secret, a bind that never reads the secret back, the token→password rebind, and
  the store the screen reports).
* The commands the page names are the commands the Rust module declares.

Still claims until a device proves them:

1. **The in-memory branch keeps the password for the session.** It is exercised as a store, but
   what a person experiences after a restart — sign in again — is only inferable.
2. **iOS and Android.** `apple-native` is assumed to cover iOS; Android has no backend in
   `keyring`, so it must fall back to memory. Both are claims about a crate that has not been
   compiled for either target, and the fallback on a phone is what the screen would say.
3. **Re-signing.** A macOS/iOS build that changes its code signature loses the Keychain's trust
   in the entry, which shows up as an empty read, not an error. The `get` path treats that as
   "nothing stored", which is the right behaviour and an untested one.
