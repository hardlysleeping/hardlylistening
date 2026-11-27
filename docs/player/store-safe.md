# The store-safe assertion

Purpose: make "the player ships no acquisition code, and speaks only Subsonic" a fact about **the
tree the window loads** rather than a claim about the sources that built it. P4 of the plan; it
replaces PLAYER-007's assembler-era version.

"Acquisition" means turning a source page somewhere else into a file in a library — the repo
search, the download queue, `downloadUrl`. It does **not** mean keeping a song the server a person
signed into already streams: a phone on a bad connection cannot play from the network at all, so
the player holds its own copies of the bytes it was already allowed to play, under the commands in
`src-tauri/src/keep.rs`. The rule this file enforces is therefore narrower than it was before that
feature existed, and the narrowing is stated here rather than left to be inferred from the code:
**no address ever comes from the page.** A keep command takes a song id; `keep.rs` builds the
request from the bound account, exactly as the play path does. The `offline_*` names,
`downloadUrl` and `/download` stay banned, so the removed feature cannot come back under a new
argument.

The first version of this assertion audited `player/assemble.mjs`'s output, because that
assembler was how the player was built from `web/`. Neither the assembler nor that tree exists
any more: `tauri.conf.json` names `../ui/dist` as its front end, and `player/shell/` plus
`player/assemble.mjs` were deleted in P4. An assertion about a build that no longer ships
proves nothing, so `tests/test_player_store_safe.py` was rewritten to audit `player/ui/dist` —
the exact directory the WebView reads — and the sources that produce it.

## What the assertion checks

Every needle is read from a live source at test time; nothing below is a remembered list.

| check | where the answer comes from | a tree that fails it |
| --- | --- | --- |
| the window loads this build | `tauri.conf.json` → `frontendDist`, `ui/package.json`, `ui/vite.config.ts` | the build pointed somewhere else, or a `dist/` that is not a package's output |
| every reference resolves | every `<script src>` / `<link href>` in `dist/index.html`, against the files present | a page asking for an asset the build left out — how a blank window ships |
| no private `/api/…` path | every `/api/…` literal in the build, plus every route `api/v1.py` registers, plus every endpoint in `web/js/repo-discovery.js` | any file naming this server's own route, except the two social reads below |
| the absence is not vacuous | `/rest/`, `getAlbumList2`, `search3` present | an empty or non-client build passing the line above by having nothing in it |
| no offline/download machinery | the ten `offline_*` command names, `downloadUrl`, `/download`, and the four deleted module paths | a kept copy of the removed feature, in the build or in `ui/src` / `src-tauri/src` |
| no acquisition vocabulary | a phrase list (see below) | copy that offers a source page as a library download |
| a kept copy is ids, not addresses | the five `keep_*` signatures in `src-tauri/src/keep.rs`, and the `subsonic::stream_url` call that resolves the origin | a `keep_*` command that takes a URL, an origin or a host — a page able to point the fetcher anywhere |
| the audit bites | a copy of the build with a private route, `offline_manifest` and "download album" planted in it | an audit that cannot fail — not evidence |

The `/api/` rule is the load-bearing one and it is unusual enough to state plainly: it is not
"the player does not reach this server", it is **"the player does not know this server's
routes"**. The player speaks `/rest/…` to whatever Subsonic server it was given. A build that
named `/api/v1/repo/tracks` or `/api/track/<id>/lyrics` would be a second client of one
implementation, and the count-based exception of the old assertion — `web/index.html` carried
the host's own inline calls — is gone with the page that carried them.

There is one exception, added in P6 and deliberately narrow: **the two social reads.**
`/api/v1/capabilities` and `/api/v1/social/activity` are this app's own endpoints, but social is
not in the Subsonic protocol, so the only honest source for "does this server have a feed" is the
server's document. The whitelist is derived at test time from `sdk/capabilities.py` and
`sdk/social.py`, each path is checked to be live (the HTTP layer — `server.py` or a module under
`httpd/` — answers the document, `api/v1.py` registers the feed), and it is a whitelist, not a
prefix: `/api/v1/social/comments`,
`/api/v1/social/summary`, `/api/boost`, `/api/social` and `/api/member` are all still a failure,
because each one is a member's read or write and this player holds no member session.

Measured on the P6 build: `dist` names `/api/v1/capabilities` and nothing else. `readActivity()`
is in `src` and in the module's tests, but nothing calls it yet, so the bundler removes it. The
test allows both paths and requires the probe to be present, so the exception is a fact about a
build that asks something, not a hole; when a view starts reading the feed, the second path
appears and the test is unchanged.

The machinery list is deliberately not the word "download". The connect screen says "nothing is
downloaded here", which is true and is not a feature; `OFFLINE_MACHINERY` is the ten command
names and `downloadUrl`, and `ACQUISITION_VOCABULARY` is phrases like `download album`,
`queue for download`, `joins your library`.

