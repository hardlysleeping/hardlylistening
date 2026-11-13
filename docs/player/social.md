# Social, only when the server has it

The player already answers the app's library surface from a Subsonic server. Social is the one
part of the app that Subsonic has no protocol for: the activity feed, comments, votes, likes,
boosts and member profiles are a HardlyListening server's own tables, reached through its own
`/api/v1/*` and its member cookie. A Subsonic server has a catalogue, a play queue and stars, and
no idea what a member is.

So this card does not draw a feed. It answers a smaller and more honest question: **is there a
feed here at all, and if not, what does a person read instead?** The answer comes from the
connected server, never from the app's source and never from a guess.

`player/shell/social.js` is the whole of it: one route, three named refusals, and a capability
probe. It is a classic script with no build step, it sits *outside* `api-bridge.js` (it wraps the
`fetch` the bridge installed), and nothing runs at load — `PlayerSocial.attach()` is what starts
it.

## The decision, and why it is these three conditions

Activity is offered only when the connected server answers all three:

1. **It answers `GET /api/v1/capabilities`.** That endpoint is public: `server.py:376-405` answers
   it *before* the `/api/v1/` dispatch that authenticates, so a client that holds no session can
   read it. A Subsonic server has no such address, so it 404s and the answer is "no social".
2. **The document names `social.activity`.** The document advertises the domain at
   `server.py:400-401` (`'social': {'comments': True, 'votes': True, 'likes': True,
   'activity': True}`), and the SDK's own reading is `available=bool(details)`
   (`sdk/capabilities.py:98-101`) with the feed's flag named `activity`
   (`sdk/social.py:523`). A domain that is advertised without that flag is *not* read as a feed.
3. **The server does not require members.** `server.py:385` reports
   `'requires_members': bool(SECURITY.required)`, and `security.py:77-78` is where that default
   comes from (`HARDLYLISTENING_AUTH_REQUIRED`, on unless it is switched off).

The third condition is the one that is easy to get wrong, and it is the reason this card exists.
`api/v1.py:244-249` reads the feed through `_reader`, which returns nobody **only** when the
deployment has accounts off (`api/v1.py:60-62`); every other social route goes through `_member`
(`api/v1.py:252-271`), which always authenticates. With members required, `/api/v1/social/activity`
answers 401 and wants `__Host-hl_session` (`security.py:598-614`) — a cookie this player never
holds, because the connect screen keeps Subsonic credentials only. So "the server has social" and
"this player can read the feed" are two different facts, and only the second one earns a screen.

The route table in the module mirrors the server's table exactly (`api/v1.py:295-297`).

## The route

| method | route | answered from |
| --- | --- | --- |
| GET | `/api/activity` | `GET /api/v1/social/activity` |

That is the whole table. The answer is the server's own v1 envelope, handed over in the shape the
host answers (`server.py:741` is `{"items": social_service.feed(...)}`): the module reads
`document.data` and returns `{ "items": [...] }`, so `web/js/social-feed.js` — which asks
`/api/activity?limit=80` and draws `data.items` — finds the same rows without a second rendering.
The page size is the app's own 80, clamped to the server's `1..200`.

## The four states

| state | when | what a person gets |
| --- | --- | --- |
| `activity` | the domain is named, `activity` is on, members are not required | the feed, read from the versioned endpoint |
| `members` | the domain is named and `activity` is on, but the server requires members | `GET /api/activity` is a 501 explaining that the feed is a member's read and this player holds no member session |
| `no-activity` | the domain is named but `activity` is not | the same 501, naming `social.activity` as the missing flag |
| `absent` | no capability document, an unreadable one, or no social domain in it | the module steps out of the way entirely and the bridge's own refusal is what is read |

