# Backlog — HardlyListening

Open work only. Board ids refer to `KANBAN.md`; finished work lives in `BOARD.md`.
Reconciled against `PLAN.md` and the current tree 2026-11-15.

## Platform separation and the two applications

Design, ownership tables, dependency rules and release gates: `PLAN.md` § Platform separation.
One core, one process, one SQLite database — no new daemons, no schema split. Card ids below are
the ARCH / SUB / REPO / UI / JS / API / SDK / HOST / PLAYER / SERVER set, separate from `HL-###`.

P0 — domain boundaries (everything else waits on these)

- [x] **ARCH-001** `docs/server-domains.md`: one owner per feature, table and route; stars vs likes separated. Done 2026-11-11 — 26 modules, 6 packages, 63 tables, 56 route literals and 10 route subtrees assigned; five real inversions recorded with their fix.
- [x] **ARCH-002** `repo/service.py` — repository snapshot, discovery releases, subscriptions, catalogue. No behaviour change.
- [x] **ARCH-005** `social/service.py` — Activity, boosts, comments, likes, profiles.
- [x] **ARCH-006** Existing routes call the services; no frontend contract changes.
- [x] **ARCH-007** Isolation tests: Library/Playback work with Social stubbed out, work with discovery stubbed out, an Activity failure cannot break playback, a discovery failure cannot break Library. Done 2026-11-12 — `tests/test_isolation.py`, four cases, each stubbing one domain's imports.
- [x] **ARCH-008** Core → Social is an event: `security.py` publishes `member.joined` / `member.renamed`, `social.service.subscribe_events()` writes the Feed, `server.boot()` wires it. Done 2026-11-12 — publishing never raises and needs no subscriber, so sign-in survives with Social absent.

P0 — OpenSubsonic on the services

- [x] **SUB-010** `subsonic.py` gets songs, albums, artists, playlists, stars, bookmarks via LibraryService instead of its own queries. Done — the adapter translates, `library.service` answers.
- [x] **SUB-011** Streaming, bookmarks and listen reporting go through PlaybackService. Done differently by design: there is no separate PlaybackService — `star`, `like_album`, `rate`, `record_play`, `save_bookmark`, `remove_bookmark` live in `library.service`, the domain that owns those tables. Streaming stays the adapter's own transcoder, which is protocol, not domain.
- [x] **SUB-012** Subsonic regression: the whole set stays green, no client-visible change. Held: the full suite is green on both trees (794 live / 710 pub, 2026-11-12).
- [x] **SUB-013** `GET /api/v1/capabilities` — server, version, openSubsonic, features (`repo`, `repoFederation`, `social`, `socialFederation`). Done — the features block is derived from the modules that implement them, so it cannot claim more than the server does.

P0 — Repo/discovery separation

- [x] **REPO-010** `/api/subscriptions` delegates to Repo; the route stays. Done — `subscriptions_with_ownership()` → `repo.service.subscriptions()`.
- [x] **REPO-011** `/api/artist` and `/api/catalogue` owned by Repo. Done — both routes call `repo.service`.
- [x] **REPO-012** `feed_items` is exposed as `repo.discovery.releases()`, not as social Feed. No table rename in this pass. Done.
- [x] **REPO-013** The acquisition Queue is declared Repo (no UI movement). Done — declared in `docs/server-domains.md`; `engine/` is Repo's and no other domain may depend on it.

P0 — UI correction

- [x] **UI-020** Repo gets Browse / Releases / Subscribed, in shared scope only. Live 2026-11-12 — `test_repo_tabs`.
- [x] **UI-021** Feed → Artists moves to Repo → Releases, behaviour identical. Live 2026-11-12.
- [x] **UI-022** Feed → Subscribed moves to Repo → Subscribed, unchanged. Live 2026-11-12.
- [x] **UI-023** Feed is Activity only; the redundant Feed sub-tabs go, no placeholder tabs. Live 2026-11-12.
- [x] **UI-024** Release → catalogue → album → In Repo and the back path still work. Live 2026-11-12 — `test_feed_artist_page`, `test_artist_catalogue_ui`.

P1 — JS ownership (one file per domain, one commit each)

- [x] **JS-030** `web/js/repo-discovery.js` — releases, subscriptions, artist catalogue, Download. Live 2026-11-12.
- [x] **JS-031** `web/js/social-feed.js` — Activity. Live 2026-11-12.
- [x] **JS-032** `web/js/library.js` — the drawing layer. Live 2026-11-12.

P2 — clean versioned APIs (must not gate the MVP)

- [x] **API-040** `/api/v1/repo/*`. Done 2026-11-12 — `api/v1.py`, `snapshot`/`releases`/`tracks`/`artist`/`subscriptions`, each one call into `repo.service` (snapshot keeps the ownership decoration `/api/state` applies).
- [x] **API-041** `/api/v1/library/*` and `/api/v1/playback/*`. Done 2026-11-12 — songs/albums/artists/playlists/playlist/favorites/bookmarks from `library.service` (three projections added there, the adapter still owns no SQL), and playback state/queue/now/stream from `nowplaying` + `library.playback`. Personal routes authenticate exactly as `/api/listening/*`; local mode refuses rather than answering an empty library.
- [x] **API-042** `/api/v1/social/*`. Done 2026-11-12 — activity/comments/summary from `social.service`, one envelope `{api, resource, data, count?}`, unknown v1 is 404 and a wrong method is 405 naming the allowed one. Reads only; writes stay on the unversioned routes, and `docs/api-v1.md` says what v1 does not cover.

Before the player — client SDK

