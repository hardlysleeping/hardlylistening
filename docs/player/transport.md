# Transport: who owns the address and the credential

Two transports implement one interface (`player/ui/src/lib/subsonic/transport.ts`), and the
caller never learns which answered:

| | `WebTransport` | `NativeTransport` |
|---|---|---|
| who builds the URL | the page | Rust (`subsonic_url.rs`) |
| where the secret is | the page, for the length of a request | the keychain; read per request, never returned |
| what the page may state | an endpoint name + its parameters | an account id, an endpoint name + its parameters |
| CORS | the server must allow the page's origin | irrelevant: the request is Rust's |
| used when | a browser build, or a shell too old to have the commands | the shell has `subsonic_bind` |

`Session` picks the native path whenever `invoke` exists and `subsonic_bind` answers, and falls
back to `fetch` when the command is absent. The fallback is deliberate: a build that predates the
commands should still reach a server rather than lock a person out of their own music. It is also
the only path on which the page reads the secret back out of the vault.

## The three checks that make the native path a boundary

1. **The origin is pinned.** `subsonic_bind(accountId, origin, username, form)` validates the
   address once (`subsonic_url::normalise_origin`) and keeps it in a `Registry` keyed by account
   id. `subsonic_request` takes no host, no scheme and no path: it looks the origin up. There is
   no argument through which a page could point the credential elsewhere.
2. **The endpoint is a name.** `^[A-Za-z][A-Za-z0-9]{0,63}$` — checked on both sides. `../admin`,
   `getAlbumList2.view?x=1` and `https://elsewhere/rest/ping` are refused before a request is made.
3. **The protocol's parameters are ours.** `u`, `p`, `t`, `s`, `v`, `c`, `f` are written by Rust;
   a caller that tries to set one is refused. A caller that could set `u` could choose whose
   credential is used.

The token is `md5(secret + salt)` with a fresh 16-byte salt per request, from the OS random source.
The password itself never appears in a URL for the token form.

## What still crosses to the page, and when it stops

`subsonic_source` returns a **stream URL**, and a Subsonic stream URL carries its authentication
in the query string because an `<audio>` element cannot send headers. So for as long as the web
audio engine plays, one request-scoped credential — `u` plus either `p` or a salted `t` — is in
the page.

That is P2's known remainder, not an oversight. A platform player streams from Rust and
`subsonic_source` stops being called by the page at all — but no build has one yet: P3 delivered
the seam and the contract, and `native_audio_available` answers `false` (see
`docs/player/native-audio.md`). So on every build that exists today the honest statement is the
one above: the password stays in the keychain, and one derived request credential reaches the
page.

## Decisions taken in P2, and their cost if wrong

- **Rust 1.90**, not 1.77.2. `tauri-plugin-http` 2.8/2.9 declare `rust-version = "1.90"` and
  edition 2024, and the local toolchain is 1.99.1. The declared minimum is what a person building
  from source needs; a downloaded binary does not care. Cost if wrong: an older toolchain cannot
  build the shell.
- **No `tauri-plugin-http`.** It was the obvious route and it was rejected: its reason to exist is
  handing a page a general-purpose `fetch`, which is the one capability this player must not have.
  `reqwest` is used directly, from `subsonic.rs` only. Cost if wrong: platform TLS roots may need
  `native-tls` or a platform verifier on Android, a one-dependency change found at the first
  device build.
- **Android Keystore is not in P2.** It belongs with the mobile builds it can only be verified on
  (P3), and adding an unverifiable JNI path here would hide more than it fixes. Cost if wrong: an
  Android build still falls back to the in-memory vault that `accounts.rs` documents, so a
  password is forgotten when the app closes.
- **A failed sign-in keeps an existing account's secret.** Only a brand-new account's secret is
  removed when the attempt fails. Cost if wrong: a mistyped password stays in the vault for an
  account that was already saved, and the person signs in again — better than erasing a working
  password because of a typo.

## The one line without which none of this runs

`tauri.conf.json` sets `app.withGlobalTauri: true`. Tauri 2 does not expose `window.__TAURI__`
unless it is asked to, and both `vault.ts` and `transport.ts` find `invoke` there. Without the
flag the shell would silently take the `fetch` fallback — working, but with none of P2's
properties — which is the kind of failure that looks like success.

## What is verified, and what is not

Verified on this machine:

- `player/ui`: 62 vitest tests, including the native transport's argument shape (`transport.test.ts`)
  and the session's paths (`session.test.ts`); `svelte-check` 0/0; `vite build`; the three-surface
  visual parity harness.
- `subsonic_url.rs`, compiled and run **standalone** in a throwaway crate
  (`#[path]`-included, `md-5` + `getrandom`): 9 tests covering origin validation, endpoint and
  parameter refusal, the token (checked against `md5("hunter2abc")`), encoding, the registry, and
  salt freshness. Three guards were also mutation-checked: allowing reserved parameters, loosening
  the endpoint rule and sending the secret in the token slot each made a test fail.
- `cargo fetch` resolves the whole dependency set; `rustfmt --check` passes on all five `.rs` files.

Not verified, and cannot be here:

- **`subsonic.rs` has never been compiled.** `cargo check` stops at `glib-sys`: Bazzite has no
  `glib-2.0`/`webkit2gtk-4.1`/`gtk+-3.0`/`libsoup-3.0` development packages. The file is glue —
  read the bound record, call `reqwest`, shape the answer — and the risky part was moved into
  `subsonic_url.rs` precisely so it could be tested here.
- Whether `reqwest`'s `rustls-tls-native-roots` finds the platform's roots on iOS and Android.
- Whether Tauri's `rename_all = "camelCase"` argument mapping behaves as expected for
  `accountId` → `account_id` (the web side already sends camelCase; the attribute makes the intent
  explicit rather than relying on the default).