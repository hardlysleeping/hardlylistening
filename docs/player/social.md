# Social, only when the server has it

Purpose: the player talks Subsonic to whatever server it was given. Social is the one part of
this app that Subsonic has no protocol for — the activity feed, comments, votes and boosts are a
HardlyListening server's own tables, reached through its own `/api/v1/*`. So the player asks a
small question before anything else: **is there a feed here at all?** The answer comes from the
server's capability document, never from this app's source and never from a guess.

P6 ships the module and the seam, not a surface. `player/ui/src/lib/hardly/social.ts` is the
module; `Session.social()` is where a future view asks it. Nothing draws a feed today, so a
person sees no social surface at all rather than a disabled one.

## The decision, and why it is these three conditions

Activity is offered only when the connected server answers all three:

1. **It answers `GET /api/v1/capabilities`.** `httpd/gate.py` answers that path *before* the
   `/api/v1/` dispatch that authenticates, so a client holding no session can read it. A plain
   Subsonic server has no such address, so it 404s and the answer is "no social".
2. **The document names `features.social.activity`.** The domain is advertised at `httpd/gate.py`
   (`'social': {'comments': True, 'votes': True, 'likes': True, 'activity': True}`), and the
   SDK's own reading is `available = bool(details)` (`sdk/capabilities.py`) with the feed's flag
   named `activity` (`sdk/social.py`). A domain advertised without that flag is *not* a feed.
3. **The server does not require members.** `httpd/gate.py` reports
   `'requires_members': bool(SECURITY.required)`, and `security.py` is where that default comes
   from (`HARDLYLISTENING_AUTH_REQUIRED`, on unless switched off).

The third condition is the one that is easy to get wrong. `/api/v1/social/activity` reads through
`_reader`, which returns nobody **only** when the deployment has accounts off; every other social
route goes through `_member` and always authenticates. With members required, the feed answers
401 and wants `__Host-hl_session` — a cookie this player never holds, because the connect screen
keeps Subsonic credentials only. "The server has social" and "this player can read the feed" are
two different facts, and only the second one earns a screen.

## The four states

| state | when | what the caller gets |
| --- | --- | --- |
| `activity` | the domain is named, `activity` is on, members are not required | the feed, read from the versioned endpoint |
| `members` | the domain and `activity` are on, but the server requires members | the state, and a reason saying the feed is a member's read this player cannot make |
| `no-activity` | the domain is named but `activity` is not | the state, naming `social.activity` as the missing flag |
| `absent` | no document, an unreadable one, or no social domain in it | the state and a reason |

`absent` is not a failure state. It is the ordinary answer from a plain Subsonic server. Every
degradation — unreachable, 401/403/404/500, HTML instead of JSON, an empty document, a body that
is an array — lands in `absent` **with a reason**, and the reason names the address and the
status. A capability read that failed is not a capability. `probeSocial()` never throws, the same
rule `sdk.capabilities.probe` follows on the server side: a failed capability call must not stop
anything else from starting.

## The module

`player/ui/src/lib/hardly/social.ts`:

* `CAPABILITIES_PATH`, `ACTIVITY_PATH`, `ACTIVITY_LIMIT` — the two paths are the server's own
  constants (`sdk/capabilities.py`, `sdk/social.py`), and the default page size is the app's 80.
* `probeSocial(base, { fetch })` → `SocialAvailability` (`state`, `reason`, `origin`, `server`,
  `requiresMembers`). Reads only `server.requires_members` and `features.social`; nothing is
  inferred from the app's source or from a name.
* `readActivity(base, availability, { fetch, limit })` → typed rows: `BoostItem` or `DayItem`,
  normalised from the server's flat shapes (`member_id`/`member`/`member_icon`, `groups` →
  entries). It refuses **before the wire** when the state is not `activity` (`SocialUnavailable`,
  carrying the state and the reason), because an empty list and a missing feature look identical
  on screen and mean opposite things. A refusal from the server becomes `SocialDenied` with its
  status and, where there was one, its own sentence. An unknown `kind` is named, never dropped.

Both calls send `credentials: "omit"` and no query parameter other than `limit`. The capability
document and the feed are public reads; holding a member cookie is not this player's business.

`Session.social()` is the seam: it asks the probe about `active.server` through the session's own
`fetch`, and answers `null` when no account is connected. It is not called by anything yet, and
no probe runs at connect — the module is inert until a view asks.

## The writes are not implemented, and that is the whole refusal

Comments, votes, likes, boosts and member profiles are a member's reads and writes: each one
needs `__Host-hl_session` (`httpd/gate.py`), and this player has no member session. The old shell
answered those routes with 501s from a wrapper; the wrapper is gone, so there is nothing to
refuse — the client simply has no code for them, and `tests/test_player_store_safe.py` asserts
the build does not name them (`/api/boost`, `/api/social`, `/api/member`,
`/api/v1/social/comments`, `/api/v1/social/summary`).

## The store-safe exception, stated plainly

The player's build must name no `/api/…` path, with one exception: the two social **reads**,
derived at test time from the server's own constants and each checked to be live (the document is
answered by `httpd/gate.py`, the feed is in `api/v1.py`'s route table). Everything else stays
forbidden, including a social route outside that pair.

Measured on this build: `dist` names **`/api/v1/capabilities` only**, because `readActivity` has
no caller yet and the bundler removes it. The test allows both paths and asserts the probe is
present, so the whitelist cannot be vacuous; the negative control plants
`/api/v1/social/comments` and requires the audit to report it. When a view starts reading the
feed, `/api/v1/social/activity` appears in the build and no test changes.

## How it is tested

```sh
cd player/ui && npx vitest run src/lib/hardly/social.test.ts
```

15 tests, no socket: a stub `fetch` records the URL and the init it was given.

* the four states, each from a document shaped like the server's;
* six degradations — a refused connection, HTTP 500, HTML, `{}`, an array, a plain Subsonic 404 —
  each landing in `absent` with the address and, where there is one, the status;
* the probe never throws, including with no address at all;
* the feed's envelope (`api: "v1"`, `data`), the two kinds typed, the day card's counts and
  groups, an unknown kind named;
* `limit` passed through, the default 80, and a 429 keeping the server's own sentence and status;
* the three non-`activity` states refusing with **zero** requests made;
* no credential-shaped query parameter and no member route in any URL the module builds.

## What is still unverified

* **CORS.** The WebView's origin is the asset protocol, not the server's, so
  `/api/v1/capabilities` and `/api/v1/social/activity` must be answered with
  `Access-Control-Allow-Origin`, the same gap [`library.md`](library.md) records for `/rest/*`.
  Neither endpoint is credentialed, so a wildcard answer is enough; if the server sends none, the
  probe degrades to `absent` and the player says it has no social rather than drawing a feed it
  cannot read. `tauri-plugin-http` is not registered (P2 ruled it out), so this is the one path
  that has no native fallback.
* **No window was opened.** This machine has no WebKitGTK development packages, so every check
  above runs under node with `fetch` stubbed. A real cross-origin preflight is a thing only a
  build confirms.
* **The `members` state has not been run against a live deployment.** It is proven from the
  server's source and from documents shaped like its own, not from a server with
  `HARDLYLISTENING_AUTH_REQUIRED` on.
* **Nothing renders it.** P6 is the seam; the surface, when it exists, is P8's.