# Board — HardlyListening

## Deployment — two instances, one platform — 2026-11-15

HardlyListening is deployed, and the earlier board said it was not. Verified on the host, not from
notes:

- **VPS** — Coolify application `player.hardlysleep.ing` (project `hardly-sites`), container
  `e1wjnhmipaa9jnju9t2k5lyv-20261115T112001`, `restart: unless-stopped`, port 8470 behind the
  Coolify proxy, built 2026-11-15 11:21 UTC from Forgejo `dilroopgill/hardlylistening` branch
  `master` at `1c4df377`. Environment: `HARDLYLISTENING_PUBLIC_ORIGIN=https://player.hardlysleep.ing`,
  `HARDLYLISTENING_AUTH_REQUIRED=1`. Volumes `-data:/data`, `-music:/music`. Live answers:
  `/api/health` 200 `0.18.0`, `/rest/ping.view` 200, `/` 303 → `/login` 200, capabilities with the
  web UI and OpenSubsonic 1.16.1 on. The music volume holds **no files** — it is a test instance and
  may be given a different library on purpose.
- **PC** — `cloudflared-hardlylistening.service` tunnels `music.hardlysleep.ing` to
  `127.0.0.1:8470`, where `server.py` runs from `~/apps/hardlylistening`. Same version. This one
  carries the real library: **998 tracks, 4 members**, 5.9 MB database. The VPS origin answers 503
  for that hostname, which is how the two front doors were told apart.

**Owner's direction, recorded:** both stay up. They are two instances of the same application on
the same codebase, on different subdomains — not a migration in progress. The PC keeps the real
library and remains the primary instance for now; later it becomes the backup/fallback while
`player.` is used for testing. No retirement, no cutover, no reverse proxy between them.

**Packaging follows the standing preference: containers, not distro units.** `SERVER-PKG-005` was
written as a production systemd unit blocked on a host. The deployment answers it differently, so
the card is re-scoped and closed: the container image already in the tree is the production package,
and the systemd unit stays only as the portable/offline install path. That also settles the
**Headless MVP** gate — the VPS container runs the core with no display, no desktop session and no
Node toolchain, serving the web UI, OpenSubsonic and the API.

**What it changes:** `HL-220`/`HL-225` gain a real https OpenSubsonic target to test a client
against; the iOS gate itself is unchanged (no `xcodebuild` here, build needs a macOS runner).
`HL-224`'s desktop bundle is untouched — a container deployment is not a desktop installer. The
open deployment question is no longer "is it installed" but "which instance a client should default
to", which is a player-side decision, not a server one.

## Player direction — one client, spike before implementation — 2026-11-15

The board had HL-220 as a separate generic Flutter iPhone app and HL-225 as the Tauri player, despite both targeting the same OpenSubsonic client. That would create parallel UIs and playback implementations. Reframed the work: **HL-220 is a bounded architecture spike** to test the risky iOS behavior (OpenSubsonic playback, background audio, lock-screen controls, Bluetooth/Car controls) and compare Tauri with Flutter only enough to decide. **Tauri/Svelte remains the default** because it reuses `player/ui`; switch only if the spike shows serious iOS background/media-control problems. **HL-225 is the single implementation/validation card**, dependent on the spike. No second Flutter product is to be built in parallel. This records the owner's direction; the final stack decision still waits on the device half of the spike.

**Spike, local half measured 2026-11-15.** Run in the live tree at `player/ui` with its installed dependencies: `npm test` **122 passing in 10 files** (native engine, Subsonic client/transport/auth/compat, library store, session, social, lyrics, native seam), `npm run check` **0 errors, 0 warnings**. That makes the reusable-asset argument for Tauri measured rather than assumed: the Svelte client, its Subsonic transport and its player engine seam all hold up under their own tests.

**What the spike did not measure, and why.** `PLATFORM_PLAYER_PRESENT` is `false` in `player/src-tauri/src/native_audio.rs`, so `native_audio_available` answers `false` on every build and playback runs through the WebView's `<audio>`; the platform bridge is a seam with no player behind it. This host has no `xcodebuild` and no `swiftc`, and `cargo check` stops at `glib-sys` for want of the WebKitGTK/GTK development packages, so neither the crate's commands nor any iOS behavior can be compiled here. **Background audio, lock-screen controls, Bluetooth and Car controls remain unproven on a device** — that is the half of HL-220 that decides Tauri versus Flutter, and it needs iOS hardware on a macOS runner. Until it runs, the default stands: no measured iOS failure justifies a second client.

## HL-413: the server card belongs to the choice — 2026-11-15

Measured on the `HL-224` build, pointing the window at `https://music.hardlysleep.ing` while running
no local server left the **SERVER** card polling `127.0.0.1:4545`: it said `Connection refused`,
kept **Start** live and showed every capability as `—`. The choice reached *Two choices* and stopped
there. `mode.js` already knew all four combinations; `app.js` used `controls()` and
`needsAttention()` and nothing else.

**What the card is now.** `Mode.card(current)` returns overrides — and only overrides — when
`talkTo === 'remote'`: the address, a status that names the remote instead of judging it, no
`healthError`, empty `version` and `capabilities`, and Open carrying `openUrl`. It returns `null`
for either local combination, so the local card is untouched code. `app.js` merges the two, and
**keeps the local runtime gate on top**: Start and Stop are `local.enabled && remote.enabled`, so a
remote card can withhold a button the mode forbids but can never enable one the lifecycle refuses
(starting, owned here, owned by systemd). The LAN share line is hidden while pointed elsewhere — an
address to the wrong machine — and *Open in browser* now passes the address it is showing.

`open_web_ui(url: Option<String>)` takes it: `None` means the window's own server, `Some` goes
through `remote_origin()`, which refuses anything but `http`/`https`, an origin (no path, query or
fragment), no credentials, no whitespace or control characters, and canonicalises to one trailing
slash. That is the second reading of the same rule `mode.js` applies, at the boundary where a string
becomes an argument to `xdg-open`. It is also the crate's **first** `cargo test`: two tests, fifteen
refused strings and five canonical forms.

**What it deliberately does not say.** Nothing in this window probes a remote origin: `server_status`
is `std::net` on loopback and the page's CSP allows `connect-src ipc: http://ipc.localhost`. So the
card reports **not asked**, never *down*: "Remote server", `another machine · this window does not
probe it`, and `—` for what `/api/health` would have said. Guessing "stopped" from a loopback refusal
is exactly the claim the old card made.

**Two defects the walk found, both fixed here.** With a local server running behind a remote target
the pill said only "Remote server" and hid the one process the person owns — it now reads
`Remote server · running (started by this window)`. And **Version** still printed the local server's
`0.18.0` on a remote card, because `version` comes from `state.version`, not from `capabilities`; the
remote card now carries `version: null`. Both have checks that fail without the fix.

**Walked on the built binary** (Xvfb, X11, sandbox config on 4545, his live 8470 server never
touched), all three combinations: remote-only — address, amber pill, Start and Stop dead, Open live;
local-behind-remote — Start brings up `0.0.0.0:4545`, the pill reads both, Version stays `—`, Stop
takes it down again and reports *Local server stopped*, not a crash; back to *This computer* — the
loopback card returns whole (green `Running (started by this window)`, `http://127.0.0.1:4545`,
the LAN line, Copy enabled, `0.18.0`). **Open was proved by its argv**: a logging stub as
`xdg-open` on the window's `PATH` recorded exactly `https://music.hardlysleep.ing/`, and the window
said so.

Also in the same pass: the `stop-on-close` label spanned the whole card width, so a click ~40 px
below the buttons flipped the preference (it happened during the HL-224 walk). The label now sits
inside its paragraph and only the checkbox and its text toggle.

Suite: `node tests/test_host_mode.cjs` 123 checks (was 94), `test_desktop_state` 55, `test_host_wizard`
92, `test_host_address` 44, `test_host_settings` 64, `test_host_lifecycle` 69, all passing;
`cargo test --release` 2 passed, `rustfmt --check` clean; `python3 -m unittest` over the seven host
modules 260 tests OK; `snap.cjs` Visual baseline PASS. `tests/test_desktop_host.py` had pinned the
old README line "the Rust has never seen a compiler" — true in HL-224, false since — so it now pins
where the crate compiles and that no bundle exists.

**Still open, plainly.** How a remote target may be *checked* is a decision this card does not make:
a TLS client in the crate, `connect-src` plus CORS on the server, or an explicit *Check the remote*
button. Until one is chosen a remote card can only say it was not asked. And the desktop install's
auth mode — the reason "use the full UI" is unreachable on loopback — is still the owner's call.

## HL-224: the desktop shell compiles, and running it found five things — 2026-11-14

`HOST-001…008` had never seen a compiler. They have now. The `huibuild` distrobox (Ubuntu 24.04)
carries `webkit2gtk-4.1` 2.52.6, `gtk+-3.0` 3.24.41 and `libsoup-3.0` 3.4.4 — the dev packages this
immutable host cannot hold — so `cargo build --release` runs there and the binary links the host's
own WebKit at run time. No `cargo-tauri` CLI is needed for that. The window was then driven under
Xvfb on the X11 backend with `xdotool`, against a sandboxed config and data directory on port 4545;
the live server on 8470 was never touched.

**What the walk proved, on the built binary.** The four first-run answers are written one step at a
time to `setup.json`, and a relaunch resumes at the unanswered step instead of starting over. The
administrator is created by the server's own account code (`admin_created: true`, database written
into the chosen data folder), never by the window. Start brings up a real server — `/api/health`
`0.18.0`, `GET /` 200 html, `/api/state` live, `/rest/ping` answering OpenSubsonic XML/JSON. The
settings panel writes `{"host":"0.0.0.0","port":4545,"subsonic":true,"web_ui":true}` and the LAN
line renders the reachable address (`http://10.0.0.10:4545 · Local network`). Stop stops only the
server this window started. Quit with *stop on close* off hands off: the server keeps answering,
`lifecycle.json` gains `{"handoff":{"pid":…,"listen":"0.0.0.0:4545"}}`, and the next launch says
`Running (started elsewhere)` with every setting intact.

**Five defects that only a run could find**, all fixed here:

* `icons/32x32.png` and `icons/128x128.png` were palette PNGs. Tauri refuses anything but RGBA and
  panics inside `generate_context!`, so the build failed before any of the crate's own code ran.
  Converted in place, not replaced.
* `Lifecycle::start` took a `config` it never read; now `_config` with the reason stated.
* The account step invoked `setup_admin` with `data_dir`. Tauri renames command arguments to
  camelCase, so Rust answered *missing required key `dataDir`* and the account was never created.
  Now `dataDir`, with a source-level guard in `tests/test_host_wizard.cjs` that fails on a
  snake_case invoke key (checked by putting the old key back).