## The narrowed rule, and what it still forbids

Keeping a song is a fetch, so the honest question is what makes it not acquisition:

* **The origin is not a parameter.** `keep_add` takes `accountId` and a `song` record — id,
  title, artist, album, suffix. `keep.rs` calls `subsonic::stream_url`, which reads the bound
  account's origin and credential out of the registry and the keychain. No payload can name a
  host, so the page cannot ask Rust to fetch anything the account was not already allowed to
  stream. The test reads the five signatures and fails on `url:`, `origin:`, `address:` or
  `host:` in any of them.
* **The bytes are the server's own.** The request is the plain `stream` endpoint with no
  `format`, so what is stored is what the server stores — not a re-encode and not a second copy
  fetched from somewhere else.
* **The address handed back plays, it does not reach out.** `keep_source` answers
  `http://127.0.0.1:<port>/<id>`: this process's own listener, serving a file it already has.
  The page never learns a path and never gets a URL to a remote host.

The banned list still does the work it did: `/download` is refused even though the server
implements it, because a player that can ask for a whole file by route is a player that can be
pointed at a library by route. `keep.rs` fetches through `/rest/stream` and nothing else. The
feature itself — the index, the loopback listener, the player's kept-copy-first order and what is
still unverified — is [`kept-copies.md`](kept-copies.md).

Measured on the kept-copies tree: `tests/test_player_store_safe.py` **10 tests, OK**. The build
it audits is three files — `index.html`, one CSS asset, one JS asset. Five `keep_*` commands
are in it, and no `/download`, `downloadUrl` or `offline_*` name is.

## How it runs

The test is the gate. Its exact command:

```sh
python3 -m unittest tests.test_player_store_safe -v
```

It needs the build to exist and says so rather than passing quietly:

```sh
cd player/ui && npm run build
```

`setUpClass` skips with "player/ui/dist is not built" when the directory is absent, so a fresh
checkout gets a skip, not a false green. This repository has no CI configuration; the gate is
that command on the machine making the release. If a runner is added, this test belongs on it
unchanged — it needs the Python standard library and the build, nothing else.

`tauri.conf.json`'s `beforeBuildCommand` runs `npm --prefix ui run build`, so an artefact can
never be packaged from a stale `dist/`. The test exists because a build can be run without it,
and because a build script only refuses the names it was told.

## What a reviewer looks at in a release

```sh
python3 -m unittest tests.test_player_store_safe -v      # 10 tests, OK
cd player/ui && npm run build && cd ../..
find player/ui/dist -type f | sort                       # index.html + one CSS + one JS
grep -oI '/api/[A-Za-z0-9_./-]*' player/ui/dist/assets/*.js | sort -u
#   /api/v1/capabilities        the probe, and nothing else
grep -oI 'offline_[a-z]*\|downloadUrl' player/ui/dist/assets/*.js     # no output
grep -oI 'keep_[a-z]*' player/ui/dist/assets/*.js | sort -u
#   keep_add keep_clear keep_list keep_remove keep_source
```

Anything under `/api/` other than the two social reads is the failure this document is about.

## What the assertion cannot prove

* **It reads bytes, not behaviour.** A URL built at runtime (`'/api/' + 'tracks'`, a string in
  a server payload, a redirect) passes every check here. What would close it: a policy at the
  layer that makes requests — `security.csp` `connect-src` allowing only the server the person
  typed, or a Tauri HTTP scope. Since 2026-11-17 `csp` is set and no HTTP plugin is registered:
  `connect-src` allows the asset origin and IPC only, so the page still cannot reach a host a
  payload names, and the native transport (P2) is the only path that can. The limit that remains
  is `img-src`/`media-src`, which must allow `http:`/`https:` because a cover and a stream are
  URLs a media element fetches itself; see [`../player.md`](../player.md) for why.
* **Vocabulary is a phrase list.** It catches the words acquisition uses about itself; it does
  not catch a synonym. It is a backstop, not the argument — the argument is that the modules
  do not exist.
* **It allows two paths by derivation, not by behaviour.** A build could name
  `/api/v1/social/activity` without ever using it, and this audit cannot tell that from a build
  that reads the feed. What closes that is the reader's own tests (`src/lib/hardly/social.test.ts`)
  and the fact that the whitelist is exactly the pair, never a prefix.
* **The source half has a blind spot.** `test_the_sources_carry_no_machinery_either` walks
  `ui/src` and `src-tauri/src` for the same tokens but skips files with "test" in the name, so a
  regression could hide in a fixture. `dist` is the half without that gap, and it is the half
  that ships.
* **It says nothing about the packaged bundle.** This test never opens an `.app`, `.deb` or
  `.apk`, and the Rust crate is uncompiled here — see [`../player.md`](../player.md) for what is
  verified and why.