`absent` is not a failure state. It is the ordinary answer from a plain Subsonic server, and the
refusal a person sees there is the one `api-bridge.js` already writes ("the activity feed arrives
with its own card; this one does not answer for it"). `tests/test_player_social.cjs` compares that
sentence byte for byte against a page that never loaded `social.js`, so "we did not make it worse"
is checked rather than claimed.

Every degradation — unreachable, 401/403, 404, HTML instead of JSON, an empty document — lands in
`absent` **with a reason**, and the reason names the address and the status. A capability read
that fails is not a capability.

## What is refused, and why that is the answer

The rest of the app's social surface stays refused even when the server does have social, because
each one is a member's read or write and the cookie it needs is not this player's to hold:

| route | answer |
| --- | --- |
| `/api/boost` | 501: a boost is written as a member; the server records it against `__Host-hl_session` (`server.py:540-546`) |
| `/api/social/…` | 501: comments, votes and likes are read and written against `__Host-hl_session` (`server.py:559-561`) |
| `/api/member/…` | 501: a member profile is the server's member record, and this player has no member session to read it with |

A refusal is always a 501 with the reason, never an empty list and never a silent no-op, and it
never reaches the server — so a control that hits one cannot look like a lost feed.

## Nothing carries a credential

The capability document is public and the activity read is the server's own public read, so the
module builds no `u`, `p`, `t` or `s` and holds no password: `attach()` reads only
`session.server.url` from the connect screen's session. It also never touches
`localStorage`, `sessionStorage`, `document.cookie` or `indexedDB`. `PlayerSocial.log()` records an
endpoint and a status, never a query string — the same rule `PlayerApi.log()` follows.

## Wiring

`connect.html` is PLAYER-001's file. This card adds files and reports this block; it does **not**
apply it. One tag goes after the existing boot tag, and one hook goes inside `library-boot.js`:

```html
<script src="./connect.js"></script>
<script src="./accounts.js"></script>
<script src="./api-bridge.js"></script>
<script src="./library-boot.js"></script>
<script src="./social.js"></script>
```

`player/shell/*` is the assembly's allow-list, so the file ships with no change to
`player/assemble.mjs`. The call site is `graft()`, immediately after `api.install(...)`
(`library-boot.js:172-188`): that is the one point where the session is known and the bridge is
already on `window.fetch`, so the wrapper lands outside it rather than under it:

```js
    if (root.PlayerSocial && typeof root.PlayerSocial.attach === "function") {
      root.PlayerSocial.attach({ session: session, say: message => say(message, document) });
    }
```

`say` is `library-boot.js`'s existing shelf line (`library-boot.js:136-141`, `say(message,
document)`), so a server that has social but requires members says so where every other server
sentence is said. `attach()` is idempotent (`detach()` then re-wrap) and `detach()` puts
`window.fetch` back, so a session change does not leave two wrappers on the same transport.

Neither block is applied by this card: `connect.html` is asserted, in the Python half, to contain
no reference to `social.js`. Applying them is a one-line change to each file when the wiring card
lands.

## How it is tested

Two halves, one fixture, and neither half is the claim on its own:

    python -m unittest tests.test_player_social
    node tests/test_player_social.cjs

The Python half builds `tests/fixtures/player/social.json` from this repository's own answers: the
activity envelope goes through `api.v1.envelope`, the rows are asserted to be rows
`sdk.social.activity_item` parses, and each capability document is read twice — once by the
module's rule and once by `sdk.capabilities` — so the two readings cannot drift. It also asserts
the decision against the server's source (`api/v1.py`, `server.py`, `security.py`,
`sdk/capabilities.py`), so a change to who may read the feed fails here rather than silently
turning the player's feed into a guess.

The node half loads `api-bridge.js` and then `social.js` into one `vm` realm with `fetch` stubbed
by the fixture, and checks the decision for all five documents, every degradation, the feed's
envelope and page-size clamp, the three refusals, the bridge's sentence for an `absent` server, and
that `detach()` restores the transport.

## What is still unverified

* **CORS.** The player's WebView origin is the asset protocol, not the server's, so
  `/api/v1/capabilities` and `/api/v1/social/activity` must be answered with
  `Access-Control-Allow-Origin` — the same gap `docs/player/library.md` records for `/rest/*`.
  Neither endpoint is credentialed, so a wildcard answer is enough; if the server does not send
  one, the probe degrades to `absent` and the player says it has no social rather than drawing a
  feed it cannot read. `tauri-plugin-http` (PLAYER-002) is the fallback.
* **WebKit.** This machine has no WebKitGTK development packages and no `cargo-tauri`, so no
  window can be opened here. Every check above runs under node with `fetch` stubbed; a `Response`
  built by a real WebKit, and a real cross-origin preflight, are the pieces only a build confirms.
* **No window was opened.** Consequently there is no screenshot and no console log from a live
  player. The module's own `respond()` falls back to a plain object when the realm has no
  `Response` constructor, so it is not dependent on one — but that fallback path is only exercised
  by construction here, not by a real page.
* **A real hosted deployment.** The `members` state is proven from the server's source and a
  fixture shaped like its document; it has not been run against a deployment with
  `HARDLYLISTENING_AUTH_REQUIRED` on.