* "The local server exited. Nothing is listening at …" stayed on screen above a Running pill. A
  notice is now either about an attempt (cleared when this window's own server answers) or a
  standing fact about the install (a missing launcher, an unusable address); `state.js` carries the
  distinction as `noticeStanding`.
* Pressing Stop reported *the server exited* — a crash report for a deliberate act. The stop result
  went only to the `lifecycle` module, so the host state still believed the server was this
  window's and the next probe read the stop as a death. It is now dispatched to both, with a
  state-machine check and a source-level check that fails if the second dispatch is removed.

**What it did not prove, plainly.** No bundle: `cargo tauri build` needs a `cargo-tauri` CLI this
box has not got, and no `.deb`/AppImage exists, so the *install* step and the sidecar's location
inside a bundle stay inferred. And **"use the full UI" is still unreachable on a loopback install**:
with `HARDLYLISTENING_AUTH_REQUIRED=0` the page loads and `/api/state` answers, but every
member-scoped route (`/api/listening/*`, `/api/genres`) returns `503 Authentication is not
configured`, because `Security.authenticate` needs an https `HARDLYLISTENING_PUBLIC_ORIGIN` and a
`__Host-` cookie that plain http forbids. That is documented behaviour (`docs/api-v1.md`), not a
desktop bug — but it means the Desktop MVP needs a decision this card cannot make: which auth mode
a desktop install runs in.

Suite on this build: `node tests/test_desktop_state.cjs` 55, `test_host_wizard` 92,
`test_host_address` 44, `test_host_mode` 94, `test_host_settings` 64, `test_host_lifecycle` 69
checks, all passing, plus `tests.test_desktop_host` (43 tests, OK). `desktop/README.md` no longer
says the crate cannot be compiled, and records what the run corrected.

**The remote target, measured afterwards.** Choosing *A remote server*
(`https://music.hardlysleep.ing`) with the local server switched off paints the way `HOST-004`
says it should — "The window will not start a server here. The window still works: it can use a
remote server instead", "The window talks to https://music.hardlysleep.ing/" — and the choice
survives a relaunch. What does not follow is the status: the **SERVER** card keeps polling
`127.0.0.1:4545`, reports `Connection refused`, leaves **Start** enabled and shows every capability
as `—`. `mode.js` already carries the answer for all four combinations (`combine()`, per-combination
`start`/`stop`, the `unreachable` sentence); `app.js` uses only `controls()` and `needsAttention()`.
The probe is the real blocker: `server_status` is `std::net` against loopback and the window's CSP
allows `connect-src ipc: http://ipc.localhost` only, so neither side can ask a remote origin today.
That is carded as **HL-413** with the measurement, and it is why "point the window at a remote
server" cannot yet double as the way to reach the full UI.

## HL-221: the import gets a caller, and it is a person's decision — 2026-11-14

`library/funkwhale.py` could already import a track and say whether it arrived; nothing called it.
That open half is now wired, and the shape of the caller is the point of the card. Publishing is
**an administrator's POST and nothing else**: `POST /api/track/<id>/publish` lands on
`LibraryActions.do_track_publish`, which refuses before any bytes move when the instance or the
token is unset, when the track has no stored file, or when the id is unknown. The credential is the
server's own, so the generic `/api/` policy already makes the route administrator-only; a member is
refused at the boundary with nothing leaving the process. There is no timer, no sweep, no "publish
what was downloaded" — a stored file is not consent to publish it somewhere else.

The upload runs **off the request thread**, because the adapter may wait a minute for Funkwhale to
transcode (`ATTEMPTS × INTERVAL`), and a browser or a proxy gives up long before that honest answer
arrives. `library/publishing.py` holds one in-memory job: `start()` claims it or refuses because it
is busy, the worker reports through a token so an abandoned upload cannot land on a newer job, and
`status(track_id)` scopes the answer to the song the page asked about while still saying whether the
single worker is occupied. `GET /api/track/<id>/publish` is the administrator-only read.

In the page, an item's menu gains **Publish to Funkwhale…** for administrators, disabled when the
track has no stored file. `publishTrack()` confirms first, keeps a sticky toast while it waits, polls
the status route for 90 seconds and then shows the adapter's own refusal, not a paraphrase.

`tests/test_publish_api.py` (12 tests, stub instance only — no real token, no real upload) pins the
whole shape: a finished upload that names a track is a success and the entire file went with the
token in a header; an errored import and a finished-without-a-track keep their reasons; plain http
is refused by the adapter itself; a member can neither POST nor read the status; a second publish
while one runs is refused; unset instance, unset token, no file and unknown id all refuse before the
stub is touched; and the adapter's only callers are the job wrapper and this route. The bug the card
found was the same one HL-205 fixed in its own modules: `start()` built its answer out of `status()`
while holding the same lock, so the plain `Lock` would have deadlocked the first upload.

**Not built, plainly:** no library or privacy-level choice (the adapter sends its defaults) and no
retry, because Funkwhale may already hold the previous upload and asking again has to stay the
person's decision.

## HL-235: the artist page is chronological, and the rows say when — 2026-11-14

The album list already came back newest-first. The **songs** did not: they were A→Z by album name,
which puts a 2019 record above this year's single, and a song row carried no date at all — a track
row has no `release_date` of its own, because the date belongs to the release it came from.

`Store.artist_tracks` now LEFT JOINs `artist_releases` on `(watch_id, provider, release_id)` and
orders by that date DESC, then keeps the album's own track number inside a release, then title. A
release that is no longer stored is undated and sorts after the dated ones rather than disappearing;
the joined `release_date` rides on each row, so `artistSongMeta` and `artistAlbumMeta` print it in
every layout — list rows, cards, tiles — instead of only on the album above.

The order is the **server's**, not a client re-sort, so paging stays correct: *load more* asks for
the next offset and gets the same chronological order. `tests/test_artist_catalogue.py` covers the
newest-first order, a date on each song row, an undated release last and unchanged paging;
`tests/test_artist_dates.cjs` proves it at 1280 (album rows newest first with their dates, songs in
the server's order with dates, the card layout keeping that order).

## HL-205: a track's sound is a vector, and the lock was not reentrant — 2026-11-14

Tempo was already live; what was missing is what a track *sounds* like. `library/fingerprint.py`
decodes with ffmpeg, takes 23 ms frames, and builds one 60-number vector: 48 log-mel band means and
12 chroma means, each block centred and scaled **on its own** before the two are concatenated and
L2-normalised. The per-block centring is the whole trick — centre the blocks together and a loud
master and a quiet one of the same recording stop being neighbours, which is the one thing a
fingerprint has to survive. It is deterministic (so a stored vector and a fresh one compare equal),
it is `VERSION`-stamped (so bumping the maths invalidates everything instead of drifting), and a
silent file returns `None` rather than a zero vector: zero scores 0 against everything and would
pretend a broken file belongs nowhere, so silence is stored as `dims=0` — "analysed, nothing there"
— and is never queued again or compared.

`library/sonic.py` owns `track_features` (the only writer of that table), cosine neighbours with the
seed excluded, and a greedy path with a visited set and a hop cap so it always terminates. Greedy is
the right choice here: a path is a listening sequence, so each step should sound like the one before
it, not be shortest. Asking about a track that has not been analysed enqueues it and answers
**empty** — an empty list that fills in later is honest, invented neighbours are not.

numpy is imported directly, with no guarded probe and no fallback, per the owner's decision; it is
confined to those two modules and imported inside the sonic branches only, so the rest of the
Subsonic surface stays stdlib and the server still has no build step. Both `getSonicSimilarTracks`
and `findSonicPath` left `NOT_IMPLEMENTED` (`docs/subsonic-compatibility.md` cell 13), and the app
gets `GET /api/sonic/status`, `/api/sonic/similar/<id>`, `/api/sonic/path` (`409` when an end has not
been analysed) and a granted `POST /api/sonic/analyse`.

The card also turned up a live bug that had nothing to do with fingerprints. `tempo.start()` and its
copy `sonic.start()` build their answer out of `status()` — while holding the same lock `status()`
takes. With a plain `Lock`, `POST /api/library/tempo` with nothing left to analyse blocked forever,
held the lock, and took `/api/tempo/status` down with it for every later request. Both locks are
`RLock` now, and the merge order was wrong too: `**status()` last overwrote the `started` boolean
with a timestamp, so "nothing to analyse" reported truthy. A bounded thread test in
`tests/test_tempo.py` fails in five seconds on a regression instead of hanging the suite.

What is **not** built, plainly: the in-app half. No "Sounds like this" under a track, no "Sonic
path" in the row menu, no UI for the analyse action — the web UI does not call `/api/sonic/*` at all.
Thirty-nine tests in `tests/test_sonic.py`; `docs/sonic.md` is the contract.

## HL-234: retention is a policy the channel asks for — 2026-11-14

HL-209 shipped the cap and refused to prune, on purpose: silently deleting somebody's file to make
room for another file is not a decision this server gets to make on its own. That was right, and it
left the budget with only one way out — the member deleting things by hand. This card gives a
channel a policy, which is a different thing from the server inventing one: the policy belongs to the
channel, the member sets it, and the server only carries it out.

So retention is **opt-in and per channel**: `keep_newest` (the newest N downloads) and/or
`keep_days` (anything downloaded inside the window). Both default to `0` — keep everything — and a
channel created before this card gets `0` by migration whatever the environment says. An upgrade
therefore deletes nothing, and the cap-only refusal is still what a channel with no policy sees. The
two environment variables only seed channels created after they are set, and the per-channel numbers
are bounded (1000 downloads, 3650 days) and refused beyond it, because a typo that reads as "keep
nothing" would be a delete-all.

The purge itself is deliberately literal: only downloaded episodes of a channel with a policy are
candidates, a file goes only when neither rule keeps it, and the window beats the count — a two-year
old episode leaves a "keep 10" channel even if it is one of the ten. Removal unlinks the file and
clears `file`/`bytes`/`downloaded`, but the **episode row stays**: the client still lists it, now as
`new`, so a pruned episode can be downloaded again. Pruning is a storage decision, not an edit to the
feed.

Where it runs is the difference between a policy and a suggestion. `downloadPodcastEpisode` runs the
member's policy before reading the budget, counting the episode being downloaded as kept, so a full
member gets room made instead of a refusal — and when the policy still cannot free enough, the refusal
says what it freed rather than pretending nothing was tried. The scheduler runs `prune_all` every
fifteen minutes for members who actually have a policy, so a folder does not grow between downloads.

Every removal is one append-only row in `listening_podcast_prunes`, with the bytes and the rule (`age`
or `newest`), and `getPodcasts.storage.pruned` reports that same list. Download, sweep and
`deletePodcastEpisode` all take the member lock, and the purge only logs a removal whose unlink
returned a file — so a manual delete and the policy can never both claim a file, and a row already
gone is never written off twice. Seventeen tests in `tests/test_podcast_prune.py`; the contract is
`docs/podcast-retention.md`.

## HL-232: a feed is not a page — 2026-11-14

Following covered SoundCloud and Apple, so a label with a Bandcamp feed, a self-hosted artist or a
podcast had no way in at all. The card looked like "add a scraper"; the whole design question was
not to. A feed is a documented release list, and a page is text somebody else can change under you.
So `providers/feed.py` claims only a URL that says it is a feed — by extension, by last path segment,
or by `feed=rss|atom`/`format=rss` — and sits in the registry after SoundCloud and Apple but before
the yt-dlp fallback, which claims every http(s) URL and would otherwise swallow feeds too. A page is
never parsed for links.

Inside, an entry is six fields and no more: title (tags stripped, cut at 300 characters), id (its
`<id>`/`<guid>`, falling back to the link so a guid-less feed still dedupes), date (UTC, sorted when
every entry has one), artwork, the link a Download opens, and nothing else. `<summary>`, `<content>`
and `<description>` are dropped **before** storage rather than escaped at display time: a feed is
untrusted, and the field it could poison is the one the web UI renders. The tests feed it `<script>`
and `<b>` and assert no stored value contains either.

Failure is the other half. HTTP errors, non-XML answers, transport failures and bodies over 1 MB
raise `FeedUnavailable` with a sentence the follow box can show; a failed *check* is recorded on the
follow row instead of raising into the sweep, so one dead feed cannot stop the others. Nothing is
ever silently read as "this artist has no releases".

Nine tests in `tests/test_feed_follow.py` over two fixtures, including one that reads `web/index.html`
and fails if the follow box stops advertising feeds — the copy was the last place claiming
SoundCloud/Apple were the only sources. `docs/feeds.md` writes the contract down.

## HL-233: the plan line was stale, the mixer was not — 2026-11-14

`PLAN.md` still listed "BPM/sync" as roadmap next to Funkwhale and the iPhone player. It is not
roadmap: tempo is stored per track and computed by a worker, the mixer shows live BPM, SYNC solves
the other deck's pitch percent and refuses past the fader's ±8 %, and Auto DJ picks by tempo and
crossfades. All of it ships, and `tests/test_dj_sync.cjs`, `tests/test_dj_autodj.cjs` and
`tests/test_autodj_pick.cjs` already pinned the maths and the selection.

What no test pinned was the **scope**, which is where the plan line was quietly wrong in the other
direction: SYNC matches tempo and leaves phase alone. Two matched tracks can still start on
different beats; there is no MIDI-clock master, no phrase detection and no multi-speaker alignment.
A DJ reading "sync" would expect at least the first of those, so the honest fix is to say what ships
rather than to keep the word on a roadmap list.

The line is retired from the roadmap and replaced with a paragraph that states both halves — tempo
sync, no beat sync — and names the tests. `tests/test_dj_sync_scope.cjs` reads `web/dj.html`'s
`syncDeck` body and fails if it starts touching `currentTime` or phase while the text still says
"tempo only", so the two cannot drift apart without somebody noticing in the same commit.

## HL-231: the protocol, measured against another server — 2026-11-14

Every Subsonic test in this tree had our server answering our client. That proves the surface is
self-consistent and says nothing about compatibility: a third-party server arranges the same
protocol differently, and a strict client refuses to guess. The card asks for one matrix, one test
per cell, and **no claim without a recorded response**. So the first thing built was a recorder.

`tools/record-subsonic-fixtures.py` issues `GET` only — nothing on the recorded server is starred,
scanned or written — against the public Navidrome `0.64.2 (10114574)` demo, and files each response
with its endpoint, its parameters and the SHA-256 of the bytes sent
(`tests/fixtures/subsonic/navidrome-0.64.2/`, `manifest.json`). Ids chain `getArtists` → `getArtist`
→ `getAlbum` → `getSong` → `getLyricsBySongId`, so the set agrees with itself instead of being
invented. The demo answers `403` to `Python-urllib`'s default `User-Agent`, so the recorder names
its own and the manifest records that, or a refresh would look like a protocol failure.

`tests/test_subsonic_fidelity.py` drives both directions over those bytes. `RecordedThirdParty` puts
our SDK client (`sdk/subsonic.py`) on real third-party JSON with no network and no server of ours in
the loop: extension list, nested `artists.index[].artist`, a song row parsed into the same value we
use for ours, an empty lyrics answer, and two refusals that arrive as protocol errors rather than
HTTP failures. `OurServerHoldsTheSameContract` asks us the same questions: envelope keys, declared
extensions with integer versions, JSON and XML agreeing, form-POST credentials, error codes,
pagination as an actual slice, the ten fields a player needs to play and cache a song, singular-key
arrays, an empty search, a declared extension that really answers, and error `30` for what we
refuse to implement.

**The recordings corrected the card twice, which is the point of recording.** A missing `id` is
error `70` "Artist not found" on Navidrome — not the missing-parameter `10` the card assumed — and
we already answered the same, so the cell is written from the recording. And for a song with no
lyrics Navidrome sends `{"lyricsList": {}}` while we send `{"structuredLyrics": []}`; both mean "no
lines", so the cell accepts both spellings rather than pinning ours and calling the other broken.
Our XML is namespaced as the protocol says, so the JSON/XML cell compares local names.

Two real defects fell out of writing it. `lyrics.lookup` returned on `HTTPError` without closing
it — a leaked response object in the live app, fixed. And the first run of this suite reached
LRCLIB over the network; a test must not, so the provider is stubbed at the one function that owns
the socket, leaving the cache and the payload shaping as the real code under test.

Proof: `tests/test_subsonic_fidelity.py` — 26 tests, 12 matrix cells plus provenance. The matrix is
`docs/subsonic-compatibility.md`, one row per cell, each naming its test, with the limits stated
plainly: one third-party server and one version, no real third-party client driven against us, and
the endpoints the recording does not touch (shares, bookmarks, jukebox, transcoding) still proven
only by our own suites.

Recounting while shipping this went wrong once and is corrected here: a first count used an id
pattern that missed eighteen done rows and reported 133 cards, so the status line briefly called the
earlier 152 "never a row count". Counted as table rows, the earlier figure was right: this card took
the file from 152 to 153, and 151 after HL-217 was dropped the same day — 1 doing, 7 actionable,
3 blocked, 18 source-complete, 122 done.

## HL-230: a moved library keeps its undos and its trash — 2026-11-14

Moving the media root rewrote `tracks.file` and stopped there. Everything else in the library that
names an absolute path stayed pointing at the old folder, and both of the folders that hold what a
person can get *back* were left behind:

* `.replacements/retained/<id>/` is where a completed source swap parks the file it replaced.
  `reverse` puts it back at `old_file`. After a move the media was still under the old root while
  the root setting named the new one, so `reverse` refused with *"Retained previous file is not valid
  within the current library root"* — the undo was there, on disk, and unreachable.
* `.trash/` holds what a delete moved aside, with `library_trash.path` and `trash_path` naming both
  ends. After a move `restore` refused for the same reason, so a deleted song could only be found by
  hand.

Both now travel. `_move_dotfolder` carries the two folders into the new root and
`_rewrite_replacements`/`_rewrite_trash` rewrite the rows against it, so an old replacement still
reverses and a deleted song still restores. The folders are named by a unique id — a replacement id,
or a timestamp plus a uuid for a trash file — so the two trees **merge rather than overwrite**: a
name already present on the far side is left alone (a duplicate is better than a clobbered undo),
which also makes a move that died halfway safe to run again, and only folders this run emptied are
removed.

**Two cases stated instead of discovered later.** A destination *inside* the current library is
refused before anything moves: the retained media would be copied into the tree it is being moved
out of. The route maps that refusal to 400 with the sentence; before this it was a stack trace.
And a swap whose previous file already lives outside the library cannot be adopted by a move — a
move cannot make somebody else's folder part of this library — so it comes back in `unserviceable`
and in one log line instead of being rewritten into a path that does not exist. `reverse` already
refused those rows before the move and still does; the point is that the move no longer pretends.
The move dialog's result line names such a replacement rather than reporting a clean move.

Proof `tests/test_library_move.py` (6 tests): a moved library plays from the new root, the parked
original and its scratch directory arrive, the row is rewritten and `reverse` restores the original
and the old source URL; the trash travels and `restore` puts a song back inside the new root; a
nested destination is refused with the tree and the rows untouched; an outside previous file is
reported while the other replacement in the same move still reverses; a library with no swaps moves
exactly as before; and a copy already on the far side is merged, not overwritten. Plus
`test_import_api.py::test_a_refused_move_answers_400_with_the_reason` for the HTTP refusal.
`snap.cjs` and `library.cjs` PASS unchanged — the dialog keeps its layout, the result line just
names a replacement when there is one.

Not built: no progress, resume or rollback for a move that fails mid-tree (it is a series of file
moves, not a transaction), and no cross-filesystem optimisation — a move to another device copies
every byte, one file at a time, exactly as it did before.

## HL-229: what a source change keeps, and for how long — 2026-11-14

A source change is a staged swap: the new file is downloaded privately, verified, and only then does
the library start naming it. The file it replaced is not deleted but parked under
`.replacements/retained/<id>/` so the change can be reversed. That was the whole policy — keep it,
forever — and it was wrong in two ways: the folder grew with every source change and nothing ever
decided a keep had done its job, and the scratch directory each download wrote into was left behind
once the swap moved the file out.

**A window, per replacement.** `replacements_keep_days`, default 30, measured from the swap itself
rather than from when the collector last looked — a collector that was not running for a week does
not shorten anybody's window. `0` keeps everything, which is what the app did before this card. Past
the window the copy is collected, one log line per file **written before the unlink** so a line that
never had its removal cannot look like one that did, and the row is marked `pruned`. A reverse after
that point does not fail on a missing file: it answers with the fact — *the previous file was
collected on 2026-10-02 and cannot be restored; change the source again to get another copy*.

**Staging is decided differently, because it is a different thing.** An empty scratch directory goes
at once: a finished swap moved its file out, so an empty one is litter with nothing to lose. One that
still holds a failed download goes only after its replacement is over *and* the window has passed.
One a `queued` or `downloading` row points at is never touched at any age, because a live download
owns its own folder.

**Nothing outside `.replacements/` is read or written.** The library tree is not this collector's
business, and canonical media is never globbed: a file is collected because a row names it, never
because it looks like an orphan.

**When it runs.** At boot and then every six hours, in one daemon thread — a standing job, not a
scheduler job, because collection has to happen whether or not anybody downloads anything and must
not hold up the queue. `POST /api/replacements/collect` runs it on demand (administrator,
CSRF-checked) for the case where the window has just been changed and waiting six hours is silly;
there is no button, because the standing job is the interface.

**Never as disk pressure.** The disk ceiling's job is to throw the trash away, oldest first.
Collecting a reverse's copy early because the disk is full would take away an undo the person was
told they had, so the two never meet.

A first run on a large history examines at most 500 rows; the next pass continues. A database that
predates this card gains the `pruned` column by `PRAGMA table_info` + `ALTER`, with a default of 0
("not collected"), so an old row is never read as collected on the day the database was written.

Proof: `tests/test_replacements.py` — class `Retention` (8 tests: a window that passed collects the
retained copy, logs the line, marks the row and makes `reverse` name the date; a reverse inside the
window still works; `0` collects nothing and still reverses; an empty scratch directory goes at any
age; a queued or downloading directory is never touched even at 400 days; a failed swap's leftovers
go only after the window; the old-schema migration; and the collector is one daemon thread) and
`ReplacementRetentionHTTP` (a member gets 403 and nothing is written, an administrator gets the
report). `docs/replacement-retention.md` is the policy.

Not built: no Settings field — the key is set through `/api/settings` — and no bucket awareness;
retention is about this disk.

## HL-228: a drive prepared for a CDJ — 2026-11-14

`/api/library/export` was a hand-off, not preparation: it writes a playlist naming paths **on this
machine**, so carried to another computer every path in it is dead. The other half of the export
now exists — the audio is written onto the drive, and the playlists are written beside it naming the
drive's own paths.

**A plan before any bytes.** The plan measures free space, weighs every track (its own size, or what
a 320 kbit/s conversion of it should take, called an estimate rather than dressed up as a
measurement), and answers the two different questions apart: `fits` — does the whole set go on, and
what stays behind (named, not dropped) — and `blocked` — do not write here at all. A destination is
refused when it is empty, relative, not there, a file, not writable, or inside the library root,
where the copy would include itself. A refusal is answered as 200 with the reason, because the
dialog shows it beside the field; `start` on the same destination is the 400. Nothing is written by
a refusal, and nothing is queued.

**A copy that verifies, and a drive that can be pulled.** Each file streams into `<name>.part` while
its SHA-256 is computed, then that staged file is read back and hashed *again* with its size
compared; only then is it renamed into place, so a device that silently truncates fails the check
instead of producing a short file wearing a real name. A write error mid-copy — ENOSPC, EIO, the
device gone — fails the job with the reason, removes the `.part`, never marks that track copied, and
writes no index at all: a DJ app must not open half a library.

**No new daemon.** One worker thread inside the server, started on demand, one job at a time; the
job is a row (`usb_exports`, per-file state in `usb_export_files`), so a status poll is a database
read. The claim is a conditional UPDATE, so two workers cannot both copy the same job, and a job
left `copying` when the process stopped is marked failed at boot — `interrupted — the app stopped
before this finished` — rather than left claiming to be live or silently re-run as if it had
happened.

**What the index carries, and what it refuses to invent.** At the drive's root, atomically, only
after every track arrived: `HardlyListening.rekordbox.xml` (drive path, title/artist/album,
`AverageBpm` where a tempo was measured, the member's hot cues as `POSITION_MARK`s) and
`HardlyListening.m3u8`. There is **no `Tonality`** — this app has no key detection, and an empty
attribute would claim otherwise — and `TotalTime` is `0`, because the length was never measured
here. Both are stated as absences, not filled with guesses.

**The dialog had no way in.** `#dlg-export` ("Send to a DJ app") was in the page with no opener and
a Scope field nothing filled; it now opens from the More menu's *Send to a DJ app*, with the scope
filled on open from the member's visible playlists. The drive section is `#usbpath`, `#usbcheck`,
`#usbgo` and `#usbplan`, reading progress from the job row rather than guessing. Every control is
`type="button"` — the dialog is a `method="dialog"` form, so a bare button would submit it and close
the dialog it was asked in — and a checked plan is dropped when the scope or the folder changes,
because a plan describes one scope of one drive. `#expgo`, an `<a download>` with no box of its own,
now carries `a.btn` styling scoped to this dialog; a global rule would have flattened the
icon-sized `.sdl-dl` anchors elsewhere.

Permission is the DJ export's: any approved member, CSRF-checked, 10 starts a minute. Anyone with
the library can prepare a drive — the library is shared, the drive is theirs.

Proof: `tests/test_usb_export.py` (14 tests — refusals, an overflowing plan that still lists the
whole set, missing files and estimates, playlist scope, a verified copy whose XML carries BPM and
cues and no key, a pulled-out drive leaving nothing reported as copied, a real FLAC converted to a
playable MP3, an interrupted job, the worker queue, and the HTTP surface), `tests/test_usb_ui.cjs`
(34 checks, including the whole dialog measured at 390×844), `snap.cjs` and `library.cjs` PASS.
`tests/test_top_nav.cjs` was repaired: it reached for `#navquick`, which this page no longer has, so
it crashed rather than checking anything; it now asserts the row it actually draws, with the new
menu entry.

Not built: drive detection (the person gives the mount path), per-file byte progress, and any key
or length in the XML.

## HL-227: lyrics that follow the singing, and a member's correction — 2026-11-14

Lyrics were parked: the compact bar's button was styled by a rule that also hid it, the reading
had one highlight per line, and there was no way to fix a wrong line without editing the provider's
copy by hand. Three things were missing and one was refused.

**Word timing is the same file, in enhanced form.** `parse_lrc` now recognises `[mm:ss.xx]` as a
line start and `<mm:ss.xx>` inside that line's body as a word start. A line that carries marks gains
`words` (`[{'time', 'text'}]`), and a line that does not keeps exactly the shape it always had — so
plain LRC, the old tests and every existing reader are unchanged. The file's own `[offset:]` applies
to the words as well as the lines, the marks are stripped from the text a person reads, an empty
mark (`<00:10.50>` with nothing behind it) is a timing rather than a word, and the payload says
`word_timed` so the reading can say which kind it got. `web/lyrics.js` paints a word-timed line as
a run of spans: the sung word takes the accent and a glow, the words before it stay lit, and the
line still highlights as a whole. A line-timed file keeps the line highlight; a plain file keeps the
text fallback. Colour and glow only — a transform on an inline span would reflow the line on every
tick.

**A correction belongs to the track, not to the copy it replaces.** `POST /api/track/<id>/lyrics`
takes `{text}` or `{reset:true}` (CSRF-checked, 30/min, 400 on empty or over 512 KB, 404 on an
unknown track) and stores the result in `lyrics_edits`, keyed by track id. Reading is edit → local
sidecar → cached provider, so a provider refresh, a cache expiry or a redeploy cannot undo a
member's fix, and it wins over the `.lrc` beside the audio too: an edit made in the app is a later,
more specific statement than the file on disk. The editor (`#dlg-lyrics-edit`) speaks the file's own
format, which is why a saved correction keeps its line and word timings; it records who edited and
the reading says so. Any signed-in member can edit, because the library is shared. Reset is a POST
action with `{reset:true}` rather than a DELETE, which is the app's existing convention and the
route table's.

**Vocal removal is refused, not deferred.** It is source separation — DSP over the audio — and this
app is metadata-only and stdlib-only; nothing here diarises or subtracts anything, and the plan now
says so instead of implying it is waiting on a card.

**Fullscreen was measured, not asserted.** The reading opens from the full player's `#pflyrics` at
every width: one tap on the bar, then Lyrics. Measured at 390×844 with a real `requestFullscreen` —
the overlay covers the viewport, every tool is inside it and at least a 24 px target, and nothing
overflows sideways. The compact bar keeps a hidden `#pllyrics` only because the page forwards to it
(`$("#pflyrics").onclick`); the bar is a fixed 402×112 art-directed layer (`compact-bar-layers.svg`)
with no free slot beside the transport, so offering a second, clipped entry point there was the
worse trade. The overlay's own tool row is drawn inside the disc column rather than the viewport,
which is why it gained a glass backing and a status line of its own at every width instead of a
breakpoint that would never describe it.

Proof: `tests/test_lyrics_ui.cjs` — 32 checks in a real browser against a stub `/api`: word-timed,
line-timed and plain rendering; the accent on the sung word read from the computed colour (after the
180 ms transition settles, which is a race the test now waits out rather than a class it trusts); the
editor round trip including a reload that answers from the stored correction, reset, and the 390 px
fullscreen measurements. `tests/test_lyrics.py` — 6 tests over the parser (word marks, offset on
words, empty mark, `word_timed`) and the edit precedence (edit beats provider and sidecar, survives a
reload, reset returns the file, plain-text correction, 512 KB refusal, 404). `snap.cjs` and
`library.cjs` PASS after the fullscreen foot's new button was baselined. **Not built:** any editing of
word timings by tapping a word, and provider coverage beyond the recorded fixtures.

## HL-226: the app keeps the music, not just the page — 2026-11-14

`HL-309` kept the entry page and the last `/api/library`, which is why an installed app opened
offline — to a library whose every row drew an empty tile and whose every play button needed the
network. The audio half already existed (`cachePlayedAudio`, a per-device budget, LRU eviction);
what was missing was a complete shell, the covers, and any way to see what is actually kept.

**The shell is complete.** `sw.js` (static cache `v19`) now precaches the three head scripts as
well as the `/js/` ones: `preferences.js`, `genres.js` and `lyrics.js` were live dependencies of
the page and absent from the cache, so a cold offline boot ran without the save controls, the
Genre chip and the lyrics pane. `tests/test_pwa.py` used to compare only `/js/`; it now compares
every `<script src>` in `index.html` against the worker's list, so a new script cannot be
forgotten. The entry page is still stored only behind the `data-hl-shell` marker and only when it
was not a redirect — a sign-in page is a 200 too. The offline fallback page no longer says music is
not kept (that stopped being true when the audio cache shipped); it now says this device has kept
nothing yet and that played songs are kept once there has been a connection.

**The covers are kept.** A second cache, `hardlylistening-artwork-v1`, holds two same-origin
shapes (the page fills it for kept songs; the worker only answers from it, so it stays bounded by
the audio budget rather than growing with every cover the library happens to draw). `/api/art/<digest>` is content-addressed, so it is immutable and answered cache-first with
no revalidation; `/api/track/<id>/artwork` is a track's *current* picture, so the network answers
first and the kept copy is the offline fallback. `keepArtwork(id)` runs when a song is kept: it
learns the digest once (`ensureArt`), fetches the picture, and stores it under both keys — a row
drawn before the digest was known asks the track route, and a row drawn after asks the digest
route, so half the tiles could not miss. `songRow()` now asks `coverArt(track)` rather than the
provider URL, which means the library uses our own copy as soon as a digest is known instead of
depending on SoundCloud or Apple's CDN. Eviction deletes the song's cover with the song; a digest
shared by a song still kept stays.

**Kept is visible, and so is losing it.** Every song row carries a `Kept` badge in its title line
— markup, not a CSS `content`, so it is announced. `paintOfflineMarks()` re-applies it after every
library paint and after every add or eviction, and the badge's stylesheet says `[hidden]` out loud
because an author `display` beats the UA sheet. When the budget evicts, `cachePlayedAudio()`
announces what went and how many bytes it freed; shrinking the budget in the dialog does the same
rather than silently deleting. The dialog's figure was already reconciled against the cache
before it paints, so the number is the files, not a claim.

Proof, Chromium against a local stub (`tests/test_offline_cache.cjs`, 37 checks): a played song is
kept whole and answered as 206 slices of that copy; its cover is kept and opens with the network
off; a known digest is kept under both keys and the digest route opens offline; a kept row shows
its badge and an unkept one does not; the storage figure equals the sum of the kept blobs; the
oldest copy is evicted into a budget, the eviction is announced, and its cover goes with it;
turning the budget off empties both caches; and Chrome's own `Page.getInstallabilityErrors`
returns no blocking reason, which is the same verdict the install prompt is built from.
`tests/test_offline_shell.cjs` still passes (entry
page, last library, a sign-in page never overwrites the shell), and `tests/test_pwa.py` grew the
script-completeness and artwork checks.

Two honest limits. Provider-CDN artwork is cross-origin and opaque to the worker: it is kept only
once the digest is known, which is the first time the row is drawn or the song is played online.
And the card's acceptance ends on a device: the prompt itself, an airplane-mode launch and a played
song on a physical phone. Chrome's installability verdict and the offline code are proven here;
the phone walk is stated as remaining rather than claimed, because headless Chromium is not a
phone.

Unrelated, surfaced by `node tests/visual/library.cjs`: its five baselines predated the
`.libnote[hidden]` fix (baselines 16:29, CSS 20:59), so each still contained the cooldown banner
and a 34 px shift of everything below it. The baselines were refreshed to the current, correct
rendering; the badge is hidden in those states and changes no pixel.

## HL-223 follow-up: verify what you stored, and stop asking the bucket — 2026-11-14

Two things the first pass left weak, both fixed rather than caveated.

**A stored row is now checked, not trusted.** `migrate()` gained a second pass: every `stored` row
is HEADed, and a 404 or a size that no longer matches what was uploaded goes back to `pending`, so
the worker uploads it again. The least recently verified rows go first (`storage_uploads.verified`),
which is what makes `limit` walk the whole library over several runs instead of re-checking the
same first rows. A 403 or 5xx aborts the run with its reason — that is the bucket refusing, not an
object that is lost, and requeueing every row for it would bury the real problem. The run record
gained `repaired`, and the response reports `verified` and `repaired`.

**The cache now answers before the HEAD.** `storage_cache` (key, size, SHA-256, filled, used) is the
metadata the cache was missing: `plan_object` consults it first, so a repeat play costs one SQLite
read and one `stat` — **no request at all**, not even a HEAD. A row last used more than 300 s ago
is a miss so the caller makes one HEAD: a matching size refreshes the row and serves the local copy
with no refetch, a changed size fetches the object again. An out-of-band change in the bucket is
therefore noticed within five minutes instead of never. Changes made through this server — a new
mirror upload (`uploads.tick`), `restore()`, and a replacement (`replacements.py`) — drop the row
and the file immediately, so a cached copy of the old object is never served as the new one.
Eviction now deletes the metadata row with the file; `cache_report` reports `tracked` beside
`files` and `bytes`.

One more bug found by thinking about the eviction order rather than by a test: a cache hit updated
the row's `used` but not the file's mtime, and `evict()` sorts by mtime — so a track played all day
could be evicted for having been *filled* long ago. `cached_copy` now touches the file on a hit, and
a test fills three objects, backdates one, plays it, then needs room and asserts the played one
survives while the untouched old one goes.

The honest cost is stated in `docs/storage.md`: verification is one HEAD per stored row, which is
why `limit` exists; and between revalidation windows the cache trusts its metadata.

`tests/test_storage_migration.py` grew from 23 to 32 checks: a second play making zero requests,
expiry causing exactly one HEAD and no refetch, a vanished cache file dropping its row and
refilling, eviction forgetting the row, a new upload of the same key dropping the stale copy, a
vanished object requeued and re-uploaded, an intact object verified, a wrong-sized object
requeued, a 403 aborting without requeueing, and `limit` covering both rows over two runs.

## HL-223: migration, restore, and a cache that is not the library — 2026-11-14

HL-215 shipped the bucket and deliberately stopped at the edge of the decisions that can lose
data. This card is those decisions, each one made explicit rather than inferred.

**Migration is an action, not a behaviour.** `uploads.migrate()` is what an administrator calls;
nothing starts it at boot, and nothing infers it from a mode change. It builds the adapter first,
so a missing credential is one refusal with a reason instead of ten thousand rows burning five
attempts each. It then walks the track rows into the same persistent queue the download path
uses, and records the run — who asked, when, queued/skipped/missing — in `storage_migrations`.
Re-running is safe: pending and stored keys are skipped, failed and missing ones whose file still
exists are requeued with attempts reset. Draining stays the worker's job, so the answer to an
interrupted migration is "the rows are still there", not "start over".

**Restore is verified or it is nothing.** `uploads.restore()` writes through a `.part` file, checks
the byte count against the object's own HEAD and the SHA-256 against the queue's record where one
exists, and only then renames into place. A mismatch unlinks the staging file and raises: a
half-written track that looks playable is the failure this exists to prevent.

**The cache is deliberately not a second library.** `storage_cache_bytes` is a budget, not a mode:
`0` streams from the bucket exactly as before. A remote track that fits is fetched once into
`paths.STORAGE_CACHE_DIR`, served from the local copy afterwards, and least-recently-used files are
evicted down to the budget. An object larger than the whole budget is *not* cached — evicting
everything for one track is worse than a fetch every time. `available()` passes `cache=False`, so
answering "would this play" never downloads a track.

**Deletion scope, stated once.** A local delete removes the local copy; the object survives, and
that is the restore path. Nothing deletes an object to mirror a local deletion, and no eviction
path can reach the media root. The test proves it by deleting a mirrored file and playing the
track from the bucket, then asserting the stub bucket saw no `DELETE`.

One mistake stated plainly: the first cache test kept the local file in place, so `plan()` took the
local branch and asked the bucket for nothing — the branch was right and the assertion was wrong.
The test now removes the file first; "the local copy wins" was already covered by the HL-215 cases.

`tests/test_storage_migration.py`, 23 checks: local-mode and missing-credential refusals before
any row, the queued/skipped/missing counts and the run record, re-runs, requeue of a failed row,
batching with `limit`, the worker storing and keeping the original with the SHA-256 recorded,
resume across a reopened database, verified restore, checksum refusal with no partial left, key
escapes refused, local-mode restore refused, deletion scope (object survives, no DELETE request),
cache off/on, cache hit with no second GET, oversize object streamed, LRU eviction sparing the
library root, `available()` not downloading, and the HTTP surface (member 403, admin status,
local-mode migrate 400, unknown restore 400).

Documented in `docs/storage.md`; `docs/server-domains.md` carries the module and route rows, and
`PLAN.md` §6 now says what shipped instead of "still open".

## HL-218: the loader — one process, mediated calls, an audit — 2026-11-14

The grants were the safe half. This is the half that runs code, and the design question was not
"how do we sandbox Python" — we do not, and the doc says so in its second paragraph. It was: what
can a process boundary buy cheaply, and what must be true regardless.

`extension_host.py` runs **one enabled extension, once per request**, in a child process:
`python3 -I -B`, the extension's own directory as cwd, an environment holding only `PATH`, `HOME`
and a locale. `-I` is what makes `import server`, `import db` and `import security` fail inside an
extension by construction rather than by policy, and `extension_shim.py` — the child half — is
standalone so it cannot hand anything back. The protocol is one JSON object per line; the shim points
`sys.stdout` at stderr, so an extension that prints logs instead of corrupting its own channel.

Every call passes through `mediate()`, which re-reads the stored grant **at that moment**, so a
revoke binds on the next call and cannot start a new run. Three methods are offered:
`library.read` (the triggering member's own scope, ten fields, **no `file`, no `source_url`** — those
are how a path or a credential would leave), `social.read` (the Activity feed, capped), and
`network` (declared hosts only, https, no redirects, credentials-in-URL refused, 2 MiB, and the
audit keeps `scheme://host/path` with the query stripped). `library.write`, `social.write` and
`background` are refused **by name**: `library.write is not offered by this host yet`. A manifest may
ask; the answer is a sentence, not a no-op.

Installation is from disk and never over HTTP: an extension is a directory under
`paths.EXTENSIONS_DIR` with `manifest.json`, and the route names the directory. `GET
/api/extensions` reports what is installed *and* what is on disk but not installed — with a broken
manifest's own error — because "I put it there and nothing happened" is the failure that list
exists to answer. Runs and calls land in `extension_runs`/`extension_calls`, bounded at 200/500 per
extension, and `GET /api/extensions/<id>/calls` returns both with the last 4 KiB of stderr.

One mistake, stated: the first shim returned the *call id* instead of the call's value
(`answer["result"]` for `answer["value"]`), which read as a plausible integer output and a
`TypeError` the moment an extension did anything with it. A smoke run caught it before the suite
was written; the fix is one word and the tests now pin the value.

`tests/test_extension_host.py`, 24 checks: install grants nothing, a run reports output and records
itself, `import server` fails inside the child, 200 prints do not corrupt the protocol, a symlink
entry is refused at install, `directory()` refuses traversal, a broken manifest is reported, a
non-JSON output fails, an infinite loop is killed (`timeout`), the library read is scoped and hides
the two columns, a capability-less call is refused and audited, writes are named refusals, unknown
methods are refused, the audit trims to its cap, a declared host is fetched, an undeclared host is
refused *before the wire*, plain http needs the loopback hatch, redirects are refused, the audit
drops the query string, and the whole HTTP surface (member 403, admin install → enable → run →
calls → revoke → run refused, broken manifest 400, remove leaves the code).

Documented in `docs/extensions.md` (manifest, entry contract, calls, limits, protocol, statuses) and
`docs/server-domains.md` (module and route rows).

## HL-219: acquisition is a grant, not a role — 2026-11-14

The plan's rule is one sentence: per-user acquisition permission, checked server-side on every
work request, and membership, player access and administrator status must not imply it. The old
code implied all three — `/api/add` was open to any member, and every other POST was open to any
administrator. That is the thing this changes.

`acquisitions.py` holds one row per member who may start work and a `may_acquire()` that reads
that row and nothing else. `server.py` declares the work surface once, as `ACQUIRE_ROUTES` —
`/api/add`, `library/grab`, `failed/retry`, `feed/refresh-all`, `track/queue`,
`track/<id>/retry|source|unpick`, `playlist/<id>/requeue|verify`, `feed/<id>/queue`,
`library/verify` — and checks it *after* the role branch, so a member who is also refused for
being a member hears the real reason rather than two at once. Revoking takes effect on the next
request because nothing caches the answer.

The migration is the part worth stating plainly: the first gated request grants every existing
administrator (`granted_by='migration'`) and writes a marker, then never runs again. Without it,
deploying this would lock the operator out of his own queue. With it, the upgrade is invisible —
and a later revoke is final, which is why the marker is a row of its own and not the table's
existence: a first request can arrive before any member exists (a health check, an anonymous
read), and the grant still has to land for the administrator created a moment later.

Admin surface: `GET /api/acquisitions` lists grants, `POST` grants or revokes, both admin-only and
CSRF-checked. There is **no UI control yet** — granting is the API today — and the extension
packaging itself is still HL-218.

`tests/test_acquisitions.py`, 14 checks: the table, the migration (including the ordering case),
a role not being consulted, revoking everyone staying revoked, an unknown member refused, and the
HTTP gate for a member, a granted member, an administrator with the migration grant, and an
administrator whose grant was revoked. `test_security`'s playlist-ownership test now grants
first — it was testing ownership, not permission.

## Board reconciliation — 2026-11-14

Two things were wrong in opposite directions, so neither is a "cleanup".

**The applications were counted as Done without native-app proof.** `HOST-001…008` and
`PLAYER-001…009` have source and tests; generated frontend `dist/` artifacts are not a native
Tauri build, bundle or device run. On this machine the required WebView development packages are
absent, so the Tauri crate cannot be compiled here without the `huibuild` environment
(`desktop/README.md:171`). Browser tests against a fake Tauri bridge prove logic, not native
playback or media controls. The rows remain not done. HL-224 has since compiled and walked the
desktop host under Xvfb, but it still lacks an installable bundle and its full served UI needs an
auth-mode decision. Player work is split into HL-220 (architecture/iOS-risk spike) and HL-225
(single selected-stack implementation and validation).

**The plan's closing limitations had no cards at all.** `PLAN.md` § *User-visible unfinished-area
audit* names ten limitations and the board carried none of them, which is why "what's left" kept
coming out short. Carded `HL-226`…`HL-234` (real-device offline PWA; lyrics timing/editor/vocal
removal + fullscreen; DJ export as USB preparation; replacement-retention cleanup; root-move
history; third-party Subsonic compatibility; generic RSS/Atom follows; BPM/sync reconciliation;
podcast pruning). "Native iPhone/offline playback" is not carded twice — it is `HL-220`'s own
scope and now says so.

**One mistake, stated:** while restructuring this file the Done table was dropped by a script that
wrote back only the part above it. It was rebuilt row by row from the pre-edit content the same
session. Every id and note is restored with one exception: `HL-204`'s row is now a shorter summary
that points at this file's own full write-up, because its original line was truncated when it was
read. Counts after the rebuild, from the file: 2 doing, 14 to do, 5 blocked, 18 source-complete,
113 done — 152 cards.

## HL-215: the write side — a queue that cannot lose a file — 2026-11-14

Adapters and ranged playback made a bucket readable. The other half is getting finished tracks
there without ever saying a file is stored when it is not.

The order is the plan's and it is the safe order: a track is offered only after it is a verified
file on this disk, so nothing is uploaded before it plays locally. `library/uploads.py` keeps that
work in SQLite — `storage_uploads` — so a restart mid-upload resumes instead of silently dropping
the job. `offer()` is called from `Queue._run` at the one point where a download becomes a real
file; `tick()` drains what is due, backs off exponentially, and stops after a bounded number of
attempts with the reason kept rather than spinning on an answer that will not change.

Three states earn their own names instead of being folded into "failed". A local file that is gone
is `missing` — mirror mode has nothing to mirror and a retry would upload nothing. A bucket whose
configuration cannot even be built (no credential, a bad endpoint) is skipped **once** for the
whole pass: charging every pending row for one configuration error would burn a library's worth of
attempts on the same sentence. And an upload that the bucket does not verify is a retry, not a
success — `put` HEADs the object and compares sizes, `verify` compares the checksum where the
bucket publishes one.

The mode is **mirror**, stated rather than implied: the local original is never deleted, before or
after an upload. Remote-with-cache is a different mode with different failure behaviour and is not
implemented; migrating an existing library into a bucket is a one-shot decision with its own
interruption story, so a background thread does not start it on its own.

One performance fix fell out of this: `S3Storage.put` was reading the whole file into memory twice
(once to hash, once to send). It now hashes in one pass and streams the upload from disk, with the
digest supplied to the signer, and the retry reopens rather than rewinding a half-sent request.

`tests/test_uploads.py`, 14 checks against the stub bucket — local mode queues nothing, a finished
file is mirrored and the original still exists, a stored row is not offered again, backoff and the
attempt ceiling, `missing`, the skipped-configuration case, survival across a reopen of the
database, and the download path itself: a stubbed `Queue._run` completion has to land a row, and
local mode has to land none.

## HL-222: gateway only, by decision — 2026-11-14

The card asked to connect "actual ChatGPT subscription authentication". Closed as out of scope by
the owner, and recorded here so it is not reopened as a gap: the AI matcher already posts OpenAI
`chat/completions` to a selectable `base_url` with `HARDLYLISTENING_AI_API_KEY`, which covers any
self-run compatible gateway — that is the supported path and it works. A ChatGPT *subscription*
login would mean driving OpenAI's undocumented backend with a browser session token: outside their
terms, and gone the week they change a header. Nothing was written for it.

## HL-221: what "accepted" means at Funkwhale — 2026-11-14

The plan would not let an adapter be written until the API surface was checked on Funkwhale's own
docs, because the shape of their API is the kind of thing that gets remembered wrong. Checked
against their published OpenAPI, it had been: uploads live under `/api/v2/`, not `/api/v1/` as the
older notes in `PLAN.md` said.

The surface is two calls. `POST /api/v2/uploads/` is multipart with `audio_file` required and
answers **201 with an upload object and no track** — no song exists yet. `GET
/api/v2/uploads/{uuid}/` repeats that object carrying `import_status`
(`draft`/`pending`/`finished`/`errored`/`skipped`) and, once finished, the `track` it produced. So
"accepted" is `pending` with nothing to show for it, and the only honest success is `finished`
**with** a track that has an id.

`library/funkwhale.py` is written around that: bounded polling, and every way "accepted" lies —
pending forever, `errored`, `skipped`, or finished with no track — is a `PublishError` carrying the
reason. A finished upload with no track is explicitly a failure, because that is the state where
the bytes sit in a library the person cannot see and a zero looks like a song.

Two rules about the credential, both stated in the file rather than implied: the token comes from
`HARDLYLISTENING_FUNKWHALE_TOKEN` and travels in an `Authorization` header, never a query string
(that is where a token ends up in an access log), and the instance must be `https://` — with
loopback excepted solely because the test stub lives there.

`tests/test_funkwhale.py`, 11 checks against a local stub: the happy path, pending forever,
errored, finished-with-no-track, an instant finish needing no poll, a missing file refused before
anything is sent, and an HTTP error whose body is kept while the token is not.

Nothing calls it. Publishing has to be a person's explicit action, and the app has no publish
control yet, so wiring it to a route or a button is the card's remaining half.

## HL-218: the extension host's grants, and nothing that runs — 2026-11-14

The card asks for an opt-in host with manifests, trust, isolation, permissions and revocation. The
only part of that which can be got wrong *irrecoverably* is the permission model, so that is the
part built first and the loader is left for later: `extensions.py` records what an extension is
allowed to touch and holds no code path that could touch it.

Installing a manifest grants **nothing**. It writes a row with `enabled=0` and the requested
capabilities as a record, not a grant. `enable()` is a second act, needs an administrator, and
keeps who and when; `revoke()` sets both the flag and the timestamp in one write, so there is no
intermediate state where a revoked extension still holds something — and because a revoked row
cannot be re-enabled, trusting it again means installing it again, with a manifest a person can
read. `remove()` deletes the row and nothing else: audio, saves and history are not an extension's
to delete.

Two refusals carry the plan's intent. An unknown capability **fails the manifest** instead of being
dropped, because a typo that silently narrows a request is a permission bug that looks like a
feature. And `acquisition` is not in the capability set at all — it is *reserved*, with the reason
the plan gives: membership, player access and administrator status must never imply acquisition, so
the server does not carry the word as something it can hand out. EXT-ACQUIRE owns it, per member,
checked per request.

A manifest's `entry` is validated as a path that stays inside the extension, an id must be a slug
and a version semantic, and declared hosts are refused unless `network` was requested — the sort of
small coupling that is a real vulnerability when it is missing.

`tests/test_extensions.py`, 16 checks: install grants nothing, enable needs an admin, revocation
takes everything back, a revoked extension is not revived, an unknown capability errors instead of
returning false, acquisition is never granted even when asked, and a stored manifest round-trips.

Still open in the card: the loader and its isolation, the admin install route, and an audit trail of
what a running extension requested.

## HL-215: where a recording's bytes live — 2026-11-14

The plan asked for a storage adapter and left the hard part unsaid: a track row that names a file
path stops being true the moment the bytes live in a bucket. So the row does not gain a second
column. The object key **is** the stored path relative to the library root — the same string
`LocalStorage` writes under the root — and one track row therefore names the same object in either
mode, with no mapping table to drift.

`library/storage.py` holds two adapters and nothing else. `LocalStorage` is the default and stays
first-class: a server with no bucket runs exactly as it did before the file existed. `S3Storage`
speaks SigV4 written by hand rather than pulling in boto3 — four request shapes do not justify a
transport stack in a standard-library server — and the signer is pinned to AWS's own published
`GET Object` example, so a change that still agrees with our stub would fail against the real
service. Path-style addressing, one prefix (`hardlylistening/`), a single PUT capped at 5 GiB by
name rather than silently switching to multipart, 5xx retried / 4xx not, upload verified by HEAD
size, ETag never compared to a SHA-256 (a multipart ETag is a digest of digests), credentials from
the process environment only, no signed URLs, and a misconfigured bucket **raises** instead of
quietly falling back to local — the one failure a person can act on.

Playback did not need a second code path. `library/playback.span()` now owns the 200/206/416
arithmetic for both readers; a remote `Stream` carries a reader instead of a path and takes its
size from a HEAD and its slice from a ranged GET through `iter_range`. The headers a client sees
are identical, the local copy always wins when one exists (which is what makes mirror mode a fast
path rather than a mode), a row pointing outside the root never reaches the bucket, and a
misconfigured bucket is loud rather than a 404.

Checked: `tests/test_storage.py`, 23 tests — the published vector, canonical-request bytes built
by hand, a stub bucket that keeps the bytes (so a range read is compared to the file it came
from), prefix escape, retry rules, the 5 GiB ceiling, selection rules, the admin view never
carrying the secret, and remote playback answering identically to local. No bucket, credential or
real object is touched.

Two bugs the tests caught, both of the kind that look like nothing: an empty key was falsy and
turned a request into a *bucket-level* one instead of being refused, and a leading slash was
stripped on the way out but not in the value the caller stored. Both are now asserted.

Still open inside the card: the write side (acquisition and import through the adapter, with its
own persistent upload queue), the chosen mirror-versus-cache mode, and migration/restore/
deletion-scope tests. Storage settings joined `DEFAULTS` in `core/db.py`; `GET /api/storage`
reports mode, bucket, region, prefix and usability to an admin, never the credential.

## HL-214: the Swipe deck gets a player — 2026-11-14

Tapping a card started an `<audio>` element and nothing else: no title, no clocks, no way to
pause without tapping the card again, no volume. The page now carries the library's own compact
bar — the same markup, the same sheet — because a second player drawn by hand is a second player
that drifts.

What a one-song deck can honour is the whole difference: art, title/artist, prev/play/next,
mute and `#plvolmore`, the scrub and its two clocks. No queue sheet, no fullscreen view, no
heart/save, no repeat or shuffle — there is one card in play and the deck is the queue.

Two things had to be ordered rather than patched. `player.css` is linked **before** `swipe.css`:
the bar's rules are stated once, at one specificity, so a page sheet that wants to narrow them
has to come later in the cascade — loaded the other way round, every narrowing rule loses
silently and the bar renders 1072 px wide under a 520 px deck. And the scrub is a native 3 px
track filled by `--bar-played` rather than the library's canvas wave, because this page builds no
`.plwave-seek`; the input keeps the library's geometry so the hit area is the same.

The bar is a floating object, so `body:has(#player.on) main` adds its height to `main`'s bottom
padding; without it the action row sat under the bar at 390 and 1280.

Behaviour is the deck's, not the library's: prev restarts the song if it is past three seconds,
otherwise replays the previous audition; next auditions the card underneath **without deciding
it** (a listen is not a keep); the fader shares the library's `hl.volume` key; and dropping the
card that is playing stops playback and takes the bar with it, so a song never plays with no card
to show for it.

`tests/test_swipe_player.cjs`, 28 checks. The first version failed three of them for a reason
worth recording: the stub answered audio without `Accept-Ranges`/`206`, Chromium marks such a
stream unseekable, and setting `currentTime` restarted the file from zero — the scrub looked
dead. The test now speaks Range like the real `/audio` route, and the harness and the product are
no longer being confused with each other. Screenshots at 390 and 1280 inspected: bar under the
action row, held to the deck's column, line visible.

## HL-211/212/213: what the compact bar was actually painted with — 2026-11-14

Three live reports, three different causes, none of them the one the code read as.

**The volume speaker that only appeared after a nudge.** `$("#plvolmore").innerHTML = SVG.volume(16)`
was indented inside the loop that handles a *fader's* `input` event, so the button existed, was laid
out, and had no drawing in it until somebody dragged a fader. Chromium said it plainly: a 30×30 box
at x=733 with `innerHTML: ""`. It now sits in the boot icon block beside `#plmute`, and
`test_volume_faders.cjs` asserts the `<svg>` and the 0 px gap to mute before any interaction — the
assertion the old suite was missing.

**The fullscreen fader.** 90 px was chosen when the fader shared a row with the foot cluster; it has
had its own centred row for a while (the comment even said the width was "the fader's own business
now"), so 180 px is back. The old geometry check compared the fader's left edge with the queue
button's right edge, which only makes sense on a shared row; it is now an overlap test, and the
heart is checked the same way.

**The background that would not change.** `body #player.on.has-art` is the last surface rule in the
sheet and it still said `rgba(18,19,25,.72)`, so the earlier gradient edit never reached the screen —
the bar always had art. It now carries the fullscreen art surface's colours (`#101014`→`#0c0c11`)
at 72 % alpha: same tone, transparency kept, no art wash (the pill is a floating object; a blurred
cover rectangle behind it reads as a stray box).

**The theme that arrived late.** The saved colours were applied by `fetch("/api/listening/theme")` at
the end of the inline script, i.e. after the first paint, so every cold load showed the stock three
and then switched. `GET /theme.css` now answers the member's three `--user-*` inputs for the session
cookie and the stock three otherwise, linked from `<head>` before any script, `no-store`. The new
`Security.peek()` reads the session without raising and without spending the verification budget:
the request comes from the browser itself, so it must not be able to 401 the page or drain the
login's rate limit. The fetch stays as the in-session fallback.

A link in `<head>` crosses two invariants, and both are stated rather than bent. `player/assemble.mjs`
refuses an entry document that asks for a file the player does not carry, so `theme.css` joins its
EXCLUDED list — the shell has no browser session to colour, and `tokens.css` already defaults to the
stock three. `test_pwa` asserted "one stylesheet entry point"; it now asserts one *file* tree plus
this one generated sheet, still requiring every imported file to exist, be precached, and be the
only thing in the cache (the palette must not be, since the next person to use this browser is not
necessarily the member).

**Swipe drops.** They never deleted anything — `swipe.decide` writes one `swipe_swipes` row — but
nothing asserted it, so `test_swipe_drop_keeps_repo.py` now does: the `tracks` table is identical
after a drop, the feed loses exactly one card, a copy already in the member's library survives, and
another member is still offered the song.

## HL-204: the room's deck becomes a jukebox — 2026-11-13

The last actionable card, and the one that had been read as "who owns the audio". That was already
answered: `party.control()` leases the room's speaker for 30 seconds (`claim`/`heartbeat`/`next`/
`release`, hashed token). What was missing was the other half — the room's queue and playback state
as something a phone can steer, and Subsonic's `jukeboxControl` mapping onto it.

**Server.** `party.jukebox()` runs `get`, `status`, `set`, `start`, `stop`, `add`, `clear`,
`remove`, `shuffle`, `setGain` in one transaction per call, members only. `party_rooms` gains
`deck_playing`, `deck_gain`, `deck_position`, `deck_updated`; `party_entries` gains `position`; the
guarded `ALTER`s mean an existing room needs no reset (verified by running the migration against a
copy of the live database). `state()` now exposes `deck` — `playing` only while a lease is live,
`controlled_by` the host's display name — and the heartbeat carries `audio.currentTime` (bounded
0–24 h) and `!audio.paused`, so the position is measured and `playing` has one honest source: the
device making sound. A remote `stop` really stops that speaker, and a local pause is not undone by
the next poll; `next` resets the position.

**The queue stays the room's.** Waiting entries still sort by votes descending; `position` is the
tiebreak, so a `set` order is honoured among tracks nobody has voted on and a vote still moves a
track. `shuffle` permutes that order, `remove` takes an index in it. `start` is refused 409 with no
live lease — a status that says "playing" with no speaker would be a lie — and `setGain` is 0–1.

**Subsonic.** `jukeboxControl` maps 1:1: `status` → `jukeboxStatus`, `get` → `jukeboxPlaylist` with
full song entries, the rest → `jukeboxStatus`. `party.control_room` resolves the member's room from
presence, else one they created or whose deck they hold, so a phone app with no room window still
reaches its own room; with no room the answer is error 30, never a new party invented for the
client. The three older refusal suites that listed `jukeboxControl` as unimplemented now list only
`hls`/sonic analysis.

**UI.** A deck bar (`#deckbar`/`#deckstate`/`#deckactions`/`#decktoggle`/`#deckgainrow`/`#deckgain`)
names the device — "Host's device — playing" — for everyone in the room; only members get
play/pause and the room fader, because guests cannot play the catalog. The host applies gain to
`audio.volume` and reports its position and playback state on every heartbeat (and at once on a
local play/pause). A lapsed lease leaves the room with no
deck (the bar says so) and any member may claim it; there is no per-guest speaker to fall back to,
which is a limit of the guest model rather than a missing control.

**Proof.** `tests/test_party_jukebox.py` (15) — commands, membership, vote-over-order, `start`
without a deck, gain bounds, the speaking device's own playback report, and the two races (a second
claim, a stale `expected` advance);
`tests/test_subsonic_jukebox.py` (14) — error 30 with no room, playlist round trips (the playing
track leading), votes reordering, start/stop, gain, shuffle, unknown action; `tests/test_party_ui.cjs` extended with the
deck bar (text, play/pause POST, gain POST, and an older room state with no `deck` field still
painting); `node tests/visual/snap.cjs` PASS. Live: autodeploy restarted `hardlylistening.service`
at 19:13:42, `/api/health` 200.

Board: 128 cards, **123 done, 0 to do**, 5 blocked — every remaining card needs a decision, a
device or a host.

## HL-210: Subsonic chat maps onto the party room — 2026-11-13

The last chat gap. The room's own chat shipped with HL-206 (`party_chat`,
`/api/party/<id>/chat`, 500-message retention, server-side identity), but Subsonic's
`addChatMessage` / `getChatMessages` still sat in `NOT_IMPLEMENTED` and answered error 30 the way
it does for a feature that does not exist.