- [x] **SDK-001** OpenSubsonic client: auth, library, artists/albums/songs, playlists, search, stars, bookmarks, stream URLs, Range and resume. Done 2026-11-12 — `sdk/subsonic.py`: three credential forms (password, token+salt, device invite), envelope parser, error-code map (`Unsupported endpoint` → `NotSupported`), typed song/album/artist/playlist/bookmark/search results, stream/cover/transcode URLs, `read_range`/`iter_range` with one `Range` header and real resume. `tests/test_sdk.py` 30 tests against a local stub; no XML, no playlist writes (this server refuses them).
- [x] **SDK-002** capability client: detect social / repo / federation, degrade when absent. Done 2026-11-12 — `sdk/capabilities.py`: `probe()` reads `/api/v1/capabilities` into a frozen value; only what the document names counts (this server advertises `discovery`, not `repo`, so acquisition is unavailable; no federation flag → False, with the reason in the module); a missing/malformed document degrades to the OpenSubsonic core with `degraded=True` and never raises. `docs/client-sdk.md`.
- [x] **SDK-003** social extension (Activity, comments, likes, boosts). Done 2026-11-13 — `sdk/social.py`, the third stdlib-only module: reads are the versioned ones (`/api/v1/social/{activity,comments,summary}`, the batch summary included) and writes are the unversioned ones the adapters match (`/api/social/<kind>/<target>/{comments,vote,like}` plus `GET`/`POST /api/boost`), each confirmed in the file it lives in rather than assumed. Every entry point refuses **before the wire** when the capability document drops its flag — `activity`, `comments`, `votes`, `likes`, and the bare domain for the boosts, because no `boosts` flag exists and none was invented. A `{"error": ...}` becomes `Denied`, 404 `NotFound`, 401 `SignInRequired`, 429 `RateLimited` with `retry_after`; the session cookie stays a header and out of `repr`. `tests/test_sdk_social.py` 32 tests against a stub, 11/11 refusals asserting zero requests were made.
- [x] **SDK-004** repo extension as a separate module the public Player never imports. Done 2026-11-13 — `sdk/repo.py`: the five reads (`snapshot`, `releases`, `tracks`, `artist`, `subscriptions`) with every path and query confirmed in `api/v1.py` and every row field taken from the store's own columns. All five refuse before the wire when the document names no `repo` domain — and this server names `discovery`, so the refusal quotes that instead of inventing a flag. The separation is three assertions in `test_player_store_safe.py`, because one was not enough: route needles read out of `api/v1.py`'s table at test time, the same over the reachable closure of the player's markup and module specifiers (36 files), and `assemble.mjs` asked outright to include `../sdk/repo.py` — it refuses by suffix and leaves no tree. Stated limit: static, so a runtime-built URL is outside it, and an edit to `assemble.mjs` is outside every assertion there. **P4 2026-11-14:** two of those three assertions moved with the tree — `assemble.mjs` is deleted, and the guard now reads every route `api/v1.py` registers plus every endpoint in `web/js/repo-discovery.js` and requires that no file in `player/ui/dist` names any of them. The suffix refusal of `sdk/repo.py` went with the assembler; what replaces it is stronger, because the build names no `/api/…` path at all.

Desktop host

- [x] **HOST-001** GUI shell that launches and manages the server binary. It is a client of the HTTP API and of the `web/` the server already serves; it holds no domain rules of its own. Done 2026-11-12 — Tauri 2 in `desktop/`: six commands (status, start, stop, open UI, quit, env), the launcher as an `externalBin` sidecar staged by `tools/stage_sidecar.py` with SHA-256 drift check, dependencies tauri/serde only, CSP `default-src 'self'`, UI reads `/api/health` + `/api/v1/capabilities` and nothing else. Ownership is explicit (`none|ours|external`) so a stop never touches a server this window did not start. Native compile and Xvfb walk completed in `huibuild` for HL-224; a `.deb` now builds (2026-11-16), client-only: it resolves a launcher and disables Start when none is usable.
- [x] **HOST-002** First-run wizard: music folder, data folder, server name, admin account. Done 2026-11-12 — `desktop/host/setup.py` decides the four answers and resumes a half-finished run; `desktop/ui/wizard.js` asks them and `app.js` gates the shell on it; `wizard::server_env()` feeds the spawn plan so a start after the wizard needs no window restart. The admin account goes through the server's own one-shot account path, never a write from the window.
- [x] **HOST-003** Start/stop the local server from the GUI. Closing the GUI must not be what stops a server that was already running. Done 2026-11-12 — one lifecycle, one child slot: `lifecycle_step` is the only command, `server_status` reads ownership from the same slot, and Quit calls the same `on_close` as the window close. `SIGTERM` with a 3-second grace; a server this window did not start is never signalled; `lifecycle.json` holds the close preference and the handoff record.
- [x] **HOST-004** Two independent choices, not one mode switch: *run a local server on/off*, and *point the UI at local or remote*. Remote-only, with no local server, is a supported state. Done 2026-11-12 — `desktop/host/mode.py` models the two axes and `desktop/ui/mode.js` renders them; each handler moves only its own axis. Remembered in `localStorage`; the Rust side does not write `mode.json` yet, and the file says so.
- [x] **HOST-005** Server settings: local-only vs LAN, web UI, OpenSubsonic, autostart. Done 2026-11-12 — `desktop/host/settings.py` and the self-mounting panel in `desktop/ui/settings.js` write the exposure to `host.json` and manage the autostart entry. The web UI and OpenSubsonic are reported as having **no server-side switch**, because they have none: refused and marked unsupported rather than stored as no-ops.
- [x] **HOST-006** Show a usable address when Tailscale already provides one; do not manage Tailscale. Done 2026-11-12 — `desktop/host/addresses.py` decides which address another device could actually type (bind check, tailnet read) and the window runs it and paints the JSON through; `address.js` renders one copyable URL or nothing. It never runs `tailscale up/down/login/set`, and says nothing when nothing is reachable.
- [x] **HOST-007** Dependency direction asserted in the build: Desktop depends on Server, never the reverse. The server runs with the Desktop package absent. Done 2026-11-12 — `tests/test_host_dependency.py` walks the real import closure from `bin/hardlylistening-server` (73 modules), copies the tree with exactly `desktop/` removed and boots it to `/api/health` 200, pins the staged sidecar to the launcher by SHA-256, and parses the Dockerfile and both units. Write rule narrowed to *targets* after HOST-003 landed: no write may reach the app tree, DB, `web/` or music root; owning the config/state dir is allowed. `.dockerignore` now excludes `desktop`.
- [x] **HOST-008** One config resolver, or three that say why. Done 2026-11-13 — `resolve.state_dir()` is the one Python resolver: the `HARDLYLISTENING_DESKTOP_CONFIG` override, the `XDG_STATE_HOME` base and `hardlylistening-desktop` are decoded once, and `config_path()`, `setup.setup_path()` and `mode.mode_path()` are that directory plus their own file name, so `host.json`, `setup.json` and `mode.json` cannot land in different places. The install defaults moved beside the address rules (`MUSIC_SUFFIX`, `XDG_DATA_SUFFIX`, `DATA_LEAF`, `default_music_dir`, `default_data_dir`, `install_defaults`) and `setup.py` aliases them rather than repeating them, which keeps `wizard.rs`'s mirror test pointing at one name. Each module borrows the resolver through the sibling shim `lifecycle.py` already used, because the host modules are loaded by path in tests. The test was strengthened, not relaxed: `XDG_STATE_HOME`, `XDG_DATA_HOME` and `Path.home() / '.local' / 'state'` are asserted to be decoded in exactly one module, and behaviour is compared across three environments plus a caller-supplied config path; a control that put the XDG decode back into `setup.py` failed the test. The Rust cannot import Python, so `wizard.rs` and `host.rs` keep their own decode and are pinned instead — `host.rs`'s `CONFIG_DIR`/`CONFIG_FILE` to `resolve.CONFIG_NAME`/`CONFIG_FILE` in a new test, `wizard.rs`'s constants to the aliases. `settings.py` keeps `XDG_CONFIG_HOME`, which is the autostart entry's own location, not a fourth resolver.