They now map onto the room, and deliberately not onto Discuss: Phase 7 of the Subsonic plan keeps
the two apart, because room chatter on a song page and a guest's 24-hour identity in a member's
Activity log are both wrong. No second message store was added.

**Finding the room.** A Subsonic client carries no room id, so "the member's current room" has to
come from the server. `party.room_for(store, actor)` reads `party_presence` — the table `state()`
writes whenever a room is read — and returns the newest **open, unexpired** room the identity is
still counted in (`seen` inside the room's own `PRESENCE_TTL`). A member who left the party, or
whose room ended, gets no room rather than a stale one.

**The endpoints.** `getChatMessages` filters by `since` (milliseconds since the epoch, the
protocol's unit) and answers `chatMessages: []` when there is no room; each message is
`{username: display_name, time: ms, message: body}`. `addChatMessage` answers error 30 with no
room, ignores the submitted `username` (the author is the authenticated member, so a client cannot
post as somebody else) and keeps the room's 1–1000-character body rule, which surfaces as error 10.
`party.messages()` is the time-ordered read, so the Subsonic surface and the web room read the same
rows in the same order.

**Proof.** `tests/test_subsonic_chat.py` (7 tests): no-room empty list + error 30, a round trip with
the server identity and a millisecond timestamp, a spoofed `username` ignored, `since` filtering,
body validation, a closed room treated as no room, and the newest room winning. The three older
suites that listed `addChatMessage` as unimplemented were corrected (two now say why it left the
list, one is comment-only). Subsonic + Party subset: 96 tests OK. Live: autodeploy restarted
`hardlylistening.service` at 19:08:10, `/api/health` 200.

Board: 128 cards, 122 done, **1 to do (HL-204, the jukebox command set)**, 5 blocked.

## SOCIAL-001: Social splits by domain — 2026-11-13

The last platform-separation file split. `social/` was `service.py`, `api.py` and one
`discussion.py` that held comments, votes, likes and summaries, while the Activity log and the
24-hour boost slot still lived in a top-level `activity.py`. Now each file owns one thing:

| file | owns |
| --- | --- |
| `social/activity.py` | the log: `record`, `log_play`, `listen`, `feed`, `day_key`, the kinds |
| `social/boosts.py` | the 24-hour slot: `boost`, `current_boost`, `active_boosts`, `BOOST_TTL` |
| `social/comments.py` | comments, votes, summaries, `annotate`/`album_id`, `validate_target`, `schema` |
| `social/likes.py` | a like on a comment |
| `social/discussion.py` | a re-export facade over `comments` + `likes` |
| `social/service.py` | the only entry point (`ARCH-005`); it delegates and defines nothing |

The split is internal, which is the part that is easy to get wrong. Top-level `activity.py` is now a
`sys.modules` alias of `social.activity` — the same pattern `discovery/*.py` uses for `repo/*` — so
`import activity` from the queue, Subsonic, the web routes and the tests is the *same module
object*, not a copy, and a patch through the old name reaches the code that runs. `social.activity`
re-exports `BOOST_TTL`, `boost`, `current_boost` and `active_boosts` from `social.boosts` for the
same reason. `social/discussion.py` re-exports rather than reimplements, so `social/__init__.py`
and `social/api.py` were left untouched.

One real trap, found by the tests: `social/__init__.py` re-exports the *function* `comments`, and
that package attribute shadows the submodule of the same name — `import social.comments as _comments`
binds the function. `service.py` asks the loader for the module instead, with the reason written
beside it; the test file does the same.

Boundary unchanged: no `social/*` module imports `library`, `subsonic`, `listening`, `server` or
`party`, and `tests/test_social_service.py`'s import check still passes.

Tests: `tests/test_social_split.py` is new (file ownership, object identity through both old paths,
delegation targets, boundary) and `tests/test_social_service.py` was repointed at
`service._boosts` / `_comments` / `_likes`. Activity + Social + SDK subsets: 128 tests OK; Party +
tempo + Subsonic + queue subsets: 158 tests OK; the full Python suite: `Ran 1535 tests ... OK`
(skipped=1). Live: autodeploy restarted `hardlylistening.service`, `/api/health` 200,
`/api/social/track/1`, `/api/feed` and `/api/boost` answer 401 rather than 500 under auth.

Board: 128 cards, 121 done, 2 to do (HL-204, HL-210), 5 blocked. `KANBAN.md`, `BACKLOG.md`,
`PLAN.md` and `docs/server-domains.md` agree.

## Dependency audit: two "blocked" cards had already shipped — 2026-11-13

The board said 1 actionable card and 8 blocked. Three of the eight were waiting on work that has
since landed, so each was checked against the code rather than the label.

**HL-206, party chat — shipped, and the blocker was a decision that went the other way.** Phase 7
of the Subsonic plan already says party chat and Discuss stay separate ("merging would put room
chatter on song pages or strip discussion of its target"), so the party-scoped `party_chat` table
is the intended store, not a shortcut — guests are 24-hour identities and their messages must not
reach a member's Activity log. `POST`/`GET /api/party/<id>/chat` answer, the newest 500 rows per
room are kept, author name and icon come from the server's identity (a submitted profile is
refused 400), and the UI renders names and bodies as text nodes. `tests/test_party.py` (24) and
`tests/test_party_ui.cjs` pass. The two Subsonic endpoints it does *not* cover are carded as
HL-210 instead of being quietly folded into the closed card.

**HL-303, Auto DJ from BPM — shipped, blocked on a dependency only half its scope needed.** The
card waited on HL-205 because that is where numpy arrives, but tempo is stdlib work and landed
long ago: `library/analysis.py` fits a beat grid, `library/tempo.py` stores `bpm/bpm_conf/
bpm_phase/bpm_source/bpm_at`, and 771 of 985 live tracks carry a BPM. The picker reads it —
`autoDjFold` folds a candidate into the current octave (174 blends with 87), `autoDjScore` rewards
a log2 distance under `AUTODJ_TEMPO` and subtracts drift, `autoDjPick` skips the last
`AUTODJ_RECENT`, `autoDjSeed` draws from the queue when it is a real set and the library otherwise,
and `autoDjEnsure` keeps `AUTODJ_AHEAD` queued so the panel, the prefetch and the crossfade agree
on the next track. `tests/test_autodj_pick.cjs`, `tests/test_dj_autodj.cjs` and
`tests/test_tempo.py` pass.

**HL-204, jukebox — unblocked, not done.** It waited on "party audio ownership", which shipped as
`party.control()`'s aux host: a 30-second `host_until` lease with a hashed `host_token`
(`claim`/`heartbeat`/`next`/`release`), and `web/party.js` plays the room on that device. So a
remote client has exactly one deck to queue to. What is missing is breadth — `set`/`start`/`stop`/
`add`/`clear`/`remove`/`shuffle`/`setGain`, the deck bar, per-guest playback when a heartbeat
lapses, and the 1:1 `jukeboxControl` mapping — plus two races to settle first (a second deck
claiming, and advance vs. vote). The card now sits in To do with that scope written down.

**HL-205 stays blocked, but narrowed.** Tempo is done; the card is the vector half — log-mel/chroma
fingerprint, cosine neighbours, `getSonicSimilarTracks`/`getSimilarSongs*`/`findSonicPath` — which
is the only part that wants numpy as a runtime dependency.

`KANBAN.md`, `BACKLOG.md`, `PLAN.md` and the Subsonic surface plan now agree: 128 cards, 120 done,
3 to do, 5 blocked. No application code changed in this pass, so the only tests run are the ones
that prove the three cards: `tests/test_party.py` + `tests/test_tempo.py` → `Ran 24 tests ... OK`,
`tests/test_autodj_pick.cjs` → PASS, `tests/test_dj_autodj.cjs` → PASS.

## UI-028…029: releases can be folded back open, Subscribed becomes a card — 2026-11-13

Two Repo sections, one cause each.

**A collapsed release group had no way back open.** The head's portrait and name both open the
artist's catalogue page, so folding everything else into the peek strip left a faint chevron
under a faded preview as the only affordance, and nothing at the row's edge said what pressing
it would do. The head now ends in a 30 px `.feedgfold` button — always drawn, rotating 180°
while open, its `aria-expanded`, `aria-label` ("Show 4 releases from …") and title kept in step
with the head by `toggleGroup()`. The peek strip says `Show N releases` instead of a bare
arrow, and the whole strip is the target. The preview also stopped pretending to be disabled:
the veil is 38 px instead of 56 and the peek cards are no longer dimmed. On a phone the head's
plain date was hidden (`.feedgmeta > span:not(.pill)`) because it squeezed the name once the
fold took its 30 px. Measured at 390 and 1280: both controls flip `aria-expanded` false → true
→ false, labels stay in sync, the name is 137 px at 390 and unclipped.

**Subscribed was three bugs wearing one row.** The name reused the header's user chip `.who`,
so every follow wore a pill and a green status dot; the meta line was inside a zero-width box;
and both containers carried `data-admin-only`, whose `display:revert !important` turned their
flex rows into blocks of inline buttons. `.subrow` is a grid now — 40 px `.subart`, `.subid`
with `.subname`/`.submeta`, and `.subacts` on the right — with `data-admin-only` moved onto the
buttons themselves. The subscribe form `.followbox` hides through `html.is-admin` instead of
the attribute, for the same reason. On ≤640 px the controls take a full-width line under the
identity. Measured: 362×150 at 390 with the name at 276 px and the meta wrapping, identity left
and controls right at 1280.

`node tests/visual/snap.cjs` → **Visual baseline PASS**; `web/sw.js` cache is
`hardlylistening-static-v18`. Nothing is built: `web/` is served as-is.

## UI-025…027: the compact scrub is the visualizer, and the clocks stand on the base — 2026-11-13

Three changes to one bar, each measured in the fixture.

**The scrub is the wave.** The visible range slider is gone. `#plseek` keeps its id, its
keyboard and its screen reader, and is stretched invisibly over the `.plwave-seek` canvas
(`left:85px;right:95px;bottom:0;height:65px`, transparent track, a 0×0 thumb,
`touch-action:none`). It sits at z-index 1, under the transport's 2, so the buttons that overlap
the rectangle's top edge still take their own presses — the volume button opens its popup with
the seek stretched underneath it. `:focus-visible` paints the accent outline on the real input.
The feedback is drawn where the finger is: `markScrub()` reads `progress()` on pointerdown, input
and pointerup, keeps the bars lit up to the finger, lifts the bars past it to alpha 0.82, and
stands a 2 px light playhead (`#eef1f6`) over the wave band (0.575 × canvas height) on the base —
light, not accent, because the bars either side of it are accent and a same-colour line vanished
into the lit edge. `scrubHeld` holds that playhead from `pointerdown` until
`pointerup`/`pointercancel`/`lostpointercapture`, so a finger resting mid-drag keeps it; only a
released control fades after 260 ms, which is therefore the keyboard seek's case (`input` alone).
With no decoded envelope yet, a flat 3 px progress line carries the same light line, 21 px tall
around it. Measured: a tap at 50 % of the rectangle seeks to 1.5 s of a 3 s fixture; a drag to
45 % reports value 450 exactly; the volume button that overlaps the rectangle still opens its
popup.

**The clocks moved down to the base.** With the slider gone, `.ptime` went from `bottom:19px` to
`bottom:4px`, so `#pltime` and `#pldur` stand on the line the bars stand on instead of floating
above it. Measured at 390: clock boxes y 776–792 against wave x 100–280 with 1 px gaps; at 1280:
y 764–780 against wave x 190–1080; mute does not overlap at either width.

**The rest of the same pass.** `#plvolmore` sits right of mute and opens a fixed 46×132 vertical
fader 10 px above the button, centred on it, sliding out on opacity + translateY over 160/220 ms,
closing on outside click, Escape or resize, and hidden under `.no-volume`. `body #player.on
.ptrans` gained `margin:0 -6px 0 0`, which puts the play button on the bar's centre line: 198
against 195 at 390, 218 against 215 at 430, 643 against 640 at 1280. The fullscreen play button
carries its progress as `#player-full #pfplay::after`: a conic accent ring on `--pf-progress`
(0–1) in a 3 px masked band — dim at 0, top + right + bottom at 0.5, a full circle at 1 —
verified by pixel sampling.

`node tests/visual/snap.cjs` → **Visual baseline PASS**; the baselines were re-captured. Nothing
is built: `web/` is served as-is, so all three are live on reload.

## The fullscreen foot spreads to the row's edges, and the profile panel hangs off its chip — 2026-11-13

His calls, in order. The foot was one centred row; it is now a `1fr auto 1fr` grid — Cue and the
equalizer on the left edge, the queue alone on the screen's centre line, save slot and heart on
the right edge. The row is `width:720px` (capped at `100vw - 40px`) and `align-self:center`
inside the 560 px body, because at 560 the right group's 255 px overflowed its 242 px lane and
ran 3 px into the queue; 720 puts the sides where he wanted them and leaves 67 px of slack.
The desktop volume fader then moved out of the right group onto its own centred row under the
foot: it is 90 px wide and 118 px with its speaker, which was the group's overflow in the first
place, and a row of its own reads as the row's volume rather than a fourth button. Measured:
queue centre = screen centre at 1920/1280/1024/761 (960, 640, 512, 380.5), `.pf-end` 111 px
flush to the foot's right edge, fader centred on the same line, no horizontal scroll at any
width, fader hidden below 761 px. `snap.cjs` baselines re-captured for fullscreen/queue at
1280 (the added row lifts the centred stage) and they pass; the phone states did not move.

The profile panel (`.usermenu`) was pinned to the bar's right edge (`right:16px`), 150 px wide,
four text rows with Sign out styled like every other item. On a phone the chip wraps onto a row
of its own at the left, so the panel opened nowhere near it. It now hangs off the chip — `#who`
and `#usermenu` share `.userwrap{position:relative}` — and `placeUserMenu()` chooses the edge
(left half: open rightward from the chip's left edge; right half: open leftward under its right
edge) and clamps it inside the viewport. Rows are 34 px with a 2 px rhythm in a 208 px panel, and
Sign out sits behind a full-width rule, muted until hovered and red on press. `sw.js` cache →
v15. `tests/test_usermenu_stack.cjs` now loads the real page (the stripped document could not run
the placement) and checks both a short and a long display name at 390/760/1280: inside the
viewport, painted over the nav, and aligned to the chip's edge on the side that has the room.

## LIB-001: the personal tables leave the Subsonic file — 2026-11-13

`listening.py` was three things at once: device credentials, the Subsonic surface, and the SQL for a
member's saved scope and folders. The third is Library's, so it moved: `library/saves.py` (saved
tracks and playlists, the download intents that fill the library once a file exists, `snapshot`),
`library/folders.py` (the personal tree, with the name rules as rules and the same 400/404/409
answers), `library/playlists.py` (member playlists: ordered membership, optimistic `expected`
check, owner-only writes in one transaction) and `library/api.py` (the two listening endpoints'
request handling, the same JSON as before).

Two things that had to be got right rather than moved:

* **The rate limit's identity.** `library/api.py` first imported the module-level `SECURITY`, which
  counted `listening-write` on the real singleton instead of the instance the server authenticated
  with — `test_cues` then hit 429 because the injected test security had never seen the writes. The
  adapter now takes `security` as an argument, the same object `listening.browser` was handed.
* **The M3U projection's name.** `library/playlists.py` already existed and means the playlist
  views written into the library root; the domain card asked for that name. The projection is now
  `library/playlist_files.py` and `core/db.py` schedules *that* — the test reads the import so a
  later edit cannot quietly re-point it at the domain.

`listening.want_playlist` / `deliver_track` / `forget_track` and `user_playlists.listing` stay
importable and delegate, so `engine/queue.py` and `server.py` did not change. `tests/test_library_split.py`
pins the files, the delegation, the route-to-adapter wiring, and that the new files do not import
acquisition, Party or Social.

## The compact bar's add-to-library slot sits under the heart — 2026-11-13

His call, twice: the slot belongs on the time line, not in the top-right cluster. It is absolutely
placed at `right:11.5px; bottom:13px` (29 px wide), which centres it 26 px in from the right edge —
the exact centre line the heart uses at `right:9px; width:34px` — so the two read as one column and
it clears the clocks (`#pldur` ends at 50 px in) and the scrub (ends at 95 px). Measured in the
fixture at 390 and 1280 wide: save centre 349 / heart centre 349 at phone width, 1149 / 1149 at
desktop, with no overlap against the clocks, the seek or the artwork. `snap.cjs` still passes — its
stub paints no save slot, so the baselines never covered this control.

## REPO-014: Repo becomes files, not a folder of one file — 2026-11-13

`discovery/` held the artist page, the tracklist reader and following an artist; `repo/service.py`
reached into it. The split is internal — every facade signature is unchanged — so the risk is not
behaviour, it is *identity*: a copy of a module means a patch through the old import path no longer
reaches the code that runs, which is exactly the ARCH-009 mistake in a different shape. So the
implementation moved to `repo/repository.py`, `repo/catalogue.py` and `repo/subscriptions.py`, and
`discovery/*.py` became three-line aliases ending in `sys.modules[__name__] = _impl`.

Beside them, `repo/acquisition.py` is the Repo-side name for the one operation the API performs on
the worker (`ingest`, signatures and refusals unchanged) and `repo/api.py` holds
`subscriptions_for(handler, store)` — the "who may call this follow theirs" rule, read off the
request. `server.py` now ingests through the facade, so no route types `queue.ingest` any more.

`tests/test_repo_split.py` pins the two things a split can break: `discovery.artist_profile is
repo.repository` (and the other two pairs), and `repo/service.py` no longer imports `discovery` at
all. Full suite: **1515 tests OK**; the live database hash is unchanged afterwards (the ARCH-009
test guard holds). `docs/server-domains.md` now says which file owns which read.

## The wordmark is Home — 2026-11-13

Asking for "click HardlyListening to go to the Library page" is asking for the one control every
other app already has, so it is not a new route: `#brandhome` is the existing `.brand` as a button,
and its handler clears the open detail page and clicks the Library tab that is already wired —
`closeDetail()` then `.tab[data-view="library"][data-scope="mine"]`. No second navigation path to
drift out of sync, and the remembered scope, the nav's `on` state and the reload landing all follow
from the tab's own handler.

The CSS is the part that could have gone wrong. `button` carries `min-height:40px` in `base.css`,
so a naive `button.brand` made the header nine pixels taller and moved the whole page down; the
baseline caught it (`compact-*`, box from y=14). `button.brand` therefore resets `min-height:0`,
`padding:0`, `line-height:inherit` and the border, keeping the type exactly where the `div` had it,
with the accent only on `span` and an underline on hover instead of a filled control. `sw.js` is
`hardlylistening-static-v14` so the shell is re-fetched.

`node tests/visual/snap.cjs` → **PASS** (header height 47 px, unchanged). Behaviour proven in the
fixture at 1280 and 390 wide: from an album detail page, a brand click closes the detail, shows
`#view-library`, lands scope `mine`, `hl.view=library`, and logs no page error.

## ARCH-009: one module identity, and the mistake that bought it — 2026-11-13

`core/` now holds `security.py`, `config.py` and `db.py`; the top-level names stay the import
every module uses and are *aliases*, not copies: each one does `sys.modules[__name__] = core.x`,
so `import db` hands back the very object `core.db` is, and `mock.patch.object(db, "DB_PATH")`
reaches what `server` and `deletion` read.

**The first attempt was a star-import shim and it wrote to the live database.** `from core.db
import *` created a second set of attributes. The suite's `mock.patch.object(db, "DB_PATH", …)`
patched the copy, `server`/`deletion` kept reading `core.db`'s path, and two full runs therefore
ran against the real library: `download_jobs` 947 → 6, `settings.root` re-pointed at a temp
fixture, and test tracks, releases, playlists, members and Activity rows written into the real
tables. Nothing else was supposed to happen before the whole suite went green; that rule was the
one thing standing between a refactor and his data.

**Repair, from the 2026-11-12 backup.** Every real track row was intact (998, and every file on
disk still had its row), so the loss was queue history plus five settings. Restored `root`,
`disk_limit_pct`, `audio_format`, `pacing` and `ai_apple_enabled` from the backup and re-inserted
its `download_jobs` (944 of 947 — the three `review` rows whose tracks now have files stay out as
stale). Deleted only the rows the run created, one DEL line each: 78 tracks, 7 releases, 8
playlists and their 9 memberships, 2 members, 3 `member_items`, 1 swipe, 2 trash and 30
`library_deletions` entries, 13 `source_picks`, 1 `match_decisions`. Kept the 44 Activity rows and
the 329 `listen_events`, the cues, EQ, playback and favorites — that was real usage, and the job
ids 1038–1068 created between the backup and the damage are gone for good.

**Two tests so it cannot repeat.** `tests/test_core_aliases.py` pins the identity (old name is the
module, a patch through it is visible in `core.x`, string patches land on the same object, and
`APP_DIR` is the checkout rather than `core/`). `tests/test_db_guard.py` pins a guard inside
`db.connect()`: a process that has imported `unittest` and resolves to the install's own database
is refused before SQLite is opened, compared against the path captured at import so a shim copy is
caught too; `HARDLYLISTENING_ALLOW_LIVE_DB_TESTS=1` is the deliberate-read hatch.

Suite after the repair and the move: 1497 tests OK, live DB byte-identical across the run.

## Five cards, and the first time the rollback was actually run — 2026-11-13

The board's last five actionable cards were one chain plus two loose ends: federation's mirror,
radio's scope, and an autodeploy path nobody had ever watched fail.

**Radio: the code was right and the plan was stale.** `listening_radio_stations` carries
`member_id`; every read, update and delete filters on it, so another member's station id answers
`70` rather than a row. The plan still promised "one shared list". Decided: **member-scoped**, and
for a reason that outlives the doc — a `stream_url` can embed a credential, so a personal list must
not be readable or editable by another member. The plan now says so, says what is not built (the
More-menu page, the `getMusicDirectory` Radio folder, the HEAD/ICY probe — there is no request to
guard, because nothing is fetched server-side), and `tests/test_subsonic_phase34.py` fails if the
document ever promises the shared list again.

**The mirror is three decisions, not one.** `repo/mirror.py`:

* **002** — a descriptor becomes a request. `plan()` returns a `url` when the descriptor names a
  page *its own provider claims*, or a `search` when it does not; an unknown provider, a
  metadata-only provider (`apple`) and a page the claimed provider disclaims are each refused with
  a reason that names no URL. `matches()` then judges the answer: the provider's own id settles it,
  otherwise duration within `max(3 s, 2 %)` and title similarity ≥ 0.5 — and an unknown duration is
  reported as *unverified*, not as a mismatch.
* **003** — a copy comes only from an address an operator pinned (`{base_url, node_id}`), https
  only (loopback behind `HARDLYLISTENING_ALLOW_LOCAL_PEERS` for the tests), no redirects, 64 MiB cap
  checked while reading, SHA-256 as it arrives, atomic `os.replace`. **The serving half is not
  built**: handing music to a peer needs peer authentication, and the node identity still has no
  signature scheme. The contract this side calls is written into `docs/federation.md` §5 so the two
  halves cannot disagree later.
* **004** — `metadata` (default, fetches nothing), `cache` (on play), `selected`
  (`tracks.mirror_selected`), `all` (every import). An unknown stored value is reported `invalid`
  and refused, never defaulted: a typo must not be the thing that starts mirroring a library.

`GET /api/federation` is the read-only view of that — and it deliberately does **not** mint key
material; `identity/` stays empty on an install that never federates.

**Autodeploy: rollback, measured.** `tools/autodeploy.sh` was already written to refuse a file that
will not parse and to roll back when health never answers; neither path had ever run. Its four
paths and the health address are now `:-` defaults, so `tests/test_autodeploy.py` can point it at a
throwaway tree with stub `systemctl`/`curl`/`date`: baseline restarts nothing, a Python change
restarts once, a web-only change does not restart, a syntax error restores the previous Python and
leaves the service alone, and a service that never gets healthy is rolled back, restarted again and
the state left at the last version that worked. Two real defects fell out: the script depended on
the unit's `WorkingDirectory` (it now `cd`s itself), and two changes inside one second shared a
backup directory, so the second overwrote the copy a rollback would need (now `-$PID`).

## A switch the server actually reads — 2026-11-13

**SERVER-006 — the two rows that were an apology are now controls.** HOST-005 shipped a settings
panel that listed "web UI on/off" and "OpenSubsonic on/off" as *unsupported*, with the reason
written out: the server served `web/` on every start and dispatched `/rest/*` unconditionally, so a
stored flag would have been a promise the next start broke. That was honest, and it was also a
card. Now the server has the switches, so the window stores them.

**Server first.** `config.web_ui()` and `config.subsonic()` read `HARDLYLISTENING_WEB_UI` and
`HARDLYLISTENING_SUBSONIC` — on unless `0`/`false`/`off`/`no`, and a value that is none of those is
**refused** rather than read as truthy, the same rule a bad port already followed. `--no-web-ui`
and `--no-subsonic` are declared with `dest=` and `store_false`, so the parser returns *what to
serve* and `main()` writes it into `server.FEATURES` once, before the socket binds: no request ever
sees a half-gated server, and there is no second negation to get wrong.

**Off means less, not broken.** The web-UI switch covers `web/` — the pages, scripts, styles and
PWA assets — through `static()`, the one function that serves them, which answers `404` with
`The web UI is disabled on this server.` `/api`, `/rest/`, `/api/health` and the rendered
`/s/<token>` share pages are not the web UI and keep answering, so a headless install stays usable.
Off also skips the `/` → `/login` redirect: sending someone to a sign-in page that 404s is worse
than telling them the truth. OpenSubsonic off answers the *protocol's* refusal —
`subsonic-response`, `status: failed`, error code `0`, "OpenSubsonic is disabled on this server" —
in the format the client asked for (`f=json` or XML), because a bare 404 reads as a broken network.
`/api/v1/capabilities` now reports a real `subsonic.enabled` and publishes `web_ui.enabled` beside
it; the endpoint exists so a client can hide what is not there, which only works if it cannot lie.

**Then the window.** `resolve.Features` is the one reader of the two variables, and its vocabulary
is compared to `config.py`'s value by value — a window that read `HARDLYLISTENING_WEB_UI=no` as on
would contradict the server it starts. `Settings` gains the two flags, `features()`/`flags()` and a
`save()` that writes JSON booleans only: `"false"` is refused, because string truthiness is how a
`"false"` becomes on. A field pinned by an environment variable is refused with the variable named,
exactly as the address already was, and no patch may carry `autostart`. The `--no-…` flags reach
both places that start a server: `build_launch()`'s argv and the autostart entry's `Exec`.
`settings.rs` mirrors all of it; the crate still cannot be compiled here, so `rustfmt --check` is
the whole proof of the Rust half, and the panel's decisions are driven in Node.

**Verification.** `tests/test_server_switches.py` (ten tests): the parser's defaults and each flag
on its own, `404` with the sentence for pages and assets, the API and health probe unaffected, both
controls with the switch on, and the `/rest/` refusal in JSON and XML with `subsonic.enabled`
following `FEATURES`. Settings, lifecycle, config, capabilities and the Node harness updated with
it. Pub: 1336 tests OK, `rustfmt --check` clean. Live: suites re-run, service restarted and healthy
on `:8470`, `/api/v1/capabilities` answering `web_ui {"enabled": true}` and
`subsonic.enabled true`.

**One honest limit, and one loose end found on the way.** The web-UI switch gates the `web/` app;
share pages under `/s/` are served by a different path and are deliberately left reachable. And
`tests/test_pwa.py` had been failing since PLAYER-004: `index.html` loads `/js/playback.js`, but
`sw.js`'s `SCRIPTS` list was never extended, so the worker precached one script fewer than the page
uses. One line added; the precache check and the visual baselines both pass.

## The delete that waits in a folder — 2026-11-12

**HL-306 — a delete is now a move.** Deleting unlinked the file: one click and the audio was gone,
and an import whose source had also gone could never come back. A delete now renames the file into
`<root>/.trash/` — same filesystem, so the rename is atomic and costs no space — and a
`library_trash` row remembers the path it came from. Restoring a song renames its own file straight
back, so it neither downloads nor re-copies; a playlist or album delete, whose rows are gone by the
time you would ask, is restorable one file at a time through the trash row. The folder is hidden,
so the library scan, the art sweep and folder import all skip it: a trashed file is never adopted as
a loose track or mined for a cover.

**The ceiling is still the ceiling.** A trash that never shrinks would make the 95% guard a lie, so
trashed files count towards disk usage and the queue empties them oldest-first once the drive passes
`disk_limit_pct`. That is the one reclaim that takes nothing away from the library — everything in
there was already deleted — and each purge is logged with the file's title rather than disappearing
quietly. Settings reports the count and size, and "Empty trash" is the only irreversible delete left,
so it asks first.

**Verification.** Pub: 768 Python tests OK (nine new trash tests: the move, the rename-back, the
per-file restore, the outside-root refusal, emptying, and the ceiling purging oldest-first). Live:
the same suites after shipping; service restarted and healthy on `:8470`; the live database gained
`library_trash` on boot and holds no rows yet.

## A kept link, and five gigabytes that are nobody's to prune — 2026-11-12

**HL-207 — the link comes back.** A share exposed its token exactly once: only the SHA-256 was
stored, so closing the dialog lost the URL and the only recovery was revoke-and-recreate. The token
now sits beside the hash; `GET /api/shares` returns it and Subsonic `getShares` sends a real `url`
instead of an empty one. Lookup is unchanged and still by hash, so nothing about how a public
request is authorised moved — and whoever can read that row could already read its target, so the
token adds no reach. A row created before the column existed keeps `token=''` and the listing says
`recreate: true`, which is the honest answer rather than a `/s/` that cannot work. The schema mirror
in `shares.py` also ALTERs an old store, because `CREATE TABLE IF NOT EXISTS` never adds a column.

**HL-209 — 5 GB per member, no automatic pruning.** `downloadPodcastEpisode` answered error 30
because there was no storage policy. The policy is a hard per-member budget
(`HARDLYLISTENING_PODCAST_CAP_MB`, 5 GB default) counted over the files this server actually holds
for that member, checked before and during the copy. At the cap the download is refused with the
numbers — the server does not delete somebody's episode to make room for another one. Deleting an
episode unlinks its file, so the space genuinely returns, and `getPodcasts` reports the budget and
the files inside it. Two details matter for the cap being real: the feed's declared `length` is not
trusted (a wrong one would refuse a file that fits; the streaming bound is what must be right), and
one download runs at a time per member, or two parallel requests would each read the same headroom.
Held files are state, not cache — declared in `paths.py` as `podcasts/<member>/` and added to the
backup boundary, because a cache clear must not silently delete what someone chose to keep.
`streamPodcastEpisode` serves only a held copy, with Range.

**Verification.** Pub: 759 Python tests OK, including the two new suites. Live: the same suites
plus the full suite green after shipping, service restarted and healthy on `:8470`. The live
database had no share rows and no podcast tables, so the migration added a column to an empty table
and the podcast tables are created with the new columns on first use.

## Four small cards, and a board that was lying — 2026-11-12

**Ticks, not work.** Sixteen cards were shipped and still ticked open: `ARCH-007`
(`tests/test_isolation.py`), `REPO-010…013`, `SUB-010…013`, `UI-020…024`, `HL-410` (Google
sign-in), `SERVER-001`. Each was verified against the tree before ticking — the routes call
`repo.service`, the Subsonic adapter delegates to `library.service`, `test_repo_tabs` pins the
three-section Repository, `/auth/google` and its link/unlink pair exist, and
`bin/hardlylistening-server` runs the core alone. `SUB-011` is ticked with a correction: there
is no separate `PlaybackService` — the member-state writes went to `library.service`, the
domain that owns those tables. `SUB-012` is held by the full suite being green on both trees.

**SERVER-005 — the core is asserted headless.** `tests/test_headless.py` parses every core
module and checks its imports for a GUI toolkit (a nested import still means the desktop is not
optional, and a grep misses it), asserts the core carries no `src-tauri`/`package.json`/
`web/dist`, and boots the real entry point with `DISPLAY`, `WAYLAND_DISPLAY` and
`XDG_SESSION_TYPE` removed to ask `/api/health`, `/rest/ping.view` (the Subsonic envelope,
auth refusal included) and the web UI. `docs/server-packaging.md`'s release gate now says the
two headless items are asserted rather than assumed.

**HL-304 — `verifying` is a state.** yt-dlp exiting 0 is not proof of a track; ffprobe
decides. While it read the file the row sat at "downloading 100%", which is exactly a hung
queue. It is a real job state now: counted as running, named in the snapshot, "checking the
file…" with no bar, card badge "Checking the file", requeued when a hard kill leaves it, and
neither a decision reverse nor a source swap may run under it.

**HL-305 — a cooldown belongs to its source.** The loop waited on the latest cooldown across
every provider, so one 429 stopped the whole queue. `next_job(blocked=…)` skips only the
provider that asked to be left alone, and `blocking_cooldown()` reports the wait only when a
source with queued work is the reason nothing starts. Still one job at a time; two downloads
at once is a different change with its own cancel and rate-limit questions, and the card says so.

**HL-309 — the shell opens offline.** The worker keeps the entry page and the last
`/api/library` in the versioned cache, so a deploy drops the old shell instead of serving it
over new scripts. Two boundaries held: the page is stored only when the response is the app
itself (the `data-hl-shell` marker on `<html>`, no redirect) — a sign-in page is a 200 too and
must never overwrite it — and only that one API is kept, never credentials, member data or
artwork. A fixed bar says the library is a kept copy.

**Verification.** Pub: 733 Python tests OK, the four new files included; `test_queue_ui`,
`test_offline_shell` and `test_offline_cache` pass. Live: the same Python and DOM suites pass
after shipping, and the full live suite is green. One latent break fixed on the way: the queue
DOM test had been failing since the admin-chrome split (`adminUI` was never stubbed), so it is
green for the first time in a while — `tests/test_queue_ui.cjs` now carries the stub plus the
verifying-state checks.

## JS-032 — the library's drawing layer leaves the inline script — 2026-11-12

- **`web/js/library.js`** (1091 lines live) holds everything that turns library state into
  markup: `groupTracks()`, the songs list/cards/grid renderers, the album and artist walls,
  `playlistCard()`, the album/playlist/artist detail views and their editors, and the two-step
  delete controls. State (`lib`, `libQueue`, `playingId`), the view/layout/sort choices, the
  density control and the boot/poll stay inline — the file reads them at call time. That
  remainder is the next slice; the file header and `docs/UI_COMPONENTS.md` both say so.
- **The split tool refuses to write** if the moved block — at load depth — paints, reads a
  `const` from the inline script, or touches `SVG`. What it does carry at load is constants and
  `document` listeners, neither of which needs markup that is not parsed yet.
- **Pixel proof.** `library-shot.cjs` renders the library against one stub API from three trees
  (pre-split HEAD, pub after the move, live after the move): byte-identical shots, desktop
  `652322ef…` and phone `da370c25…` all three. `index.html` 413 KB → 356 KB.
- **Tests moved with it.** `tests/_page.cjs` assembles the page's JavaScript once (inline +
  every `web/js/*.js`) so a slice does not have to know which file its block landed in;
  `test_sort` and `test_song_table` use it, `test_delete_ui` reads the file that owns the delete
  helpers and asserts the page loads it. `sw.js` precaches `library.js`, cache `v8`.
- **Verification.** Pub: 710 Python tests OK, `test_pwa`, the ten UI suites touching the library
  and Activity, visual baseline PASS. Live: 794 Python tests OK, the same ten suites,
  `snap.cjs` PASS, library render byte-identical. `test_song_grid` / `test_song_table` /
  `test_song_layouts` / `test_library_ui` / `test_personal_library_ui` / `test_sort` failures
  were reproduced on the pre-JS-032 worktree and are pre-existing. Commit `96ace65`.

## JS-031 — Activity leaves the inline script — 2026-11-12

- **`web/js/social-feed.js`** owns the Activity feed: the day cards, the boost pin, the member
  profile dialog — and, on the live tree, that dialog's listening numbers, which the pub tree
  does not carry. Loaded before the inline script like `repo-discovery.js`, so every name stays
  global. `index.html` 443 KB → 413 KB.
- **The split tool has a load-time guard.** It refuses to write if the moved block looks up
  markup or reads a `const` from the inline script at load — the two things that break when a
  file moves earlier. Depth-aware, because a query inside a function is fine. The live block
  registers two `document` listeners for the stats rows at load; those need no markup and no
  `const`, so they are allowed, and the file's header says so rather than claiming "definitions
  only".
- **Tests moved with it.** `test_activity_day_card` and `test_boost_ui` slice the block out of
  a file; they now read `web/js/social-feed.js`, and one asserts the page still loads it, so a
  file nothing loads cannot pass. `sw.js` precaches it, cache `v7`.
- **Verification.** Pub: 710 Python tests OK, `test_pwa`, the two Activity tests, `test_repo_tabs`
  / `test_feed_artist_page` / `test_artist_catalogue_ui`, visual PASS. Live: 794 Python tests OK,
  the same set, `snap.cjs` PASS, plus a browser smoke that boots the real page with the file
  external, paints a day card and the boost pin, opens a card and the member dialog with its
  listening numbers, with no page error on desktop or phone. `test_top_nav`'s one failure is the
  known one (the row lists the same sections in the same order). Commits `8553036`, `fea7b8a`.

## JS-030 — the release, follow and artist half leaves the inline script — 2026-11-12

- **`web/js/repo-discovery.js`** (903 lines) now owns releases, the follows, the artist/release
  pages. It is a plain classic script loaded before the inline one, so every name stays global
  and no call site changed. `index.html` is 484 KB → 443 KB.
- **One block had to stay behind.** The load-time DOM wiring (the feed provider switches, the
  refresh and "mine" icons) needs `SVG`, a `const` in the inline script that does not exist
  while an earlier script runs. It stays in `index.html`, after `const SVG` — the position it
  effectively ran in before.
- **The service worker was the thing that broke.** `test_pwa` asserts the worker precaches
  exactly the `/js/` scripts the page loads; the new script made that false, and it failed
  honestly rather than silently shipping an app that cannot boot from the cache. `sw.js` now
  lists it and the cache is `v6`.
- **Pixel-neutral, proven.** Every state in `tests/visual/snap.cjs` renders byte-identical from
  the pre-split page and the current one. That is also how the six `CHANGED` results were
  explained: the live fullscreen/queue baselines were stale in the player bar (the live-only
  media-session work), reproduced by the old page, so they were refreshed. Not this change.
- **Verification.** Pub: 710 Python tests (the PWA failure fixed and rerun), `test_repo_tabs`,
  `test_feed_artist_page`, `test_artist_catalogue_ui` pass, visual PASS. Live: 794 Python tests
  green, the same three UI tests, `test_pwa`, `snap.cjs` PASS after the refresh. Commits
  `add5276` (mixer), `fb0c03a` (JS-030), `0853325` (baselines), `d5fae38` (sw.js).

## Mixer — the phone rail, and the songs that stopped by themselves — 2026-11-12

- **The stop had a cause.** The room sent a deck's anchor as server time (`at`), and
  `applyRemoteDeck` subtracted it from the phone's `Date.now()` to work out where the deck
  should be. Two unrelated clocks: with the phone behind, the difference landed past the end
  of the track, which fires `ended` — the song "stopped on its own" a minute in. The room now
  sends `position_now`, the position already resolved on the server's clock, and the page
  re-anchors it on its own clock as the event arrives. A tab driving a deck also no longer
  seeks itself on its own echo (`localDeckClock` guard), and no seek is clamped to the very
  end (`duration - 0.35`) — a rounding error there fires `ended` too.
- **The phone layout was two grids fighting.** `@media (max-width:860px)` pinned deck B into a
  second implicit column while the ≤900px block declared one column, so the deck row and the
  rail overlapped. That override is gone. The rail is a full-width strip under the decks, read
  top to bottom: CROSSFADER, meters, MASTER — each a full-width throw, with a hairline above
  MASTER so the two never read as one control.
- **Nothing removed.** Every label, chip, button and hint stayed; only padding, gaps and the
  two long paragraphs shrank. Desktop is unchanged apart from the same rail order.
- **Verification.** `tests/test_mixer_phone.cjs` (new): layout, real playback, and a room echo
  from a clock an hour ahead *and* an hour behind. It fails 5 checks against the old page,
  which is the regression it pins. `tests/test_mixer_room.py` +2 wire tests (30 total).
  Live: all mixer suites green after the restart, shots on phone (390×844) and desktop.

## Separation — REPO-012, UI-020…024 and ARCH-008 ship, the set is closed — 2026-11-12

- **Feed is Activity only.** Release pages and the follows were Feed sub-tabs; a release a
  followed artist put out is catalogue, so both moved. The shared Repository is now Browse /
  Releases / Subscribed (`#repotabs`), the personal Library keeps its one list, and the
  new-releases badge moved to the Repo tab. `.feedtabs` is gone, not hidden.
- **`/api/feed` asks Repo.** `repo/discovery.py` owns `releases()` and `release(id)`; the route,
  `do_feed_queue` and `do_feed_seen` go through it. Same rows, same order, same follow-name join
  — pinned row for row in `test_repo_discovery.py`, and `server.py` reading `feed_items` again
  fails the suite. No table rename in this pass.
- **`GET /api/v1/capabilities`** answers what the server does (server/api, Subsonic facts,
  feature flags) without a session, derived from the modules that implement it.
- **Core → Social is closed.** `security.py` publishes `events.MEMBER_JOINED` /
  `MEMBER_RENAMED`; `social.service.subscribe_events()` writes the Feed rows and `server.boot()`
  is the only place that wires them. Publishing never raises and needs no subscribers, so the
  server still boots — and people can still sign in — with Social absent. `security.py` naming
  `activity` fails `test_events.py`. The domain table's last live inversion is struck through.
- Also worth knowing: `paintFeedCount` was reading `feed_new` off the whole `/api/state`
  snapshot instead of `stats`, so the new-releases count had never painted. Moving the badge to
  the Repo tab is what surfaced it.
- **Verification.** Pub tree: 707 Python tests green, `test_repo_tabs.cjs` 30 checks on desktop
  and phone. Live tree: 770 tests green, `test_repo_tabs` / `test_feed_artist_page` /
  `test_artist_catalogue_ui` pass, `tests/visual/snap.cjs` PASS after a baseline refresh. That
  refresh is the honest note: the compact baselines had been stale since the More button became
  `...` — old web files reproduce the same six diffs, so it predates this work.
- **Known divergence, stated plainly.** The live tree carries work the pub tree does not
  (`activity.log_play`, `listen_events`, the media-session experiment in `web/index.html`); the
  pub tree carries the separation. Shipping applied the separation diffs to the live files
  rather than copying whole files, so neither side lost anything.

## Separation — ARCH-006, the routes stop keeping their own copies — shipped 2026-11-11

- `server.py` now reaches Repo and Social only through the facades. Repo: the `/api/state` snapshot,
  `/api/artist`, `/api/tracks`, `/api/catalogue`, `/api/subscriptions` and all five subscription
  actions. Social: `/api/activity`, `/api/member/<id>`, `/api/boost`, `/api/activity/listen`, and
  the `/api/social/*` adapter.
- The `mine` rule for follows now lives in one place. It used to be inline in
  `subscriptions_with_ownership`; leaving a follow, toggling one, and the ownership mark are all
  Repo now, so the endpoint and the feed cannot disagree about who owns what.
- `dispatch` moved out of `discussion.py` into `social/api.py`: parse → call one domain function →
  answer. The domain side no longer sits in the same file as `parse_qs` and a handler object.
- Behaviour is what the tests had pinned: full suite 631 tests, unchanged three pre-existing errors
  (`test_social_likes` ×2, `test_artist_follow`). A smoke boot on a fresh data directory answered
  `/api/state`, `/api/subscriptions`, `/api/artist?name=…` and `/api/catalogue?watch=1` (400 "no
  such follow", as before) through the facades; `/api/boost` still refuses without a sign-in.
- **Honest gap:** `security.py` still calls `activity.record` on join, rename and session events —
  the documented Core → Social inversion. Delegating routes could not close it: the fix is a domain
  event the composition root subscribes to, and that is a separate change. It is written in
  `docs/server-domains.md` as still open rather than counted as solved here.

## Separation — ARCH-002/005, Repo and Social get an entry point — shipped 2026-11-11

- `repo/service.py`: snapshot, artist page, releases, tracks, subscriptions with the exact `mine`
  rule the server had inline, follow pause/remove, and leaving a follow without stopping the
  tracking. `ARCH-002` is "no behaviour change", so every function is pinned in
  `tests/test_repo_service.py` against the call it delegates to — a facade that returns a slightly
  different dict is worse than no facade, because `ARCH-006` is about to point the routes at it.
- `social/service.py`: Activity (feed, record, listen), the 24-hour boost slot, summary/batch,
  comments, votes, likes and profiles. `social.py` became the `social/` package — `discussion.py`
  holds the domain functions and re-exported names keep `import social` working — because a module
  and a package cannot share a name, and the plan already drew `social/service.py`.
- The extraction inside `discussion.py` is the part worth checking: `dispatch` is now parse → call
  one function → answer, with `comments()`, `vote()`, `like()` and `comment()` callable without a
  request object. The existing 40 social/activity/profile HTTP tests pass unchanged, which is what
  says the page size, the like toggle, the moderation rule and the 404s all still behave.
- Both facades carry a boundary test that parses the file's imports: Repo must not import Library,
  Social or security; Social must not import Library, Subsonic, listening or server. That is
  `docs/server-domains.md` enforced rather than asserted.
- Deliberately not done: the routes still call their own code. `ARCH-006` is the card that makes
  adapters delegate; doing it here would mean two behaviour changes in one commit, and the second
  one is the one that would be untested.

## Packaging — SERVER-PKG-009 storage half: the documented layout is now real paths — shipped 2026-11-11

- The layout in `docs/server-packaging.md` §2 claimed `identity/`, `state/`, `tmp/`,
  `cache/metadata/` and `cache/federation/`; `paths.py` declared three of the eight paths. The doc
  described a tree that partly did not exist. Both now name the same paths.
- `IDENTITY_DIR` is created `0700`, separately from the loop, because mode on `mkdir` applies to the
  leaf and is unaffected by a `022` umask — which is exactly the property identity needs the day
  private keys appear.
- Nothing writes to `state/`, `tmp/`, `cache/metadata/` or `cache/federation/` yet. They exist so a
  volume missing one fails at `check` rather than inside a feature six months later.
- `check` reports identity only for a declared install (a checkout has none and `ensure()` creates
  nothing there), warns if the directory is group- or world-listable, and still creates nothing.
  `backup` prints the resolved identity path in its "not copied" block, so a database backup cannot
  be mistaken for a complete one.
- Proven on a real run, not just a unit test: a server started against a fresh data directory
  answered `/api/health` 200, created all eight paths, showed `identity` at `700`, and exited on
  `SIGTERM` with its documented shutdown. `tests/test_migration.py` seeds a `node-id` file and
  asserts it survives the move; `test_paths.py` asserts the mode and that an undeclared checkout
  gains nothing.
- **Not done, deliberately:** `node-id`, `private-key`, `public-key`. Generating keys for a
  consumer that does not exist is inventing a format that would later need a migration. That is
  FED-001.

## Packaging — SERVER-PKG-006, an installer that never deletes the library — shipped 2026-11-11

- `deploy/install.sh` (`install` / `uninstall` / `--dry-run`) creates the account, the directories,
  the code copy, the environment file, probes with `check`, installs the unit and enables it.
- Two promises kept literally: `uninstall` removes `/opt` and the unit and then prints what it kept
  — database, library and config survive, because removing a library is an operator's `rm`, never a
  side effect of uninstalling a service; and `--dry-run` routes every command through one `run()`
  so it cannot miss a step, because the honest way to review an installer is to read what it would do.
- The copy is the part that fails quietly — an exclusion one level too wide ships a live database
  into `/opt`, where the next upgrade overwrites it — so `tests/test_install.py` reproduces the same
  tar invocation and asserts both directions: `web/`, `bin/`, `cli.py`, `VERSION` present;
  `hardlylistening.db`, its `-wal`/`-shm`, `.cache`, `artist-art`, `backups`, `tests`, `docs` absent.
  The copied tree then has to pass its own `check` and must not create a database beside itself.
- That test found a real inconsistency in `check`: with a data directory that does not exist yet —
  the normal state before the first start — the database row said `fail` while the row above said
  "will be created". Both now judge by the nearest existing ancestor, so a first run reports
  "will be created" for the chain and a genuinely unwritable parent still fails (`/proc/nope/x` →
  `fail  /proc is not writable`), without creating anything.

## Packaging — SERVER-PKG-007/008, a container that is the same server — shipped 2026-11-11

- `Dockerfile`: `python:3.13-slim`, ffmpeg from apt, **yt-dlp from pip** — the archive's copy is a
  year behind the sites it parses, and a downloader that cannot read SoundCloud is the whole app.
  Non-root `uid 10001`, `/data` and `/music` owned by it, no second implementation of anything.
- `HOME=/data`, because yt-dlp and any provider caching cookies write under `$HOME`; at the default
  that cache lands on the container filesystem and vanishes with the container.
- `.dockerignore` excludes `hardlylistening.db`, its `-wal`/`-shm` siblings, `.cache`, `artist-art`
  and `backups`. A checkout holds a live database, and `COPY . /app` would bake somebody's library
  metadata into an image meant to be reproducible — verified: `/app/hardlylistening.db`, `/app/.cache`
  and `/app/backups` are all absent from the built image while `/app/web` is present.
- `deploy/docker-compose.yml`: `name: hardlylistening` (otherwise the project takes the directory's
  name), `/data` beside the file, media an absolute path, `127.0.0.1:8470:8470` so nothing is
  published publicly, `stop_grace_period: 30s` so SIGTERM reaches Python through the wrapper's
  `exec` and the documented shutdown order actually runs.
- The health check lives in the image, so native and container agree on what healthy means:
  `/api/health` answered. `check` is deliberately not the probe — it wants a writable music mount.
- Verified end to end, not just built: `docker build` succeeded; `check` inside the container reports
  `data /data`, `music /music`, yt-dlp and ffmpeg found, `result: ok`; a fresh container logged
  `library root seeded from HARDLYLISTENING_MUSIC_DIR: /music`, answered `/api/health` 200 (both the
  published port and Docker's own probe) and created its database at `/data/hardlylistening.db`.
  The 856 MB test image was removed afterwards; `docker build -t hardlylistening:local .` recreates it.

## Packaging — SERVER-PKG-011, the move is proven not to lose anything — shipped 2026-11-11

- The migration is deliberately small — copy the database and the caches, set
  `HARDLYLISTENING_DATA_DIR` — and that smallness is the risk: `paths.py` resolves four paths from
  the environment at import time, so one misspelled variable or a half-moved cache tree reads as an
  empty library rather than as an error.
- `tests/test_migration.py` builds a checkout-shaped instance (Repo: an artist, a release, two
  tracks; Library: a playlist and its one membership; an account with a credential; a settings
  root, an art row, a log row), moves it the way §11 says to, and compares the store opened through
  the packaged path against the store opened before the move — row by row, not "the file exists".
- It also asserts the cache directories resolve under the data directory afterwards, that a second
  start changes not one byte (idempotent migrations are what make an update a file replacement),
  that `HARDLYLISTENING_DB` still beats `DATA_DIR`, and that with nothing declared the four paths
  are exactly the old ones — checked by importing `paths` alone, because opening a store would run
  migrations against whatever database sits in the application tree and this suite must never touch
  a real one.
- Docs §11 now points at the test instead of asserting the same claim in prose, and the step about
  the Repository root names the seed variable that exists for it.

## Packaging — SERVER-PKG-010, a backup that is a database — shipped 2026-11-11

- `hardlylistening-server backup <dir> [--force]` copies the database with SQLite's online-backup
  API, so a running instance keeps serving and the copy is a consistent snapshot of every committed
  write. `shutil.copyfile` would be wrong twice: with WAL on, `hardlylistening.db` alone is not a
  database, and a byte copy taken mid-write can be torn.
- The source is opened `mode=ro` — a backup can never write to the database it is protecting — and
  the copy lands in `<name>.part`, is normalised to `journal_mode=DELETE`, integrity-checked, and
  only then renamed. A failed or torn copy therefore cannot replace the last good one, and an
  existing file is never replaced without `--force`.
- The journal-mode step is about the artifact, not the process: the page-1 header carries the
  source's mode, so without it the "backup" is a database in three files that a careless rsync can
  copy inconsistently.
- Scope is the database, and the output says so: media wants rsync or a snapshot, `identity/` gets
  its own line once SERVER-PKG-009 exists.
- `tests/test_backup.py` keeps a real writer open while the backup runs and then reads the copy
  back: settings and rows match, the copy opens standalone with no `-wal`, the source is
  byte-identical afterwards, a missing database is a refusal rather than an empty backup, and an
  existing copy is untouched without `--force`. Live check: 5.5 MiB, 1370 tracks, 7 playlists,
  947 jobs, integrity ok, taken while the service was up.

## Packaging — SERVER-PKG-001/003, runtime config and a launcher — shipped 2026-11-11

- `config.py` answers the three settings that are not paths: `host()`, `port()`, `music_dir()`. An
  explicit `--host`/`--port` still beats the environment, which beats today's defaults; a malformed
  `HARDLYLISTENING_PORT` refuses to start (exit 2) rather than quietly binding 8470 somewhere the
  operator did not ask for.
- `MUSIC_DIR` is a seed, not an override, and "new database" needed a sharper definition than it
  first looks: `db.connect()` inserts every `DEFAULTS` key on creation, so a fresh database already
  holds `root = ~/Music/HardlyListening`. `seed_music_root()` therefore applies while the stored
  value is still that default (or absent) and stops the moment an admin chooses a location.
- `bin/hardlylistening-server` is a `sh` wrapper that resolves its own directory and `exec`s
  `cli.py`; `cli.py` owns `run`, `check` and `version`, so the logic is testable without spawning a
  shell. `HARDLYLISTENING_PYTHON` covers an interpreter that is not on `PATH`.
- `check` reports and repairs nothing, which is what makes it safe as systemd's `ExecStartPre`: a
  missing music directory is a `fail` and is still missing afterwards, a missing database is
  reported "will be created" and is not created, and the database is probed `mode=rw` inside
  `BEGIN IMMEDIATE` … `ROLLBACK` to prove the write lock without writing. A busy port is a warning —
  a manual `check` against the running instance is the normal case.
- `server.boot()` logs the one start that seeds a root, so a packaged first boot says what it did.
- `tests/test_config.py`: 11 tests, all in subprocesses against a temporary data directory. Full
  suite 591 tests, 158 s, and the only errors are the three that were already red before this work
  (`test_artist_follow` — `KeyError: 'following'` — and two in `test_social_likes`, both a missing
  `user` helper on `CommentLikes`).

## Packaging — SERVER-PKG-002, data isolation — shipped 2026-11-11

- New `paths.py` is the only place that answers "where may this process write". With
  `HARDLYLISTENING_DATA_DIR` declared, the database, the artwork cache, the browser-download cache
  and artist art all resolve under it (`cache/artwork`, `cache/browser-downloads`,
  `cache/artist-art`); `HARDLYLISTENING_DB` still wins over the lot.
- Undeclared, every path is byte-identical to before — `<app>/hardlylistening.db`,
  `<app>/.cache/…`, `<app>/artist-art`. The live install therefore needed no move and no migration,
  and this is why the two layouts are one code path rather than two.
- `paths.ensure()` creates the declared directories and does nothing when they were not declared;
  creating them "just in case" would be the write to the source tree the module exists to prevent.
- `tests/test_paths.py`: resolution with and without `DATA_DIR`, and a run that opens the store,
  writes artwork, artist art and a browser archive, and asserts the app tree is untouched. Full
  suite 554 tests, the same 3 pre-existing errors.

## Packaging — SERVER-PKG-004, graceful shutdown — shipped 2026-11-11

- `SIGTERM`/`SIGINT` stop in the order the system unit assumes: HTTP stops accepting and closes its
  socket, the scheduler stops and joins, the queue worker gets a 5 s grace period, SQLite commits
  and closes. The handler only sets a flag; the main thread does the stopping, since `shutdown()`
  may not run on the thread that owns `serve_forever`.
- A download cut short by shutdown is not marked cancelled. The child is killed, its partial file
  removed, and the row returns to `queued` so the next start resumes it — the same rule the
  constructor applies to rows left by a hard kill. Manual cancel keeps meaning "cancelled".
- `Store.close()` added (commit then close, under the same lock every other thread uses).
- `tests/test_shutdown.py` starts the real entry point, signals it, and asserts exit 0, the log
  line, a released port, a checkpointed WAL, and that `Queue.stop()` returns.

## Platform separation — ARCH-003/ARCH-004, Library and Playback facades — shipped 2026-11-11

- `library/service.py` now owns the personal-library queries: the scope union, starred, ratings,
  bookmarks, playlists, playlist membership, favourites and devices. `listening.catalog()` is a
  one-line delegate (tests and `zip_export.py` call it by that name), and the Subsonic dispatch
  reads starred/ratings/bookmarks/playlists/favourites through the service instead of the SQL it
  used to paste into the response builder. One query, two callers — no second copy to drift.
- `library/playback.py` decides what to serve: `Stream` carries `length`, `content_range` and
  `chunks()`; `plan()` reproduces the old handler's semantics exactly (no header or an unparsable
  one → 200 whole file, satisfiable range → 206, past the end → 416 with `bytes */size`). The three
  "is it playable" checks (`stream`, `download`, `getTranscodeStream`) use `available()`.
- `server.stream_audio` is now only headers and bytes; the file/range decision is the facade's. All
  three callers — `/api/track/<id>/audio`, Subsonic, the public share page — get the same semantics
  because there is only one implementation.
- Behaviour is unchanged on the wire, which is the point. Subsonic set 93/93 green; new
  `tests/test_playback_plan.py` (200, 206 open-ended, 206 suffix, 416, missing track, missing file)
  green; full suite 547 tests with 3 pre-existing errors (`test_artist_follow`,
  `test_social_likes`), none in the touched files. Live service restarted by autodeploy, health 200.
- Still open from `docs/server-domains.md` §6: `preferences.py` → Social, `shares.py` →
  `subsonic.BLANK_PNG`, `rest()` living in `listening.py`, `security.py` → Activity, and the
  `db.py` repository accessors (ARCH-002/005, ARCH-006, SUB-010/011).

## Platform separation — ARCH-001, domain ownership documented — shipped 2026-11-11

- `docs/server-domains.md` gives every feature, table and route exactly one owner across Core, Repo,
  Library, Playback, Social, Party and the OpenSubsonic adapter. Nothing moved: the database is
  untouched, no module was renamed, no behaviour changed.
- Measured against the live tree rather than estimated: 26 top-level modules, 6 packages, 63 tables
  created by code (56 present in a quiet database, 7 created on first use), 56 distinct route
  literals plus 10 prefix-dispatched subtrees, all assigned.
- Five real inversions recorded, each with its fix and its card: Core → Social (`security.py` calls
  `activity.record`), Library → Social (`preferences.py` uses `social.album_id`/`validate_target`),
  Library → Adapter twice (`shares.py` imports a Subsonic constant; `listening.py` hosts the
  Subsonic dispatcher), and Core+Repo sharing `db.py`. Everything else already points the allowed
  way; `server.py` importing every domain is correct as the composition root.
- Decisions the document makes explicitly: Party is its own domain (not Social), capability share
  links are Library (not Social), podcasts and provider search are Repo, radio and swipe are
  Library, `library/` keeps its name even though it currently holds Repo-side helpers — moved one
  file at a time, never mass-renamed.
- Also recorded: the `/api/library` (shared Repo snapshot) versus `/api/listening/*` (personal
  Library) inversion in the URL names, and that seven tables are created lazily, so their absence
  from a database proves nothing.
- Runtime boundary added in the same pass: the Python core is the canonical runtime and the Desktop
  shell is an optional controller/client that depends on it, never the reverse. The core must start
  with no display, no WebView and no Node toolchain; a VPS runs the core alone. The Desktop app has
  two independent choices (run a local server on/off; point the UI at local or remote), so
  remote-only and headless are both first-class. The packaging layout (`server/`, `web/`, `desktop/`,
  `player/`) is a build target, not a repo reorganisation.

## Subsonic — transcoding extension, token auth, OpenSubsonic flag — shipped 2026-11-11

- `getTranscodeDecision` (POST, JSON `ClientInfo`) and `getTranscodeStream` are served as part of
  OpenSubsonic extension #1, but only as direct play. The decision checks the client's
  `directPlayProfiles`, codec limitations and `maxAudioBitrate` against the stored container, codec
  and average bitrate; an accepted file gets `canDirectPlay` plus an opaque `transcodeParams`, and
  the stream endpoint returns those exact bytes through `stream_audio` with Range and 206.
- A client that would need a re-encode gets `canTranscode: false`, a stated `errorReason` and one
  `transcodeReason` per refused profile — never audio in the wrong format. `offset` is refused with
  error 30, because a time offset cannot be honoured without an encoder. `hls` stays error 30.
- Container names match through aliases, so an `.opus` file is direct-playable for a client that
  calls that container "opus" rather than "ogg". Relevant while the library converts to Opus.
- Device creation also stores the secret in Subsonic's `enc:` hex form so MD5 token auth
  (`t = md5(password + s)`) can be verified; that hash cannot come from the stored SHA-256. Devices
  created before the column stay password-only, and `SECURITY.md` records the trade-off.
- `subsonic-response` now carries `openSubsonic: true` — without it a client never discovers the
  extension or OpenSubsonic fields — and `getOpenSubsonicExtensions` advertises `songLyrics`
  alongside `formPost` and `transcoding`.
- Measured: 36 new tests (17 transcode, 13 auth/capability, 6 format matching); the Subsonic set is
  80 green; the full isolated suite is 481 tests with its 31 pre-existing party/import/search
  errors and none new; health 200 after the restart.

## Accounts P0 — invite-only signup, passwords, admin signup links — implemented, deployed

- The permanent login link is untouched. A **signup link** is a separate, temporary, single-use
  credential (`/signup#invite=…`): redeeming it creates the member, their password credential and
  their session in one transaction, and the link dies on use. Only the SHA-256 digest is stored,
  so it can be shown once and never again. Seven-day expiry.
- Every way in mints the same `__Host-hl_session`: link exchange, password login and signup go
  through `Security.create_session()`, and `browser_sessions.auth_method` records how that device
  got in. `members.id` stays canonical; playlists, saves, quota, Party, Subsonic, profiles and
  roles are unchanged.
- Passwords: `hashlib.scrypt` with a per-member salt, no new dependency, 10-character floor, one
  generic `Invalid email or password` for both a wrong password and an unknown address, and the
  existing per-IP `Security.limit()` buckets. No public sign-up button, and `/login` never creates
  a member.
- Password change: a login-link session already holds the whole credential, so it changes the
  password without the current one — that is what makes the link a recovery path. Password and
  provider sessions must confirm it. A change never revokes the login link.
- Profile → Account shows the email, method, change-password form, connected providers (only when
  one exists) and “Invite a friend”: one outstanding signup link per member, secret shown once,
  afterwards only whether one is active and when it expires. Admins keep an unlimited
  “Generate signup link” in the People panel; per-member invites are relabelled “Regenerate login
  link”. Enrollment no longer pre-creates empty members; `/api/members/create` remains for
  recovery and migrations.
- Google and Apple buttons are **not** rendered yet: `member_identities` exists and the Connected
  accounts row only appears when a provider is linked. That is HL-410/411.
- Verified: `test_email_auth` + `test_invites` + `test_security` 33/33; a 20-check HTTP smoke on an
  isolated DB (signup begin → account created, reuse refused, generic login failure, one-invite
  rule, member refused admin mint, password change rules); live DB migrated with all 8 existing
  sessions preserved; service restarted, health 200, `/login` and `/signup` 200.

## Session follow-on requests and partial fixes

- **Already-owned songs count as grabbed, and the gap is explained — implemented, not yet deployed:** adding a playlist links songs already on disk instead of copying them (`playlist_tracks.reused`, migration via `ADDED_COLUMNS`) and reports `N already in your library (linked, not copied)` in the add strip and the queue log; the playlist card now shows `N already in library` and `N to review`, so a card reading `161/300` no longer hides its 111 parked review rows. `done` already counted reused files, so the denominator explanation was the missing piece. Python suite 310/310 OK; `test_queue_ui.cjs` and `test_library_ui.cjs` pass. Pre-existing, untouched failure: `test_song_table.cjs` asserts the save control still renders `SVG.minus` (source has used a check since the square-icon change).

- **Member Library separation — implemented, deployed:** each member has private nested folders whose song memberships point to canonical Repository track IDs. Existing per-member saved tracks and playlist snapshots remain intact. Folder create/rename/delete and add/remove-song APIs are member-scoped; folder-only tracks join that member’s Library/Subsonic catalog. UI adds a personal Folders view and right-click/long-press “Add to <folder>” actions. Folder deletion removes memberships only; no audio files copied or deleted. Full Python suite 292/292, personal Library renderer test passed; the actual context-menu code was exercised in a disposable browser DOM. Service active, health 200. Authenticated full-page smoke was not performed because the app redirects to its login page.
- **One-audio-file import — implemented, verified, deployed:** max 20 MB; reject folders, zips/archives, batches and non-audio in UI and API. Request parser 28 MiB supports base64+JSON overhead. Isolated API test uploads an actual valid 20 MiB WAV through the handler and verifies it imports into a temporary library root. Over-limit/type/path cases tested. Python suite 289/289; service restarted, health 200.
- **Move library — implemented, verified, deployed:** `/api/library/move` authenticates, enforces admin + CSRF before the move; a member is denied with 403 before the move function. Isolated admin route check passes. No real move performed.
- **Daily boost — implemented, deployed:** one active song/member per rolling 24h. Choosing another song swaps the target but preserves the original expiry; choosing the current song does not extend it. Active boosts sort before activity entries and disappear after expiry; existing song context menu provides the action, with no new button. Member auth, CSRF, rate limit, expiry, swap, and independent slots tested. Full Python suite 295/295; Feed renderer and actual context menu tested in disposable DOM. Service active, health 200.
- **Profile pins and likes — not started:** pin songs/playlists/albums to own profile; compact liker names/icons popup on hover/right-click/long-press with count. Same compact named-voter popup for up/downvotes.

Detailed request list: `SESSION-REQUESTS.md`. Import, Move-library, personal folders, and daily boost are deployed; the current full Python suite passes 295 tests. Profile pins/likes and focused Activity feed/profile verification remain open.

## Songs grid and context actions — source implemented, not deployed

- Songs in Repository and Library now offer a persisted Grid/List switch (`hl.songLayout`), retaining the existing list as default and detail-page tables. Cards reuse the same song renderer/actions: artwork, status, votes/discussion, explicit play/pause, saves and downloads. Search, sort and personal/playlist scope are unchanged.
- Right-click, touch hold (550 ms), Shift+F10 and ContextMenu open an additive song menu. Existing visible actions remain; movement/scroll/pointercancel abort holds, links/inputs keep native behavior, Escape/outside dismiss, keyboard arrows navigate, and placement is viewport-bounded. Long-press release cannot count as a song double-tap.
- Menu adds Play now (`playTrackNow`), Play next and Add to queue (`queueSong(id,next=false)`); enqueue never starts audio and explicit ordering survives unloaded-player polls. Source editing is not added: song rows have no existing Source control to delegate safely.
- Verified with isolated `tests/test_song_grid.cjs`: real renderer and mocked saves, headless Chromium at 1280/390, blocked network/media, no DB or live writes. Covers scope/search/sort/status/control parity, enqueue ordering, inert ordinary card click, menu invocation/cancellation/native controls. Screenshots: scratch `hardlylistening-songs-{1280,390}.png`.
- Latest all-DOM run: 27/28 suites passed; `test_mixer_live.cjs` fails its two remote-play assertions (also on two standalone reruns; mixer source is outside this change). An earlier run passed 28/28. Full output: scratch `hardlylistening-grid-dom-results.json`. No live restart or Python/backend change.

## Finding the upload, and undoing a wrong pick — 0.10.7

- **Recall was the bug, not ordering.** The wanted video was never in the pool:
  three results per engine is too shallow, and the query carried the guest credit
  — `"Friction What I Need (feat. Poppy Baskcomb)"` returns a different upload
  entirely, while `"Friction What I Need"` returns the wanted video first. Both
  engines are now asked for ten, the feature credit is dropped from the *search*
  string, and the queue searches up to three query variants (artist-first,
  title-first, title alone) until something scores ≥ 90 %. Measured on the reported
  track: the correct upload now appears at 0.93 and the mistitled "I Need To Feel"
  uploads fall below it.
- **Scoring keeps the full title.** Searching and scoring want different strings:
  dropping "feat." finds the upload, keeping "(Fredrick Remix)" is what separates a
  remix from the original. `ytdlp.query_variants()` searches; `ranking.query_for()`
  stays the pick key and the scoring query.
- **A wrong pick can be undone.** Rows whose source was chosen by hand show
  **Undo pick** (never mid-download): it forgets the lesson, optionally deletes the
  file it produced, and returns the track to review with its candidates intact.
  An accidental click no longer trains the ranking forever.
- **A pasted URL also teaches the ranking** — its metadata is read with yt-dlp, so
  a source the search never offered is still a stated preference (and is just as
  removable by Undo pick).
- Verified: 231 Python tests (new `test_recall.py`, `test_unpick.py`), 29 DOM
  suites (new `test_undo_ui.cjs`); undo exercised live on a library copy
  (`forgot_pick: true`, status back to `review`), and the correct upload located
  by the real search.

## Sorting the song table — 0.10.5

- The column headers are the sort controls: clicking **Title / Artist / Album /
  Time** sorts by it (A–Z, shortest first), clicking it again reverses it, and
  clicking another column replaces the order. Exactly one column is ever active,
  and it carries the accent colour and an ↑/↓ arrow plus `aria-sort`.
- **Votes has two readings**, because "most votes" means two things here: first
  click sorts by everyone's votes (highest first), second reverses, third switches
  to **your own votes**, fourth reverses, fifth returns to recently added. The
  header renames itself to "Votes" or "My votes" so the mode is never a guess.
  Ties break on fewer downvotes, then title.
- The sort dropdown stays and now lists the same seven orders, kept in step with
  the headers in both directions. That is what phones use, since the header is
  hidden there with the columns it names — no capability is desktop-only.
- Order persists (`hl.librarySort`, `hl.librarySortDir`); preference ranking stays
  a tie-breaker and never a competing order.
- Vote counts are loaded for the whole library now (300-target chunks, not just the
  first 300 tracks), so a track deep in the list cannot sort as zero.
- Verified: a new DOM suite covers each order ascending and descending, the votes
  cycle, exclusivity, persistence, select sync and the header's aria state; 213
  Python tests and 26 DOM suites pass. Checked live at 1440 px (header cells sit
  exactly over their columns) and 390 px (header hidden, select offers all seven,
  no horizontal overflow).

## Ranking learns from what he picks; one song table — 0.10.4

- **His "Use" clicks are now the training data.** A picked candidate is recorded
  (`ranking.py`, table `source_picks`) with its query, uploader and whether its
  version matched the request, and the ranking reads it back:
  - the exact upload already chosen for this query scores 1.0 and goes first — a
    decision that was already made is not re-litigated;
  - an uploader he has chosen for a *different* song by the same artist is lifted
    ×1.10 (scoped to the artist, never global);
  - version tolerance follows his picks: only-named-versions → a different named
    version is ×0.5, only-other-versions → ×0.85 instead of fighting him.
  - A query with history is searched without the 0.45 floor, because his pick may
    sit under it; ranking lifts it back to the top.
- **Choices made before the table existed were adopted**, not lost: a job with
  `pinned=1` and a resolved URL inside its stored candidates is the same signal as
  pressing Use. 24 real picks were seeded on first boot.
- **The judging model sees his taste** for the first time: the last picks for that
  artist go into the request as `previously_accepted`, flagged in the system
  prompt as untrusted data that cannot excuse a different artist or version.
- Queue review rows show "picked before" and the strip reports "N picks learned".
- **One song table.** `SONG_HEAD` + `songTable()` are the only definition of the
  `# / Title / Artist / Album / Votes / Time` columns; the list page and the album
  and playlist detail pages all use it. Header and rows share one grid (Votes was
  `max-content`, so each row widened it differently and the header drifted), header
  is a fixed 34 px, rows a fixed 46 px, Time right-aligned and Votes centred, and
  the whole table is a bordered card with zebra rows. On a phone the header is
  hidden with the columns it names.
- `dialog` gets `max-width:min(520px, calc(100vw - 24px))` in polish.css: the phone
  overflow guard stays, desktop gets its 520 px back.
- Verified: 213 Python tests (12 ranking tests incl. a pick below the floor winning,
  artist-scoped affinity, version tolerance following picks, the judge payload) and
  25 DOM suites (new: song table structure and column maths).

## Ranking: the requested version now wins — 0.10.3

- Two scoring bugs, both of which made the >=90% rule unsafe:
  - `title_cover` compared the query tokens (artist **and** title) against the
    title alone, so an uploader who wrote "Artist - Song" in the title collected
    the full 0.2 and a correctly split artist/title row got 0.1. Coverage is now
    the better of title-only and artist+title.
  - `norm()` deletes bracketed text before scoring, so the word that names the
    version — "Fredrick" in "New Day (Fredrick Remix)" — was gone before the
    comparison. `_version_tokens()` reads bracket contents (full-width ones too,
    after NFKC), ignoring generic words (remix, official video, HD, extended mix,
    …). A requested version compared against a different named one is multiplied
    by 0.7; a candidate that names no version gets 0.9 (probably the plain edit).
- Measured on the reported case, "Alison Wonderland — New Day (Fredrick Remix)":
  the two other people's remixes were 0.939 / 0.933 and the right upload 0.900.
  Now 1.0 vs 0.658 / 0.652.
- `AUTO_ACCEPT` (0.90) is what makes that matter: a 90%+ candidate is taken and
  downloaded without asking, and the judge is not called at all above the line —
  "do not spend a model call second-guessing a 94% metadata match". Below it,
  review is unchanged.
- Stored review candidates were scored by the old formula, so every read re-scores
  them: the queue rows show current percentages, `stats.review_strong` counts rows
  whose best candidate clears 0.90 under the current ranking, and the
  "Use N strong matches" action re-scores before applying. On the live library
  that moved 18 eligible rows down to 9 — the old numbers were inflated by the
  same bug.
- Verified: 201 Python tests (7 new scoring tests: requested version wins, the
  reported regression case pinned, full-width brackets, generic bracket words,
  clean split not losing to a stuffed title, official video not a remix, live
  pushed below studio) and 24 DOM suites.

## Deletion, ownership and the disk ceiling — 0.10.2

- **The drive cannot be filled.** A second guard beside the GB floor: downloads
  stop once the library volume passes `disk_limit_pct` (default **95**, editable
  in Settings). Either guard stopping is enough, and the banner now names which
  one fired — "the disk is 96.2% full and the limit is 95%" versus "only 1.1 GB
  free, the floor is 2 GB". Nothing new starts; deleting files lets the queue pick
  itself up on its own.
- **Deleting is a first-class action**, in `deletion.py`, for a playlist or a
  whole album: rows, playlist links, download jobs and — on request — the audio.
  It plans first (`{"dry": true}`) so the UI can say what will go, and it never
  unlinks a path outside the library root: a wrong root setting cannot turn
  "delete album" into anything else. A file another playlist still needs is kept,
  and its track row survives; an album delete lets go of surviving tracks by
  clearing their `release_id` first.
- **Who may delete.** `security.may_delete` is the single rule: an admin, or the
  member who added the thing (`member_items`, plus `member_playlists` for pasted
  playlists). An unclaimed row is admin-only, and no member at all (local mode) is
  the admin case — a delete defaults to "no". The route checks it and answers 403
  with that sentence; the UI hides the button unless `can_remove`.
- **Two clicks, never one.** "Delete album" and "Delete files" arm themselves
  first ("Click again to confirm") and disarm after 4 s, so a stray tap on a phone
  cannot bin an album. "Remove" still drops the entry and keeps the audio; the
  files only go when that is what was asked for.
- **Ownership is recorded where the thing is created:** a playlist pasted through
  `/api/add`, and tracks/playlists made by an import or an upload, are claimed for
  the member who did it.
- Verified: 19 new Python tests (playlist/album delete, dry run, shared tracks,
  outside-root guard, member vs other member vs admin vs unclaimed, 95% at-the-
  ceiling and just-under) and a new DOM suite for the two-step buttons, the
  ownership-gated card and the banner wording.

## Queue page — visible progress and honest retry — 0.10.1

- A strip above the queue names the job the worker is on right now: title,
  artist, a real progress bar, percent, bytes, speed and time left. When nothing
  is running it says so and lists the counts that explain why (queued, retrying,
  needs review, failed, paused) instead of leaving an empty page.
- Why nothing showed before: the snapshot already carried `progress`, `speed` and
  `eta` per job, but the rows printed them as a single grey hint only while a
  status read exactly `downloading`. `GET /api/state` also hard-coded
  `"current": None` while the server overwrote it with `QUEUE.current`, whose
  field is `phase`, not `status` — so cooldown and disk waits were invisible too.
  Now the server falls back to the persisted row when the worker is between jobs,
  and the strip understands `downloading`, `resolving`, `cooldown` and `disk`.
- Every row carries one line of state: `43% · 3.0 MB · 825 KB/s · 1:37 left`,
  `retrying in 0:31 · attempt 3/4` (counted off the snapshot clock, so it ticks
  with the poll), `failed after 4/4`, `done · 6.9 MB`, `queued`,
  `finding a source…`, `needs review`, `not on the source`, `stopped`. A live row
  also gets a 108 px bar.
- A playlist card counts work in progress, not just finished files, so a 252-track
  playlist does not sit at 0% for the first download, and its sub-line names how
  many are downloading and how many are waiting to retry.
- Retry answers: the button shows `Retrying…` while the request is in flight, then
  a toast — `Back in the queue`, `Nothing to retry — it is already queued or done`,
  or the error. `POST /api/track/<id>/retry` now returns `queued` and
  `max_attempts`; `Re-run` reports how many tracks it requeued. A finished track
  no longer offers Retry (that was an accidental re-download), and its size is
  printed instead.
- Verified live against a real yt-dlp download (fixture DB and a scratch root):
  `current` reported 3.3% → 20 → 39.6 → 53.3 → 73.3 → 95.3 → 100% with speed and
  ETA moving, cleared to null on completion. Nine new Python tests, one new DOM
  suite (per-row text, bar widths, countdown, strip phases, partial card
  progress, retry toasts), 168 Python tests and 23 DOM suites overall. Phone check
  at 390×844: strip is 139 px tall, no horizontal overflow, nothing clipped.

## Multiplayer mixer room — 0.10.0

- The mixer is now one shared board. Controls, both decks and both preload
  queues live in a server-side room (`mixer_room.py`), so everyone on the page is
  driving the same mixer; the audio still runs locally in each browser, because
  Web Audio is local by definition and a server-rendered mix would add latency to
  everything.
- Transport is server-sent events: the room pushes only what changed and the page
  posts only what it changed. Measured on this box, a fader move reaches another
  subscriber in the same millisecond tick the patch is broadcast; the client
  throttles its own posts to ~18/s.
- Who is holding what: touching a control claims it for 2.5 s and every other
  browser paints a coloured badge on that control (`you` on your own). Claims
  expire on their own from a 0.5 s sweeper, so a closed laptop never locks a
  fader. Colours are slot-assigned on arrival, never shared between two people.
- The header shows a live chip and a roster of everyone in the room with their
  colour and initials; "· you" marks your own chip.
- Shared decks: loading a track, play/pause, seek and pitch publish, and remote
  changes load the same track, seek to the room's clock (anchor + rate, drift
  corrected at 0.9 s) and start or stop playback. Browsers refuse audio without a
  gesture, so a remote "play" that is blocked surfaces a **Join audio** button
  instead of failing silently.
- Shared queues: add/remove/reorder/clear publish the whole order; a remote order
  replaces the local one and repaints, and each browser still prefetches its own
  blobs.
- Fail-closed: `/api/mixer` and `/api/mixer/stream` require an approved member and
  same-origin POSTs; unauthenticated requests answer 401. With auth off (local
  development) each tab identifies itself through `?cid=`, so two tabs are two
  participants.
- Room state is in memory: a service restart resets the board to defaults and the
  queues dangle until someone re-queues. Nothing is written to the database.
- Verified: 26 room tests (clamping, claim expiry, unique colours, deck clock,
  queue cap, fan-out, slow-subscriber resync, dispatch, SSE writes), a 34-check
  DOM suite for the client layer, 159 Python tests and 22 DOM suites overall.
  Also exercised live: two participants, remote values, badges, roster, shared
  queue and a remote deck load.

## Scrub discs on the mixer — 0.9.12

- Each deck now has a small record beside its seek bar: drag it with the mouse (or
  a thumb on a tablet) and the playhead follows the rotation, DJ style. One full
  turn moves 12 seconds; the disc turns with your hand and its notch shows the
  angle, so it reads as vinyl rather than as another slider.
- Seeking is live — playback carries on from wherever you leave it, the seek bar
  and the time label update during the drag, and the disc dims when the deck has
  no track. Arrow keys nudge a second, Shift+Arrow ten, Home rewinds, so the disc
  is reachable from the keyboard as a `role="slider"`.
- Two maths that matter: the angle seam at ±180° wraps (otherwise crossing it
  jumps most of a turn), and each drag measures from where it started (the disc's
  total rotation is kept for the visual, not for the seek) — without that a second
  drag replayed the first one's distance.
- Hidden on phones (`display:none` under 900 px): he asked for desktop only, and
  the seek bar it sits beside still works there.
- Verified in Chromium: quarter turn = +3 s, half turn = +6 s, clamping at both
  ends, rotation matches the hand, keyboard nudges, and the disc stays dim with an
  empty deck. 133 Python tests + 21 DOM suites pass; new `tests/test_dj_jog.cjs`.

## Phone / PWA mixer layout — 0.9.11

- The board is the first thing on a phone: Deck A and Deck B share one row, the
  crossfader sits directly beneath them, and both queues keep their own column
  under that. At 390×844 all of that is on one screen with nothing to scroll;
  at 360×640 the decks and crossfader fit and only the queue tails need a nudge.
- Landscape gets its own shorter block (34 px sliders, tighter rows) so the
  crossfader and master land inside a 390 px viewport instead of 30 px below it.
- The deck header now gives the track name a full line with Play / Cue / Sync
  wrapped underneath — a 183 px column had been cutting titles to fragments.
- Nothing was removed to make it fit. Every fader, label, chip, queue button and
  the tour card are still there; the tour card moved below the board
  (`#learnbar{order:9}`), and only long copy (the two queue paragraphs, the
  tour's "when to use" line, the idle track counter) is hidden on a phone.
- Queues keep their chips and both action buttons by stacking each row onto two
  lines instead of dropping controls.
- Verified in Chromium at 390×844, 360×640, 412×915, 768×1024, 844×390 and
  1400×900 (desktop unchanged: 519/90/519 columns, 92 px sliders, 14 px titles).
  New `tests/test_dj_mobile.cjs` pins the rules and the control inventory;
  133 Python tests + 20 DOM suites pass.

## Mixer names are links into the library — 0.9.10

- Every name in the mixer opens the page it names, in the scope the finder is
  showing: song title and album/set go to that release with the row marked,
  artist goes to the artist profile, and the selected playlist gets an `↗` next
  to its selector. Deck A/B now-playing follows the same rule.
- Links open a new tab. A same-tab navigation would stop the mix, and the two
  preload queues are the whole point of the page.
- The library side gained one-shot deep links: `/?scope=mine&artist=NAME`,
  `&album=<album_id>&track=<id>`, `&playlist=<id>`, `&track=<id>`. Applied once
  after the first library paint, then stripped from the URL, so reload does not
  reopen it and Back still leaves the app. A track id resolves its album when the
  mixer has no album id, and a song outside the chosen scope says so instead of
  opening nothing.
- Two bugs found while wiring it: the deep link applied only in a visible tab
  (the normal library poll is visibility-gated, so the page now loads the library
  once for itself), and the row highlight was deferred inside
  `requestAnimationFrame`, which never runs in a background tab — it is a timer.
- Verified: real rows carry the right hrefs in both scopes, album pages open with
  the arriving row outlined in green, and a synthetic click on a queue link never
  loads the deck. 133 Python tests + 19 DOM suites pass; new
  `tests/test_mixer_links.cjs` and `tests/test_deep_links.cjs`.

## Finder: missing tracks, numeric titles, thumbnails, paging — 0.9.9

- The finder only ever rendered five rows with no hint that more existed. It now
  lists every match in pages of 60 with `Show 60 more · N left`, and the header
  reports what is actually on screen (`60 of 246 matches`) instead of a silent cap.
- Thumbnails: provider artwork when the row has it, otherwise the cover embedded
  in the local file, extracted server-side to a 160 px JPEG in `.cache/artwork`
  (`GET /api/track/<id>/artwork`). A `.none` sentinel stops re-probing art-less
  tracks; a broken image degrades to a note tile, never a broken-image icon.
- Numeric titles: 246 SoundCloud rows carried the provider track id as their
  title because the set expansion stored the id when no title was present.
  `library/metadata_repair.py` reads the tags yt-dlp already wrote into the file
  and replaces only `title`/`artist_name`/`album`. A real title is never replaced
  and a file without usable tags is skipped rather than guessed.
- Preview is a count plus three samples — probing 250 files inside a request
  blocked every other page load. Apply runs on a worker thread
  (`POST /api/library/titles {"apply": true}`, `GET /api/titles/status`) and the
  button shows `fixing 67/246…`, then the fixed count.
- Verified on a copy of the real DB: 246 candidates, 241 titles recovered,
  5 rows with missing files counted not guessed, 60/60 thumbnails rendered,
  paging 60 → 120 → 180. Live library still needs one click to apply.

## Shared EDM genre metadata — source implemented, not deployed

- `genres.py` and external `web/genres.js`/`genres.css`: controlled multi-label
  taxonomy and OR filtering of already-scoped Repository / personal Library rows.
  Filtering never grants personal membership or changes another member's saves.
- Approved-member taxonomy/status reads; admin-only CSRF/rate-limited explicit
  generation/correction, 1–25 selected tracks. Select tracks in Genres, or enter IDs.
  Manual replacement supports adding/removing labels and prevents AI overwrite.
- Metadata-only estimates (artist/title/album), not audio analysis or confidence.
  Unknown/non-EDM are explicit exclusive labels. Shared persistence records model,
  source, timestamp, manual flag and metadata fingerprint; stale AI labels are hidden.
- One asynchronous in-memory worker, queue cap 50, 20-second HTTP timeout,
  two attempts maximum with pacing; exact OpenRouter credential scope and no
  credential-bearing redirects. No generation during state polling/render.
- No automatic completion tagging, full-library sweep, live inference, data writes
  or restart performed. Pending in-memory work is lost on restart; completed labels
  persist. Existing labels are cached, not re-generated; use manual correction.
- Verification: five isolated genre Python tests passed; full Python suite at this
  checkpoint: 117 tests OK (41.506s). Parent should rerun after sibling integration.
- Deployment pending owner approval: restart service to load backend, refresh UI,
  then explicitly select a small set to generate. Nothing claims the library is tagged.

## Repository / Library — source implementation, parent deployment pending

Repository is the shared pool; Library is the caller's saved songs and playlist
snapshots. Separate top tabs and visible scope buttons, four browse/search views,
counts, actionable empty guidance and Add to library / Remove from library.
Admins get the same personal scope. Saved playlist entries come from additive
`playlist_tracks` in the saves response; later shared additions stay excluded.
Details, ZIP selection and playback selection retain snapshot order/scope.
Explicit song saves are independent of playlist access; unsaving never deletes
shared files. Existing device panel now reads Player connections.

No personal playlist creation/entry editing, richer public member/activity pages,
offline music or cloud storage is claimed. DJ export still targets the shared pool.
See PLAN's current unfinished-area audit; historical completion sections below
describe earlier milestones, not deployment evidence for this change.


## Lyrics + karaoke display — source verified, deployment pending

- Player Lyrics dialog: plain fallback, synchronized LRC current line, large
  current/next karaoke view and optional browser fullscreen. Existing audio only;
  seek/rate updates, no autoplay, no frame loop; close removes listeners/requests.
- Always-member-authenticated, rate-limited lyrics GET; bounded LRCLIB metadata
  matching/cache and read-only canonical-root UTF-8 sidecars. No audio upload,
  acquisition URLs, lyric writes, vocal removal or instrumental generation.
- Verified: 100 Python tests OK (34.384s); all nine discovered DOM suites PASS.
  New tests cover timestamps/offsets, malformed/mismatched provider data, caching,
  membership, sidecar escape, plain fallback, safe text and late-response cleanup.
- Not deployed/restarted; no live DB/media writes or audible playback. Provider
  coverage and real installed-device fullscreen remain unverified.

## Lightweight UI + installable PWA — source verified, deployment pending

- Cohesive system typography, spacing, card/navigation/button/dialog/player surfaces;
  responsive wrapping and touch controls, visible keyboard focus; every existing
  tab/action/permission and shared-versus-personal boundary retained. No framework,
  remote fonts, animated background or continuous decorative effect.
- Completion-driven foreground polling and single-flight state/library reads;
  hidden documents cancel scheduled polls, visibility/pageshow resumes promptly.
  Visible covers alone rotate comments; offscreen/hidden/reduced-motion covers
  stop timers without discarding comment arrays/index. Audio/backend queue untouched.
- Existing icons/manifest reused, explicit root ID/scope, iOS metadata and root
  service worker. Only public static assets cached; authenticated HTML network-only,
  offline informational entry fallback, no API/audio/invite caching, push/sync,
  forced reload or offline-music claim. Updates activate on normal next launch.
- Verified: 96 Python tests OK (33.469s); nine DOM suites PASS including new
  performance/PWA suite. Isolated Chromium fixture: 1280px/390px, no horizontal
  overflow or page errors, fixed player positioning, worker root registration.
  Screenshots: `$TMPDIR/hardlylistening-desktop.png`, `hardlylistening-mobile.png`.
- Pending: parent deployment, real HTTPS installed-device/PWA checks. No measured
  battery-saving claim, service restart, real media playback or live DB writes.

## Queue scroll / automatic source choice / decision history — verified source

Done: keyed playlist/track polling reconciliation preserves expanded scrollers,
scroll offsets and source drafts/focus. Automatic Apple matching tries up to six
ranked candidates instead of parking after the first AI rejection; SoundCloud
first, then bounded safety-net search, with unchanged floor and hard version/
duration refusals. All-uncertain or unavailable judge remains review, not a
fabricated approval. Metadata selection is not audio-identity verification.

Done: separate admin-only Queue decision history, durable SQLite audit records,
change source, safe reverse for stopped undownloaded jobs, clear entry/all history
without modifying tracks/jobs/audio. Explicit downloaded-source replacement now stages
unique output under the library root, validates before atomic canonical/source switch,
and retains previous media. Durable queued/downloading/completed/failed/cancelled/
interrupted/reversed states; interrupted work stops safely rather than auto-retrying.
Completed replacement reversal validates/restores the retained previous file/source;
metadata-only downloaded history still cannot reverse absent previous media. Clear
history hides operation display without deleting active work or media. UI exposes
confirmation, explicit states/errors, cancel and only eligible reversal controls.

Verification: full Python discovery 95 tests in 32.746s, OK; all eight DOM suites
(MIDI/library/social/listening/invites/profile/queue/replacements) PASS. Fake acquisition
fixtures plus silent WAV validation prove failure/cancel/recovery preserves original,
clear during active work, admin stage/cancel/reverse, and HTTP Range follows canonical
file before/switch/after reverse. Log: $TMPDIR/hardlylistening-python-tests.log. New coverage:
reject first/accept second, fallback floor, uncertain review, hard wrong-version
refusal, history persistence/clearing/reversal, admin/CSRF, audio preservation,
scroll/DOM identity and draft retention (old replacement positive control).
No restart, live acquisition, playback or existing-review bulk requeue. Pending:
deployment, live model-quality check and real-browser scroll geometry verification.
Limits: retained/failed staged files have no GC; history clear hides reversal controls;
replacement requires a download job and explicit URL. Root relocation does not migrate
retained history paths; out-of-root/missing prior files safely refuse reversal.

## Profile onboarding / self-management — implemented source, deployment pending

Verified full isolated Python discovery: `Ran 74 tests in 28.349s`, `OK`, zero
tracebacks. All six DOM suites PASS: library, social, MIDI, listening, invites
and profile. Log: `$TMPDIR/profile-python-final.log`. Earlier concurrent
queue-selection failure resolved by the parallel queue work before this clean run.
No live HTTPS/browser/device-client verification is claimed.

Admins invite unnamed accounts and select roles only. Invitees choose their own
1–100 character name and built-in icon; validation, profile assignment and invite
consumption are atomic. Recovery preserves existing profiles. All approved users,
including Owner/admins, have My profile in the header and the same personal saves
and listening connections. Self-profile API cannot change IDs/roles/other users.
People shows Not joined or chosen names, wraps actions on narrow screens and labels
the current admin You without destructive actions. Backend denies self-block,
self-demotion and self-revoke-all; logout and last-admin protections remain.
Shared library/feed and canonical media remain unchanged. No live DB writes,
restart, media playback, deployment or real HTTPS enrollment verification.


## Local invite authentication — source implementation, deployment pending

Verified: `python3 -m unittest discover -s tests -v` → `Ran 61 tests in 25.953s`,
`OK` (zero tracebacks). All five DOM suites PASS: library, social, MIDI, listening,
and invites. Retired Supabase/email tests were replaced by local invite, migration,
CLI, concurrency, persistence, revocation and authorization fixtures; existing
library/device/social/export coverage remains. Full log:
`/home/bazzite/.hermes/cache/scratch/invite-tests-final.log`.


Browser sign-in no longer depends on Supabase/email: administrators create unnamed
accounts; invitees choose names/icons, administrators choose roles and issue one permanent, reusable bearer profile link per account for new
or existing users. No public signup or first-user takeover. Local terminal-only
`auth_admin.py` creates/recover-invites an administrator; parent runs it privately.
SQLite stores SHA256-only profile links and opaque browser sessions renewed on use;
expiry, exact HTTPS Origin CSRF, per-request role checks, blocking,
logout and admin session/invite revocation are implemented. People shows the
copyable link only in the creation result, clears on close/dismiss and ignores
late responses. Existing IDs/email/library/saves are preserved additively;
invitees choose a display name and built-in icon. See SECURITY.md for exact commands and limits.

ONE shared community library/feed/pool remains visible to approved members;
acquisition adds canonical recordings there. Personal saves/playlists reference
that SAME pool, not isolated storage/catalog. The personal Library view and device
connections are scoped to saves; Repository remains the shared pool. Invite enrollment adds no acquisition capability;
legacy web behavior is preserved, independent extension permission is future work.
No live accounts, migration, service restart, public exposure or audible playback.


## Listening roadmap — current ordering (supersedes older priority notes)

1. **Personal saves + scoped OpenSubsonic backend first (LISTEN-1 / row 50).**
   Implemented source milestone, not deployed or verified with a real client.
   `listening.py` owns separate explicit track saves, playlist saves and snapshot
   playlist-track memberships. No backfill, admin whole-library bypass or automatic
   saving on acquisition. Saving a playlist explicitly snapshots current entries;
   future shared additions require another explicit save. Unsave removes only the
   caller's membership, not canonical tracks, files or shared playlists. Tracks in
   another saved playlist remain accessible. Approved community browser browsing
   remains shared; this new listening/device surface is personal, not a privacy
   change to community activity.
2. **Connection/credentials panel (LISTEN-2 / row 51), implemented in source.**
   Member-visible My listening connections in Library: named device creation,
   list/revoke, HTTPS server URL and individually copied URL/username/password.
   Secret exists only in the creation panel and is cleared on close/cancel/revoke;
   no external QR service or browser persistence. Songs and playlists expose
   explicit personal Save/Remove save controls using existing scoped APIs.
   Shared browsing, playback and administrator/acquisition controls stay separate.
   Isolated tests only; no deployment or real-client compatibility verification.
3. **One cross-platform OpenSubsonic player (PLAY-IOS / row 52), after an architecture spike.**
   HL-220 proves iOS playback/background/lock-screen/Bluetooth-Car controls and chooses
   Tauri or Flutter; Tauri/Svelte is the default because `player/ui` already exists.
   HL-225 implements and validates the single chosen stack. No parallel clients. Initial
   scope is compatible-server streaming, seek/queue/background playback, no acquisition;
   offline downloads and local audio remain undecided/out of initial scope.
4. **Generic opt-in extension host (EXT-HOST / row 53), later.** Disabled by
   default, explicit installation/trust, capability manifests, isolation,
   permissions, revocation and removal. NO bundled acquisition functionality.
5. **Separately installed acquisition extension (EXT-ACQUIRE / row 54), later.**
   Independent per-user acquisition permission checked server-side on every work
   request; membership, player access, extension installation and administrator
   status must not implicitly grant this permission. Audit without leaking source
   credentials. No extension or companion work in the backend milestone; legacy
   web acquisition remains unchanged pending a separately scoped migration.
6. **Storage adapters (rows 33,55,56), later.** Local-only stays supported; B2/S3
   and WebDAV first, Drive later. Private server-held credentials, canonical
   object IDs, verified retryable transfers, Range playback and bounded cache.
   Explicit mirror versus remote/cache migration; never automatically evict local
   originals. B2 uses isolated `hardlylistening/` prefix; unrelated bucket objects
   and policies stay untouched. WebDAV path/redirect/host validation and Drive
   authorization/refresh/revocation require independent tests before deployment.

### Isolated verification

Previous listening milestone: `Ran 65 tests in 29.394s`, `OK`. Current invite-auth verification is recorded below (retired provider-specific tests replaced with local-auth fixtures).
All four DOM suites passed: `test_library_ui.cjs`, `test_social_ui.cjs`,
`test_dj_midi.cjs`, and new `test_listening_ui.cjs` (credential lifecycle, copies,
HTTPS, explicit saves/removal, escaping and API errors). Listening fixtures cover two members,
guessed record IDs, no admin bypass, revocation, pending/blocked membership,
JSON/XML, form POST, canonical-root rejection and actual generated WAV ranges.
No audible playback, live DB writes, restart or publishing. Existing source-health
HTTP tests still emit unread-body/BrokenPipe diagnostic noise while passing.

### Backend milestone contract and limits

`/rest/<endpoint>[.view]` authenticates generated device credentials before the
browser boundary, including legacy auth-disabled mode. Every request rechecks
local membership (`member`/`admin` only) and revocation. Random device secret is
returned once, stored only as SHA256 digest, and is independent of browser invite sessions.
Supports `u` + `p` (plain or `enc:` hex) over HTTPS; MD5 salt/token (`t`/`s`) is
not supported. GET and form POST; XML default or `f=json`. Protocol failures use
Subsonic envelopes with HTTP 200. No JSONP. HTTPS must be provided externally;
reverse-proxy access logs must redact `/rest` queries as app logs already do.

Implemented subset: ping, getLicense, getMusicFolders, getArtists, getArtist,
getAlbum, getSong, getPlaylists, getPlaylist, search3, getAlbumList2, stream,
download. Album lists support alphabeticalByName/alphabeticalByArtist only.
Artists/albums derive solely from saved tracks; metadata grouping uses artist
and album names, not recording/edition identity. No artwork fetching, arbitrary
URL proxying, transcoding, scrobbling or playlist mutation over `/rest`. Missing
media remains browseable but media requests fail; canonical `local_path` and the
existing Range audio handler serve only existing in-root files. Download returns
original audio bytes (not transcoded MP3). No paths, source URLs or queue logs in
listening metadata. Unknown/unsaved IDs fail identically. No real-client
compatibility claim, full OpenSubsonic feature claim or discovery advertisement.

Always-authenticated browser APIs (including local legacy mode):
- `GET /api/listening/devices`; `POST /api/listening/devices {label}` returns
  `{username,password}` once; `POST /api/listening/devices/revoke {id}` scopes to
  caller, including admins. Up to 20 active devices, central rate limits.
- `GET /api/listening/saves`; `POST /api/listening/saves
  {kind:"track"|"playlist",id:<integer>,saved:<boolean>}` changes only caller's
  saves of existing community catalog records. Verified approved membership,
  browser Origin CSRF and write rate limit apply. No member ID supplied by client.
- Additive idempotent schema creation on first listening request; no live DB
  migration executed during development. Revocation prevents subsequent requests,
  not an already-running file transfer; offline copies cannot be remotely erased.

Columns move left to right. Every card names its backlog row in `BACKLOG.md` so
the two files cannot disagree.

**MVP (owner, 2026-11-04): Downloader → Library → Player.** Cards below that line
are parked no matter how interesting they are.

**WIP limit: 1 card touching `web/index.html`.** One UI change at a time, checked
by the owner before the next.

---

## The direction is asserted — 2026-11-12

"Desktop depends on Server, never the reverse" is the kind of rule that is true until someone adds a
convenience import. So it is now a test, and the test works from evidence rather than intent: it walks
the real import closure from `bin/hardlylistening-server` (73 modules, following `from pkg import sub`
and literal `import_module`), reads `web/`, `bin/`, `deploy/`, the Dockerfile, `install-service.sh`
and `start.sh` as text, and looks for a host token in any of them.

The strongest half is the copy: the tree is duplicated with exactly one directory removed — `desktop/`
— and that copy's own launcher is started on a loopback port with a temporary data directory. It
answers `/api/health` 200, `/` 200, creates its database in the temp directory, and the original
`desktop/` is byte-identical afterwards. That is "the server runs with the Desktop package absent",
measured.

It also pins the one edge that exists: the staged sidecar's SHA-256 equals `bin/hardlylistening-server`,
so the window bundles the same artefact rather than a second implementation. And the Docker image
now excludes `desktop` in `.dockerignore`, so "the image is the server" is literally true instead of
merely true of the instructions that run.

One reconciliation worth recording. The first version of the write rule banned `fs::write` anywhere in
`desktop/`, which HOST-003's handoff record legitimately breaks. Rather than drop the rule, it now
judges write *targets*: no write may reach the app tree, the database, `web/` or the music root, while
owning the config/state directory is allowed — with a negative control proving a write into the app
root or the database still trips it. Two cards disagreeing is a good moment to make an assertion more
precise rather than quieter.


## The exclusion is asserted, not promised — 2026-11-12

A store build that merely hides acquisition is one refactor away from shipping it. So PLAYER-007 asserts
over the **built tree** rather than over the list that built it, and derives every needle at test time:
the acquisition endpoints come from `web/js/repo-discovery.js` itself, so if that module changes, the
test follows rather than going stale. In the output: no excluded file, no acquisition endpoint named
outside the one inherited inline script, the acquisition closure over `web/` is exactly that single
module, every `<script>`, `<link>` and CSS `@import` resolves inside the tree, and the total bytes equal
the sources minus the removals the receipt records.

The part that makes it an assertion rather than a description: a negative control assembles with the
module forced back in and must fail, and a second audit runs over a *doctored copy* of the output —
module restored, manifest link returned — reporting excluded-file, closure, dangling reference and
vocabulary. A test that cannot fail is not evidence.

What it cannot prove is written down instead of glossed: it reads code and markup, not behaviour, so a
URL built at runtime would pass; closing that needs the CSP `connect-src` scoped to the user's server,
which is a build-time decision PLAYER-003 has to make first. It also says nothing about the packaged
artefacts, because none has been produced yet.


## Two shells, neither of them the server — 2026-11-12

Tauri 2 for both, native WebView, no Electron, no bundled browser, no second HTTP server. The
decision that shapes everything else is that **Tauri is the optional desktop interface, not the
foundation**: the server still starts from `bin/hardlylistening-server` under systemd or Docker with no
display and no toolkit, and `tests/test_headless.py` still proves it after both trees landed.

`desktop/` is the host. Six commands — status, start, stop, open the web UI, quit, and the env the
window needs — with the canonical launcher bundled as a Tauri sidecar, staged by a script that copies
it under the target triple and records a SHA-256 so a stale copy is reported instead of run. Its
dependencies are Tauri, serde and nothing else: no shell plugin, no opener plugin, so the capability
file grants `core:default` and the CSP is `default-src 'self'`. The window asks `/api/health` and
`/api/v1/capabilities` and holds no library vocabulary at all — a test forbids the words. Ownership
is explicit state (`none`, `ours`, `external`), so the case the later cards care about is already
decided: closing the window must not stop a server it found running, and the state logic says so.

`player/` is the client. One tree for desktop, iOS and Android, and one allow-list — `assemble.mjs` —
that is the only thing deciding what ships: it copies `web/` byte for byte into a gitignored
`player/www/`, and the one document it rewrites is the entry `index.html`, where references to
excluded files are dropped and recorded in a receipt. A reference to a file the allow-list merely
forgot fails the assembly before anything is written, because silence there is how a store build ends
up shipping acquisition code by accident. The shell opens on a connect screen that pings
`/rest/ping.view`; credentials live in memory only until PLAYER-002 gives them a safe home.

What is not proven, plainly: neither crate has been compiled. This host has no `webkit2gtk-4.1`,
`gtk+-3.0` or `libsoup-3.0` dev packages and no `cargo-tauri`, and layering them means a reboot. So
Rust is kept small, `rustfmt --check` is the only static proof, and every decision that can be tested
lives in Python or node — 60 checks across the two shells plus 72 node assertions. First real build
should be expected to correct details; the config, the sidecar contract and the state machines are
pinned so those corrections stay small.


## The versioned surface, delegated — 2026-11-12

`/api/*` is what the app has used for years and it is not going to be rewritten underneath a client.
So v1 is a second table, `api/v1.py`, and every route in it is one call into a service the app already
has: `repo.snapshot/releases/tracks/artist/subscriptions`, `library.service` for songs, albums,
artists, playlists and favourites, `nowplaying` plus `library.playback` for playback state, queue and
stream, `social.service` for activity, comments and a summary. Nothing in `api/v1.py` runs SQL. The
library service gained exactly three projections — albums and artists grouping the scope it already
computes, and one saved playlist — because that is the domain's job, not the adapter's.

One envelope, `{api, resource, data, count?}`, errors still `{"error": …}`, an unknown v1 path a 404
and a wrong method a 405 that names the one that works. Authentication is per route rather than per
prefix, mirroring its neighbours: shared reads need a session only when accounts are on, personal
routes always authenticate, and capabilities keeps answering first and publicly. With accounts off
there is no member, so the personal routes refuse exactly as `/api/listening/*` does — an empty
library would be a lie. `docs/api-v1.md` states the contract and the deliberate gaps: reads only, no
acquisition, no profiles or search, subscription writes still on their old routes.


## One artefact may cross, and it names no secrets — 2026-11-12

A track can be acquired from a provider, and the day a peer is allowed to ask for that track the
question is what exactly travels. `repo/federation.py` answers with a descriptor and nothing else: the
provider, the provider's own track id, enough source metadata to re-acquire, and a canonical versioned
encoding. The field list is an allow-list, and a bad input is **refused** rather than quietly trimmed —
the caller gets to see what it lost, and the refusal names the fields, never their values, so the
exception can go in a log. Names are judged, values are not: a song called "Password" is fine, while
`source_url` must be http(s) or empty, which is what stops a descriptor describing a file on disk.
`parse` re-checks on the way in, so a descriptor from a peer is held to the same rule as one built
here.

The direction is asserted, not promised. FED-001's test reads the AST and pins that exactly one module
imports the federation code — `repo/service.py`, which is the facade the rest of the app already talks
to — and that the player-facing modules and the shipped `web/` client never mention it. It also gave
the node an identity: `identity/node-id` and a 256-bit `identity/node-secret` at `0600`, created once
with `O_EXCL`, never rewritten, and nothing generated at boot so a fresh install's `identity/` stays
empty until it is needed. What is not built, plainly: an asymmetric keypair and peer signatures. The
stdlib has no Ed25519, no dependency was added, and the secret is opaque symmetric material with no
scheme chosen yet — that lands with the mirror cards that actually need authentication.


## A client library that asks first — 2026-11-12

Two modules, no framework, stdlib only. `sdk/subsonic.py` is an OpenSubsonic client that follows what
this server actually answers rather than what the protocol allows: three credential forms (password,
token + salt, the device invite the phone app already uses), one envelope parser, the error codes
mapped to exceptions — including the two endpoints `subsonic.py` honestly refuses, which raise
`NotSupported` instead of pretending — typed songs, albums, artists, playlists, bookmarks and search
results, and stream/download/cover URLs. Ranges are real: one `Range` header per request, and
`iter_range` resumes from the byte it stopped at, so an interrupted download is not a restart.

`sdk/capabilities.py` is the other half of the same rule. `/api/v1/capabilities` is public and derived
from the modules that implement each feature, so a client asks instead of probing. Only what the
document names counts: this server advertises `discovery` but no `repo`, so a client must not upgrade
"knows the source" into "has acquisition", and federation has no flag at all, so it is False with the
reason written down. A missing, malformed or failing document degrades to the OpenSubsonic core with
`degraded=True` and a reason, never an exception — a client asks this before it can do anything else.


## The platter follows the pitch — 2026-11-12

The card was a question, not a bug: does the mixer keep its platter tied to the deck after a pitch
move? It does, and now it is pinned. `jogSpinTick` runs on the frame clock and reads the deck's
`playbackRate` every frame rather than caching it, so a fader move is on the record's speed by the
next frame, going with the turn pitched up and backwards going against it. A dragged disc is left
to the hand, a paused deck stops where it is, and the library player's record stays the fixed one by
design — its 1.8 s is a literal animation with no rate in it, only the phase it resumes from.

`tests/test_mixer_rate.cjs` drives the loop with hand-supplied timestamps and reads degrees off the
disc: 1.00×, ±8%, a mid-play fader move and back to 0%, plus a dragged disc, a paused deck and the
library record's fixed duration. Six of those checks fail if the rate is hard-coded, so the test is
not vacuous. `test_dj_jog.cjs` slices the same block and had been failing since the spin loop
landed inside it; it now stubs the frame clock.


## Who deleted that — 2026-11-12

A delete left one line in `logs`, which keeps 400 rows and shares them with download noise, and that
line did not say who did it. A delete can take a file off the disk, so it is the one action worth a
record that outlives the log: `library_deletions` is append-only and never pruned, and every row
carries the member's id and name, what was deleted and its title, how many tracks and files went, the
real bytes measured from disk, and when. Local mode records "local" instead of inventing a member.

Only `deletion.py` writes it — that module is the only code allowed to delete, so a new delete path
cannot quietly skip the record. Restoring a song and emptying the trash both leave the row alone:
the file may come back or go for good, the fact that someone deleted it does not. `GET /api/deletions`
is admin-only, like deleting itself, and the "Deleted songs" dialog lists it under the tombstones.


## Mixer preload queues + control reset — deployed 0.9.6

- Two ordered queues (Deck A / Deck B) fed by one search panel. Every queued
  track is fetched into memory ahead of time, so cueing a deck is a pointer swap.
  Live per-row state: `queued` → `preloading NN%` → `ready`, or `no preload` when
  the fetch fails (the deck still streams on demand); 3px progress bar, live
  header count, two downloads at a time in arranged order.
- Copy a queued track to the other deck (`→B` / `→A`) or duplicate it in place
  (`⧉`). Both reuse the same in-memory audio — no second download — and the same
  track may sit on both decks. Queue order persists per browser.
- Queue panels carry identical helper text, stretch to equal height, hint pinned
  to the bottom.
- One unobtrusive header `Reset` glides every control back to its page default:
  duration scales with travel within the control's own range (≈145 ms nudge,
  1.2 s full cut, ease-out), double-click is instant. rAF with a 250 ms timer
  fallback so a hidden tab still finishes. Old reload button renamed `Reload`.
- Tests: `tests/test_dj_queue.cjs`, `tests/test_dj_reset.cjs`, `tests/test_dj_midi.cjs`;
  browser checks at 1280 px and 390 px with network throttling for the transfer
  states. Limits: 12 tracks per queue; preloaded audio is held in RAM.

## Tempo: analysis, display, sync — deployed 0.9.7

- `library/analysis.py` now fits a beat grid (with subdivisions) to the onset
  envelope over 60–190 BPM, weighted by a soft prior near 130 BPM, instead of
  plain autocorrelation. Synthetic click tracks return 100/124/128/140/150
  exactly; real tracks land in their genre range and each result carries a margin
  (`bpm_conf`) and a beat `phase` in seconds.
- Octave ambiguity is inherent (an 87 BPM kick-and-hat pattern and a 174 BPM
  pattern are the same onsets), so the UI ships explicit `½` / `×2` correction
  next to each deck's tempo plus a typed value; a manual value is stored
  `bpm_source='manual'` and never overwritten by re-analysis.
- `library/tempo.py` runs one background worker (2–3 tracks/second, capped
  batch 500) writing `tracks.bpm|bpm_conf|bpm_phase|bpm_source|bpm_at`; progress
  at `GET /api/tempo/status`, start at `POST /api/library/tempo` (admin).
  `/api/track/<id>/bpm` corrects one track (admin).
- Mixer: every deck shows its live BPM (stored tempo × playback rate) with the
  rate beside it; `SYNC` solves the pitch percent that matches the other deck and
  refuses with the exact required percent when it exceeds the ±8% fader instead
  of half-applying. Queue rows and search results show `NN BPM` or `—`.
- Tests: `tests/test_tempo.py` (8), `tests/test_dj_sync.cjs`,
  `tests/test_dj_queue.cjs`, `tests/test_dj_reset.cjs`, `tests/test_dj_midi.cjs`.
- Limits: no beatgrid/quantised nudge, no master tempo, no key detection; a
  correct BPM still needs an ear on octave-ambiguous tracks.

## Mix recordings stay in the tab — deployed 0.9.8

- Recording no longer downloads automatically. `MediaRecorder` stop produces a
  Blob held in `recordings[]` in memory; a panel appears with size, duration,
  `Play`/`Pause`, a native scrub bar over a shared player, an explicit `Download`
  link and `×` to discard. `Clear all` drops everything.
- Nothing is uploaded and nothing is written to disk until Download is clicked;
  the server has no recordings route. A warning in the panel states the mix lives
  only in this tab and is lost on reload/close, and `beforeunload` prompts while
  un-downloaded takes exist.
- Names are timestamped and de-duplicated (`…-2.webm` for a second take in the
  same second); size rolls from KB to MB at 1 MiB; a blob that never reports a
  length shows `—` rather than a stuck `…`.
- Tests: `tests/test_dj_recording.cjs` (no `a.click()`, tab-local copy, explicit
  download link, naming/size/clock helpers, escaping).
- Not verified here: actual playback/scrub of a recording — this headless
  Chromium cannot decode audio (readyState stays 0 for mp3 and wav blobs). The
  player element, wiring and layout were checked; the audible path needs his
  browser.

## Now


LISTEN-1 / row 50 and LISTEN-2 / row 51: backend plus member-visible connection setup and explicit personal song/playlist save controls implemented in source. All 65 Python tests and four DOM suites pass. Parent review/deployment pending; no restart, live DB writes, publishing, audible playback or real-client compatibility verification. Companion/extensions remain future milestones, not part of this delivery.

DL-MP3 / row 49: a single-track album or playlist saves the audio file itself
(named `Artist - Title.mp3`) instead of a one-file zip; anything with more than
one playable track still has to be a zip, because a browser saves one file per
click.

DJ-PICK / row 47: the mixer's single full-width library search is now two pickers
side by side, each aligned under its deck and each carrying both **A** and **B**
buttons — search one thing on the left while the right keeps what you already
chose. Each picker also has its own **Playlist** dropdown — All library or one set —
so one side can browse a set while the other searches everything. They stack below
860 px with the decks.

SOC-1 / row 42..44: votes move on the click (counts repaint before the round trip,
pressed button pulses, failed votes roll back with a toast); the newest comments
drift over an album cover as bubbles, one at a time, and the ticker survives the
library poll because the grid is only re-rendered when its markup changes.
ALB-1 / row 45: album and playlist pages list the tracks that never downloaded,
say "N not downloaded" in the hero, and offer **Download missing (N)** — admin only,
and it never re-queues a file that is already on disk.
ART-1 / row 46: artist pages carry a profile block — the source's own RSS feed
(SoundCloud only; Apple publishes none), follow state, and the releases that feed
has produced. Nothing downloads from a follow.
Waiting on the owner's look before the next `web/index.html` card.

MIDI-1 / row 40: the mixer speaks MIDI — faders send CCs, hardware can drive it back,
with a Learn mapping panel on `/dj`.
DJ-EXP / row 41: Export… in the Library header hands the whole library or one playlist
to a DJ app as m3u8 or Rekordbox XML.
Both wait on the owner trying them with his own controller/software.



LIB-UI-1 / row 39 (done): library rebuilt as album grid, artists, song table and playlist
cards with detail pages and a full player bar (shuffle, repeat, volume, seek).
Social counts ride on one batched endpoint; discussions open from any card or row.
Waiting on the owner's look before the next `web/index.html` card.



ADMIN-UI / row 37: system controls are admin-only in the interface, matching the
backend. Settings, pause, clear-finished, import and library move, folder import,
dropzone, activity log, follow box, feed refresh/queue/ignore, shared-track
re-run/source/skip/review are hidden for members; playback, library browse,
downloads, discussions and removing their own playlist stay. `/api/state` carries
`viewer.is_admin` and per-playlist `can_remove`. Verified in a real browser with the
member view simulated and restored.

## Next

FED-1 / row 38: federation roadmap recorded in PLAN (peer keys, canonical identity,
opt-in sharing, peers are not members, storage never merges). Not scheduled for
building until single-instance stability.



SOCIAL-1 / row 36: shared comments and atomic up/down/reset votes implemented
for tracks, canonical artist+album groups and playlists. Always verified approved
membership, including legacy local mode; own-comment editing/admin moderation,
plain-text DOM, pagination, CSRF and centralized limits. Isolated Python + DOM
verification only; no restart/exposure, profiles or live sign-in completion claim.
See `PLAN.md` for grouping and live-refresh limitations.

AUTH-1 / row 35: local admin-only invite enrollment, persistent hash-only browser
sessions and People controls implemented in source. Parent review, HTTPS deployment
and private terminal bootstrap remain; no provider/email configuration needed.
Profile names/icons now implemented above; history/legacy ownership remain future work. See SECURITY.md.

Downloader reliability remains the owner priority; export and RSS are deferred.

| Card | Row | Item |
|---|---|---|
| — | — | No active playlist-organization work; implemented and isolated tests passed |

## Next

| Card | Row | Item |
|---|---|---|
| FEED-4 | 22 | Generic RSS/Atom source into the same feed |

## Later (post-MVP, in order)

| Card | Row | Item |
|---|---|---|
| PLAY-2 | 19 | BPM detection + sync on the mixer (analysis module is written and tested) |
| FED-1 | 24 | Funkwhale connection + ingest queue |
| FED-2 | 26 | Federated feed items |
| PKG-1 | 27 | Packaging: desktop entry, AppImage/flatpak, Docker |

## Done

LIB-6 / row 34: Browser attachment and playlist/group ZIP downloads; pending
controls unavailable, omission manifest, read-only disk-spooled exports. Isolated
HTTP/generated-audio and DOM tests passed; service not restarted.


LIB-5 / row 16: Early playlist organization — relative M3U8 + all-track manifest, safe managed cleanup, Library selector and pending placeholders; isolated Python and DOM tests.

| Card | Row | Item |
|---|---|---|
| DL-4 | 32 | Apple AI metadata approval; fail-closed review, pins preserved; mocked tests and live synthetic fixtures |
| DL-1 | 28 | Downloader hardened: live progress, Stop that kills yt-dlp, output verified |
| DL-2 | 29 | Retry ladder 30 s / 2 m / 10 m / 1 h with up-only jitter |
| DL-3 | 30 | Needs Review: weak/no matches park with candidates instead of failing |
| CORE-1 | 1 | SQLite schema, canonical single-copy store, membership tables |
| CORE-2 | 2 | Provider contract + SoundCloud / Apple Music / generic providers |
| CORE-3 | 3 | yt-dlp wrapper: search ladder, scoring, error classification |
| CORE-4 | 4 | Queue worker: per-track jobs, backoff, provider-wide 429 cooldown |
| CORE-5 | 5 | Verify end to end: SoundCloud set, Apple song → YouTube fallback, DRM fallback |
| UI-1 | 6 | Queue UI (paste, progress, per-track retry/skip/source) |
| RUN-1 | 7 | systemd user unit, autostart, service-hub row, 0.0.0.0 binding |
| PLAY-0 | 8 | DJ mixer at `/dj`: 2 decks, EQ, filter, FX sends, meters, record |
| CORE-6 | 9 | Range-request audio endpoint (`/api/track/<id>/audio`) |
| LIB-1 | 10 | Library view: search, artist grouping, click to play, empty state |
| PLAY-1 | 11 | Player bar: art, title, play/pause, prev/next, seek, time (muted test) |
| QUEUE-1 | 12 | Reorder waiting jobs; list now sorts by real run order |
| QUEUE-2 | 13 | Clear finished button; a track with a file stays "completed" without its job |
| LIB-3 | 17 | Import local audio: path, picker, drag-drop; dedupe by content key |
| LIB-4 | 18 | Move library root: files moved, paths rewritten, streams follow |
| FEED-1 | 20 | Follow a SoundCloud artist; seen-set baseline, no backfill |
| FEED-2 | 21 | Feed tab + Following tab: Queue / Open / Ignore, handled items behind a toggle |
| LIB-2 | 14 | Playlist membership on library rows: a chip per set, searchable by set name |
| QUEUE-3 | 15 | Disk guard: nothing starts below `min_free_gb`; auto-resumes, banner explains |
| FEED-3 | 23 | Apple Music following via the public iTunes lookup; same feed, same submit-only rule |

## Parked — stretch goals

EVENTS-CA / backlog 57: California performances on artist profiles, ticket links,
then a personal Shows page prioritizing most-listened artists with upcoming dates.
Gate: verified artist/event sources + actual per-user listening history. No implementation now.

| DJQ-1 preload queues | done 0.9.5 | Two drag-ordered queues (A/B) fed by one search. Every queued track is fetched to a Blob up front — status chip queued → preloading NN% → ready (or "no preload" on failure), 3 px progress bar per row, live header count, 2 downloads at a time in queue order. Copy a row to the other deck (→B/→A) or duplicate it in place (⧉) with no second download; same track may sit on both decks and shares one blob. Queue order persists in localStorage. Tests: tests/test_dj_queue.cjs + live browser checks at 1280 px and 390 px. |

## 0.11.0 — UI, playlists, queue, mixer and Party

Implemented Songs grid/list, right-click/touch-hold menus, persisted browser playback queue with reorder and move controls, owner-edited manual playlists, Auto DJ BPM selection/crossfade and late-join gesture synchronization. Party page permits all approved members to create/invite; reusable guest identity links expire within 24 hours, guests choose name/icon and remain party-scoped. Aggregate queue votes reorder waiting songs; member aux lease controls playback. Party chat resolves sender profiles server-side and preserves drafts.

Verified combined: 251 Python tests and 32 DOM suites passed, no audible tests or real-record test writes. Deployed service 0.11.0; local health and Party assets 200, unauthenticated Party/library APIs 401. External HTTPS probes returned 403, so real public guest enrollment remains unverified. Multi-speaker synchronization not implemented; Auto DJ is basic BPM/crossfade, not beat/phrase matching.

## Public HTTPS verification follow-up

Resolved the earlier probe ambiguity: Cloudflare returns 403/error 1010 for Python-urllib/3.14, but 200 with HardlyListening-HealthCheck/1.0 and in Chromium. No tunnel/security configuration changed. Read-only tools/check_public.py passes health/Party HTML/JS/CSS 200 and signed-out Party/library APIs 401 on the public origin. Browser confirms Party shell renders. Actual guest enrollment is still not exercised on live data; isolated Party enrollment/security tests remain the evidence for that flow.

## Edge offline fallback — deployed and outage-tested

Cloudflare Worker `hardlylistening-offline` route music.hardlysleep.ing/* deployed to the domain-owning account. Origin remains the PC tunnel; no VPS proxy or DNS change. Worker serves red offline/unreachable HTML with 30-second refresh for document requests and no-store 503 JSON for API requests on tunnel/gateway failure. Normal responses/streams and auth denials pass through. Live verification stopped only the local tunnel connector: public /party returned 503 edge HTML and /api/health 503 edge JSON. Connector restarted in finally; public health recovered to 200/version0.11.0. Six public smoke checks passed before outage test. Worker version 9ec2a3cd-a7c1-4bff-85b8-718fdb2aa245. Source/tests/config in edge-fallback/. Cloudflare Workers plan quotas apply; no physical PC reboot was needed for the disconnected-tunnel test.

## 0.11.1 — compact phone dock, Following inside Feed, song-grid density

The phone navigation is one compact 48 px bar flush to the bottom edge; the stale
floating-dock rule in `index.html` (width 560 px, `translateX(-50%)`, 18 px radius,
blur and shadow) was overriding nothing but leaving those props live, which is why
the bar read as a broken floating pill. Five slots now: Queue, Feed, Repository,
Library, "…" (Settings/Party/Mixer). Following moved into the Feed view as a third
tab (Activity / Artists / Following) instead of taking a nav slot; `#view-following`
no longer exists and the feed panes own the follow list.

Songs grid: a density slider (2–6 columns on a phone, 2–12 on desktop, remembered in
`hl.songCols`, applied as the `--song-cols` CSS variable) replaces the fixed
auto-fill track. Cards lost their position row and slack lines — art, title,
artist|album on one line, votes, save+like+pin, duration+actions — and Like/Pin are
the same 26 px box as the vote chips instead of a 34 px button.

Measured in a Playwright fixture against the real CSS: 390 px → 2 columns, 177 px
cards, bar height 48 px, bottom 844 (flush), left 0, no transform/shadow/blur, five
slots at 74.8 px each, `scrollWidth == innerWidth`; 1280 px → 6 columns, 170 px
cards, no overflow. `setSongCols` clamping checked in a vm harness (phone 9 → 6,
desktop 12 allowed, floor 2, value persisted). Note: `tests/test_song_grid.cjs`
fails on this checkout before and after this change (its DOM stub has no `.style`
and `setView` writes `$('#libsort').style.display`), so it was not the evidence used.

Addendum (same release): the player now stacks on the phone dock instead of
floating 22 px above it — `#player{bottom:calc(var(--nav-h) + var(--safe-b))}`
with `z-index:60` against the bar's 50, so the bar can never paint over the
transport controls (`elementFromPoint` at the player's bottom edge returns the
player, not `#appnav`). The More panel went to `z-index:70` so it still sits above
the player, `#toast` moved to `118px + nav-h`, and the body reserve with the player
open is `131px + nav-h` (179 px at 390). Measured 360/390/430: player bottom == nav
top exactly, gap 0, overlap 0, `scrollWidth == innerWidth`, tab pill 39 px inside a
48 px bar with 4 px inset margins.

Fullscreen playback is modal: `body.fullplayer` now hides the dock and the queue
drawer (`body.fullplayer .tabs, body.fullplayer #playback-panel{display:none!important}`)
and `#player-full` moved to `z-index:90`, so no bar can sit under or over the
options row. Measured 390/430/1280 with `.on` + `fullplayer`: nav `display:none`,
#player-full spans 0→viewport height at z 90, every hit-test point at the bottom
edge resolves to `#player-full`, no horizontal overflow; closing fullscreen
restores nav `flex` at 48 px with the mini player's bottom still on the bar's top.

Fullscreen disc scrub. The drag used to rotate the record and set
`currentTime` on every pointer move while painting nothing else, so the clock and
seek bar only moved on the browser's `timeupdate` (~4 Hz), and the seek storm made
playback stutter. Now `paintFullPlayerPosition()` runs on every move (clock, seek
bar, duration), `pfDiscSeek()` asks the audio to move at most once per 60 ms, the
first real movement pauses (a tap does not), and release commits the exact dragged
position, resumes if it was playing, and re-syncs the record's angle to the
committed time. Keyboard arrows on the disc repaint the clock too. New regression
test `tests/test_pf_disc_scrub.cjs` (vm-sliced handler block, fake audio) passes
alongside `tests/test_fullplayer_ui.cjs` at 390 and 1280.

Playback queue rebuilt as a calm sheet that works inside fullscreen playback.

* Opening the queue from the fullscreen player no longer closes it
  (`#pfqueue` just calls `togglePlaybackPanel(true)`); the drawer becomes a
  full-width sheet docked to the bottom edge at `z-index:95`, rounded top only,
  above `#player-full` (90). Escape peels one layer: the sheet closes first, then
  fullscreen. The old rule that hid `#playback-panel` while `body.fullplayer` is on
  was the reason the queue "ended" fullscreen; only `.tabs` is hidden now.
* Much less busy: rows are 46 px one-liners (title + muted artist), no per-row
  borders, the drag grip appears only on hover/focus (hidden on touch), the remove
  × stays until hover, the state word is a 9.5 px muted label, the now-playing strip
  is a single line marked by the live dot alone, the two decorative paragraphs
  (gesture hint) are gone, and the footer is two quiet outline buttons.
* Fewer items on screen on purpose: the list is capped at min(46vh,320px)
  (min(40vh,300px) on phones) — 6 of 9 rows visible, the rest scroll.
* Two real bugs found while checking it: `bindOrderDrag` captured the pointer on
  pointerdown and cleared `dataset.rendered` on pointerup, so every plain click on a
  queue row repainted the list under the cursor and the click never landed — tapping
  a song did nothing. Capture now waits for a grip or real movement, and a tap no
  longer repaints. `tests/test_playback_queue_ui.cjs` needed two stale stubs
  (`aheadAudio`/`aheadJobs`) and a `justDragged` reset; it passes at 1280 and 390 now,
  alongside test_pf_disc_scrub and test_fullplayer_ui.

Measured: 390 px sheet left/right 8, bottom 0, h 424, rows 46 px, 6 visible of 9,
toolbar intact, no horizontal overflow, `#player-full` still `.on`; the non-fullscreen
drawer sits 374 px wide with ~18 px above the mini player.

Player icon buttons aligned. The global `button{padding:10px 14px}` was pushing every
glyph inside its circle (a 32 px play button had 28 px of horizontal padding, so the
icon sat flush against one edge), and `button{min-height:40px}` turned the 30/34 px
controls into ovals on desktop while the phone layout declared 27x29 and 22x27 —
three different shapes in one row. Fixed for `.iconbtn`, `.splay`, `.sdl`, `.aplay`,
`.plartwrap`, `.mdclose`: `padding:0;min-height:0;line-height:1`, and the phone sizes
are now square 26/30/34 px (volume 28). Measured 390 and 1280: every button reports
width==height, border-radius 50%, and glyph centre offset 0.00 px both axes; the
fullscreen set is 34 px with a 60 px play. Queue/disc/fullscreen suites still pass.

Feed Activity is now one collapsed card per member per day (0.11.1).

`activity.feed()` folds every event a member produced between midnight and midnight
into a `kind='day'` card: `day` (local YYYY-MM-DD), `ts` (newest event), `first_ts`,
per-group `counts` and `groups.listen/add/playlist/other` carrying the entries
themselves. Cards sort by their newest event, so any new action lifts that member to
the top; boosts stay pinned above. The old per-sitting listen stack and its tests are
gone — one evening of listening used to fill the feed with a dozen entries.

The client renders `.actday`: a single header line (name · "18 songs played · 13
added · 2 playlists" · Today/Yesterday/date · chevron) with the detail collapsed. A
group shows five rows and then `Show N more`; the member name still opens the
profile and does not toggle the card; expanded cards survive the poll via `openDays`.
On phones the count line drops to its own row instead of truncating.

Verified: `python3 -m unittest tests.test_activity` (12 tests, new day-folding /
new-day / per-member / top-lift cases) and the new
`tests/test_activity_day_card.cjs` (collapsed height < 80 px, 5 then Show 3 more,
name-tap opens the profile, no overflow at 1280/390). Against a read-only copy of the
live database the feed went from ~19 separate entries to 2 cards (one member's day
counted as 18 listen + 13 add). Service restarted so the new module is loaded:
/api/health reports 0.11.1.

No zoom. Every page's viewport meta went from `maximum-scale=5, user-scalable=yes`
to `maximum-scale=1, user-scalable=no` (index, party, dj, login, link), and the CSS
adds `html,body{touch-action:manipulation}` globally with
`@media(max-width:760px){html,body{touch-action:pan-x pan-y}}` to block pinch and
double-tap zoom. The "it jumps to zoomed when I tap the paste box" part was iOS's
focus zoom, which fires for any field under 16 px: form controls now carry
`font-size:16px` at phone widths (index/polish.css, party.css, dj.html inline,
login/link inline). Desktop field sizes are unchanged (13–15 px).

Measured at 390 px: meta is `maximum-scale=1, user-scalable=no`, html/body
`touch-action:pan-x pan-y`, and #url / .libsearch / #followurl / #playlist-name all
compute to 16 px (they were 13–15 px). At 1280 the touch-action is `manipulation`
and the original field sizes are intact. Queue, disc-scrub, fullscreen and activity
suites all pass.

Adding a playlist link now always answers.

The paste box could sit on "Reading…" for up to five minutes with nothing else on
screen: `queue.ingest` resolves the whole set inside the request and the SoundCloud
provider used yt-dlp's 300 s default timeout, while the client showed only the button
label and, on success, just emptied the box (a repeat link returned `{existing:true}`
and produced no message at all).

Client (`addUrls`): a live "Reading… Ns" counter, then an inline status line under the
box (`#addstatus`) saying exactly what happened — "✓ “Shuffle” — 399 songs, 399
queued for download", "• … is already in the queue — nothing new was added", or
"✕ <server error>"; a 150 s AbortController watchdog that frees the button with "the
source did not answer within 150 s"; the pasted text is kept on failure so it can be
retried; a toast on success. No more `alert()`.

Server: provider expansion is capped at 90 s (`ytdlp.json_info(url, timeout=90)` in
providers/soundcloud.py resolve and providers/generic.py) so a stalled source fails
while the person is still looking at the page. `server.log_message` no longer raises
AttributeError when a keep-alive timeout logs before `self.path` exists.

Verified: `python3 -m unittest discover -s tests` — 300 tests OK; new
`tests/test_add_feedback.cjs` covers success counts, already-queued notice, inline
server error, abort message and the empty-box guard. `SoundcloudProvider.resolve`
returned 399 tracks in 8.5 s for the share link already in the library. Service
restarted; /api/health 0.11.1. A live POST cannot be exercised without his session
(401), so the end-to-end evidence is the earlier successful add plus these tests.

The Queue page now says which playlist is being downloaded.

`db.state()`'s `current` job carried only the track, so the top strip could name the
song and the percentage but never the collection it belonged to, and every card with
work in it printed the same "1 downloading" badge — three of them were
indistinguishable. Now:

* `current` also returns `playlist` / `playlist_id`, resolved through
  `playlist_tracks → playlists` for the job's track (first membership, so a shared
  track still names a real collection).
* The top strip leads with that name as an accent chip before the track:
  `[Late Night Set] ● Track 13 · Nvll` — with percent, bytes, speed, ETA and the bar.
* The playlist card that is actually being worked on gets `class="card active"`
  (accent border), a solid **Downloading now** pill instead of "N downloading", and a
  live line under the counts naming the track and its progress
  (`● Track 13 · 47%`). Cards with work queued but not started read "N queued to
  start"; idle cards carry neither.

Measured at 390 px: the strip's chip + track sit on one line (row 48 px tall, stats
wrap under), card `.active` present, `.livenow` = "Track 13 · 47%", no horizontal
overflow; at 1280 the strip is a single 21.5 px row. `tests/test_queue_progress_ui.cjs`
now asserts the playlist chip, the active class, the live track line and that an idle
playlist claims nothing.

Phone player pill rebuilt as four named rows.

It was a 3-row grid whose areas only accounted for some children, so anything
unplaced fell into implicit rows: the scrub landed with the times on a row stretched
by the transport column (duration cell 198 px wide, seek only 142 px and offset), and
`input[type=range]{height:4px}` cut the thumb in half, so the bar looked clipped and
shifted. The Lyrics button also sat between the mute icon and the volume slider.

Now `grid-template-areas:"art meta meta" "ctrl ctrl ctrl" "time seek dur"
"extras extras extras"` with columns `auto minmax(0,1fr) auto`; every child owns an
area (`.pvol` included), the transport row is centred, the scrub is a real 20 px box
with a 4 px painted track and an un-clipped thumb, and the volume row reads
[mute][——————][Lyrics] via flex `order`. The pill is 163 px tall at 390, so the body
reserve moved to `170px + nav-h` and the toast / queue drawer to `176px + nav-h`.

Measured 390 and 430: rows 41/34/24/28, seek 20 × 264, times 38 and 34 px at the ends
of the same row, mute < volume < lyrics left-to-right, no unplaced children, no
horizontal overflow. New `tests/test_player_pill.cjs` pins all of it; fullscreen,
queue-progress and disc-scrub suites still pass.

Library and Repository list only what can play.

`renderLibrary` took its rows from `libAll` on purpose, so an album with undownloaded
tracks listed the holes with a status and a play button that could not work — queued
songs were clickable and silently failed. He wants them to appear only once usable, so
the browsing source is now `subject.filter(t => t.playable)`; the count of what is
still on its way is one quiet line above the list (`.libnote`, "N tracks still
downloading — they appear here the moment they are ready"). Playlist detail lists ready
tracks only, says "N still downloading" in the hero and keeps `Download missing (N)` so
the queue can still be grabbed. `libAll` remains the full membership for the player,
queue restore and the mixer; the album ZIP lists ready ids.

`tests/test_library_ui.cjs` was rewritten for this (ready-only rows, the note, the
playlist notice, `Download missing (1)`, playable-only queue) with a fourth fixture
track that is ready but has a dead source, so the gone-badge coverage survives.
Verified in a fixture: 2 ready tracks listed, 2 queued hidden, note rendered, no
overflow at 390/1280.

Sorting the library, including oldest → newest.

`#libsort` had a single "Recently added" entry, so the only date reading was
newest-first and the direction toggle lived on a table header nobody clicks in the
song grid. Now the select reads: Newest added, Oldest added, Title A–Z, Artist A–Z,
Album A–Z, Shortest first, Most votes, My votes — `SORT_KEYS.oldest` carries
`dir:'asc'` and the same `added` key, so picking it orders the song grid and list
oldest-first and persists to `hl.librarySort`. `sortAlbums()` learned `oldest` and
`duration` too, instead of falling through to newest-first.

Filters are unchanged and still stacked in the bar: the playlist select, the search
box (title/artist/album/set names) and the genre filter. Measured 390/1280: eight
options present, no horizontal overflow, the toolbar wrapping as before.
`tests/test_library_ui.cjs` now asserts that newest-first puts the oldest fixture
track last and oldest-first puts it first.

Add-to-library is a plus icon; no minus anywhere.

`listeningSaveControl` was a 170 px text button that flipped between "Add to library"
and "Remove from library". It is now one fixed square icon button: a plus when the
track/playlist can be added, a check once it is in (`.ghost.lsave.on`), 32 px on
desktop, 34 px on a phone, 30 px inside a song card. The words survive only as
`aria-label`/`title`, so nothing reflows when the state flips and a screen reader still
announces "Add to library" / "Remove from library". Clicking a check still removes —
that action stays, it just is not drawn as a minus.

Two CSS traps: `.lsave` alone loses to `.ghost{padding:9px 16px}` (same specificity,
later in the file) and measured 34×32; the rule is `.ghost.lsave` with `box-sizing:
border-box`. Row/card flex overrides now pin the icon instead of stretching it.

Measured 390/1280, grid and list: 30/32/34 px squares, `on` class plus check icon for
the saved track and plus icon for the unsaved one, centred. Updated
`tests/test_song_table.cjs` (square sizing + no minus glyph), `test_rowactions_ui.cjs`
(save box is a square, not 170 px) and gave `test_listening_ui.cjs` an `SVG` stub;
five library suites pass.

Song list separators are inset, so nothing runs into the card border.

Every row carried `border-bottom:1px solid #222229`, a full-bleed line that met the
card's 1px rounded border head-on: at the T-junction the darker row line died against
the lighter border and left a 1 px notch, and the header's own border-bottom did the
same. The list now draws its separators as `::after` insets — 14 px from each side on
desktop, 10 px on a phone — including under the sticky header, with the last row's
separator removed. The end-row rounding rule also never matched (`.songrow:first-child`
can never be true because `.songhead` is the first child); it is now
`.songhead + .songrow`.

Measured 1280 and 390: no row has a bottom border, all dividers start and stop at the
same inset, the first row keeps its 13 px top corners, and the card's border runs
unbroken. test_song_table, test_library_ui, test_rowactions_ui and test_queue_progress_ui
still pass.

The "…" panel now outranks the player pill.

`.navpanel` had `z-index:70` against the pill's 60, but it is a child of `#appnav`
(`z-index:50`), so its 70 only counted *inside* the dock's stacking context and the
whole dock still painted under the pill — the panel's rows were visible but the pill
sat on top of them and swallowed the taps. The dock itself is now `z-index:80`
(panel 70 within it, pill 60, fullscreen 90), so the panel renders over the pill and
every row is clickable. The bar only occupies the bottom 48 px and the pill starts
exactly at its top edge, so raising the bar changes nothing else visually.

Verified 390/430 with the player on: panel 132 px tall fully above the pill, hit-testing
every visible row lands inside `#navpanel`, no overflow. `test_player_pill.cjs` now also
asserts the dock outranks the pill and that each panel row is hit-tested through.

Phone player pill: 10 px top corners, flat bottom.

`#player.on` on a phone was `border-radius:20px`, so it read as a floating pill with a
rounded bottom sitting on the dock. It is now `border-radius:10px 10px 0 0` — a squircle
card that sits flush on the bar. Desktop keeps its 999 px pill (the media query is
`max-width:640px`).

Measured: 390 px → computed `10px 10px 0px 0px`, 374 × 163, no overflow; 1280 px still
`999px`. test_player_pill and test_fullplayer_ui pass.

Playing state is now visible on the item itself in Library/Repository.

Before, a playing track was only a tinted row and a pause glyph in the small play
button — easy to miss in a grid of covers. Now:

* **Grid card**: an equaliser badge (`.nowbars`, `SVG.bars`) sits on the bottom-right of
  the artwork, on top of the accent border, while the play button flips to pause.
* **List row**: the index cell swaps its number for the same animated bars in accent
  colour (`.idx.playing`), alongside the existing tinted row and pause glyph.
* **Album card**: the cover of the album that contains the playing track gets the badge
  too (30 px version), with `.acard.playing` as before.
* Rows announce `— playing` in their `aria-label`; the badge is `aria-hidden` and
  `pointer-events:none`, and `prefers-reduced-motion` stops the SMIL bars.

Measured 390/1280: badge 24 px in a card, 30 px on an album cover, exactly one
`.songrow.playing` per list, no overflow. `test_library_ui.cjs` now asserts the badge in
grid, the bars-in-index in list, the marked album cover, and that nothing is marked when
playback stops; song-table, row-actions, player-pill and activity suites still pass.

Favorites are two dynamic playlists now, and likes are public.

The "Favorites" section under Pinned is gone. `preferences.snapshot()` returns
`public_favorites` alongside the caller's own `favorites` — `{track_id: [{id,name,icon}]}`
from `listening_favorites LEFT JOIN members`, name-ordered, unknown names falling back to
`Member`, stale track ids dropped. Pins stay private and are never in that payload.

The Playlists view now leads with two cards inside the same grid as real playlists:

* **Favorites** — every song you liked, `2 songs · yours`, rebuilt on each render; nothing
  is copied into `playlists`/`playlist_tracks`, so liking or unliking changes it instantly.
  Opens the normal song list (grid or rows, respecting the layout switch).
* **Everyone's Favorites** — the union of every member's likes, sorted most-liked first,
  `4 songs · 6 members`, with coloured circles of the members who liked them on the card.
  Opens a row per song: cover, title, artist, then the liker circles — up to four, then
  `+N` — each circle opening that member's profile. A row plays on click or Enter.

Shared scope keeps Everyone's Favorites and hides your personal one. Version 0.12.0.

Verified 390/1280: two cards at 216 px desktop / full width phone, no overflow, 4 liker
circles + `+2` on the top row, chips 22 px on a phone. `tests/test_preferences.py` (15
tests, OK) covers the payload shape, per-member entries and unlink removal;
`tests/test_preferences_ui.cjs` covers both cards, the scope rules, the chips, `+N`, and
that a liked row plays; listening/library suites still pass.

Feed: name the action, and never run a stale shell.

"… did something" was `actLine`'s fallback, and the only way to reach it is an item kind
the shell does not know. That is exactly what an *open* tab hits after a deploy: the page
keeps polling `/api/state` for days, the API starts returning `kind:'day'` cards, and the
old shell — whose `actCard` has no `day` branch — hands each one to `actLine`, which shrugs.
A reload fixed it; now it cannot happen again.

Two changes:

* The summary names joins and renames instead of counting them: `activity.feed()` sorts them
  into `groups.other`, and the glance line used to say "1 other action". It now says
  "joined"/"1 rename", the detail group is titled Account ("joined the library", "is now
  known as …"), and an unknown kind is dropped rather than printed raw. `actLine`'s fallback
  is "was active" — still reachable only for a kind added later.
* `web/lifecycle.js` watches the server version: it stores `db.VERSION` from `/api/health`
  in `sessionStorage` on boot and reloads once when the server moves on (checked on load and
  every 60 s). The new version is stored before reloading, so it cannot loop, and an offline
  shell keeps running.

Measured on a copy of the live DB (second server on 8481, session minted by hand): the two
real cards now read "19 songs played · 64 added" and "joined". `test_activity_day_card.cjs`
asserts a join card says "joined" and that no raw kind or "other action" appears;
`tests/test_shell_version.cjs` covers remember/reload-once/no-loop/offline.
Version 0.12.1.

## Library grid cards: bigger save button, aligned Post comment, round queue-next

The save/plus button on a grid card (`polish.css`, `.songgrid .songcard .lsave`) was 39 px
with a 14 px glyph, and the card's own padding was 9 px, so the plus read smaller than its
corner and every control sat off a fat margin. It is now a 46 px box (40 px under 760 px, scaled further by card width)
with a 20 px glyph, corner inset 6 px, and the card padding is 6 px (5 px). The
button is still absolutely positioned, so the card keeps the height its rows already had —
measured 344.8 px before and after at 1280, and the vote chips stop short of it. The album
cell takes a 56 px right padding on desktop so the bigger button cannot run under the text.

Two defects surfaced while checking that corner. `button{min-height:40px}` in `polish.css`
was stretching the 30 px play-next circle on the artwork into a 30×40 oval; that rule now
carries `min-width:0;min-height:0` and it measures 30×30. And in the discussion drawer the
Post comment button sat at the left under the textarea like the vote buttons; it now takes
`margin-left:auto`, measured 8 px from the right edge, still on its own line below the
textarea.

`test_rowactions_ui.cjs` and `test_library_ui.cjs` pass. `test_song_grid`, `test_song_table`,
`test_personal_library_ui` and `test_listening_ui` fail identically on a pre-change copy of
the tree, so those failures predate this work and are untouched by it.

## Grid cards now shed controls as the density rises

The card only looked right at two per row, where every control has room. At 3 and up the
vote row, the comment count and the save button were fighting over the same 80–260 px, and
at phone densities the download/play pair spilled past the card edge.

`applySongCols()` now writes `data-cols-tier` on `<html>`: 2 per row keeps everything, 3
drops the vote row (up/down/comment), 4 and up keeps only the save button. The attribute
decides *which* controls exist; every size comes from the card's own width, because the card
is a container (`container-type:inline-size`) with breakpoints at 380 / 280 / 150 / 80 / 62 px.
So the same width looks the same whether it got there by 6 columns on a desktop or 4 on a
phone. At 4 and up the save button sits in the row instead of floating, so it can no longer
cover the artist/album line, and the two text columns are `1fr 1fr` so a long album cannot
squeeze the artist to zero. Below 62 px the download arrow steps out — downloading and
queueing are still on the card menu.

Measured 1280 and 390 at 2/3/4/5/6 columns: no element leaves its card at any density
(previously `.send`/`.sdl` overflowed at 390 with 5 and 6). The 2-per-row card is byte-for-byte
the same size as before this change (531×615.3 at 1280). `test_rowactions_ui.cjs` and
`test_library_ui.cjs` pass; `test_song_grid.cjs` still fails on its missing `style` stub, as it
did before this work.

## Grid titles slide out when the grid is idle

Titles in the song grid truncate with an ellipsis and stayed truncated until the pointer
happened to hover the card. Now, three seconds after the grid stops scrolling, every
cut-off title in view slides left far enough to read the rest and slides back — the loop
holds half a second at each end, and its travel time scales with how much text is hidden
(900 ms + 22 ms per hidden pixel, 1200 ms floor).

It is a transform on the `<b>`, never a resize: nothing reflows and no card changes height,
which is why the existing layout, the density tiers and the card's measured sizes are
untouched. `text-overflow` switches to `clip` while a title is moving (an ellipsis would sit
there during the slide) and returns to `ellipsis` the moment it stops.

Any scroll, wheel, touch, pointer press or key press stops every animation immediately and
starts the three-second wait again; the grid's MutationObserver reschedules after a re-render,
and `prefers-reduced-motion: reduce` disables the whole thing. Titles that already fit are
skipped, so a card whose title is short never moves.

`tests/test_title_marquee.cjs` (new) drives a real browser: nothing animates at 400 ms, one
animation appears per cut-off title after the linger, the transform changes over time, the
short title keeps `text-overflow:ellipsis`, a scroll clears both the animations and the clip,
and a reduced-motion page stays still. PASS. `test_library_ui.cjs` and `test_rowactions_ui.cjs`
still pass; the full-script syntax check inside `test_library_ui.cjs` covers the new block.

## Per-playlist Apple restore backups (0.15.0)

Every Apple playlist now writes a restore file, `<Title>.hardlyplaylist.json`, from
`GET /api/playlist/{id}/apple-backup` and inside that playlist's ZIP as `apple-music.json`.
One shared Shortcut reads it; the app never generates a shortcut per playlist.

`library/playlist_backup.py` owns the contract and nothing else reads the tables for it:
`format: "hardlylistening-playlist"`, `version: 1`, `backup{id,created_at,app_version}`,
`playlist{title,provider,provider_playlist_id,source_url,storefront}`, and one entry per
membership with `position`, `provider`, `provider_track_id` (always a string), `title`,
`artist`, `album`, `duration_ms`. Order comes from `playlist_tracks.position`, never from
array order; duplicate positions raise `ValueError` instead of being silently sorted. The
database's provider key `apple` is mapped to the contract's `apple_music` in one table, so a
second restorable provider is a one-line change. Storefront and `pl.…` id are read out of the
source URL, and anything unknown becomes `null` rather than a guess.

On the playlist page, an Apple playlist with tracks shows **Apple Music Backup** (the file)
and **Add Restore Shortcut** (the shared iCloud link). The shortcut link comes from
`HARDLYLISTENING_APPLE_RESTORE_SHORTCUT_URL`, surfaced in `/api/state`, and stays hidden
while it is empty — a button that installs nothing is worse than no button. Only `https://`
values become links, and the URL is escaped into the markup. Non-Apple playlists keep their
own provider name in the file but do not offer the Apple controls.

`/api/library` playlists now carry `provider` and `source_url`, which is what lets the page
gate those controls without a second request.

Tests: `tests/test_playlist_backup.py` (new, 9 cases) asserts the contract, ordering, string
ids, `duration_ms`, duplicate-position rejection, the 404 for an unknown playlist, the ZIP
sidecar, and the real HTTP route including its `Content-Disposition`; `tests/test_apple_backup_ui.cjs`
(new) covers both buttons, the configured/unconfigured shortcut, the provider gate and URL
escaping. `test_browser_downloads.py` was updated: a playlist archive now holds three entries
(audio, `MANIFEST.txt`, `apple-music.json`) while a bare id-selection archive still holds two.
Full suite: 310 tests OK. Deployed on 8470 and restarted; `/api/health` reports 0.15.0.

Known limit: `playlist_tracks` is keyed `(playlist_id, track_id)`, so the database cannot hold
the same song twice in one playlist. The contract allows duplicate track ids — it just never
produces them today.

## Restore file is its own button; a ? explains the whole flow (0.15.1)

The Apple restore JSON no longer rides inside the playlist ZIP. `archive()` is back to audio
plus `MANIFEST.txt`, and the per-playlist file has exactly one home: the **Apple Music
Backup** button on the playlist page, which is the `/api/playlist/{id}/apple-backup` route.
A playlist archive therefore holds two entries again, and a bare id selection never carried
one. `tests/test_browser_downloads.py` is back to asserting two entries and, newly, that
`apple-music.json` is absent; `tests/test_playlist_backup.py`'s zip case flipped from "carries
the sidecar" to "carries no sidecar".

Next to the two pills there is now a small round **?** (`.ghost.abhelp`, 34 px) that opens
`#dlg-apple-backup`: a four-step guide — get the shortcut once, download the playlist's backup
file, share that file to the shortcut, the shortcut rebuilds the playlist with its name and
order and reports what it could not find — plus one line saying one shortcut restores every
playlist. The dialog reuses the discussion drawer's box styles and is 480 px wide (366 px at
390), sitting inside the viewport at both widths. `openAppleBackupHelp()` only shows a modal
that is not already open, so a double tap cannot stack dialogs.

Measured: 1280 → dialog 480×349.8, ? button 34×34; 390 → dialog 366×370, both inside the
viewport. Full suite 310 tests OK on a staged copy, then deployed and restarted; `/api/health`
reports 0.15.1. `tests/test_apple_backup_ui.cjs` now also asserts the ? button, the round
style, the presence of the guide markup, that the guide names both buttons, that the file is
no longer inside the ZIP, and that opening twice does not double-show.

## Song cards: plus in the bottom-right corner, playlist names off the card

Two owner-named fixes on the Library cards (layout `Cards`):

* **The plus moved to the card's bottom-right corner.** `polish.css` put `save save` as the
  second grid row, directly under the cover — 86 px above the card's bottom edge at 2 per
  row. The row is now last (`art`, `title`, `artist/album`, `votes`, `save`), so the 40 px
  button lands 7 px from both the right and bottom edges. Measured inset before → after:
  2 cols 86 → 7 px, 3 cols 56 → 8, 4 cols 54 → 8, 6 cols 53 → 7; at 390 px, 7 → 7 (2 cols)
  and 7 px at 4 cols with a 28 px button. Card heights are unchanged (655 / 446 / 349 / 258
  px at 2/3/4/6 columns, 294 / 161 px at 390), because the row was only reordered.
  Same order for `trio`/`dense` tiers and for an opened compact card.
* **A playlist name is no longer printed on a card.** A SoundCloud set's name is stored as
  the track's album, so the right-hand cell repeated the set the song came from ("Shuffle")
  next to the artist. `songRow()` now blanks that cell on cards when the album string is
  also one of the track's playlists; real album names still show ("Real Album" in the
  fixture). List rows and group headers are untouched.

Verified on a staged copy with the real renderer and `polish.css`: no horizontal overflow at
1280 or 390 in any tier, the play button still sits inside the artwork, and the two
Shuffle-sourced cards have an empty album cell while album-sourced cards keep theirs.
`test_rowactions_ui.cjs` passes. `test_library_ui.cjs` fails on
`compact.setAttribute is not a function` (its stub), identically on a pre-change copy.
Then deployed (`web/index.html`, `web/polish.css`) and confirmed the served
`polish.css` carries the new rule.

## Cards: plus joins the vote row, title marquee removed

* **The sliding-title marquee is gone.** The animation translated the whole `<b>`, so a
  cut-off title slid across the card and out over the page instead of revealing a few
  hidden pixels — the ellipsis it was meant to complement was the better behaviour. The
  IIFE and its `el.animate` loop are deleted from `index.html`, the 3 s idle schedule with
  them, and `tests/test_title_marquee.cjs` (the only test of the removed feature) is
  removed. Truncated titles are plain `text-overflow:ellipsis` again, as before.
* **The plus shares the last row with votes.** `polish.css` gave `save` its own grid row,
  so the plus sat alone under the vote chips. The card template is now
  `art / title / artist+album / votes+save` (`"votes save"`), with the vote chips in the
  left column and the 40 px plus in the right one, 7 px from the right and bottom edges.
  `trio`/`dense` and the opened compact card use the same last row (votes are hidden there,
  so the plus keeps the corner). Cards are one row shorter for it: 2 per row 655 → 624 px,
  3 per row unchanged at 446, 4 at 349, 6 at 258; at 390 px, 2 per row 294 → 264 px.

Measured on a staged copy with the real renderer and stylesheet: vote row and plus centres
within 8 px of each other at 1280 and 390, plus inset 7 px both axes, no grid or document
overflow in any tier, no page errors, and the long fixture title renders with an ellipsis and
zero animations. `test_rowactions_ui.cjs` and `test_song_layouts.cjs` pass;
`test_library_ui.cjs` still fails on its own `setAttribute` stub (identical pre-change).
Deployed; the served `polish.css` carries the new template and `index.html` has no marquee.

## Floating Back / Forward, shown only when there is somewhere to go

The app had in-page back links (album/playlist/artist pages, folder backs) but no way back
from a tab switch, and the browser's own Back left the app. Navigation is now one history
entry per move: `#floatingnav` holds two 44 px circles fixed at the bottom right
(`‹` back, `›` forward), each hidden while unusable and the whole pair hidden when there is
no history either way.

* Entries are pushed from `setView`, `openAlbumAt`, `openArtistAt`, `openArtistByName`,
  `openPlaylist`, `closeDetail`, `openPersonalFolder`, `leavePersonalFolder`,
  `openAlbumForTrack` (wrapped, not rewritten) and from every `.tab[data-view]` click. A
  snapshot is `{section, scope, libView, folder, detail}`; identical snapshots are not
  pushed twice, and a new move after going back truncates the forward entries.
* `popstate` restores the snapshot, so the phone's hardware/gesture Back walks the same path
  and the buttons stay in sync. The pair calls `history.back()/forward()`, never app
  functions, so there is one source of truth.
* Placement is measured, not guessed: `placeNav()` reads the real `#player` box
  (`innerHeight - player.top + 18`) and runs on resize, on a `ResizeObserver` over the
  player, and on class changes; with no player it sits 12 px from the bottom. Measured gap
  to the player top: 8 px at 1280 (nav 760–804, player 812) and 8 px at 390 (584–628 vs
  636), and 12 px above the phone tab bar when the player is closed. The queue sheet is
  z-index 45 against the pair's 44, so an open queue covers them instead of being covered;
  `body.fullplayer` hides the pair entirely.

Verified against a staged copy served over HTTP (history needs a real origin): library tab →
album → back returns to albums, forward reopens the album, then a tab switch and back
returns to the album list with forward available. No page errors. `test_song_layouts.cjs`
and `test_rowactions_ui.cjs` pass. Deployed; the served `polish.css` carries the rules.

## Administrator "view as user" switch

An administrator can now inspect the site exactly as a member sees it without leaving the
admin account. `View as user` sits with Settings in the app menu (`#btn-viewasuser`), is only
ever rendered for a real administrator, and stays reachable while it is on — otherwise there
would be no way back.

* Turning it on drops `html.is-admin` and sets `html.view-as-user`, so every
  `[data-admin-only]` control disappears: Pause, Clear list, the download log, Refresh
  follows, Import / Move / Check sources, the drop zone, Download missing, Delete album,
  Settings, and the follow buttons on artist pages.
* Admin markup that is built in JavaScript is gated too, not just re-hidden: the queue's
  review blocks, `Retry N failed`, `Re-run`, the album page's Delete album and the feed's
  decision history now ask `adminUI()` (`isAdmin() && !viewAsUser`) instead of `isAdmin()`.
  Toggling re-renders the library and the queue so that markup actually leaves the DOM.
* Permissions are untouched: every handler keeps its own `isAdmin()` check, so the account is
  still an administrator and the switch is chrome only. It is stored in `sessionStorage`
  (`hl.viewAsUser`), so it survives a reload in the same tab and expires with the tab — a
  fresh visit starts in the administrator view. The button reads `Exit user view` and takes
  the accent border while active.

Verified in Chromium with the real page and stylesheet: nobody signed in → toggle hidden;
admin → toggle visible, Settings/Pause shown; on → `view-as-user` class, Settings/Pause
`display:none`, toggle reads `Exit user view`, `hl.viewAsUser=1`; off → back to `is-admin`;
signed in as a member → toggle hidden and admin controls hidden; reload with the session flag
set → comes back in user view. No page errors. `test_song_layouts.cjs` and
`test_rowactions_ui.cjs` pass. Deployed; the served `polish.css` carries the switch rules.

## Search tab: find a song on SoundCloud/YouTube and download it from the Queue

The Queue area now has two tabs — `Queue` and `Search` (`setQueueTab()` swaps `#qmain` for
`#qsearch`). Search asks both engines at once and lists what exists instead of one judged
winner: cover, title, uploader, length and a source chip, with a **Download** button per row.

* `GET /api/search?q=` → `search.find()` → `ytdlp.search_all()` (`search_candidates` is left
  alone: that one is for the queue's automatic picker). SoundCloud first, YouTube after,
  deduped by URL, capped at 20. Thumbnails now come out of `_search`, so rows have covers.
  Queries under two characters are a 400; 30/min per member.
* `POST /api/search` with a chosen hit → `search.queue_hit()` writes one row into a single
  `From search` playlist (provider `search`, `hl-search://from-search`) and enqueues the job
  **pinned to that upload's URL**, so the downloader never re-searches the title. A track that
  is already on disk is linked (membership only); pressing Download twice does not double-queue.
  20/min per member. Members may use it, the same scope as adding a playlist.
* The tab switches back to Queue when a download is queued, so progress is visible where it
  happens.

Measured: a live search for "fisher atmosphere" returned 20 hits (SoundCloud first, artwork
populated). Against a copy of the live DB, one hit produced playlist `From search`, one track
row with `source_url` set, and a `queued` job carrying the same URL; a second press returned
`queued: false` with the job count still 1; a track with a file on disk came back `already`.
In Chromium the tab hides `#qmain`, renders both rows with source chips and no page overflow,
and a Download click posted the hit's URL and returned to Queue.
`tests/test_search.py` (11 tests) covers provider mapping, normalisation, the cap, the pinned
job, the double-press and already-on-disk cases, plus the route (401 without a session, 200
with hits, 400 for a short query, 200 + pinned job on POST). Full Python suite 317 tests OK.
Service restarted; `/api/health` 200, `/api/search` answers 401 without a session.

## Song menu: "Play now" removed

The right-click / long-press menu opened with `Play` (the row's own play button) and then
offered `Play now`, which did exactly the same thing — a plain tap on the row already plays
it. The explicit entry is gone; the menu is now the visible controls, then `Play next`,
`Add to queue`, folders, boost and playlist actions. Verified in Chromium on a card: menu
items are Add to library, Upvote, Downvote, comments, Download, Play, Play next, Add to
queue, Boost, Add to playlist.

## Song cards: tighter text block

The card's row gap was 5 px (4 px on a phone), which read as a loose stack under the
artwork: title → artist measured 5 px, artist → the vote/save row 12 px.

`gap` is now `2px 6px` at every width. Measured after: title → artist 2 px (2 px on a phone),
artist → vote row 9 px (8 px) — the remainder is the 26 px chip box around 19 px text, not
spacing. Card heights: 2 per row 624 → 615, 3 per row 446 → 437, 4 per row 349 → 340, 6 per
row 258 → 249; at 390 px, 2 per row 264 → 258. The plus stays on the vote row (same row
confirmed), 7 px from the right/bottom edges, and no grid or document overflow at 1280 or 390.
Deployed; the served `polish.css` carries the new gap.

## Long press: the artwork no longer drags over the song menu

The menu opens from a 550 ms hold or a right click, but the cover was a plain `<img>`, so
the same hold also armed the browser's native image drag. Its ghost is painted above the
page — over `.songmenu` (z-index 1000) — which is the cover "expanding" that covered the
menu, and it vanished on release, hence "temporary".

Fixed at three levels: `artImg()` emits `draggable="false"`, `polish.css` carries
`img{-webkit-user-drag:none;user-drag:none}`, and the context-menu script cancels `dragstart`
for `.songcard,.acard,.plcard,.covercell,#playback-item,img` so markup built elsewhere cannot
bring it back.

Verified in Chromium with real artwork: the card image reports `draggable = false` and
`-webkit-user-drag: none`, a synthetic `dragstart` on it is `defaultPrevented`, an unrelated
element's `dragstart` is not, a touch hold still opens the menu (`.songmenu` 265×454 at
z-index 1000) and `elementFromPoint` in the middle of the menu returns the menu itself, with
no page errors. Deployed; the served `polish.css` carries the rule.

## Song menu: three vote rows became one Discuss

The menu copied every `.votes` control, so Upvote, Downvote and "N comments — open
discussion" were three rows for one conversation. It now lists `.splay,.lsave,.sdl` only and
adds a single **Discuss** entry that opens the same discussion popup (it clicks the row's own
comment button, falling back to `openDiscussion('track', id)`), where voting and commenting
both live.

Verified in Chromium on a card: menu is Add to library, Download, Play, Discuss, Play next,
Add to queue, Boost to top of Feed (24h), Add to playlist… — no vote rows left, and Discuss
opened `#dlg-social` with `track:2`. No page errors.

## Compact cards: collapsed is the only difference

Compact mode used to redraw the card when it opened: the plus went under the cover, Like and
Pin came off the artwork, and the grid was `art / title / artist+album / votes+save` instead of
the normal card. It now hides content with `:not(.open)` and overrides nothing while open, so
an opened compact card is the card the non-compact mode draws — same rows, same height, play,
download and the clock still on the cover, plus in the same corner.

Measured at 2 per row, 1280 px: collapsed 553 px tall showing only `.thumb` and `.st`;
opened 615 px with areas `"art art" "title title" "artist album" "votes save"`, play 42×42 at
(580,1001), download 32×32 at (542,1006), clock 36×21 at (117,1022), queue-next 30×30 at
(117,538), save 40×40 at (588,1093) — every one identical to the full-mode card measured in
the same run, and the same set of visible children.

`tests/test_song_layouts.cjs` asserted the old shape ("plus at +44 px under the cover", "like
and pin drop below the cover"); those checks now compare the opened card against full mode
instead of against the old layout. PASS. Deployed; the served `polish.css` carries the rule.

## Menus per view: the cover wall gets the full one, cards and rows only what they hide

Grid mode is a wall of `<button class="covercell">` tiles with no controls on them, so a long
press there had nothing to offer — `rowAt()` only knew `.songrow` and `native()` treated the
tile as a button. Now:

* `.covercell` is a menu target, and the native-control guard ignores a control that *is* a
  cover tile. Long press and right click both open it.
* The cover wall's menu carries the whole set — Play, Download, Add/Remove from library,
  Discuss, Play next, Add to queue, Boost to top of Feed (24h), Add to playlist… and folder
  entries — because none of it is visible on the tile.
* A card or a row already shows Play, Download, save and the comment count as buttons, so its
  menu only adds what those cannot do: Discuss, Play next, Add to queue, Boost, Add to
  playlist, folders. The old "copy every visible control" loop is gone.

Verified in Chromium: right click on a tile → 8 entries as above; a touch hold on a tile opens
the same menu without opening the album (`detail` stays null); cards and list rows both give
Discuss, Play next, Add to queue, Boost, Add to playlist. No page errors.

## Compact cards: the title sits on the cover

Collapsed compact cards were the cover plus a title line below it (553 px at 2 per row). The
title is now drawn on the artwork itself — bottom-left, over a short dark gradient, with the
cover's bottom corners — so a collapsed card is one square of artwork. Opening still restores
the normal card untouched (615 px, title back under the cover).

Measured, 2 per row at 1280: collapsed 525 px with the title's box inside the art's box and
0 px between title bottom and art bottom; opened 615 px with the title outside the art. At
390 px: collapsed 171 px (art 159), opened 276 px. No document overflow at either width.
Deployed; the served `polish.css` carries the rule.

## Card titles scroll inside the card, after 3 s

A truncated card title now scrolls within its own clipping box instead of sliding out of the
card. The title text is wrapped in `.mqt` at runtime (only for cards whose title is cut off);
the `<b>` stays the clipper — `overflow:hidden`, 247 px wide — while the span (883 px) is
translated under it, so nothing can ever leave the card. Motion: `text-overflow` switches to
`clip`, the text holds 650 ms, travels left just far enough to read the rest, holds, and comes
back, repeating while the card is on screen. Titles that fit are never wrapped and never move.

Timing: an `IntersectionObserver` starts the 3 s clock when the card scrolls into view and
stops the animation when it leaves (the clock restarts on return). A compact card that is
tapped starts at once — the tap is the request to read it — and closing it stops the motion.
`prefers-reduced-motion` keeps the ellipsis. Re-renders are picked up by a MutationObserver on
`#liblist`.

Measured at 4 per row, 1280: nothing at 0.9 s; at 3.4 s one animation on the 883 px span with
`text-overflow:clip`; at 4.2 s the transform has moved (-1.33 px and travelling) while the
`<b>` box is unchanged at 247 px. Compact: 0 animations collapsed, an animation running 220 ms
after the tap. `test_song_layouts.cjs` and `test_rowactions_ui.cjs` pass. Deployed.

## Compact cards: a collapsed neighbour no longer stretches

Grid items default to `align-items: stretch`, so opening one compact card made every collapsed
card in that row as tall as the opened one — the controls were hidden but the box stayed
extended. That is the grid, not the card: `.songgrid` now carries `align-items:start`.

Measured, 2 per row: both collapsed 525 px; after opening the second card the opened one is
615 px and the collapsed one is 525 px (before the fix it was stretched to 615 px with only
two children visible). `test_song_layouts.cjs` and `test_rowactions_ui.cjs` pass.

## Compact grid: an opened card floats over the row instead of resizing it

With the row left at the collapsed height, the opened card grew only downward: the grid row
height is unchanged (525 px), so the card beside it never shows a void — but the opened card's
extra 90 px sat in the 10 px gap and over the row below. Toggling now adds a negative bottom
margin equal to the growth (`-90px`) and the card is `position:relative;z-index:3` with a soft
shadow, so it reads as a lifted card rather than a stretched row.

Measured, 2 per row: collapsed all 525 px; opened card 615 px with `margin-bottom:-90px`, its
neighbour 525 px, the next row still at y=1060 (no reflow) — the opened card's content ends
over the top of the card below it. Closing clears the margin and returns 525 px.
`test_song_layouts.cjs` passes. Deployed.

## Pinned: a full-width, compact cover carousel

The Pinned block above the library used to be a list of small text rows inside main's
1120 px column. It now reads like the Grid layout: square covers, a kind label and the name
under each, a pin button in the corner, and the heading carries `n/18`.

* **Width**: `margin-inline: calc(50% - 50vw + 12px)` plus `max-width: calc(100vw - 24px)`
  takes the strip out of the column and across the window with a 12 px gutter (6 px on a
  phone), without a horizontal scrollbar.
* **Grid**: `grid-template-rows: repeat(2, auto)`, `grid-auto-flow: column`, tile width
  `clamp(118px, 13vw, 176px)` — two rows, columns to the right, `scroll-snap-type: x
  mandatory`. Compact by design: 166 × 206 px tiles at 1280, 101 × 141 px at 390.
* **Carousel**: everything past the visible columns scrolls sideways, so 18 pins is three
  screens of six on a phone and roughly 7 columns (14 tiles) visible at 1280.
* **Cap**: `preferences.py` refuses the 19th pin (`Up to 18 pins — unpin one first`, 400) and
  the pin buttons disable at the limit; pinning something already held stays idempotent.

Measured at 1280 with 18 pins: 9 columns × 2 rows, 7 columns visible, `scrollWidth 1582 >
clientWidth 1226`, tile 166 × 206, no page overflow. At 390: tile 101 × 141, 3 columns
visible (6 tiles), scrolls, no page overflow. `tests/test_preferences.py` 16/16 including a
new cap test; full Python suite green.

## Pinned: drag to reorder, pages of six, 18 max

Pins carry a `position` (new column, `ADDED_COLUMNS` migration) and `POST /api/listening/pin-order`
writes the order for the member's own pins (unknown pins in the payload are refused; pinned rows
missing from it are kept at the end). The strip is one page wide = six pins (3 columns × 2 rows),
so page 2 exists only with 7+ pins and page 3 only with 13+; the heading shows `page n/m`.

Dragging a tile reorders live (pointer events, 8 px threshold, 2 px accent outline), holding it
within 64 px of an edge for 420 ms pages the strip over so it can be dropped there, and the drop
posts the new order. A render mid-drag is skipped so the strip is not rebuilt under the pointer;
clicking a tile after a drag is muted.

Measured: 6 pins → 1 page, no horizontal scroll; 7 → 2 pages; 12 → 2; 13 → 3; 18 → 3
(`scrollWidth 3690` vs `clientWidth 1226`). Drag: `901,902,903,...` → `902,903,901,...` and the
posted body matches. Edge hold paged from 0 to 1207 and the chip read `page 2/2`.
`tests/test_preferences.py` 17/17 (order round-trip, forgotten pin kept, bad payloads 400).

## Genre tagging: admin-only, including in view-as-user

`POST /api/genres/generate|correct` already required an administrator (`security.admin`), so a
member could never tag. The buttons, however, were hidden with `isAdmin()`, which stays true for
an administrator using "view as user" — the panel was the one admin surface still visible there.

`genres.js` now gates on `adminUI()` (falling back to `isAdmin()` when the function is absent),
and `toggleViewAsUser()` dispatches `hl:chrome` so the panel re-reads the chrome without a reload.
Verified in Chromium: hidden while signed out, shown for an admin, hidden in view-as-user,
shown again on exit, hidden for a member.

## Swipe: Tinder-style Repository triage (rudimentary by request)

New `Swipe` entry in the app menu (`/swipe`, public shell like Party; data is authenticated
`/api/swipe`). Cards are playable Repository tracks that are **not** in the member's library and
that the member has not swiped.

* right = keep → added to the library; left = drop → remembered, never offered again;
  up = superlike → added to the library **and** liked. Tap plays the track.
* Decisions live in `swipe_swipes(member_id,track_id,decision,at)`; the feed excludes anything
  owned or swiped, per member. Stats line reads `kept · liked · dropped · left`.
* API: `GET /api/swipe/next?limit=` → `{tracks, stats}`, `POST /api/swipe/decide {id,decision}`,
  member-authenticated, CSRF on writes, rate limited.

Verified live: `/api/health` 200, `/swipe` 200, `/api/swipe/next` 401 without a session.
`tests/test_swipe.py` 5/5 (feed filtering, keep/like/drop effects, stats, per-member isolation).

## Player: volume is a small vertical fader

The horizontal volume slider took `flex: 0 0 120px` of the player row beside a mute button and
the Lyrics button. It is now a 20 × 58 px vertical fader pinned to the player's top-right corner
(`position:absolute; right:8px; top:6px`, the input rotated -90deg), with the mute and Lyrics
buttons keeping their place and a 26 px gap so nothing collides. Hidden under 760 px as before
(the fullscreen player has its own fader).

Measured at 1280: fader box 20 × 58 at (1151, 829) in a 74 px bar, no overlap with the mute
button or the duration, seek grows from 218 to 298 px; at 900 px: fader (859, 729), seek 111 px,
no row or page overflow.

## Player artwork opens the album, like a Grid cover

The mini-player's thumbnail only opened an album when the track carried an `album_id` and it
never marked the song. It now calls `openAlbumForTrack(playingId)`, the same opener the Grid
layout's cover tiles use: it finds the album (including a loose track's pseudo-album), shows the
detail page, scrolls to the top and highlights the song's row for five seconds; a track with no
album page says so in a toast.

Verified in Chromium with a stub: clicking the artwork routed the playing id through the opener
for a track with an album and for one without, no page errors.

## Subsonic surface expansion — planned, nothing implemented

Audit result: the Subsonic REST server (`listening.rest()`) answers **13** of the **82** documented
Subsonic/OpenSubsonic endpoints, scoped to the device member's saved catalogue; everything else returns
"Unsupported endpoint" (error 70). Decided scope and order (owner, this session):

- **Phase 0, one release — endpoints that map onto existing data:** richer `song` payload (`contentType`,
  `suffix`, `size`, `path`, `created`, `coverArt`, `starred`, `genre`, `bpm`), `getCoverArt`, `getIndexes`,
  `getMusicDirectory`, `star`/`unstar`/`getStarred`/`getStarred2`, `setRating`, `scrobble`/`reportPlayback`/
  `getNowPlaying`, `savePlayQueue*`/`getPlayQueue*`, every `getAlbumList2` type plus legacy `getAlbumList`,
  `search`/`search2`, `getRandomSongs`, `getGenres`/`getSongsByGenre`, `getLyrics*`, info blocks,
  `getOpenSubsonicExtensions`, `getScanStatus`/`startScan`, self-only `getUsers`/`getUser`/`getAvatar`.
  Two new tables: `listening_ratings`, `listening_plays`. Everything still unsupported answers error 30.
- **Phase 1 — bookmarks** (`listening_bookmarks`, "Bookmark here", resume prompt, bookmark endpoints).
- **Phase 2 — public shares**: track/album/playlist, **24 h expiry**, members and admins only (guests refused),
  32-byte hashed tokens, `/s/<token>` no-login player with Range streaming, revoke, per-token rate limit.
- **Phase 3 — jukebox in Party mode**: the **party host's browser** owns the deck (claim + 5 s heartbeat +
  release on silence); queue actions `get/status/set/start/stop/skip/add/clear/remove/shuffle/setGain`
  behind `POST /api/party/<id>/jukebox`; Subsonic `jukeboxControl` maps onto them.
- **Phase 4 — audio analysis**: BPM already exists (`library/analysis.py` grid fit, `library/tempo.py`
  worker, `bpm/bpm_conf/bpm_source` columns). Add `track_features` vectors (numpy permitted), cosine
  neighbours, `getSonicSimilarTracks`, real `getSimilarSongs*`, `findSonicPath` (greedy walk, hop cap),
  `song.bpm`, plus "Sounds like this" in the UI.
- **Phase 5 — internet radio**: one shared station list, "Radio" in the More menu, SSRF-guarded URLs,
  full Subsonic radio CRUD and a Radio folder.
- **Phase 6 — podcasts**: shared feeds, explicit per-episode downloads under a 5 GB cap, "Podcasts" in More,
  full Subsonic podcast endpoints.
- **Phase 7 — chat**: party-room timeline only (`party_messages`), distinct from Discuss (per-item comments
  with votes); `addChatMessage`/`getChatMessages` bound to the member's current room.
- **Dropped / out of scope:** music videos (owner decision), transcoding and HLS, Subsonic user administration,
  jukebox on the VPS sound card (no audio device).

No code has been written for any phase. The only Subsonic change in this session was the earlier read-only
13-endpoint server that was already live. Plan: `~/.hermes/plans/2026-11-09_0610-subsonic-jukebox-videos-shares-bookmarks.md`.

## Subsonic Phase 0 — full read surface, stars, ratings, plays, queue, artwork — deployed

The device-facing Subsonic server went from **13 to ~45 answered endpoints** in one release. New state:
`listening_ratings(member_id, track_id, rating)` and `listening_plays(member_id, track_id, plays,
last_played)`. No audio, no member data and no acquisition path was touched.

- **Payload**: `song` now carries `suffix`, `contentType`, `size`, `created`, `coverArt`, `albumArtist`,
  `bpm`, `genre`, `starred`, `userRating`; the filesystem `path` is deliberately **not** exposed (the
  no-path-leak assertion in `test_listening` still passes).
- **Artwork**: `getCoverArt` redirects (302) to the member's known artwork URL for a known album/track,
  answers a 1×1 PNG when the item exists but has no art, and errors 70 for an id outside the catalogue.
- **Structure**: `getIndexes`, `getMusicDirectory` (root → albums, artist → albums, album → songs).
- **Stars**: `star`/`unstar` accept repeated `id`/`albumId` parameters (the repeated-parameter guard now
  only rejects duplicates outside `{id, songId, albumId, artistId}`), `getStarred`/`getStarred2`.
- **Ratings**: `setRating` (0 clears) and `userRating` in song payloads.
- **Plays**: `scrobble`/`reportPlayback` accumulate `plays` and `last_played`; `getTopSongs` and the
  `frequent` album list read them.
- **Queue**: `savePlayQueue`/`savePlayQueueByIndex`/`getPlayQueue`/`getPlayQueueByIndex` over the existing
  per-member "where you left off" state, including repeated `id`s and `position` in milliseconds.
- **Lists/search**: all `getAlbumList2` types (`newest`, `recent`, `random`, `frequent`, `highest`,
  `byYear`, `byGenre`, both alphabetical), legacy `getAlbumList`, `search`, `search2`, `getRandomSongs`.
- **Genres/lyrics/plumbing**: `getGenres`, `getSongsByGenre`, `getLyrics`, `getLyricsBySongId`, four info
  blocks, `getTopSongs`, `getSimilarSongs*` (same artist for now), `getOpenSubsonicExtensions`,
  `getScanStatus`/`startScan`, self-only `getUsers`/`getUser`, `getAvatar`.
- **Honest refusals**: podcasts, radio, shares, chat, bookmarks, jukebox, transcoding, sonic analysis,
  videos and user administration answer Subsonic **error 30** with the endpoint name — never error 70 or a
  silent empty success.

Verification: `tests/test_subsonic_extra.py` (new, 15 assertions over the real HTTP fixture: payload fields,
no path leak, cover-art 302/PNG/70, indexes/directories, star+rating round trip, scrobble→top songs,
repeated-id play-queue round trip with position, legacy search, album-list types, plumbing, error 30), plus
the existing `test_listening` (6 tests) and the full Python suite. Deployed web-free: service restarted,
`/api/health` 200.

Still unsupported by design: transcoding/HLS, user administration, jukebox on the VPS sound card. Shares,
bookmarks, radio, podcasts, jukebox-in-party, sonic analysis and party chat are Phases 1–7 of the plan.

## Compact is a Cards-only switch again

The Compact toggle had been removed from every layout, but it was only ever meant to leave
Grid and List. It is back beside the layout picker and shows in Cards only (`#compactbtn`),
remembered in `localStorage["hl.cardsCompact"]`, and `<html data-cardstyle>` is `compact` only
while Cards is the current layout. Tap a card to fold it open; a control inside the card still
belongs to the control, and a second tap closes it. A stored `compact` reaches the first paint
because `paintLibraryToolbar()` applies the attribute.

Collapsed is the artwork and its title, nothing else — the tap does not play. Opened is the
normal card untouched; on an album card the first tap opens the card, the second opens the
album.

One real bug surfaced while measuring: at 4+ per row (`dense`) the card's two equal columns
from the density tier survived the collapse, so the "square" art was half the card (120 px in
a 261 px card at 1280). The collapsed rule now forces one column.

Measured in Chromium (cards, 6 songs):

| width | collapsed card | art | opened | delta |
|---|---|---|---|---|
| 1280, 4/row | 261 × 261 | 247 | 341 | -81 px margin, floats |
| 700, 2/row | 332 × 332 | 320 | 418 | -86 px margin |
| 390, 2/row | 177 × 177 | 165 | 258 | -81 px margin |

Collapsed cards carry no play, download, save, pref, vote or artist rows (visibility probe);
opened matches the full-mode card's height, grid areas and control boxes. Album cards measure
261 collapsed / 341 opened and stay in the library. `test_song_layouts.cjs`: every compact
check passes; the only two reds (`List / Cards / Grid` order, cover-wall density) fail
identically on the pre-change tree. No page errors. Deployed, health 200.

## Lock-screen skip buttons — registered, then re-registered

The iPhone lock screen showed +10/-10 instead of ⏮/⏭ and the desktop media hub lost the
skip pair. Cause: `bindMediaSession()` ran exactly once at page load, before the `<audio>`
element had ever been active. WebKit discards action handlers registered on an inactive
media session, so the platform fell back to its default seek pair — and the same happened
after every pause, backgrounding or PWA resume. That is why the buttons came back after a
fix and vanished again later.

Now `bindMediaSession()` is idempotent and re-claimed on every activation point: after
`metadata` is published, on every `play`/`pause` state publish, on `visibilitychange`,
`pageshow` and `loadedmetadata`. Seek actions are still cleared and never registered — that
alone is what swaps ⏮/⏭ for +10/-10.

Guard: `tools/media-session-test.cjs` (Chromium, playwright) extracts the media-session
block and asserts prev/next are claimed at boot, on metadata, on play/pause, that the
resume hooks exist in the file, that no seek action is ever registered with a handler, and
that the handlers call the transport. The pre-change tree fails it on both re-claim checks.

```
NODE_PATH=/home/bazzite/apps/ada-bot/node_modules \
PLAYWRIGHT_BROWSERS_PATH=/home/bazzite/.cache/ms-playwright \
node tools/media-session-test.cjs web/index.html
```

Stray `NUL` byte removed from a comment at line 4273 — it made every `grep` treat
`web/index.html` as binary.

## 30-second songs — SoundCloud previews accepted as finished downloads

Every short track measured 29.8 s on disk with a 3–5 minute duration in the catalog. Cause,
measured not guessed: those tracks were matched to a SoundCloud upload, and SoundCloud serves
a ~30 s preview of a restricted label release. `yt-dlp --simulate --print "%(duration)s"` on
two of the source URLs reports exactly `30.0`, so the download was complete, not truncated —
30 seconds is all the source would give.

`verify_audio()` only had a 1-second floor, so a 30 s sample passed, was recorded as
completed with the *catalogue* duration, and played as a 30-second song with a 3:13 label.
37 files were in that state (all `provider=apple`, matched to SoundCloud), newest 11-07 22:26.

Fix: `verify_audio(..., expected_seconds=track["duration"])` rejects a file shorter than
`min(expected − 20, expected × 0.6)` as "a preview, not the track"; the queue passes the
catalogued length at both call sites (new download and already-on-disk check) and, when the
reason says preview, calls `_next_source` instead of retrying the same sample URL. One
anomaly is unrelated: track 1289 is a 177 s edit of a 293 s listing and passes the 60% floor.

Repair 2026-11-10 11:48: 35 of the 38 were repaired — the short file was deleted, `tracks.file`
cleared and one fresh queued job inserted per track (same shape as the UI retry button),
DB backed up to `backups/2026-11-10-preview-requeue/`. 346, 1044 and 1289 were left alone:
186 s of 236, 172 s of 180 and 177 s of 293 are whole files of a shorter edit, not samples.
Not every bad file was 30 s — 1016 was 90.9 s and 1024 94.8 s, i.e. interrupted writes that
also exited 0, which the same 60%-of-catalogue floor catches.

## Local ranking benchmark — the scorer is already at the data's ceiling

`tools/ranking-bench.py` re-scores the candidate lists recorded in `match_decisions` and
reports where the pick that was actually made lands. Baseline on 698 decisions:
**top-1 92.3%, MRR 0.947, 641/653 of the 0.90+ scores correct (98.2%), 83 us/score**;
40 rows where a wrong candidate scored 0.90+ above the correct one.

Three candidate upgrades were measured and all lost:

| change | top-1 | auto-accept precision |
|---|---|---|
| baseline (difflib, exact tokens) | 92.3% | 98.2% |
| rapidfuzz WRatio similarity | 92.3% | 95.8% (worse) |
| rapidfuzz + fuzzy token matching | 92.3% | 95.8% (worse) |
| stronger "requested version missing" penalty (0.9 -> 0.5) | 90.1% | 98.1% |
| duration mismatch penalty, any tolerance/penalty pair | 71-77% | 96.7-96.9% |

Why: WRatio and fuzzy tokens inflate every candidates score, which narrows the gap at the

## Local ranking benchmark — the scorer is already at the data's ceiling

`tools/ranking-bench.py` re-scores the candidate lists recorded in `match_decisions` and
reports where the pick that was actually made lands. Baseline on 698 decisions:
**top-1 92.3%, MRR 0.947, 641/653 of the 0.90+ scores correct (98.2%), 83 us/score**;
40 rows where a wrong candidate scored 0.90+ above the correct one.

Three candidate upgrades were measured and all lost:

| change | top-1 | auto-accept precision |
|---|---|---|
| baseline (difflib, exact tokens) | 92.3% | 98.2% |
| rapidfuzz WRatio similarity | 92.3% | 95.8% (worse) |
| rapidfuzz + fuzzy token matching | 92.3% | 95.8% (worse) |
| stronger "requested version missing" penalty (0.9 -> 0.5) | 90.1% | 98.1% |
| duration mismatch penalty, any tolerance/penalty pair | 71-77% | 96.7-96.9% |

Why: WRatio and fuzzy tokens inflate every candidate's score, which narrows the gap at the
auto-accept threshold; the recorded picks include human overrides and fallbacks, so a heavy
version penalty punishes choices that were correct at the time; and candidate durations
legitimately differ from the catalogue (remix lengths, uploader intros), so duration is a
verification signal, not a ranking one.

Conclusion: leave `hit_score` alone. A real accuracy gain needs a different signal — audio
fingerprints (Chromaprint/AcoustID) to verify the recording, or a ranker fitted on cleaned
labels — not another coefficient.

## Player surfaces: library add/remove, and the record drives speed again

Cards had a `+`/`−` library control; the player surfaces did not. Both now render the same
control (`listeningSaveControl('track', playingId)`), so `paintSaveControl()` repaints the
card, the fullscreen foot and the compact bar from one handler — they cannot disagree.

Measured with the real markup and stylesheet at 430 px and 1280 px, stub button injected:
phone bar height **112 px before and after**, save slot 30x30 at (376, 777) with **no overlap**
with the heart (375,743,34), the queue (326,774,34) or expand (372,809,34). Fullscreen foot
height **55 px before and after** with the 40 px control inside, matching its 44/55 px siblings.
Desktop compact bar keeps its exact children (`display:none` for the new slot).

The record: a drag used to pause the audio, seek, and play a stand-in scratch (decoded buffer,
or a synthesized noise fallback). He reported "a lot of fake vfx" and that a slow spin used to
slow playback. The drag now sets `playerAudio.playbackRate` to the finger's angular speed
(clamped 0.0625-4x), keeps playing, and restores rate 1 on release; neither the noise nor the
scratch voice is started from the drag path any more.

## Offline audio: a played song is kept, the worker slices it (2026-11-10)

Playing a song again did two network round trips that did not need to happen: the file is
served `Cache-Control: no-store` with Range support, and the worker deliberately kept no
audio at all. Ahead-of-play prefetch (`aheadAudio`, four tracks) already made the *next*
song instant; nothing survived the song ending.

What changed: five seconds after a track actually starts (`playing`, so the copy never
competes with the first seconds of the stream) the whole file is fetched once and stored in
`hardlylistening-audio-v1`. The index (`hl.offlineIndex`) records `{bytes, at}` per track id,
and `finally`-style eviction drops the oldest copy while the total is over the budget — the
track that is playing is never dropped. The budget is per device (`hl.offlineBudget`, default
2 GB, off/1/2/5/10 GB in the dialog), `navigator.storage.persist()` is requested on first use,
and *Remove copies* empties both the cache and the index.

The worker is the other half. `<audio>` only ever asks with a `Range` header, and a stored
copy is one `200`, so `sw.js` answers with a `206` slice: `Content-Range`, `Content-Length`
and the correct body, including open-ended (`bytes=3990-`) and suffix (`bytes=-5`) forms, and
`416` past the end. Anything not kept is passed straight to the server, so auth, a missing
file and every other failure stay the server's answer. The static cache moved to `v3`; the
audio cache is never swept by `activate`.

Verified: `tests/test_offline_cache.cjs` — 24 checks over a real worker registration in
headless Chromium, a 4 KB fixture and a local server (kept whole, index size, all four range
shapes, passthrough when not kept, oldest-first eviction against a 6000-byte budget, off
removes and stops storing, a stale index claim dropped by reconcile, the dialog's figures, and
the automatic path once a song plays). `snap.cjs` PASS — the dialog is hidden and the user menu
gained one item, so no rendered state moved.

Known and stated rather than hidden: the browser may evict the whole cache at any time
(`persist()` only reduces that; iOS has no `persist()` and caps storage), a re-imported file
keeps its old copy until it is played again, copies are per browser profile rather than per
member (a kept file plays without a server round trip), and the app shell itself still needs a
connection (HL-309).


## The cards were merged, so this is the pass that made them real — 2026-11-12

Eight cards had their logic, their tests and their docs merged while nothing called any of them:
they all touched the same four files, and eight racing edits would have collided. Shipping a module
nobody loads is a feature that silently is not there, so the merge and this pass are separate events
and both are on the record.

**The host window now does what its cards say.** A first run asks four questions and hands over to
the shell; `wizard::server_env()` is read inside `lifecycle::command()`, so a start straight after
the wizard needs no window restart. The buttons call one command, `lifecycle_step`, and
`server_start`, `server_stop`, `Shell`, `stop_owned` and `terminate` are gone with it — one lifecycle
with one child slot, because two spawn paths in one window is the duplication this project keeps
refusing. `server_status` reads ownership from that same slot, so the status pill and the state
machine cannot disagree, and Quit calls the same `on_close` the window close does: a Quit that killed
a handed-off server would contradict the rule the card exists for. The two choices render as two
controls, each handler moving only its own axis. The settings panel mounts itself and writes
exposure and autostart; the web UI and OpenSubsonic are reported as having no server-side switch,
because they have none — refused rather than stored as no-ops. The address card runs
`desktop/host/addresses.py` as a bundled resource and paints its JSON, which also means the window
still has no second precedence of its own.

**The player connects, and the library it draws is the app's.** `connect.js` now hands the session
to `PlayerAccounts`, so the address and username are a plain record and the password goes to the
system keychain — or stays in memory on a platform with no keychain, which the module says out
loud. `api-bridge.js` answers sixteen `/api/*` shapes from `/rest/*`, and `library-boot.js` grafts
the app's own page into the same realm, so the library, its grouping and its look are the app's
rather than a copy that will drift. One test reads every `/api/…` literal out of `web/` and fails if
any reaches the generic 404; it caught four routes the deployed page asks for and the published
tree did not have — the equaliser, the playback session, a track's cues and listening statistics.
Those are now refused by name with the reason, because a 200 carrying empty data would have been
the worst possible answer for state the server does not keep.

**Two assertions stop the next version of this happening.** Every `.js` under `desktop/ui` must be
loaded by `index.html`, and every element `app.js` reaches for must exist in the markup. A module
written and never wired was exactly the state these eight cards were in.

The live window was checked in a real tab as markup with no Tauri bridge: it paints, the wizard gate
falls through, the two-choice card and the self-mounting settings panel are there, the lifecycle text
reads "No server was started by this window", and the console is empty. What that cannot prove is the
bridge itself, and the screenshot tool refused for want of a visible window, so no picture is claimed.

Stated rather than hidden: the two crates are still uncompiled — no `webkit2gtk-4.1`, `gtk+-3.0`,
`libsoup-3.0` or `cargo-tauri` on this machine — so the Rust proof is `rustfmt --check` and every
decision that can be tested lives in Python or node. 380 Python tests and eleven node files are
green, and the headless promise still holds. Three things are deliberately still open: PLAYER-006 is
being written, PLAYER-004's module has no call site in `web/index.html` until the choice between
moving it to `web/js/playback.js` and leaving that page alone is made, and the two no-op HOST-005
settings would need a server-side switch to become real — that is a server card, not a window one.

## PLAYER-004: one owner for the media session — 2026-11-13

The playback module had been written and tested for two days with no call site, because loading it
into `web/index.html` meant deleting that page's own media-session implementation first — two owners
fight over the same action handlers, and two `ended` listeners advance two songs. The choice had been
recorded as (A) do the refactor or (B) leave the page alone. It is A, and the reason is the same one
that keeps the library the app's: the player runs this page, so anything left here is code the player
carries and the browser carries twice.

The module moved from `player/shell/` to `web/js/playback.js`. The shell directory is the assembly's
allow-list, which is why it lived there, but a copy in the shell would now be a second owner — the
player's assembly serves the app's page, so the app's tree is where the file belongs.
`player/assemble.mjs` lists `js/playback.js` in `WEB_ALLOW`, and `Ships` asserts the shell copy is
gone rather than merely unused.

What the page lost: `mediaArtwork`, `publishMediaSession`, `publishPosition`, `publishPlaybackState`,
`bindMediaSession`, `republishMediaSession`, their `pageshow` / `visibilitychange` / `play` /
`loadedmetadata` / `playing` / `emptied` / `canplay` / `error` listeners, `lastPositionPush` and
`pushPositionState`, `restoreQueueOrder` / `shuffleUpNext` / `shuffleBase`, and the inline `ended`
handler with the `saveNowState(true)` copy that ran beside it. The 2026-11-12 skip-boundary
experiment went with them: its only consumer was the media session, and its `"none"` hand-over is
now the module's milestone republish. `warmArtwork`, `realAlbum`, `albumFor` and `playerSub` stay —
they serve the prefetch and the player's own second line.

What the page gained: `playFromLibrary` tags the new row with `playback.publish()` and re-publishes on
a rejected `play()`; the two toggles call the module and keep their toasts; `attach` receives the
persisted `hl.shuffle` / `hl.repeat`, a `coverUrl` that takes a row, and a no-op `report`, so
`reportListen` remains the only listen report and no play is counted twice. The `ended` listeners are
ordered deliberately — AutoDJ's top-up first, the module's advance second, the save and the mode
repaint last, so they see the row that is now current.

Two gaps were found while wiring, and both were real. Persisting shuffle means a reload opens with
the button pressed, so `attach({shuffle, repeat})` adopts the stored modes **as state** — calling
`setShuffle(true)` at boot would reorder a queue the listener never touched. And `setShuffle(true)` is
a no-op when shuffle is already on, so a freshly built album queue arrived unshuffled; `shuffleQueue(anchor)`
shuffles it from the song the listener chose, and `appQueue().setOrder` redraws the queue panel the
way the deleted `shuffleUpNext` used to. A third, smaller one: `appQueue().rowOf` now asks
`currentRows` last, so a search result or a release-page row — in neither the queue nor `libAll` —
still gets a tag instead of the previous song's.

**An unrelated fix was required before any of it could run.** `web/index.html` carried two top-level
`fmtBytes` declarations: the `const` from 0.11.0 and a `function` added with HL-306. Two declarations
of one name in one classic script is a parse error, so the entire first inline block had stopped
evaluating — `$`, the library, the player and every wiring step were undefined while the page still
painted. It shipped that way in `4d6a883` and survived a day of work on top of it. `tests/test_index_scripts.py`
now extracts every inline block and runs `node --check` over each one, and it was proved to fail on
the broken copy before the fix was committed.

Verified in the in-app browser against a static copy of `web/`, on both trees: all four inline blocks
parse; `playback` attaches with `publish` and `shuffleQueue`; playing row 3 tags the session as
`Song 3` / `Artist 3` / `Album 3` with `art/3-t500x500.jpg` at `image/jpeg`; a search-result row tags
as `Search Hit`; a boot with `hl.shuffle=1`/`hl.repeat=one` comes back with both buttons pressed and
the queue untouched; `ended` advances exactly one, the last song with repeat off stops, repeat all
wraps to the first; shuffle permutes then restores the original order. `tests/visual/snap.cjs` is
unchanged pixel for pixel in both trees, which is the point — a refactor of who publishes must not
move anything the listener sees. Python: `test_player_playback`, `test_player_shell`,
`test_player_store_safe`, `test_index_scripts` and `test_headless` green; node:
`test_player_playback.cjs` green. One gate the move tripped and then passed: `web/` is served as
written, so `test_host_dependency.py` scans it for strings that assume a desktop, and the module's
comment pointed at the wrapper's directory — reworded to say the audio session belongs to the
wrapper without naming it.

Stated rather than hidden: this machine still cannot compile the Tauri crates (no `webkit2gtk-4.1`,
`gtk+-3.0`, `libsoup-3.0`, no `cargo-tauri`), so the media session was proved in Chromium on the
assembled web UI, not on a phone's lock screen, a car head unit or the Tauri window. What Chromium
can show — the tag, its artwork and type, the position state and the advance — was checked in the
browser; the action handlers are asserted in `test_player_playback.cjs` against a fake session, since
Chromium does not expose which handlers were claimed; and what only hardware can show is unverified.

## HOST-008: one resolver, because three copies were being held equal by a test — 2026-11-13

The acceptance for this card was honest and slightly absurd: `tests/test_host_dependency.py` did not
require that the shell resolve its state directory once, it required that three hand-written copies
stay byte-equal — `resolve.py` for `host.json`, `setup.py` for `setup.json`, `mode.py` for
`mode.json`. A test keeping copies equal is a maintenance tax on a rule that has exactly one right
answer, so the copies went.

`resolve.state_dir()` is now that answer. It decodes `HARDLYLISTENING_DESKTOP_CONFIG` and
`XDG_STATE_HOME` once and returns the directory; `config_path()` is it plus `host.json`,
`setup.setup_path()` is it plus `setup.json`, `mode.mode_path()` is it plus `mode.json`. The point is
not tidiness: the override that moves the config file now moves the wizard's state and the mode
choice with it, so a window cannot read its address from one directory and its first-run answers from
another. A caller that already resolved a config path (`resolve(..., path=…)`) hands it in, and the
wizard follows it — that is what the tests do and what a future command can do.

The install defaults moved with it. `MUSIC_SUFFIX`, `XDG_DATA_SUFFIX`, `DATA_LEAF`,
`default_music_dir`, `default_data_dir` and `install_defaults` live in `resolve.py` now, beside the
address rules, because "where this install keeps its things" is one kind of decision; `setup.py`
aliases them (`MUSIC_SUFFIX = resolve.MUSIC_SUFFIX`) so `wizard.rs`'s mirror test still compares a
single name. Each module borrows the resolver through the sibling shim `lifecycle.py` already used:
the host modules are loaded by path in tests as well as imported as a package, so a bare
`import resolve` would fail in one of the two.

The test got stronger. Instead of comparing copies, it asserts that the expressions decoding
`XDG_STATE_HOME`, `XDG_DATA_HOME` and `Path.home() / '.local' / 'state'` exist in **exactly one**
module, that `setup_path()`/`mode_path()` call the resolver and carry none of its rules, and that all
three files land in one directory across three environments and a caller-supplied config path. A
control put `env.get('XDG_STATE_HOME')` back into `setup.py`; the test failed, and the control was
removed. That matters more than the green run: the assertion is the thing that would catch the drift
coming back.

What could not be collapsed, stated plainly: the Rust cannot import Python. `wizard.rs` and
`host.rs` still decode the same two variables themselves, so the mirror is pinned rather than
shared — `host.rs`'s `CONFIG_DIR` and `CONFIG_FILE` against `resolve.CONFIG_NAME`/`CONFIG_FILE` in a
new test, `wizard.rs`'s constants against the aliased Python ones, and the override names checked in
place. A future edit that moves the directory can therefore fail in three files instead of silently
succeeding in one. `settings.py` keeps `XDG_CONFIG_HOME` for the autostart entry: different
variable, different place, not a fourth opinion about state.

223 host tests green in the working tree, 224 in the published tree, `rustfmt --check` unchanged
because no Rust was touched. Nothing here is deployed — the desktop is a shippable, not a service —
so "live" for this card means the source tree that builds it.

## PLAYER-008/009: the car's artwork, and a drive that keeps playing — 2026-11-13

Two reports, one root each. The artwork was never missing from the page; it was missing from the
**car**. iOS fetches media-session artwork itself, outside the page's own session, so a member-only
cover route answered that fetch with a login page and the head unit drew an empty square — and
because the app's music is a personal library behind a session, there was no public URL to hand it
instead. The fix is a capability URL: `/api/art/<md5 of the stored 640 px cover>`, anonymous for
the same reason `/s/<token>` is (the digest *is* the secret), `public, max-age=31536000, immutable`,
and outside the `web_ui` switch because a headless server still feeds a car. `/api/track/<id>/artwork`
now answers `X-Art-Hash`, so a row whose picture had never been extracted — the common case for a
SoundCloud import that has only an embedded frame — learns its digest on the next prefetch instead
of waiting for a `/api/state` poll that never carries one. The page prefers `row.art_hash` and falls
back to the provider CDN when it has none, and a failed fetch still draws nothing, which is the
honest state: a stale picture would claim the wrong song.

The second report — playback stops after a song or two, then the head unit falls back to its source
list — was the page tearing the audio element down on **every** track change. That reset exists
because iOS otherwise keeps the item it latched and drops the tag handed to it, which is why a
skipped song used to freeze on the previous cover. But it ran on a song that had simply **ended**
too, and each teardown destroys the now-playing item; a head unit that lost its item stops, and the
next `play()` can be refused while the page is in the background. So the reason now travels:
`stepTrack(delta, reason)` → `playFromLibrary(id, {reason})`, and only `manual` releases the element.
`playback.release()` is the module's own statement that the item is gone (`playbackState = "none"`,
then pause) rather than paused, so the skip keeps the fix it was added for and a finished song keeps
its session.

The rest of the module treats the platform as an unreliable narrator. A pause it did not ask for,
or a `play()` it was refused, is an **interruption**, not a decision: `wanted` stays `"playing"`, one
immediate second ask follows (never a timer — a suspended page runs none), and the next
`visibilitychange`/`pageshow` resumes it. A pause the listener asked for is never undone because
`wanted` says so first. Mid-track a stream can also die; one reload is requested per track, through
the page's own `reloadSource`, and only for `MEDIA_ERR_NETWORK`/`DECODE` — a source the platform
cannot use fails the same way twice and a reload there would be a loop. The module still never
assigns a `src` itself; `tests/test_player_playback.py` pins that, and it is why the hook exists.

Last, the log. Without evidence the next report is "it still stops", and neither the head unit's
behaviour nor the page's own state can be checked afterwards. `POST /api/playback/events` appends
one line per interesting event (`refused`, `interrupted`, `recovered`, `error`) to
`state/playback-events.log`: member-scoped, 4 KiB request cap, a fixed field list with free text
clipped to 64 characters, and one rotation at 256 KiB. It records a category for the source
(`blob`/`stream`), never a URL and never a cookie.

Two pieces of pre-existing rot were fixed because they stood in the way of the proof. The
fullscreen-player harness had been dead since the bar lost `#plfull`: it clicked a button that no
longer exists, never loaded `web/js/playback.js`, and had none of the page containers the script
binds to at load. It is green again and now asserts the rule itself — a finished song calls no
`load()`, a manual skip calls exactly one. And the library coverage test failed the moment the two
new routes existed; they are named in the bridge's refusal table with their reasons, which is where
every other host-only route lives, so nothing under `/api/` reaches the generic 404.

Node suite in the working tree: the four touched harnesses green, the visual baselines unchanged,
34 other `.cjs` files still failing for their own pre-existing reasons (stale harnesses, missing
modules) and untouched here.

What a test cannot settle, stated plainly: whether a real head unit redraws the artwork or keeps
playing. There is no iPhone and no BMW in this tree. What is pinned is what the page publishes and
what the server answers; `docs/player/playback.md` now says so in those words.