Public player

- [x] **PLAYER-001** One cross-platform shell: iOS, Android, desktop. Done 2026-11-12 — Tauri 2 in `player/`, one source tree with desktop + iOS/Android bundle config; `assemble.mjs` is the single allow-list copying `web/` byte-for-byte into a gitignored `player/www/` and rewriting nothing except dropping references to excluded files in the entry document (recorded in a receipt, and a reference to a file the allow-list forgot fails the build); a connect screen pings `/rest/ping.view`, credentials in memory only until PLAYER-002. Not compiled here: no WebView dev packages. **P4 2026-11-14:** `assemble.mjs` and `player/www/` are deleted; the window loads `player/ui/dist` (Svelte 5 + TS + Vite), and `test_player_shell.py` now asserts only the Tauri configuration and the player's independence. No shipped behaviour changed — `frontendDist` had already moved to `../ui/dist`.
- [x] **PLAYER-002** Multiple OpenSubsonic server accounts, credentials stored safely. Done 2026-11-12 — `player/shell/accounts.js` keeps public records and hands the secret to `player/src-tauri/src/accounts.rs` (the `keyring` crate: Keychain, Credential Manager, Secret Service); a platform with no backend keeps it in memory and says so rather than writing a password to disk. The connect screen migrates the session on a successful ping. **P4 2026-11-14:** the page half moved to `player/ui/src/lib/accounts/vault.ts` + `session.svelte.ts`; `accounts.rs` and its contract are unchanged.
- [x] **PLAYER-003** Library: songs, albums, artists, playlists, search, in the current visual language. Done 2026-11-12 — `player/shell/api-bridge.js` answers 16 `/api/*` shapes from `/rest/*` (covers as blob URLs, token URLs, 18 families refused by name) and `library-boot.js` grafts the app's own page into the same realm, so the library, its grouping and its look are the app's, not a copy. A coverage test reads every `/api/…` literal out of `web/` and fails if one reaches the generic 404 — it caught the deployed equaliser, playback session, cues and stats routes. **P4 2026-11-14:** the bridge is deleted with the rest of the shell. The library is now `player/ui/src/lib/library/store.svelte.ts` over the typed Subsonic client (P0/P1 of the Svelte plan), and the build names no `/api/…` path at all. The 16-shape translation and its coverage test are gone with it; the look still comes from the app, through `player/ui/src/app.css` importing `web/styles/app.css`. **Surfaces finished 2026-11-18:** the store already fetched artists in search and albums/artists in stars, and the shell drew neither; both now render under their headings. Each wall says what is missing when the server has nothing ("This server has no albums yet", "Nothing is starred on this server yet", "No results."), each detail view says it too (an album with no songs, an artist with no albums, an empty playlist) and disables the Play button over nothing, a failed load carries its own **Try again**, and the message is suppressed while `library.error` is set so a dead connection cannot read as an empty library. The album grid also takes the host's density defaults (`--song-cols` 2 on a phone, 4 from 761 px): without them it fell back to 4 columns at any width and a 390 px window truncated every title to "N…". Walked under Xvfb against a stub OpenSubsonic server, including a second stub with an empty library, at 1200×860 and at 390×844.
- [x] **PLAYER-004** Player: now playing, queue, seek, shuffle, repeat, background/media/Bluetooth metadata. Done 2026-11-13 (option A) — the module moved to `web/js/playback.js`, which is the app's own tree and what the player assembly carries (`WEB_ALLOW`), and `web/index.html` lost its whole media-session implementation: `mediaArtwork`, the four publishers, `bindMediaSession`, `republishMediaSession`, their eight listeners, `pushPositionState`, `restoreQueueOrder`/`shuffleUpNext`, the inline `ended` advance and the 2026-11-12 skip-boundary experiment. `playFromLibrary` tags through `playback.publish()`; the toggles call the module and keep their toasts; `attach` is given the persisted modes and a no-op `report`, so `reportListen` stays the only listen report. Two parity adds: `attach({shuffle, repeat})` adopts stored modes without reordering a restored queue, and `shuffleQueue(anchor)` shuffles a newly built queue from the chosen song. Measured on a static copy of `web/`: all four inline blocks parse, `Song 3` tags with `art/3-t500x500.jpg` at `image/jpeg`, a search-result row tags as `Search Hit`, boot restores `hl.shuffle`/`hl.repeat` with the queue untouched, `ended` advances one, the last song with repeat off stops, repeat all wraps, shuffle permutes then restores; visual baselines unchanged in both trees. `tests/test_player_playback.cjs`, `tests/test_player_playback.py`, `tests/test_index_scripts.py` (new); `test_host_dependency` clean with the module in `web/`. **P4 2026-11-14:** `web/js/playback.js` is still the web app's playback module and still correct; it no longer reaches the player, which has its own store and engine seam under `player/ui/src/lib/player/`. The `WEB_ALLOW` half and `tests/test_player_playback.py` are gone with the assembler; `test_player_playback.cjs` (the web app's) stays.
- [x] **PLAYER-005** Offline copies downloaded from the connected server (not Repo acquisition). Done 2026-11-12 — `player/shell/offline.js` and `player/src-tauri/src/offline.rs`: a kept track plays from its copy, a half-written one resumes, budget and eviction in the module. Ten registered commands; nothing calls them until the player UI is wired, and that is written in `docs/player/offline.md`. **Deleted in P4, 2026-11-14.** Nothing ever called the ten commands, and offline downloading is exactly what the store-safe contract forbids in a streaming client. The plan's rule is that it does not come back before the native audio engine (P3) exists, and if it does it comes back as a platform-player feature with a stated budget, not as 841 lines of shell. `docs/player/offline.md` is deleted with it; the design is summarised in the plan's P4 entry.
- [x] **PLAYER-006** Optional Social when capabilities allow. Done 2026-11-13 — `player/shell/social.js` decides from the server's own public capabilities document: Activity is shown only when it answers `GET /api/v1/capabilities`, names `social.activity`, **and** reports `requires_members === false` (`server.py:376-405`, `:400-401`, `:385`). Four states — `activity`, `members`, `no-activity`, `absent` — and the last one hands `null` back to the bridge so its existing named refusal stands unchanged. A Subsonic server, which has no social surface at all, therefore keeps the refusal it already had. Boosts, comments, votes, likes and member profiles are refused 501 with the reason on a server that has no accounts, because those are a member's. Wired in the integration pass: the tag after `library-boot.js`, the attach inside `graft()` where the session is known. **Deleted in P4, 2026-11-14**, because the bridge it attached to is gone. Now P6 of the plan: `player/ui/src/lib/hardly/*`, inert when the server has no feed. `docs/player/social.md` keeps the three conditions and four states as the specification; `tests/test_player_social.cjs` went with the module, so P6 needs its own tests. **P6 2026-11-15:** `player/ui/src/lib/hardly/social.ts` — `probeSocial()` (four states, never throws, `absent` carries the address and status) and `readActivity()` (typed rows, refuses before the wire unless the state is `activity`, `SocialDenied` keeps the server's status and sentence); `Session.social()` is the seam and nothing draws it yet. 15 vitest cases; the store-safe audit now allows exactly `/api/v1/capabilities` + `/api/v1/social/activity`, derived from the server's constants, and still fails on any other social route. **The read moved to Rust, 2026-11-17:** the window's CSP (`connect-src` = asset origin + IPC) refuses a page `fetch` to any host, so `player/ui/src/lib/hardly/native.ts` recognises exactly the two URLs the probe builds — the origin compared whole, so another host/scheme/port or an unknown query is refused before IPC — and invokes `subsonic_public` with `{ accountId, resource, limit }`. Rust resolves the address from the bound account (`subsonic_url::public_url`), sends no credential, and returns the parsed JSON, which the adapter hands back as a `Response`; `social.ts` is unchanged and still knows no transports. 12 seam tests + 4 pure URL tests + 5 socket-level Rust tests (request line carries no `u`/`t`/`s`/`p`/`v`/`c`; 500 stays a refusal rather than an empty feed; HTML stays 'not a document'; unknown name and unbound account refused with no connection opened). A build without a native side keeps the plain `fetch` path. `dist` now names both social paths because the seam is live code, which the audit already allows.
- [x] **PLAYER-007** Store-safe build assertion: no Repo acquisition code, not merely hidden UI. Done 2026-11-12 — `tests/test_player_store_safe.py` assembles into a temp dir and asserts over the *output*, with every needle derived from the sources at test time: no excluded file present, no acquisition endpoint named outside the entry document's inherited script, the acquisition closure over `web/` is exactly the one module, every script/link/import resolves, and output bytes equal source bytes minus the recorded removals. A doctored-tree audit proves the assertions bite. `docs/player/store-safe.md` states the gaps (runtime-built URLs, CSP not yet scoped, packaged artefacts). **Rewritten in P4, 2026-11-14:** it audits `player/ui/dist` — the tree the window actually loads — and its strongest claim is now that the build names **no** `/api/…` path at all, with the forbidden names derived from `api/v1.py`'s route table and `web/js/repo-discovery.js` at test time. 8 tests; the count-based entry-document exception is gone because the document that needed it is gone. Unchanged limit: a URL built at runtime passes a static audit, which is why the native transport (P2) and a future `connect-src` matter more than this test. **The CSP half closed 2026-11-17:** `security.csp` is set in `player/src-tauri/tauri.conf.json` — `script-src 'self'` with no `'unsafe-inline'`, `object-src`/`frame-src`/`base-uri` none, `form-action` none, `connect-src` at the asset origin plus IPC — while `img-src`/`media-src` allow `http:`/`https:` because a cover and a stream are URLs the page's own `<img>`/`<audio>` must fetch (a media element carries no headers). `style-src` keeps `'unsafe-inline'` for attributes only: `style-src-attr` is ignored by WebKit before 15.4 and would block them outright. Proof is a walk of the built Linux binary against a stub server — connect, album wall with covers and the drawn fallback, artists, playlists, playlist detail, artist detail, album detail, a queue add and the full player, zero CSP refusals. **Swept 2026-11-18:** search (`search3` → the one matching song under a SONGS heading), paging (a full 50-row page draws "Show more"; the 10-row second page appends to 60 and the button goes — two `getAlbumList2` requests, no duplicate page) and the star toggle (the heart sent `star` then `unstar` and the pressed state followed both) behave correctly; `media-src` still untested because no playback was run. The social probe's page-side `fetch` and the no-native transport fallback are refused by this policy by design, so wiring social means going through Rust. Written up in `docs/player.md` and `docs/player/store-safe.md`.

- [x] **PLAYER-008** The car's album art: a URL iOS can fetch itself. Done 2026-11-13 — iOS fetches media-session artwork outside the page's session, so a member-only cover route answered that request with a login page and the head unit drew nothing at all. `/api/art/<md5>` is a first-party capability URL (digest = secret, `Cache-Control: public, max-age=31536000, immutable`, not gated by `web_ui`), and `/api/track/<id>/artwork` answers `X-Art-Hash` so a row whose cover had never been extracted learns its digest on the next prefetch (`ensureArt`). The page prefers `row.art_hash` and falls back to the provider CDN; a failed fetch still draws nothing. `tests/test_art_route.py` (8); the library coverage test names both new routes instead of letting them reach the generic 404.
- [x] **PLAYER-009** A drive that keeps playing — and evidence when it does not. Done 2026-11-13 — a finished song no longer tears the element down: `stepTrack(delta, reason)` carries `auto`/`manual` into `playFromLibrary`, and only a manual skip releases the element (`playback.release()` declares `playbackState = "none"`, then pauses; that pause is the module's own, never an interruption). A platform pause or a refused `play()` sets `interrupted`, keeps `wanted = "playing"`, asks once more immediately — no timer, because a suspended page runs none — and resumes on the next visibility/pageshow; a pause the listener asked for is never undone. A stream that dies mid-track reloads once through a page-supplied `reloadSource`, and only for `MEDIA_ERR_NETWORK`/`DECODE`. With it ships the bounded log: `POST /api/playback/events` → `state/playback-events.log`, member-scoped, 4 KiB cap, fixed field list, rotated at 256 KiB. `tests/test_player_playback.cjs` (reasons, refusal, recovery, release, reload, telemetry), `tests/test_media_session.cjs` (the item boundary on a skip, never on a natural advance), `tests/test_fullplayer_ui.cjs` (its harness had gone stale — `#plfull` no longer exists and the page's containers and `playback.js` were not loaded; green again, and it now asserts the auto/manual teardown rule), `tests/test_playback_events.py`.


Headless server and packaging

Detail and acceptance criteria: `docs/server-packaging.md`. The earlier SERVER-002/003/004/006
lines were folded into SERVER-PKG-001/005/007/011 rather than kept as a second set.

- [x] **SERVER-001** The canonical runtime: the same core the Desktop launches, runnable alone. No Tauri, WebView, display or Node toolchain needed to start it. Done — `bin/hardlylistening-server` (`run`/`check`/`version`), stdlib only, no build step; the Dockerfile and the native install use the same entry. The "same core the Desktop launches" half is asserted when HOST-001 exists.
- [x] **SERVER-PKG-001** Runtime config: `HARDLYLISTENING_DATA_DIR`, `MUSIC_DIR`, `HOST`, `PORT`; existing installs keep today's defaults. `MUSIC_DIR` seeds a new database's root only, never overrides a chosen one.
- [x] **SERVER-PKG-003** `bin/hardlylistening-server` launcher with `run`, `check`, `version`; no daemonisation, no PID file.
- [x] **SERVER-PKG-005** Production packaging — re-scoped to the container and closed 2026-11-15.
      Containers are the standing preference, so the production package is the Docker image already in
      this tree (`Dockerfile`, non-root uid 10001, `/data` + `/music`), deployed as the Coolify
      application `player.hardlysleep.ing` on the VPS from Forgejo `dilroopgill/hardlylistening`
      (`master`, `1c4df377`): container on port 8470, `restart: unless-stopped`,
      `HARDLYLISTENING_PUBLIC_ORIGIN=https://player.hardlysleep.ing`, `HARDLYLISTENING_AUTH_REQUIRED=1`,
      live `/api/health` 200 `0.18.0` and `/rest/ping.view` 200. The systemd unit in `deploy/` stays as
      the portable/offline install path, not the production target. There is **no cutover**: the PC
      instance (`music.hardlysleep.ing` via tunnel to `127.0.0.1:8470`) keeps the real library and stays
      primary; the VPS instance is the test one and may hold a different library. The PC later becomes
      the backup/fallback rather than shutting off the traffic.
- [x] **SERVER-PKG-006** Native install script/package: user, directories, permissions, config, unit. Never delete music or database on uninstall.
- [x] **SERVER-PKG-007** Dockerfile: same runtime, non-root uid 10001, `/data` + `/music` volumes, no special server implementation.
- [x] **SERVER-PKG-008** Compose example with persistent `/data` and external music tree.
- [x] **SERVER-PKG-009** Docker health check on `/api/health`; health means the process is alive, never provider or peer availability.
  Storage half done: `IDENTITY_DIR` (`0700`), `state/`, `tmp/`, `cache/metadata`, `cache/federation` declared and created. Key generation and format are FED-001.
- [x] **SERVER-PKG-010** `backup <path>` using the SQLite online-backup API, plus the documented media/identity backup boundary.
- [x] **SERVER-PKG-011** Migration test: an existing instance moved to the packaged layout with no Library, Repository or account loss.
- [x] **SERVER-005** Headless assertion: a test that no GUI/desktop module is imported anywhere in the core, plus a smoke run with no `DISPLAY` answering `/api/health`, `/rest/ping.view` and the web UI. Done — `tests/test_headless.py`: every core module is parsed for a toolkit import (nested ones included), the core is asserted to carry no `src-tauri`/`package.json`/`web/dist`, and the real entry point is booted with no display to answer all three.

- [x] **SERVER-006** Two settings the window can write have no server-side meaning. HOST-005 refuses
      to store "web UI on/off" and "OpenSubsonic on/off" because the server has no switch for either —
      the UI is always served and `/rest/*` is always mounted. Done 2026-11-13 — `config.web_ui()` /
      `config.subsonic()` read `HARDLYLISTENING_WEB_UI` / `_SUBSONIC` (on unless `0`/`false`/`off`/`no`,
      anything else refused like a bad port); `--no-web-ui` / `--no-subsonic` use `dest=` with
      `store_false`, so the parser returns what to serve and `main()` writes it into `server.FEATURES`
      once, before the socket binds. Off gates `web/` only: `static()` and the `/` sign-in branch answer
      404 with a sentence, while `/api`, `/rest/`, `/api/health` and rendered `/s/<token>` pages keep
      answering; `/rest/*` off answers the protocol's own error envelope in the requested format.
      `/api/v1/capabilities` now reports a real `subsonic.enabled` and publishes `web_ui.enabled`. The
      window stores both as JSON booleans (a string is refused, not guessed), refuses a field an
      environment variable pins, and puts the `--no-…` flags in the autostart `Exec` as well as the
      launch argv. Honest limit: the switch covers the `web/` app, not share pages served from `/s/`.
      `tests/test_server_switches.py` (10) + settings/lifecycle/config/capabilities/node updates;
      pub suite 1336 OK, rustfmt clean, live on `:8470`.

Platform separation, file-split half (the boundary half is done — see ARCH-002…008, SUB-010…013,
REPO-010…013, UI-020…024 above)

- [x] **REPO-014** Repo file split: `repository.py`, `catalogue.py`, `subscriptions.py`,
      `acquisition.py`, `api.py` beside `service.py`. Shipped 2026-11-13: the code moved out of
      `discovery/` into those files, `service.py` keeps every signature, and `discovery/*` stays as
      `sys.modules` aliases so the old import paths are the same objects. `tests/test_repo_split.py`.
- [x] **LIB-001** Library file split: `saves.py`, `folders.py`, `playlists.py`, `api.py`. Shipped
      2026-11-13: the save/folder/playlist rules moved behind `library/`, the M3U projection became
      `library/playlist_files.py`, and `listening.py` / `user_playlists.py` keep the routing and
      delegate. `tests/test_library_split.py`.
- [x] **SOCIAL-001** Social file split: `activity.py`, `comments.py`, `likes.py`, `boosts.py`.
      Shipped 2026-11-13: the log is `social/activity.py`, the 24-hour slot `social/boosts.py`,
      comments/votes/summaries `social/comments.py`, and a like `social/likes.py`. Every old import
      is the same object rather than a copy — top-level `activity.py` is a `sys.modules` alias, and
      `social/discussion.py` is a re-export facade — so a patch through an old name still reaches
      the code that runs. `service.py` is still the only entry point and keeps every signature.
      `tests/test_social_split.py`, `tests/test_social_service.py`.

Federation follow-up (after the separation gate)

- [x] **FED-001** Repo federation attaches to RepoService only; the Player never speaks it. Done 2026-11-12 — node identity in `identity/` (`node-id` 0644, `node-secret` 0600, created once with `O_EXCL`, never rewritten, nothing made at boot), and the direction is asserted from the AST: exactly one importer of `repo/federation.py` (`repo/service.py`), the player-facing modules named, `web/` carrying no federation mention. Asymmetric keys/signing are not built — the stdlib has none and no dependency was added; that belongs with FED-MIRROR-002/003. `docs/federation.md`.
- [x] **FED-MIRROR-001** Safe acquisition descriptor (provider, provider track id, source metadata — never credentials). Done 2026-11-12 — `repo/federation.py`: explicit allow-list that *refuses* rather than drops (the caller sees what it lost), refusals name fields and never values so the exception is log-safe, names are judged but not values (`source_url` must be http(s) or empty, which is what stops a descriptor naming a file on disk), canonical versioned encoding, and `parse` re-checks on the way in. `tests/test_federation.py` 18 tests.
- [x] **FED-MIRROR-002** Re-acquire from the original source. Done 2026-11-13 —
      `repo/mirror.py`: `plan()` returns a `url` only when the descriptor's own provider claims
      the page, else a `search`; an unknown provider, a metadata-only provider (`apple`) or a page
      the claimed provider disclaims is refused with a reason that names no URL. `matches()`: the
      provider's own id settles it, otherwise duration inside `max(3 s, 2 %)` and title
      similarity ≥ 0.5, and an unknown duration is *unverified*, not a mismatch. Not wired into
      `engine/queue.py` — the plan is an input to that work, not a replacement. `tests/test_mirror.py`.
- [x] **FED-MIRROR-003** Fall back to copying from a trusted peer. Done 2026-11-13 —
      operator-pinned allow-list (`{base_url, node_id}`), https only (loopback behind
      `HARDLYLISTENING_ALLOW_LOCAL_PEERS`), no redirects, 64 MiB cap checked while reading,
      SHA-256 as the bytes arrive, atomic `os.replace` into `cache/federation/<digest>`. **The
      serving half is not built**: a peer has to be authenticated before it may be given music,
      and the node identity still has no signature scheme, so a serving route would be an
      unauthenticated file server. The contract this side calls is in `docs/federation.md` §5.
- [x] **FED-MIRROR-004** Mirror modes: metadata only / cache on play / selected / all. Done
      2026-11-13 — one settings row, `should_fetch(event=import|play, selected=)`, `tracks.mirror_selected`
      for `selected`, `metadata` the default and fetching nothing. An unknown value is reported
      `invalid` and refused, never defaulted. `GET /api/federation` is a read; writing the mode or
      the peer list is an operator action through `repo.mirror.set_mode`/`set_peers`.

## Subsonic

- [x] **HL-204** Jukebox inside Party — done 2026-11-13. One leased deck
      (`party.control()`'s 30 s `host_until` lease) plus remote steering:
      `POST`/`GET /api/party/<id>/jukebox` runs `get`/`status`/`set`/`start`/`stop`/`add`/
      `clear`/`remove`/`shuffle`/`setGain`, members only, one transaction each. The room's order
      stays its own — votes descending, then `position` — so a client's order holds among equal
      scores and a vote still moves a track. `start` is refused 409 with no live lease,
      `setGain` is 0–1, the heartbeat carries the host's playback position and `next` resets it.
      `state()` exposes `deck`; Subsonic `jukeboxControl` maps 1:1 (`jukeboxStatus`/
      `jukeboxPlaylist`, error 30 with no room, never a new party); the party page has the deck
      bar with play/pause and the room fader. A lapsed lease leaves no speaker (the bar says so,
      any member may claim); guests cannot play the catalog, so there is no per-guest speaker.
      Races pinned: a second claim, and a stale `expected` advance. `tests/test_party_jukebox.py`,
      `tests/test_subsonic_jukebox.py`, `tests/test_party_ui.cjs`.
- [x] **HL-205** Sonic analysis: the vector half — shipped 2026-11-14. `library/fingerprint.py`
      (the tree's only numpy importer, a real dependency by decision) turns a file into one 60-dim
      vector; `library/sonic.py` owns `track_features`, cosine neighbours, a terminating greedy path and
      the worker; `getSimilarSongs`/`getSimilarSongs2`/`getSonicSimilarTracks`/`findSonicPath` plus
      `/api/sonic/*` serve it. The in-app "Sounds like this" / "Sonic path" UI is **not** built.
      `tests/test_sonic.py`, `docs/sonic.md`.
      (`library/analysis.py` beat-grid fit, `library/tempo.py`, `tracks.bpm/bpm_conf/bpm_phase/
      bpm_source/bpm_at`) and Auto DJ consumes it. Remaining: log-mel/chroma fingerprint in
      `library/fingerprint.py`, cosine neighbours and `getSonicSimilarTracks`/`getSimilarSongs*`/
      `findSonicPath` in `library/sonic.py`, one background worker. **This** is where numpy comes
      in as a runtime dependency, so it is a decision, not code.
      **Decided 2026-11-14 (owner):** numpy is a real dependency of the sonic half, imported directly —
      no guarded probe, no pure-Python fallback. The analysis is not reimplemented in interpreted
      arithmetic to protect a stdlib-only rule; numpy is confined to `library/sonic.py` and
      `library/fingerprint.py`, and the rest of the app stays stdlib.
- [x] **HL-210** Subsonic `addChatMessage` / `getChatMessages` — done 2026-11-13; both map onto
      the party room (`party_chat`), never a second store. `party.room_for` resolves the member's
      current room from `party_presence` — the newest open, unexpired room they are still counted
      in — `getChatMessages` filters `since` (ms) and answers an empty list with no room,
      `addChatMessage` answers error 30 with no room and ignores a submitted `username`, so the
      author is always the authenticated member. `tests/test_subsonic_chat.py`.
- [x] **HL-206** Party chat — shipped; the card's blocker was stale. Phase 7 of the Subsonic plan
      already decided against sharing the Discuss log ("merging would put room chatter on song
      pages or strip discussion of its target"), so the party-scoped `party_chat` table is the
      intended store, not a shortcut: guests are 24-hour identities and their messages must not
      reach a member's Activity log. Retention is the newest 500 per room, author/icon come from
      the server's identity (a submitted profile is refused 400), and the UI renders names and
      bodies as text nodes. `tests/test_party.py` (24), `tests/test_party_ui.cjs`.
- [x] **HL-209** `downloadPodcastEpisode` answers error 30 — needs a storage cap and a
      pruning policy before episodes can be stored locally. Done — 5 GB per member, with no
      automatic pruning at the time: at the cap the download is refused with the numbers and
      the member deletes an episode to make room, which unlinks the file and frees the bytes.
      The policy half landed later as **HL-234** (opt-in per channel).
      The cap is counted over the files held for that member, checked before and during the
      copy, one download at a time per member. Files are state (`podcasts/<member>/`), not
      cache. `streamPodcastEpisode` serves a held copy with Range. `tests/test_podcast_downloads.py`.
- [x] **HL-207** A share's URL cannot be shown again after creation: only the token hash
      is stored. Either keep the token or accept show-once and say so in the UI. Done — kept:
      the token sits beside its SHA-256 and the member's listing returns the URL again;
      `getShares` now sends a real `url`. A row created before the column exists answers
      `recreate: true` rather than a broken `/s/`. Lookup is unchanged and still by hash.
      `tests/test_shares_listing.py`.
- [x] Radio is **member-scoped**; decided 2026-11-13 and the plan doc now agrees. The reason
      outlives the doc: a `stream_url` may embed a credential, so a personal list must not be
      readable or editable by another member, and Subsonic carries no owner field on a station —
      each caller simply gets their own set. `docs/2026-11-09-subsonic-surface-plan.md` decision 5
      and Phase 5 say what shipped (CRUD only) and what did not (the More-menu page, the
      `getMusicDirectory` Radio folder, the HEAD/ICY probe — nothing is fetched server-side, so
      there is no request to guard). `tests/test_subsonic_phase34.py` fails if the doc promises
      the shared list again.

## Accounts and signup

Full design in `PLAN.md` (Accounts). Invite-only stays: a signup link is temporary and
single-use, a login link is permanent and is the recovery method, and password / Google /
Apple are extra ways to mint the same `__Host-hl_session`. `members.id` never changes.

P0 — shipped and deployed (HL-401…HL-409; see `BOARD.md`).

P1

- [x] **HL-410** Google OAuth. Live — `/auth/google` and `/auth/google/callback`, plus the link/unlink pair in Profile; `capabilities.accounts.google` reports whether it is configured.
- [ ] **HL-411** Apple Sign In. Blocked: skipped by the owner.
- [ ] **HL-412** Link/unlink providers from Profile.
      The Google half shipped with HL-410; the Apple half is blocked on HL-411.

## Queue and downloads

- [x] **HL-304** Progress while *verifying* a finished file (ffprobe) has no row state —
      it reads as `downloading` at 100%. Done — `verifying` is a job state: counted as
      running, named in the snapshot, labelled "checking the file…" with no bar, requeued
      after a hard kill, and a decision or source swap waits for it. `tests/test_queue_verify.py`.
- [x] **HL-305** No per-provider concurrency: one job at a time, so a stuck source blocks
      the queue. The strip makes it visible, nothing more. Done for the blocking half: a
      cooldown is the provider's, not the queue's, so a rate-limited source only parks its
      own jobs and the strip names the source that is actually holding work back. Two
      downloads at once is still not done — that needs its own cancel and snapshot story.
      `tests/test_queue_provider_gate.py`.
- [x] **HL-306** A delete cannot be undone: files are unlinked, not trashed. A trash fights
      the 95% disk ceiling and needs space accounting. Done — a delete renames the file into
      `<root>/.trash/` (same filesystem, so it is atomic and costs no space) and a
      `library_trash` row says where it came from. Restoring a song renames it back, with no
      download, even for an import whose source is gone; playlist/album deletes are restorable
      file by file. It still uses the disk, so at `disk_limit_pct` the queue purges the trash
      oldest-first and logs each file — the one reclaim that takes nothing from the library.
      Settings reports the count/bytes and Empty trash is the only irreversible delete.
      `tests/test_deletion.py`.
- [x] Member deletes are a durable audit trail, not a trimmed log line. Done 2026-11-12 — `library_deletions` is append-only and never pruned: member id and name, kind, title, tracks, files, real bytes, when. Written only from `deletion.py`; `GET /api/deletions` (admin) feeds a "Who deleted what" list under the tombstones. `tests/test_deletion.py` `Audit`/`DeleteRouteAudit`.

## Playlists and playback

- [x] **HL-303** Auto DJ from BPM — done; the blocker named HL-205, which only the sonic half
      needs. The picker reads `tracks.bpm`: `autoDjFold` folds a candidate into the current
      octave (174 blends with 87), `autoDjScore` rewards a log2 distance under `AUTODJ_TEMPO` and
      subtracts drift, `autoDjPick` skips the last `AUTODJ_RECENT`, `autoDjSeed` draws from the
      queue when it is a real set and the library otherwise, and `autoDjEnsure` keeps
      `AUTODJ_AHEAD` queued so the panel, prefetch and crossfade see the same next track. Live:
      771 of 985 tracks carry a BPM. `tests/test_autodj_pick.cjs`, `tests/test_dj_autodj.cjs`,
      `tests/test_tempo.py`.

## Party / mixer

- [x] Mixer platter rotation is tied to the deck's playback rate; the library disc stays at
      33⅓ by design. Confirmed 2026-11-12: `jogSpinTick` reads `audio.playbackRate` every frame
      (`web/dj.html`), so a pitch move takes effect on the next frame, a dragged disc is left to
      the hand and a paused deck stops where it is; the library disc's 1.8 s is a fixed animation
      and never scaled. `tests/test_mixer_rate.cjs` 17 checks.

## Ops

- [x] **HL-309** The app shell cannot launch offline. The service worker keeps only
      `/offline.html` and the static files; the entry HTML, `/api/library` and artwork are
      fetched fresh every launch, so an installed PWA with no connection shows the offline
      notice. Audio itself is kept (HL-308) — the shell around it is not. Done — the worker
      keeps the entry page (only when the response is the app itself: the `data-hl-shell`
      marker and no redirect, so a sign-in page cannot overwrite it) and the last
      `/api/library`, both in the versioned cache. Artwork is still not kept. A fixed bar
      says the library is a kept copy. `tests/test_offline_shell.cjs`.
- [x] Autodeploy restarts on any Python change, and a deliberately broken edit is rolled back.
      Done 2026-11-13 — `tools/autodeploy.sh`'s four paths and health address are `:-` overrides
      (live values unchanged as defaults), it `cd`s into the tree so it no longer depends on the
      unit's `WorkingDirectory`, and two changes inside one second no longer share a backup
      directory (the second overwrote the copy a rollback would need). `tests/test_autodeploy.py`
      runs it against a throwaway tree with stub `systemctl`/`curl`/`date`: baseline, restart on
      change, web-only no restart, syntax-error restore with no restart, and health-failure
      rollback + second restart with state left at the last good version.

## Player architecture — one client, spike before implementation

- [ ] **HL-220** Player architecture decision/spike: prove the risky iOS pieces for one
      OpenSubsonic client — playback, background audio, lock-screen controls, Bluetooth/Car controls —
      and compare Tauri with Flutter only enough to choose. Use a throwaway/minimal vertical slice,
      not a second product app. Record actual-device evidence separately from simulated evidence.
      Tauri/Svelte is the default because the existing `player/ui` is reusable; switch only if the
      spike shows serious iOS background/media-control problems. HL-225 depends on this decision.
      Local half measured 2026-11-15 in `player/ui`: `npm test` 122 passing / 10 files, `npm run check`
      0 errors and 0 warnings. Device half unmeasured: `PLATFORM_PLAYER_PRESENT` is `false`, this host
      has no `xcodebuild`/`swiftc`, and `cargo check` stops at `glib-sys` — background audio, lock
      screen, Bluetooth and Car controls still need iOS hardware on a macOS runner.
      Offline downloads/local audio import remain outside the initial player scope pending a later decision.
      The iOS recipe now declares the local-networking ATS exception on the product and verifies it
      inside the packaged IPA, so a plain `http://` LAN server is not refused by the WebView's own
      cover/stream loads; whether it answers on hardware is still this card's device half.

## Carded 2026-11-14 — the plan's own limitations, plus app validation

Open work. Detail is in `KANBAN.md`; these are here so `BACKLOG.md` stops looking shorter than the
project. Each one is a limitation that `PLAN.md` states in its own words, not a new feature. HL-220
is the only actionable player card; HL-225 is dependent on its architecture decision, and HL-224
needed a bundle and an auth-mode decision; the bundle landed and the loopback auth decision was made on 2026-11-16 (see HL-224's row).

- [ ] **HL-224** Desktop app: bundle the compiled Tauri crate and close the desktop MVP gaps —
      installable package plus a decision on desktop authentication so the full served UI can be
      exercised. **2026-11-16:** the `.deb` builds and installs (`HardlyListening_0.1.0_amd64.deb`,
      1.50 MiB) after fixing `beforeBuildCommand`, which pointed one directory too high — that wrong
      path is why no bundle had ever been produced. The package is client-only by decision (no server
      core): it resolves a launcher, accepts it only when `cli.py` is beside it, and disables Start
      with the stated reason otherwise; the extracted package was walked under Xvfb and shows exactly
      that. **Auth resolved the same day** (owner decision, `core/security.py`): with no
      `PUBLIC_ORIGIN`, a provably local request — loopback peer, loopback `Host`, no forwarding
      headers — signs in over plain http with a plain `hl_session` cookie; tunnels are refused, and
      a configured https origin keeps `__Host-`+`Secure`. Proven live. The package was then walked
      end to end from the extracted deb: wizard in four steps (the server created the administrator,
      no terminal), Start spawning the launcher as a child of the window, that account signing in on
      loopback and the full UI rendering, Stop from the package killing the spawned pid, and a
      relaunch with no wizard. The in-bundle sidecar sits at `/usr/bin/hardlylistening-server` and is
      correctly refused (no `cli.py` beside it). A second, instrumented run closed both leftovers:
      typed `/tmp/…` paths were accepted (step 4 summarised them) and a stub `xdg-open` recorded
      `http://127.0.0.1:8493` from `Open in browser` — the first run's "path fields ignore typing"
      was stale click coordinates, not a defect. **2026-11-16, after the walk:** the stray write it left behind is fixed — the
      wizard saved state after every step with the *suggested* data folder already in it, so
      step 1 created `~/.local/share/hardlylistening` and orphaned it when another folder was
      chosen; creation is now gated on the data step being completed, `setup_admin` creates the
      folder it needs, and `defaults()` honours `XDG_DATA_HOME` (it hardcoded
      `$HOME/.local/share`). Proven by `cargo test --release --lib` (8 passing, 3 new) and a
      rebuilt `.deb` walked with both XDG homes in `/tmp`. One limit stays open: an `https://`
      remote target is accepted but never probed (`probe.rs` is `http://` only); options are
      listed in PLAN.md.

## Removed — no longer applicable

- **HL-217** Shows (EVENTS-CA): upcoming California performances and a per-member ranked Shows page —
  dropped by the owner 2026-11-14. Every usable events source needs a credential only the owner
  can issue (Ticketmaster `apikey`, org-scoped Eventbrite OAuth, per-artist Bandsintown key), and
  scraping public pages is what the plan forbids, so the card could never leave Blocked without a
  key nobody wanted. `PLAN.md` § *Stretch goal: California shows* records it as dropped, not parked.
- Private folders (`listening_folders`, the Folders tab, the folder menu entries) were
  built unrequested and removed on request. The tables remain untouched.
- Compact card mode and its toggle were removed; cards always render full.
- "Cards reflow while a compact card is open" — the grid no longer reflows at all.

### Re-checked 2026-11-11 against the code, not against other docs

- **HL-301** Drag to reorder inside a playlist — shipped: `bindOrderDrag()` binds
  `#playlist-members` in `web/js/playback-queue.js`, and `paintPlaylistEditor()` renders the
  drag grips. Nothing is missing.
- **HL-307** "Room state is memory-only" — not true: `party_rooms`, `party_entries` (with
  `status`), `party_votes`, `party_chat`, `party_presence` and `party_skips` are SQLite
  tables, read and written on every request.
- **HL-208** Restore the current track and its position on reload — closed 2026-11-14. `hl.now.v1`
  locally and `/api/listening/now` for a new device; `tests/test_resume_position.cjs` proves both,
  plus "a live session is never clobbered". The phone check it waited on is a test now.
- **HL-222** "Subscription gateway transport is unit-tested, but actual ChatGPT subscription authentication is not yet connected" — closed 2026-11-14 as out of scope, not as pending work. The transport exists and speaks the OpenAI protocol against a selectable `base_url`; ChatGPT subscription auth needs an undocumented backend and is not being attempted.
- **HL-216** "Listening history for ranking does not yet exist" (quoted from `PLAN.md` § *California
  shows*) — false twice over, re-checked 2026-11-14. `listen_events` is one dated row per play,
  written by the web player, Subsonic scrobbles and `statistics`, deduped inside 30 s; and
  `stats.top_artists()` already ranks a member's artists over a rolling window using only that
  table. `tests/test_stats.py` covers the grouping and member isolation. The Shows card is left
  with its real blocker: a licensed events source.
- **HL-302** "top/bottom buttons are still missing" — the button row under the queue was
  deliberately removed. Moves live in the row's `…` menu (`Move to top`, plus Alt+↑/↓ and
  Alt+Home/End), and `test_queue_drawer` asserts the button row is gone.
- Library UI "header padding is asymmetric (8/4 desktop, 6/3 phone), `.libtop` has a −5 px
  top margin" — the current CSS is `.bar` `7px 16px` / `7px 24px` / `6px 14px` and `.libtop`
  `margin:6px 0 14px`. There is no negative margin.
- Library UI "`.libtools` is anchored at `top:46px`" — `.libtools` has no `top`; it is a flex
  child, and `.libtop .libtools` only sets `margin-left:auto`.
- Library UI "the pin toggle and the admin gear use two positioning models" — the gear is
  `#admintoolsbtn`, a flex child of `.libtop .libtools`; nothing there is absolutely
  positioned, so there is no second model to reconcile.
