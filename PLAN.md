# Plan — HardlyListening

## Current state — 2026-11-13

Status lives in `KANBAN.md` and is counted from that file rather than restated here, because a
restated count has been wrong three times; `BACKLOG.md` carries open work only. The platform-separation set is complete: REPO-014, LIB-001 and SOCIAL-001 all
shipped, and ARCH-009 shipped with `core/` aliased to the old top-level names. A dependency
audit on 2026-11-13 closed two "blocked" cards that had already shipped (HL-206 party chat, HL-303
Auto DJ from BPM) and unblocked a third (HL-204 jukebox), because the lease it was waiting on —
`party.control()`'s aux host — is in production; HL-204 then shipped the same day. The P0
packaging cards (SERVER-PKG) are built apart from a host: runtime, config, launcher, backup, install
script, unit, container, compose, migration test and identity storage (001–004, 006–011). What
is missing is a real host to install on (005) and the key material FED-001 owns. The rest of that set — JS, API, SDK, HOST, PLAYER, SERVER, FED — sits
in `BACKLOG.md` until the gate is passed. Ownership of every module, table and route is
`docs/server-domains.md`; the headless/VPS/Docker layout is `docs/server-packaging.md`.
Every Python change deploys itself: a `path` unit runs `tools/autodeploy.sh`, which restarts
the service, checks `/api/health` and rolls back on a syntax error. A state written here is
therefore what is running, and "implemented, not deployed" is not a state this app can be in.

- **Subsonic / OpenSubsonic**: read surface, browse/search, stars, ratings, plays, play queue,
  artwork, bookmarks, shares (public `/s/<token>` page with Range streaming), internet radio
  (member-scoped) and podcasts (member-scoped channels, episode storage under a 5 GB budget with
  opt-in per-channel retention — **HL-209**, **HL-234**; `docs/podcast-retention.md`). The transcoding
  extension is served as direct play with Range seeking, refusing a re-encode it cannot do; MD5 token
  auth and the `openSubsonic` capability flag are on.
  Party chat ships (HL-206) and the two Subsonic chat endpoints map onto it (HL-210); the jukebox
  steers the room's one leased deck and `jukeboxControl` maps 1:1 (HL-204). Sonic analysis ships as
  fingerprints and similarity (**HL-205**): tempo is computed and Auto DJ uses it, and
  `getSimilarSongs*`/`getSonicSimilarTracks`/`findSonicPath` answer from cosine neighbours of a
  numpy fingerprint (`docs/sonic.md`). The in-app "Sounds like this" / "Sonic path" controls are
  not built.
- **Library UI**: one card system for songs and albums; list, grid and card views share
  renderers; 44 px list rows (14 per screen) with artwork left, title over artist and a "…"
  menu; marquee titles run once after 2 s visible then pause 1 s; per-section sort with a
  reverse toggle behind a funnel button on the layout row.
- **Playback**: fullscreen queue slides in-page (no overlay) and is clipped under the
  transport; safe-area insets are honoured with no band and no jump; the compact bar aligns
  to the library's content edges.
- **State**: reload restores view, scope, library view, detail page and scroll position
  (`sessionStorage`, with `/api/listening/ui` as the new-device fallback).
- **Accounts**: the permanent login link is still the account, with invite-only signup
  links, email/password on the same `members.id` and admin signup links shipped (P0). Google
  and Apple sign-in are P1 and not rendered (see *Accounts* below).
- **Ops**: `tools/autodeploy.sh` plus a user `path` unit restarts on Python changes, health
  checks, and rolls back from `/home/bazzite/hl-deploy-backups`.

## Accounts: signup links, passwords, Google and Apple — P0 shipped, P1 open

Add ordinary accounts **without replacing** the permanent login link. `members.id` stays
the canonical identity, `__Host-hl_session` stays the only session cookie, and profiles,
roles, ownership, quota, playlists, saves, Party, Subsonic devices and admin
revoke/regenerate keep working untouched. Every new method terminates in the existing
member + session tables; nothing downstream learns how someone signed in.

### Schema

`browser_invites` is unchanged and is now explicitly the **login-link** credential: it
belongs to an existing member, is reusable, permanent until regenerated or revoked, and
`/auth/link#invite=...` keeps using `Security.exchange()`.

```sql
CREATE TABLE signup_invites (
    digest TEXT PRIMARY KEY, issued_by TEXT NOT NULL, created_at REAL NOT NULL,
    expires_at REAL NOT NULL, consumed_at REAL, consumed_by TEXT);
CREATE TABLE member_credentials (
    member_id TEXT PRIMARY KEY, login_email TEXT UNIQUE NOT NULL,
    password_hash TEXT, password_salt TEXT, password_changed_at REAL);
CREATE TABLE member_identities (
    member_id TEXT NOT NULL, provider TEXT NOT NULL, provider_subject TEXT NOT NULL,
    email TEXT, created_at REAL NOT NULL, PRIMARY KEY(provider, provider_subject));
ALTER TABLE browser_sessions ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'login_link';
```

Signup token: `secrets.token_urlsafe(32)`, SHA-256 digest only, 7-day default expiry, one
use, and the member exists only once it is redeemed. Providers: `google`, `apple`.
`members.email` already exists — it stays the contact address; `login_email` is the login
identifier and may differ.

### Passwords

Self-hosted, no new dependency: `hashlib.scrypt` with a per-member random salt,
constant-time comparison, minimum 10 characters, generic failures, and existing
`Security.limit()` for rate limiting. Helpers in `security.py`: `hash_password`,
`verify_password`, `set_password`, `authenticate_password`.

### Signup

`web/signup.html` reads `#invite=TOKEN`, erases the fragment with `history.replaceState`
at once, POSTs the token, and the server opens a short-lived signup flow — the raw token
is never needed again and never rides through OAuth.

The page offers display name, profile icon, email + password, and Continue with Google /
Apple. There is no signup without a valid invitation. Redeeming is atomic: validate
unused and unexpired, create the member (`role='member'`), attach the credential or the
identity, set name/icon, mark the invite consumed, create the session, set
`__Host-hl_session`; roll back everything if any step fails.

### Login

`web/login.html` becomes email + password, Continue with Google, Continue with Apple, and
"Have a personal login link? Open it normally." No public sign-up button. Failures return
a generic `Invalid email or password`, and a successful password login goes through the
same session-creation code as a login link.

### Google and Apple

`GET /auth/google`, `GET /auth/google/callback`, `GET /auth/apple`,
`POST /auth/apple/callback`. During signup an identity may be attached because a valid
signup flow already exists. During normal login the identity must already exist; an
unknown one answers "This account has not joined HardlyListening. You need a signup
link." **`/login` never auto-creates a member.** Matching emails are never merged —
linking happens only while authenticated.

### Session creation

One helper, `Security.create_session(store, member_id, auth_method)`, used by the existing
login-link exchange, password login, Google, Apple and signup. `auth_method` is
`login_link` | `password` | `google` | `apple`.

### Profile → Account

Extend the existing profile dialog: email, change password, connected accounts (Google,
Apple), invite a friend. `GET /api/profile` gains `email`, `has_password`, `google`,
`apple`, `auth_method` — never hashes, salts, provider subjects, signup tokens or login
tokens.

`POST /api/account/password`: a `login_link` session changes the password with only the
new one and its confirmation (possession of the permanent link already proves the
account); password/OAuth sessions also confirm the current password. A password change
never revokes the login link — the link stays the recovery path, so a forgotten password
is "open your personal login link → Profile → Change password", with no mail.

### Member signup invite

One active invite per regular member. `GET /api/signup-invite` returns
`{active, expires_at}` only; `POST` revokes expired rows, refuses while a valid unused one
exists, mints a 7-day invite and returns the full `…/signup#invite=…` URL once;
`DELETE` revokes. The secret URL is shown only immediately after generating — after a
reload the panel says an invite is active, with its expiry and Revoke. Consumed, expired
or revoked all bring Generate back.

### Admin

`POST /api/admin/signup-invite` mints as many one-time invitations as needed, from
Settings → People. Normal enrollment stops using `/api/members/create`; `create_user()`
stays for recovery, migrations and local tooling. `/api/members/invite` keeps its
behaviour but its UI action is renamed **Regenerate login link**, since it authenticates a
member who already exists.

`/api/members/revoke` keeps revoking sessions and the permanent login link, and also
revokes signup invites issued against that account; it does not delete credentials,
identities or member data. Blocked roles keep blocking every method.

### Routes

```
POST /api/auth/signup/begin      POST /api/auth/signup/password
POST /api/auth/login/password
GET  /auth/google                GET  /auth/google/callback
GET  /auth/apple                 POST /auth/apple/callback
GET  /api/account                POST /api/account/password
POST /api/account/link/google     POST /api/account/link/apple
GET  /api/signup-invite          POST /api/signup-invite    DELETE /api/signup-invite
POST /api/admin/signup-invite
```

Unchanged: `/api/auth/session`, `/api/auth/logout`, `/api/members/invite`,
`/api/members/revoke`.

### Tests

`test_security.py`, `test_invites.py`, `test_invites_ui.cjs`, `test_email_auth.py`,
`test_profile_ui.cjs`. Coverage: login links still reusable and regeneration invalidates
the old one; a signup link works exactly once and expires; two concurrent attempts cannot
create two members; one active invite per member and a new one after consumption; admin
minting; password login creates a normal session; wrong password fails generically;
hashes never appear in API output; a login-link session resets a password without the old
one; Google/Apple cannot create accounts without an invite; an existing identity logs in;
a blocked member is refused by every method; no merge by email; the secret is gone from
the URL fragment; every new endpoint enforces CSRF and rate limits.

### Order

P0 — **done and deployed** (HL-401…HL-409): schema migrations, shared session helper,
signup-invite backend, signup and password flow, login page, profile password change,
member invite UI, admin signup-link UI, tests. P1 — **open** (HL-410…HL-412): Google,
Apple, provider linking/unlinking from Profile.

Explicitly out of scope: playlist ownership, Party, Subsonic, library saves, quota, social
identities and existing member IDs.

## Superseded — kept as history

Sections below describe earlier states and no longer match the code (private folders were
removed, the layout work shipped, the test suites moved on).

## Session follow-on requests

- Member-specific Libraries: existing saved tracks and playlist snapshots are already per-member. Added deployed nested private subfolders and folder-to-track memberships referencing the canonical shared Repository. Folder APIs and UI are covered by isolated tests; folder deletion removes memberships only, never audio. Authenticated full-page smoke was unavailable without signing in.
- One audio-file import capped at 20 MB; reject folders/zips/batches/non-audio. Implemented and deployed; isolated API integration imported a valid 20 MiB WAV. 28 MiB request cap accommodates base64+JSON.
- Move library endpoint enforces admin + CSRF; isolated member-denial/admin-gate tests pass; deployed, no real move.
- Daily song boosts: one active song per member for a rolling 24 hours. A target swap preserves expiry; no same-song refresh. Active boost cards prepend the Activity feed and expire on the server; the existing context menu is the only trigger. Implemented, tested, deployed; service health 200, Python suite 295/295.
- Profile pins for songs/playlists/albums; likes with counts and compact named-voter popup; same popup for up/downvotes.

See `BACKLOG.md` / `BOARD.md` for status and acceptance detail.

## Songs layout/context enhancement — implemented in source

- Persisted Grid/List layout for Songs in both Repository and personal Library, using the existing scope/filter/sort pipeline and shared row controls. Lists and detail pages remain available.
- Lightweight delegated right-click/long-press/keyboard menu preserves visible controls and uses existing action handlers; explicit Play now, Play next, Add to queue are available for downloaded tracks. No new dependencies, backend capabilities or autoplay.
- Isolated renderer + Chromium desktop/mobile fixture: `tests/test_song_grid.cjs`; no DB/media/live writes. Latest complete DOM run is 27/28 passing; two remote-play assertions in the independently edited mixer suite remain failing. Deployment/live acceptance remains pending; service not restarted.
- Playlist creation/reordering, visible draggable playback queue and Auto DJ are separate workstreams, not claimed by this UI change.

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

## Sorting the song table — 0.10.5 (done)

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

## Ranking learns from what he picks; one song table — 0.10.4 (done)

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

## Ranking: the requested version now wins — 0.10.3 (done)

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

## Deletion, ownership and the disk ceiling — 0.10.2 (done)

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

## Queue page — visible progress and honest retry — 0.10.1 (done)

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

## Multiplayer mixer room — 0.10.0 (done)

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

## Mixer scrub discs — 0.9.12 (done)

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

## Phone / PWA mixer layout — 0.9.11 (done)

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

## Mixer navigation — 0.9.10 (done)

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

## Finder rebuild — 0.9.9 (done)

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

## Shared EDM genre metadata — shipped

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

## Repository / Library — personal browsing implemented in source

Top-level Repository opens the unchanged shared community pool; top-level Library
opens only the caller's saved songs plus saved playlist snapshots. The same visible
Repository / Library toggle remains available within the music view, including
detail pages. Both roles get the same personal view (no administrator bypass).
Albums/artists/songs derive from saved scope; Playlists lists explicitly saved
playlists, including empty ones. Search, counts, details, playable queue and ZIP
selection use that scope. Add to library / Remove from library controls retain
explicit song-save semantics: a song in a saved playlist is accessible but is not
automatically an explicit song save. Removing one membership cannot delete shared
media or remove another saved playlist's membership.

`GET /api/listening/saves` additively returns `playlist_tracks` with stored
playlist_id/track_id/position, not live shared playlist membership. Later shared
additions do not enter a saved snapshot automatically. Empty states explain how
to add music from Repository; unavailable authentication is not called empty.
Player connections is the renamed existing device panel. Export for DJ apps
remains an explicitly shared-pool/playlist hand-off, not a personal export.
No custom personal playlist creation, rename, reorder or entry editor ships here;
the saved playlists are snapshots of existing shared playlists. No automatic save
on acquisition, account backfill, offline music, new storage or permission changes.
Source verification: 100 isolated Python tests OK; new two-user/admin renderer
fixtures cover four-view search, snapshot filtering, saves/removal, counts and
unchanged shared pool. Deployment is parent-owned; no restart/live DB/media writes.

### User-visible unfinished-area audit

- Personal playlist authoring/editing and richer community member pages/activity
  history remain unfinished; current My profile is name/icon self-management,
  not a public member directory/history. Existing Queue ownership is not a full
  editable personal playlist system — **not carded yet**. These are separate uncarded gaps,
  not part of HL-220/HL-225.
- Funkwhale/federation, native iPhone playback (**HL-220** spike, **HL-225** single-client implementation), extension
  host/acquisition (**HL-218**, **HL-219**) and B2/WebDAV/Drive storage (B2 is
  **HL-215**, the rest is **HL-223**) remain roadmap, not hidden working screens. Offline playback is not committed scope for the initial client.
- Following now covers **SoundCloud, Apple and any RSS/Atom feed** (**HL-232**, shipped
  2026-11-14): `providers/feed.py` claims a URL that names itself a feed, entries dedupe
  by id → guid → link, entry bodies are dropped before storage, and a dead feed is
  reported as `FeedUnavailable` rather than as an artist with no releases. A page is
  never parsed for links — see `docs/feeds.md`.
- **BPM/sync was not a missing feature (HL-233, closed 2026-11-14).** Tempo is stored
  per track and computed by a worker, the mixer shows live BPM, SYNC solves the pitch
  percent for the other deck and refuses past the fader's ±8 %, and Auto DJ picks by
  tempo and crossfades — all shipped and pinned (`tests/test_dj_sync.cjs`,
  `tests/test_dj_autodj.cjs`, `tests/test_autodj_pick.cjs`). What SYNC is **not** is
  beat sync: it matches tempo and leaves phase alone, so two matched tracks can still
  start on different beats, and there is no MIDI-clock master/slave, no phrase
  detection and no multi-speaker alignment. That scope is asserted by
  `tests/test_dj_sync_scope.cjs`; a beat-aligning upgrade fails it until this text is
  corrected. Nothing is promised here that the mixer does not do.
- PWA installation keeps the shell, the last library, the covers and the audio a member
  has played, with the budget and the `Kept` mark (**HL-226**); only the physical-device
  walk is left to a person. Lyrics now carry word timing and a member's correction, and
  vocal removal is refused rather than deferred (**HL-227**). DJ export now has both halves: the
  on-this-PC playlist file, and a drive prepared for a CDJ — planned before writing, copied and
  verified file by file, index written only for a complete set (**HL-228**, `docs/usb-export.md`).
  It still has no key detection, so the XML carries no `Tonality`, and no length, so `TotalTime` is 0.
- Previous copies kept for a reverse are now collected once their window passes
  (**HL-229**, `docs/replacement-retention.md`): the copy goes, the row records when, and a
  reverse after that says the date rather than half-working. Moving the library root carries
  that history and the trash, with the rows rewritten against the new root (**HL-230**).
  Third-party Subsonic compatibility is now measured rather than assumed (**HL-231**,
  `docs/subsonic-compatibility.md`): our client over recorded Navidrome 0.64.2 responses, and our
  server judged cell by cell against the same recording. The PWA's real-device walk (**HL-226**)
  is the only part of that line left to a person.
These are scoped limitations, not a claim that existing controls are placeholders.
Each open line carries a card in `KANBAN.md`; the two uncarded lines above are the
only ones left without an id.

## Lyrics — shipped

`lyrics.py` owns isolated additive cache and metadata-only LRCLIB lookup with
strict artist/title/album/duration validation and canonical-root local `.lrc`
precedence. Word timing is the same file in enhanced form: `[mm:ss.xx]` starts a
line and `<mm:ss.xx>` inside a line starts a word, a line with marks gains `words`
while a line without them keeps exactly its old shape, the file's `[offset:]`
applies to the words too, and a payload says `word_timed`. `web/lyrics.js`/
`lyrics.css` own modal current-line synchronization, per-word painting (the sung
word in the accent, the words before it lit), plain fallback and large
current/next karaoke/fullscreen.

A member's correction is stored against the **track** in `lyrics_edits`, not
against the copy it replaced: reading is edit → local sidecar → cached provider,
so a provider refresh, a cache expiry or a redeploy cannot undo it, and the row
records who edited it. It is written in the file's own format, so an edited line
keeps its line and word timings. `POST /api/track/<id>/lyrics` takes `{text}` or
`{reset:true}`, CSRF-checked and limited; any signed-in member may edit because the
library is shared; `#dlg-lyrics-edit` is the dialog.

**Vocal removal is refused, not deferred.** It is source separation — DSP over the
audio — and this app is metadata-only, stdlib-only and deliberately does not touch
the signal. Nothing diarises or subtracts anything at any point.

One existing player, no acquisition/audio upload, no autoplay or continuous loop.
Fullscreen is measured at 390×844 with a real `requestFullscreen`; provider
coverage beyond the recorded fixtures is still a live-network question, said
plainly rather than claimed.

## UI polish / foreground lifecycle / PWA — shipped

Preserve existing conceptual tabs, actions, roles, personal saves and shared library;
add only local CSS polish and foreground lifecycle scheduling. `web/polish.css`
uses system fonts/static surfaces, responsive controls and reduced-motion rules.
`web/lifecycle.js` owns completion-driven foreground polls, prompt resume, observer-
gated comment rotation and passive service-worker registration. State/library
refresh entrypoints deduplicate concurrent calls; hidden documents start no polls.
No audio pause/play or server-worker changes belong to this work.

Reuse existing 192/512 icons and manifest with explicit root scope/ID and iOS
metadata. Public asset allowlist bypasses boot/auth for PWA resources only.
`web/sw.js` keeps the shell and never a credential: the entry page is stored only
when it carries the `data-hl-shell` marker and was not a redirect (a sign-in page is
a 200 as well), the last `/api/library` is stored, every stylesheet import and every
script the page loads by URL is precached, and the covers and audio a member has
played live in their own caches. Other API responses are not kept. No background
sync and no push; registration does not call `skipWaiting`, so an update applies on
the next normal launch and never interrupts playback. Deployment requires HTTPS
(localhost is test-only).

Verified in Chromium: the shell and the last library open with the network off, a
played song is kept whole and answered as 206 slices of the kept copy, a kept cover
opens offline, the oldest copy is evicted within the budget and says so, a row
marks itself `Kept`, and Chrome's `Page.getInstallabilityErrors` reports nothing
blocking an install. The prompt itself and an airplane-mode walk on a physical
phone remain a device check (`HL-226`); parent deploys.

## Queue polling and automatic source decisions — shipped

Queue rendering now reconciles playlist/track DOM keys instead of replacing the
list every three seconds: expanded containers, scroll offsets and source-input
draft/focus survive polling. No layout redesign or profile/People changes.

For future automatic Apple jobs, select the first AI-accepted eligible source
from a bounded ranked pool (six judgments maximum); try later candidates after
reject/uncertain, and only then widen SoundCloud search to the YouTube safety net.
The existing search floor remains. Hard raw-title version and duration guards
cannot be overridden by an accepting model. All uncertain/unavailable remains
honestly in review rather than silently treating uncertainty as acceptance.
This is metadata selection, not proof of audio identity.

An admin-only separate Queue decision-history section lists durable SQLite
records: previous/chosen source, candidates, decision reason/status. Change source
uses the existing pin endpoint; reverse returns a stopped undownloaded job to
review. Active jobs must be stopped first. Downloaded tracks with an existing
download job now accept an explicit http(s) URL: `replacements.py` persists a
separate operation and unique `.replacements/<uuid>/audio.<format>` output under
the library root. It validates the new output before one SQLite transaction switches
`tracks.file`, size and job source; the previous good file is never deleted or
overwritten. Failure/cancel keeps the old file/source and leaves the normal job
completed (replacement error/status remains visible in history). Interrupted active
replacements recover to interrupted without automatic retry; queued deliberate
replacements remain queued. Reverse a completed replacement to validate and restore
its retained previous file/source; stale/active/missing-file reversal is refused.
Metadata-only reversals for downloaded files remain refused: they have no previous
media snapshot. Clear entry/history hides replacement audit display only; durable
operation rows remain for recovery, never deleting jobs/tracks/audio. No bulk requeue or migration
of existing review jobs. No live library mutations, service restart or playback.

Verified: full Python discovery, 95 tests in 32.746s, OK; all eight DOM suites
(MIDI/library/social/listening/invite/profile/queue/replacements) PASS. Isolated fake
downloaders and generated silent WAV fixtures cover success, failed verification,
errors, cancellation including during verify, restart recovery, chained reversal,
admin HTTP, clear during active work and Range serving before/switch/after reverse. Queue regression
uses jsdom via NODE_PATH (scratch install), includes an old-render positive control,
five polling renders, reorder, expanded state, scroll, source draft/focus and
text-only history controls. Full log: $TMPDIR/hardlylistening-python-tests.log.
Limits: failed/interrupted staging files are collected by the window (HL-229); clearing
history hides reversal controls. No automatic replacement retry, and no acquisition for
local imports lacking a download job. Moving the library root carries the swap history and
the trash with it (HL-230): the rows are rewritten against the new root, a destination
inside the current library is refused, and a previous file that already sits outside the
library is reported rather than rewritten. Parent review/deployment and real-browser
geometry checks remain. No live model-quality benchmark, download, audible playback or
restart performed.

## Profile onboarding / self-management — shipped

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


## Local invite authentication — shipped

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
   Live, and exercised by the isolated HTTP suite against a temporary device.
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
   HL-220 is a bounded spike to prove iOS OpenSubsonic playback, background audio,
   lock-screen controls and Bluetooth/Car controls, then compare Tauri with Flutter
   only enough to choose. Tauri/Svelte is the default because the existing `player/ui`
   already provides the client UI; switch only if the spike shows serious iOS
   background/media-control problems. HL-225 implements and validates the one selected
   stack on Linux and iOS. Do not build independent Tauri and Flutter clients. The
   intended scope remains a user-configured compatible server, streaming, seek/queue/
   background playback and no acquisition dependency. Offline downloads, local audio
   import, offline indexes, interrupted-download recovery and disk limits remain
   uncommitted scope; revisit only after the core player works.
4. **Generic opt-in extension host (EXT-HOST / row 53).** Disabled by
   default, explicit installation/trust, capability manifests, isolation,
   permissions, revocation and removal. NO bundled acquisition functionality.

   **Shipped 2026-11-14 (HL-218, slice 1 — the grants).** `extensions.py` holds the part with the
   security consequences and deliberately loads nothing: a manifest is validated against a closed
   capability set (`library.read/write`, `social.read/write`, `network` with declared hosts,
   `background`), an unknown capability fails the whole manifest instead of shrinking it, `entry`
   cannot leave the extension's own directory, and `acquisition` is *reserved* — refused at install
   time because it is a separate per-member permission (EXT-ACQUIRE). Installing grants nothing;
   `enable()` needs an administrator and records who and when; `revoke()` clears the grant and the
   flag in one write and cannot be undone by enabling again; `remove()` forgets the row. Proof:
   `tests/test_extensions.py`.

   **Shipped 2026-11-14 (HL-218, slice 2 — the loader).** `extension_host.py` runs one enabled
   extension per run in a child process (`python3 -I -B`, the extension's own directory as cwd, an
   environment of `PATH`/`HOME`/locale only) over a line-delimited JSON protocol; `extension_shim.py`
   is the child half and imports nothing from this application. Every call is mediated against the
   stored grant at that moment — `library.read` over the member's own library projected to ten fields
   with no file path and no provider URL, `social.read` over the Activity feed, `network` to a host
   the manifest declared, https, no redirects, bounded — and every call and run is written to
   `extension_calls`/`extension_runs`. `library.write`, `social.write` and `background` are refused
   by name. The admin surface is `GET /api/extensions`, `/install`, `/(id)/{enable,revoke,remove,run}`
   and `/(id)/calls`, and installation reads a directory an administrator placed under
   `paths.EXTENSIONS_DIR` — code is never accepted over HTTP. **Stated plainly in the doc:** this is
   **not a sandbox**; the child runs as the same user, so enabling an extension is trusting the
   code. **Still open:** the three write capabilities, and any UI for install/enable/revoke.
5. **Separately installed acquisition extension (EXT-ACQUIRE / row 54), later.**
   Independent per-user acquisition permission checked server-side on every work
   request; membership, player access, extension installation and administrator
   status must not implicitly grant this permission. Audit without leaking source
   credentials. No extension or companion work in the backend milestone; legacy
   web acquisition remains unchanged pending a separately scoped migration.
   **Shipped 2026-11-14 (HL-219):** the grant and the server-side check — `acquisitions.py`, one row per
   member, `may_acquire()` reading the grant and never a role, every work route gated, existing
   administrators migrated once so an upgrade does not lock the operator out. What is *not* built is the
   extension packaging itself (HL-218) and a UI control; granting is the API today.
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

A self-hosted music acquisition app: paste or follow, choose, download, keep.
It is HardlyWatching's queue discipline applied to music, with the discovery and
library layers that a music app needs and a downloader does not have.

## Community discussions (SOCIAL-1 / backlog 36; live)

Approved members can read/write shared track, album and playlist comments and
votes. `social.py` owns only `social_comments` / `social_votes`; the centralized
`Handler.boundary` always authenticates social routes, even with legacy local
auth disabled. Existing verified identity, local approval, origin CSRF and request
limits apply, plus 30 social writes/member/minute. No anonymous owner fallback.
Authors edit/delete their comments; admins moderate; bodies are plain text,
1–2000 characters, rendered with textContent. Comments use ascending ID cursors,
30/page. Unique member+entity votes atomically switch between -1/+1 or retract
with 0; responses include score, up/down counts and own vote.

Library has expandable discussions on songs, album headers and the selected
playlist (including empty playlists). Clicks do not start playback. Polling keeps
open panels and unfinished drafts. Counts refresh on opening or mutation, not a
live push feed. All approved members share this activity, never the anonymous
internet. Member IDs are author identifiers, not a completed profile feature.

Tracks/playlists use validated database IDs. Album IDs are server-generated SHA256
of a normalized Unicode artist+album tuple, validated against existing tracks;
artist-only headers are not albums. This grouping intentionally shares imports
and provider copies, but cannot distinguish same-named editions from metadata
alone. Retagging artist/album changes that grouping identity; no automatic social
history migration is claimed. Missing album metadata has no album discussion.
Tests use scratch databases/roots, mocked verification and DOM fixtures. No live
DB/media, auth environment, tunnel or service restart was touched.

## Browser downloads (delivered; backlog 34)

Library song rows save existing local audio as attachments. Group headers export
explicit displayed track IDs; playlist selection exports complete membership in
playlist order. ZIPs are disk-spooled under `.cache/browser-downloads`, copied in
64 KiB chunks, and contain `MANIFEST.txt` explaining unavailable tracks. No queue
work or source mutations. Canonical-root lookup is shared with playback; filenames
are sanitized and ID-prefixed. Central `Handler.browser_download` is the future
shared-auth integration boundary, now guarded by opt-in centralized authentication.
No bucket credentials or absolute file paths are returned by download endpoints.
Tested with generated scratch audio and an ephemeral HTTP server. Group export follows
current search/selection, playlist export does not.

## Identity

Local administrator-issued invitations and persistent SQLite browser sessions
replace the retired provider identity flow. Exact public HTTPS origin is the only
browser identity configuration. Admin-only People creation/roles/invites and local
terminal bootstrap are separate from future acquisition capabilities. Existing
member IDs and canonical library/saves remain intact. New pasted playlist ownership
and request attribution stay; legacy ownership, extended profiles/history and personal
follows are not migrated. Every approved member sees the ONE shared pool/feed;
only saved listening/device views are personal. No public deployment performed.
See SECURITY.md and the source milestone above.

    Discovery  →  Feed  →  user chooses  →  Queue  →  Library  →  (optional) Funkwhale

Two separations the whole design rests on:

    follow artist  ≠  download artist      (following is discovery only)
    download track ≠  publish track        (no external federation publishing by default; approved community pool is shared)

## Layers and what owns what

| Layer | Owns | Does not own |
|---|---|---|
| Providers | one source each: identify / resolve / new_releases | queueing, files, retries |
| Queue (`engine/queue.py`) | job state, retries, provider cooldowns, pacing | what a URL means |
| Library (`library/`) | canonical files, membership, metadata | playback UI |
| Player / Mixer (web) | playing the local library, DJ board | acquiring anything |
| Feed (`discovery/`, not built) | follows, new-release detection | downloading |
| Funkwhale (not built) | users, federation, sharing | deciding what a release is |

## AI metadata approval

Apple auto-search candidates require an OpenRouter metadata judgment before download;
reject, uncertain and API errors park in Needs Review. Explicit human pins bypass
judging. `ai_apple_enabled=1` by default, `ai_model=google/gemini-2.5-flash-lite`;
credential comes only from `OPENROUTER_API_KEY` in the process environment.
This checks metadata, not audio identity. Low-cost default, not proven best or
absolute cheapest. OpenAI-compatible subscription gateway support remains pending.

## AI match transport

Apple auto-matches require metadata approval by default. OpenRouter uses the private service environment credential; model and API base URL are selectable in Settings. Generic OpenAI-compatible gateways use HARDLYLISTENING_AI_API_KEY, never the OpenRouter key. Subscription gateway transport is unit-tested, but actual ChatGPT subscription authentication is **closed as out of scope** (HL-222, owner's decision 2026-11-14): an OpenAI-compatible `base_url` plus `HARDLYLISTENING_AI_API_KEY` already covers any self-run gateway, and that is the supported path. ChatGPT *subscription* auth would mean driving OpenAI's undocumented backend with a browser session token — outside their terms and broken without notice — so it is not built. Live synthetic model testing was incomplete due to HTTP 429; no claim of consistent accuracy or audio verification.

## Early playlist organization (implemented)

SQLite membership projects to `Playlists/<sanitized-title>--<id>/playlist.m3u8`
(relative canonical audio paths only) and `playlist.txt` (every track and job status).
No audio copies or fake placeholder audio. A coalesced, mutation-triggered snapshot
refreshes projections; progress-only writes and state polling do not export files.
Library retains All library and adds playlist selection with pending status rows;
only existing canonical audio enters playback. Managed-marker cleanup preserves
unrelated files. Projection errors are exposed as `stats.playlist_projection_error`.

## Planned B2 / S3 storage (backlog 33; adapters + ranged playback shipped, write path open)

Support Backblaze B2 through its S3-compatible API, using the existing bucket
used by Karakeep. Isolate all app-owned objects under `hardlylistening/`; do not
modify Karakeep objects, bucket-wide lifecycle rules, or public-access policies.
Discover the actual endpoint/region/bucket securely during implementation; do
not copy credentials into project files, SQLite responses, logs, or the browser.
Prefer a prefix-scoped application key where available.

* Keep SQLite as the source of truth for canonical recordings and playlist
  membership. One stored audio object can belong to multiple playlists.
* Add a storage adapter with local-only and S3-backed modes; identify media by
  stable object keys rather than assuming every track has a local filesystem path.
* Download and validate locally, then upload through an independent persistent
  queue with retries/progress. Verify remote object size/checksum before marking
  it stored; multipart ETags must not be assumed to be content hashes.
* Keep objects private. Provide Range-capable playback through the server or
  short-lived signed URLs, plus a bounded local cache for playback/Mixer.
* Preserve playlist organization in-app and in exports; filesystem M3U8 paths
  and remote playback URLs need separate handling. Never persist expiring URLs
  as canonical media identities.
* Explicitly choose mirror versus remote-with-cache mode before migrating data.
  Do not remove local originals automatically after an upload. Migration,
  interrupted-upload recovery, restore, and deletion scope need isolated tests.
* This is planned work only; it is not on the current priority, which is player compatibility.

**Shipped 2026-11-14 (HL-215, slice 1+2).** `library/storage.py` holds `LocalStorage` (the default)
and `S3Storage`: hand-written SigV4 (checked against AWS's published `GET Object` vector in
`tests/test_storage.py`), path-style addressing, one prefix (`hardlylistening/`), single PUT capped
at 5 GiB by name rather than a silent multipart, 5xx retried and 4xx not, upload verified by HEAD
size (an ETag is never compared to a SHA-256), and credentials from `HARDLYLISTENING_S3_ACCESS_KEY`
/ `_SECRET_KEY` only. A misconfigured bucket raises; it never falls back to local. The object key
is the stored path relative to the library root, so one track row names the same object in either
mode. `library/playback.plan` serves a remote track with the same 200/206/416 arithmetic and the
same headers as a local one (size from HEAD, slice from a ranged GET through `iter_range`); the
local copy always wins when there is one, and no client is told which mode the server is in.
`GET /api/storage` answers the admin with the mode, bucket, region, prefix and whether the
configuration is usable — never the credential. **Write side shipped 2026-11-14 (HL-215, slice 3).** `library/uploads.py` is the persistent mirror
queue: `storage_uploads` in SQLite, `offer()` called from `Queue._run` the moment a download is a
verified file, `tick()` draining due rows through `storage.put` + `verify`, exponential backoff, a
bounded attempt count, and a standing worker started in `boot()` beside the art and reconcile
sweeps. The mode is **mirror**: the local original is never deleted, before or after an upload, and
a file that vanished locally is recorded `missing` rather than guessed at. A misconfigured bucket is
answered once (`skipped`) instead of burning every row's attempts. `S3Storage.put` now streams the
file from disk with a caller-supplied SHA-256, so a large track is not read into memory twice.
Proof: `tests/test_uploads.py` (14 checks, stub bucket) including the download-path hook itself —
a stubbed `Queue._run` completion has to land a row, and local mode has to land none.
**Shipped 2026-11-14 (HL-223, the three parts HL-215 left open).** Migration of an existing library
is `uploads.migrate()` — administrator-triggered, never automatic: it refuses a bucket it cannot
build *before* writing a row, walks the track rows into `storage_uploads` (`queued`/`skipped`/
`missing` counted), records the run in `storage_migrations` with who asked and when, is safe to run
again (pending/stored skipped, failed/missing requeued), and is drained by the standing worker, so a
restart mid-migration resumes rather than restarts. A run also **verifies what it already stored**:
each stored row is HEADed, a 404 or a changed size requeues it (least-recently-verified first, so
`limit` walks the library over several runs), and a 403/5xx aborts with the reason rather than
requeueing every row. Restore is `uploads.restore()`: an object back
to the same key through a `.part` staging file, size checked against the object's HEAD and SHA-256
against the queue's record when one exists; a mismatch leaves nothing behind. The playback cache is
`storage_cache_bytes` (0 = off): a remote track whose local file is absent is fetched once into
`paths.STORAGE_CACHE_DIR`; the `storage_cache` table records size/SHA-256/filled/used, so a repeat
play makes **no request at all**, a row older than 300 s is revalidated with one HEAD (a matching
size refreshes it without a refetch), and a new upload, restore or replacement invalidates the row
and file immediately. Least-recently-used eviction removes the row with the file, an object larger
than the whole budget streams instead of evicting everything for one track, `available()` never
downloads, and no eviction path can reach the media root. Deletion scope is stated and pinned: a local delete leaves the object, which is the
restore path, and nothing deletes an object to mirror a local deletion. Admin routes `POST
/api/storage/migrate` (rate-limited) and `POST /api/storage/restore`, `GET /api/storage` now
reports upload totals, recent migrations and the cache. Documented in `docs/storage.md`; proof in
`tests/test_storage_migration.py` (32 checks: refusals before writing, batching, re-runs, resume
across a restart, verified restore and checksum refusal, deletion scope, cache fill/hit/oversize/
eviction/`available`, stored-object verification and repair, a 403 aborting, the HTTP surface). **Still open:** WebDAV and Drive adapters (carded
separately), multipart upload, and any UI — the API is the surface.

## Current priority

**Domain separation, then the player.** The cross-platform player is the product this is being
prepared for, and it must work against HardlyListening *and* any other OpenSubsonic server. That
is only true if the player-facing surface (Library + Playback) does not depend on Repo,
acquisition, or Social. So the order is: draw the internal domain boundaries, make the web API
and OpenSubsonic delegate to them, then build the player. Details below.

Federation is explicitly deferred; it is not on the roadmap (see below).

## Platform separation — one server, two applications (decided 2026-11-11)

The end state is **one backend core** with two real applications on top of it, plus a headless
package of the same core. Not one app per mode, and not separate servers per domain.

```
              HardlyListening server core   (one process, one SQLite database)
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
      REPO            LIBRARY/PLAYBACK     SOCIAL
   acquisition          saves/playlists   Activity
   discovery            streaming         comments
   releases             bookmarks         likes/boosts
   subscriptions        OpenSubsonic      profiles
   provider search      play counts       future ActivityPub
   future federation
        │                 │
        └────────┬────────┘
                 ▼
       HardlyListening Player        HardlyListening Desktop        HardlyListening Server
       store-safe client             embedded server + full UI      same core, headless
       OpenSubsonic only             or remote client               docker/systemd/VPS
       optional Social               Repo/Library/Social/admin
```

### The rule that decides everything

The player must connect to **HardlyListening or another compatible OpenSubsonic server**. So
Library + Playback may not depend on Repo, acquisition, Social, `feed_items`, `watches`, download
jobs, or any `web/index.html` internal. HardlyListening-only features are optional capabilities
layered on top, discovered at runtime.

### Domains and what each owns

| domain | answers | owns |
| --- | --- | --- |
| **Core** | who is asking, may they | members, auth, sessions, permissions, settings, rate limits, HTTP helpers, DB connection, capabilities |
| **Repo** | what music exists or could be acquired here | shared Repository, tracks/artists/releases, imported source playlists, acquisition queue, provider search, artist discovery, artist catalogues, subscriptions, future Repo federation |
| **Library** | what *this member* chose to keep | saved songs/albums, personal playlists, folders, pins, stars, bookmarks, play counts, ratings, personal listening state |
| **Playback** | give me the bytes, remember where I am | stream resolution, `/api/track/:id/audio`, Range, artwork resolution, resume position, listen reporting, media metadata |
| **Social** | what are people doing | Activity, profiles, likes, comments, replies, boosts, shares, future ActivityPub |

Core must know nothing about discovery, downloads, Activity or Subsonic views.

**Table ownership is declared, not migrated.** Repo logically owns `artists`, `artist_art`,
`releases`, `tracks`, `download_jobs`, `watches`, `watch_items`, `feed_items`, `artist_releases`,
`artist_tracks`. Library owns `listening_tracks`, `listening_playlists`, `listening_playlist_tracks`,
`listening_folders`, `listening_favorites`, `listening_albums`, `listening_bookmarks`, `listening_plays`,
`listening_ratings`, `listening_now`. Social owns `activity`, boosts and comment tables. Nothing moves
physically yet; other domains reach them through the owning service.

**Names to stop using.** `feed_items` are *discovered releases*, not social Feed items. Code stops
treating them as social and exposes them as `repo.discovery.releases()`; a table rename is a later,
separate migration.

**Stars versus likes is not one thing.** A Library star is personal organisation. A Social like is a
public signal. Do not merge them, in schema or in copy.

### Dependencies

Allowed: `Library → Repo` (resolve playable media), `Social → Repo` (track metadata for Activity),
`Social → Library` only where genuinely needed.

Forbidden: `Repo → Social`, `Repo → personal Library state`, `Library → Social`.
**Playback must never require Social**, and Library must work with discovery broken.

### Service facades to build (files, behaviour unchanged)

```
core/        auth, members, configuration, shared HTTP helpers          (new package)
repo/        service.py, repository.py, discovery.py, subscriptions.py,
             catalogue.py, acquisition.py, api.py                       (new package)
library/     service.py, saves.py, playlists.py, folders.py, playback.py, api.py
             (the package already exists for artwork/import/analysis helpers; the
              facades join it rather than shadowing it)
social/      service.py, activity.py, comments.py, likes.py, boosts.py, api.py
                                                                         (new package)
subsonic.py  stays a single module for now; it becomes an adapter over Library+Playback,
             never a second query layer. A later `subsonic/` package is a separate change.
```

**Built (ARCH-002, ARCH-005, ARCH-006):** `repo/service.py` and `social/service.py` exist as
delegating facades — snapshot, artist, releases, subscriptions; Activity, boosts, discussion,
profiles — with no behaviour change and a boundary test on each file's imports. `social.py` is now
the `social/` package (`discussion.py` for the domain, `api.py` for the HTTP adapter, `service.py`
for the entry point), because a module and a package cannot share a name. `server.py` calls the
facades for every Repo and Social endpoint. The last Core → Social call —
`security.py`'s `activity.record` — is closed by `events.py`: Core publishes what happened
(`member.joined`, `member.renamed`) and `social.service.subscribe_events()` turns it into
Activity rows at boot (ARCH-008). Core no longer imports the Feed; there is no Core → Social
edge left in `docs/server-domains.md`.

`library/service.py` exposes `library_for(member)`, `artists_for`, `albums_for`, `playlists_for`,
`playlist`, `saved_tracks`, `starred`, `search`, `bookmark`, `record_play`. `library/playback.py`
exposes `resolve_track`, `stream`, `artwork`, `record_listen`, `get_bookmark`, `set_bookmark`.
Existing streaming code moves *behind* that boundary; it is not rewritten. Those two are the shared
implementation for the current web API, OpenSubsonic, and any future player API.

### OpenSubsonic boundary

```
OpenSubsonic request → subsonic.py → LibraryService + PlaybackService
```

Not `subsonic.py` querying tracks, saves and playlists itself and duplicating application logic.

### UI correction (web)

Today `Feed` contains Activity, Artists and Subscribed — two different concepts in one surface.

```
Feed                      Repo
└── Activity              ├── Browse      (current shared Repository: Songs/Albums/Artists/
    (social only)         │                Playlists/Folders — unchanged)
                          ├── Releases    (current Feed → Artists; named Releases because
                          │                Browse already has an Artists view)
                          └── Subscribed  (current Feed → Subscribed, unchanged)
```

Functionality moves, it is not rewritten or duplicated in the DOM. Artist detail, catalogue,
subscribe, "In Repo" and Download actions move with Releases and stay reachable from Repo Browse.
Personal Library scope keeps its current UI. The top-level navigation stays
Queue / Feed / Repo / Library / More.

The top-level **Queue is Repo** — it is the acquisition queue, not playback queue and not player
state, and the player does not need it. Anything whose primary action is Download / Queue / Import /
Acquire belongs Repo; that is the rule agents can apply without asking.

`repoSection = "browse" | "releases" | "subscribed"` is UI state that only exists in shared Repo
scope.

### APIs

Current routes keep working and become adapters over the services. Clean versioned routes appear
beside them — `/api/v1/repo/*`, `/api/v1/library/*`, `/api/v1/playback/*`, `/api/v1/social/*` — and
are built as needed, not all at once. The boundary matters first.

```
GET /api/v1/capabilities
{"server": "HardlyListening", "version": "...", "openSubsonic": true,
 "features": {"repo": true, "repoFederation": false, "social": true, "socialFederation": false}}
```

This is how the player discovers HardlyListening extensions without assuming them.

### Packaging: one core, three packages

* **HardlyListening Server** — the canonical runtime. The same Python core, headless: runs in Docker
  (the production path — containers are preferred over distro units), or directly / under systemd for
  a portable offline install. Exposes the full web UI, OpenSubsonic and the API. **Zero GUI
  dependencies** — no Tauri, no WebView, no display, no Node toolchain needed to start it.
* **HardlyListening Desktop** — an optional GUI **controller and client** for that same server:
  first-run wizard (music folder, data folder, server name, admin account), start/stop the local
  server, and connect the UI to a local or a remote instance. A nontechnical user never needs a
  terminal. Local-only binding by default; LAN is an explicit toggle; Tailscale is not reimplemented
  — show the address if the machine already has it.
* **HardlyListening Player** — iOS/Android/desktop, public/store-safe. OpenSubsonic library,
  playback, offline copies from *the connected server*, stars, bookmarks, search, lyrics, media
  controls. Optional Social when `/api/v1/capabilities` says so. **No Repo acquisition code
  compiled in** — not merely hidden buttons, and a build assertion enforces it.

Player and Desktop are the only two applications to design; the rest is packaging or capability.

### Runtime boundary: the server is the application

Desktop must depend on Server, never the reverse:

```
hardlylistening-server     the application — always runnable on its own
hardlylistening-desktop    optional GUI controller/client; launches and manages the server
```

Hard requirements, in the order they will be violated if nobody checks:

1. The core imports no GUI toolkit and no desktop-shell module, and starts with no display.
2. Closing the GUI does not stop the server; starting the server does not require the GUI.
3. Desktop is a client of the HTTP API and of the `web/` the server already serves — it holds no
   domain rules of its own and duplicates nothing from `docs/server-domains.md`.
4. GUI runtime cost (memory, CPU while open) belongs to the GUI. The server's workload is unchanged
   by whether a window is open.

The Desktop app has **two independent choices**, not one mode switch:

```
Run a local server        [ on / off ]
Connect the UI to         ( ) local server   ( ) remote server
```

Which gives four legitimate combinations: Desktop + local server (normal home install); Desktop
pointed at a remote server while a local one also runs; Desktop as a pure remote client with no
local server; and no Desktop at all.

### Deployment boundary

Full operational detail — filesystem layout, environment file, launcher CLI, systemd unit, Docker
image and Compose file, backup boundary, migration steps and the release gate — is
`docs/server-packaging.md`. The decisions, in one place:

| host | what runs | what is exposed |
| --- | --- | --- |
| home machine, GUI user | core + Desktop shell | web UI, OpenSubsonic; local-only by default, LAN by toggle |
| home machine, headless | core only (systemd/Docker) | web UI, OpenSubsonic, API |
| VPS | **core only** — no Desktop, no Tauri, no WebView, no graphical session | web UI, OpenSubsonic, Repo, Library, Social, API |

The runtime stays what it is today: one Python process, SQLite, a queue worker and a scheduler,
`yt-dlp` and `ffmpeg` when needed. Packaging wraps it — no Redis, no Postgres, no Celery, no
second worker daemon, no Node runtime.

```
/opt/hardlylistening        application, read-only at runtime
/etc/hardlylistening        configuration (one env file)
/var/lib/hardlylistening    database, identity, cache, state
/srv/hardlylistening/music  Repository media
```

The four write sites that used to live in the source tree — the database, the artwork cache, the
browser-download cache and `artist-art/` — now resolve through `paths.py` and leave it entirely when
`HARDLYLISTENING_DATA_DIR` is declared; undeclared, they are exactly where they always were.
`server.py` stops on `SIGTERM`/`SIGINT` in the order the system unit expects, and a download cut
short goes back to `queued` rather than being marked cancelled. What is left of the packaging is
the launcher (`check`/`backup`), the system unit itself, Docker, and the migration test.

Two instances of this core run today, deliberately, on the same codebase and different subdomains:
the PC (`music.hardlysleep.ing`, a Cloudflare tunnel to a local server holding the real library) and
the VPS (a container, `player.hardlysleep.ing`, a test instance whose library may hold different
songs). The PC is primary for now and becomes the backup/fallback later; neither is retired and
nothing proxies between them. A VPS never needs the Desktop package, and the Desktop package never
becomes the server. Clients reach either instance through its public web UI, its OpenSubsonic
endpoint, and (later) the Player.

**Production packaging is the container**, following the standing preference for Docker over distro
units: the same image serves both instances. The systemd unit is kept as the portable/offline install
path, not the deployment target.

### Federation, split by domain (still deferred)

Repo federation attaches to Repo (a track announced by a peer is a *location*, not an automatic
download; modes: metadata only, cache on play, selected mirror, mirror all; prefer re-acquiring from
the original provider using a shared acquisition descriptor, fall back to copying from a trusted
peer). Social federation attaches to Social (ActivityPub). The player speaks neither: it talks to
its server, and the server hides where the bytes came from.

### Release gates

**Separation MVP** (before any player code): RepoService, LibraryService, PlaybackService and
SocialService exist; existing routes and OpenSubsonic delegate to them; Subsonic reads Library and
Playback through those services and all its tests still pass; Library/Playback keep working with
Social and discovery unavailable; Feed is Activity only; Releases and Subscribed live under Repo;
Repo Browse, personal Library, the acquisition Queue and the current player are intact; existing
tests and visual baselines pass; still one process and one database; **the core still starts and
serves with no GUI dependency** (a test asserts no GUI/desktop import exists).

**Player MVP:** one client stack selected by HL-220, with Tauri/Svelte the default unless its iOS
background/media-control spike exposes serious problems. HL-220 proves OpenSubsonic playback,
background audio, lock-screen and Bluetooth/Car controls; HL-225 implements and validates the
selected stack. The client connects to HardlyListening and another OpenSubsonic server; browses,
searches, plays, queues and seeks; stores credentials safely; optionally supports Social; and
contains no Repo acquisition surface. Do not build parallel Tauri and Flutter clients.

**Desktop MVP:** install, choose music folder and data folder, create the admin account, start,
use the full UI, enable OpenSubsonic, enable LAN, quit and reopen with settings intact — no terminal.
The crate compiled and was walked under Xvfb for **HL-224**, but this gate is not met: no
installable bundle exists and the full served UI still needs an auth-mode decision. The **Player
MVP** gate remains unproven (**HL-220/HL-225**): the Svelte/Tauri source passes its own suite, but
that does not establish a native app build, iOS background playback or media controls.
Both choices are independent: running a local server on/off, and pointing the UI at a local or a
remote server — including a remote-only client with no local server running.

**Headless MVP:** `hardlylistening-server` runs on a host with no display, no desktop session and no
Node toolchain, under systemd or in Docker, and serves the full web UI, OpenSubsonic and the API; the
database and `web/` are the same artefact the Desktop package uses. **Met 2026-11-15** by the VPS
container (Coolify application `player.hardlysleep.ing`, no display or session on the host): the live
instance answers `/api/health` 200 `0.18.0`, `/rest/ping.view` 200 and the web UI's own sign-in page,
from the same image and `web/` the Desktop package stages.

### What not to do

Do not split the database. Do not run Repo/Library/Social as required daemons. Do not rewrite the
server framework or OpenSubsonic. Do not rename every current API. Do not rewrite the web UI. Do not
combine federation work with this refactor. Do not start the player before the separation gate.

## MVP definition (owner, 2026-11-04) — shipped

**Downloader + Library + Player** was the MVP: paste a SoundCloud set / Apple playlist / track,
get a canonical single-copy store with per-track jobs, retries and provider-wide cooldowns; browse
by artist and album with search; play anything without leaving the app. All three are live and
have been since before this date, so the list is kept as the definition of scope rather than as a
plan. Everything built afterwards (Subsonic, accounts, Party, social, radio, podcasts) sits beside
it and does not change it.

## Architecture decisions already locked

* **SQLite is the source of truth**, files are media. Job state lives on
  `download_jobs`, never duplicated onto `tracks`.
* **Providers describe content; the queue manages work.** No `if provider ==`
  in the engine.
* **One recording is one file**: `tracks` unique on `(provider, provider_track_id)`,
  membership in `playlist_tracks`; a second playlist never copies audio.
* **Two discovery tables exist and are unwritten**: `watches`, `feed_items`
  (decision and outcome as separate columns). Nothing can accidentally download
  from them.
* **A rate limit pauses the provider**, not the track, and the deadline is in the
  database so a restart cannot dodge it.
* **Unusable ≠ broken**: DRM / geo / removed results get another source without
  burning an attempt.

## Federation — deferred, not implemented

Not on the roadmap. The owner's current priority is the Subsonic surface for a cross-platform
player; federation is parked until that is done and single-instance behaviour is stable.

When it is picked up, the shape is decided: two-node repository federation first (signed node
requests, explicit trust, a repository export/import with a cursor plus full reconciliation),
then direct remote streaming through `/api/track/:id/audio` with Range proxying, then cache/relay
on the VPS, then ActivityPub-style social. Federation OFF must equal today's behaviour exactly:
separate tables, no-op adapters, and the local player never depending on a peer.

The invariants below still hold for any version of it.

Invariants federation must preserve:

* **Storage never merges.** A peer serves audio from its own root or bucket. No
  remote paths, no mounting a friend's disk, no server-side credentials crossing
  instances.
* **Nothing is public by default.** Sharing is opt-in per playlist/album/track.
  Unshared rows are invisible to peers, not just unwritable.
* **Identity is canonical, not local ids.** Tracks match on ISRC where a provider
  supplies one, else the content key already used for imports. Playlist identity is
  a peer-scoped UUID, never a local integer row id.
* **Trust is explicit.** Peers exchange ed25519 public keys and pin them per peer;
  requests are signed and replay-protected. No shared admin token, no "accept any
  peer that knows the hostname".
* **Peers are not members.** Federation does not create accounts. A peer's playlist
  is shown as `artist@peer`, readable if the peer allows it, and never grants admin,
  settings, queue or library rights here.

Suggested shape: a `peers/` module with `identity.py` (keys, signing),
`catalog.py` (export/import manifests), `transport.py` (signed HTTP with Range
proxying) and a `peer_items` table mirroring remote playlists read-only. Playback of
a remote track proxies the peer's Range request; if the peer is down the row is
shown unavailable rather than silently missing.

Phased order: identity and keys → read-only peer playlists → peer activity/feed →
opt-in comment visibility across peers. Votes stay local per server.

## Library interface (LIB-UI-1 / backlog 39; implemented)

The library reads like a music app, not a file list. Four views share one data
path (`/api/library`, one join for playlist membership):

* **Albums** — responsive cover grid, detail page with hero, Play/Shuffle, group
  ZIP and the album discussion. Album identity is the server's canonical
  `album_id`, never a client guess.
* **Artists** — circular tiles derived from the same album groups; detail page is
  the artist's albums.
* **Songs** — dense table with number, thumb, title, artist, album, votes, length
  and per-track download; sticky header.
* **Playlists** — cards with a 2x2 cover collage, download progress and ZIP.

Rules worth keeping: nothing renders raw HTML from the database (every field goes
through `esc`), the play queue only ever holds playable rows, and per-card social
counts come from one batched request (`/api/social/summary`) rather than one per
card. A SoundCloud set writes its own name where an album would go; those groups
render as the artist (or the song, for a single-track set) with "from <set>".

## Feedback and the holes in a release

A vote repaints its counts on the click and reconciles with the server
afterwards; a failure puts the old numbers back and says so. That is the whole
"instant" contract — the optimistic state is thrown away unless the server
agrees.

An album card can show what people said without opening anything: the batched
summary endpoint also returns the newest few comment bodies when asked
(`&latest=N`), and the card rotates them in a bubble over its cover. The ticker
owns its DOM node, and `renderLibrary` only rewrites the grid when the markup
actually changed, so a 3-second poll cannot restart the animation mid-fade.

Browsing reads the whole membership (`libAll`), not just the playable rows —
an album with one undownloaded track must say so on the card and list it in the
tracklist with its status. **Download missing (N)** queues those rows through
`POST /api/library/grab`, which skips anything already on disk and is admin-only
because it starts system work.

An artist page is built from stored rows only (no network on open): the
source's own RSS feed where one exists (SoundCloud publishes per-user feeds;
Apple does not), whether the artist is followed, and the releases the watcher has
already recorded. Following never downloads.

## Not in scope, and why

* **System-wide EQ / effects** — that is a PipeWire filter chain (EasyEffects).
  A browser can only process audio it plays. Not pretending otherwise.
* **Headphone cue in the web mixer** — needs a second output device.
* **Our own ActivityPub / users / federation server** — Funkwhale exists. We
  acquire; it federates.
* **DRM removal of Apple Music audio** — never. Apple is a metadata source.

## Unknowns to resolve before building on them

* ~~Funkwhale API surface for uploads~~ — **resolved 2026-11-14** against Funkwhale's own
  published OpenAPI (`docs.funkwhale.audio/swagger/schema.yml`), not against a guess. Uploads are
  `/api/v2/` (the older `/api/v1/` paths in this document's history are wrong): `POST
  /api/v2/uploads/` is multipart with `audio_file` required (optional `library`, `channel`,
  `privacy_level`, `import_reference`) and answers **201 with an upload object and no track**;
  `GET /api/v2/uploads/{uuid}/` repeats that object with `import_status` in
  `draft`/`pending`/`finished`/`errored`/`skipped`, and carries the produced `track` once finished.
  So "accepted" is `pending` with no track, and success is `finished` **with** a track that has an
  id. Adapter: `library/funkwhale.py` (token from `HARDLYLISTENING_FUNKWHALE_TOKEN`, instance from
  `HARDLYLISTENING_FUNKWHALE_INSTANCE`, https only except loopback, bounded polling, and
  finished-without-a-track treated as failure) proven by `tests/test_funkwhale.py` against a stub.
  Nothing calls it automatically; wiring it to a person's explicit action is still open.
* Whether Apple's public playlist page always carries the whole tracklist; the
  catalog API fallback needs the web bundle token, which rotates.

## Stretch goal: California shows (EVENTS-CA)

Status: **dropped by the owner, 2026-11-14** — the card (HL-217) is gone from the board. The design
notes below stay as the record of what it would have taken, not as work waiting on a key.
Every usable source needs a credential the owner issues: Eventbrite's public event search was shut
down on 2019-12-12 and what remains is organization-scoped OAuth; Ticketmaster's Discovery API is
free but needs an `apikey`; a Bandsintown public key is tied to a single artist and anything wider
is a partner key with display rules. Scraping public pages is keyless and is exactly what this
section forbids, so it is not an option. Canonical artist identity must still be verified before
associating events; same-name artists must not be conflated.

Artist profiles should show upcoming California performances with venue, city,
local date/time, event status and official ticket purchase link. Prefer licensed
structured event APIs or provider RSS/ICS feeds (e.g. Eventbrite where actually
available); do not assume a public feed exists or embed a tracking-heavy widget.
Use cached server-side normalized results, source attribution and last-check time;
failed fetches mean unavailable, not no upcoming shows. Cancelled/rescheduled and
sold-out events need clear labels. Ticket links lead to the organizer or trusted
vendor, never an invented URL.

Later add a Shows page ranked per user: artists they listen to most, with upcoming
California appearances prioritized. Listening counts must come from actual
per-user playback/scrobble history, not download count. **That history exists**
(2026-11-14 correction, HL-216): `listen_events` in `core/db.py` is one dated row per play,
written by the web player (`POST /api/activity/listen`), by Subsonic scrobbles and by
`statistics` — namespaced per member, deduped inside 30 s by `social.activity.log_play` — and
`stats.top_artists(store, member_id, start, end)` already ranks a member's artists over a window
with no download count involved (`tests/test_stats.py` asserts the grouping and that another
member's plays are not yours). A Shows page therefore ranks with `stats.top_artists`; nothing has
to be instrumented first. Sort eligible artists by user listening score, then nearest upcoming show;
provide date/city filters and an explanation of ranking. No exact-location access
required and no listening history sent to event providers. Mobile cards/lightweight
links rather than third-party scripts. Never automatically purchase tickets.

Acceptance: fixture artists with collisions, timezone/date boundaries,
cancellation/reschedule and provider outage; real ticket URLs verified;
per-user ranking isolation; no autoplay/media or background polling.

## Mixer preload queues (0.9.5)

The mixer's picker became two ordered queues, one per deck, fed by a single
search panel. Queued tracks are fetched into memory as Blobs immediately, so
loading a deck is a pointer swap instead of a network wait. State is explicit per
row: `queued` (waiting for a download slot), `preloading NN%`, `ready`, `no
preload` (fetch failed — the deck still streams on demand). Two downloads run at
a time in arranged order; the row grip reorders with pointer events so it works
with a mouse or a thumb, and order is persisted per browser. Copy (→A/→B) and
duplicate (⧉) reuse the same blob — media is cached by track id, so a copy costs
no bandwidth and the same track can sit on both decks.

Mixing itself has never been server-side: `dj.html` builds two `AudioContext`
decks, biquads, gains and a crossfader in the page and the server only delivers
bytes from `/api/track/<id>/audio`. On a phone the DSP runs on the phone.

## Control reset (0.9.6)

A single header `Reset` returns every control to the value the page loaded with.
The glide is proportional to travel within each control's own range and eased
out, so a near-default fader snaps home and a full cut takes about a second;
double-click applies it instantly. The ramp is driven by requestAnimationFrame
with a 250 ms interval fallback so a backgrounded tab or a throttled phone still
reaches the default, and input events are dispatched on the same frames so audio,
outputs and MIDI follow the movement rather than jumping at the end. Ramp maths
lives in `resetDuration` / `resetValue` so `tests/test_dj_reset.cjs` can check
duration scaling, monotonic motion and exact landing without a running page.

## Tempo (0.9.7)

Tempo is stored per track (`bpm`, `bpm_conf`, `bpm_phase`, `bpm_source`), filled by
one background worker that shells out to ffmpeg per file (~0.5 s) and never runs
inside a request. Detection fits a subdivided beat grid to an onset envelope with
a prior around 130 BPM; that resolves most half/double errors that plain
autocorrelation makes, but octave ambiguity cannot be removed from onsets alone,
so a corrected tempo is marked manual and protected. The mixer displays live BPM
(stored × playback rate) and SYNC solves the pitch percent for the other deck,
refusing rather than clamping when the match needs more than the fader's ±8%.

## Recording (0.9.8)

A mix is captured with `MediaRecorder` from the master's `MediaStreamDestination`
and kept as a Blob in page memory. The panel is a review surface, not a file
dialog: play, scrub, compare, then Download or discard. This keeps every take off
the server and off the owner's disk; the cost is that a reload erases them, which
the UI says plainly and `beforeunload` enforces. Persisting across reloads would
need IndexedDB and a different promise, so it is a separate decision.

## 0.11.0 — UI, playlists, queue, mixer and Party

Implemented Songs grid/list, right-click/touch-hold menus, persisted browser playback queue with reorder and move controls, owner-edited manual playlists, Auto DJ BPM selection/crossfade and late-join gesture synchronization. Party page permits all approved members to create/invite; reusable guest identity links expire within 24 hours, guests choose name/icon and remain party-scoped. Aggregate queue votes reorder waiting songs; member aux lease controls playback. Party chat resolves sender profiles server-side and preserves drafts.

Verified combined: 251 Python tests and 32 DOM suites passed, no audible tests or real-record test writes. Deployed service 0.11.0; local health and Party assets 200, unauthenticated Party/library APIs 401. External HTTPS probes returned 403, so real public guest enrollment remains unverified. Multi-speaker synchronization not implemented; Auto DJ is basic BPM/crossfade, not beat/phrase matching.

## Public HTTPS verification follow-up

Resolved the earlier probe ambiguity: Cloudflare returns 403/error 1010 for Python-urllib/3.14, but 200 with HardlyListening-HealthCheck/1.0 and in Chromium. No tunnel/security configuration changed. Read-only tools/check_public.py passes health/Party HTML/JS/CSS 200 and signed-out Party/library APIs 401 on the public origin. Browser confirms Party shell renders. Actual guest enrollment is still not exercised on live data; isolated Party enrollment/security tests remain the evidence for that flow.

## Edge offline fallback — deployed and outage-tested

Cloudflare Worker `hardlylistening-offline` route music.hardlysleep.ing/* deployed to the domain-owning account. Origin remains the PC tunnel; no VPS proxy or DNS change. Worker serves red offline/unreachable HTML with 30-second refresh for document requests and no-store 503 JSON for API requests on tunnel/gateway failure. Normal responses/streams and auth denials pass through. Live verification stopped only the local tunnel connector: public /party returned 503 edge HTML and /api/health 503 edge JSON. Connector restarted in finally; public health recovered to 200/version0.11.0. Six public smoke checks passed before outage test. Worker version 9ec2a3cd-a7c1-4bff-85b8-718fdb2aa245. Source/tests/config in edge-fallback/. Cloudflare Workers plan quotas apply; no physical PC reboot was needed for the disconnected-tunnel test.

## Subsonic surface expansion — Phases 0, 1, 2, 5 and 6 shipped

Full plan: `~/.hermes/plans/2026-11-09_0610-subsonic-jukebox-videos-shares-bookmarks.md`
(owner decisions locked there). The server now handles 65 endpoint names and refuses 16 with error 30
(chat, jukebox, re-encoding via `hls`, sonic analysis, podcast episode download, user administration,
`tokenInfo`). OpenSubsonic is declared (`openSubsonic: true`), `formPost`, `songLyrics` and the
transcoding extension are advertised, and the transcoding extension is served as direct play: a client
whose profiles accept the stored file gets those exact bytes with Range seeking, and one needing a
re-encode is told so.

- **Phase 0 (shipped, one release):** everything that maps onto existing data — song fields (`contentType`, `suffix`,
  `size`, `path`, `created`, `coverArt`, `starred`, `genre`, `bpm`), `getCoverArt`, `getIndexes`,
  `getMusicDirectory`, stars (`star`/`unstar`/`getStarred*`), `setRating`, `scrobble`/`reportPlayback`/
  `getNowPlaying`, play-queue save/get, all `getAlbumList2` types + legacy `getAlbumList`, `search`/`search2`,
  `getRandomSongs`, `getGenres`/`getSongsByGenre`, lyrics, info blocks, `getOpenSubsonicExtensions`,
  scan status, self-only `getUsers`/`getUser`/`getAvatar`. Unsupported subsystems answer error 30.
- **Phase 1 (shipped):** bookmarks — `listening_bookmarks`, player "Bookmark here", resume prompt,
  `getBookmarks`/`createBookmark`/`deleteBookmark`.
- **Phase 2 (shipped):** public shares — track/album/playlist, **24 h expiry**, members and admins only, hashed tokens,
  `/s/<token>` no-login player and range stream; `createShare`/`getShares`/`updateShare`/`deleteShare`.
- **Phase 3:** jukebox in Party mode — deck owned by **the party host's browser** (heartbeat, claim, release),
  queue control actions, `jukeboxControl`.
- **Phase 4 (shipped, without the in-app UI):** audio analysis — BPM and `song.bpm` already existed;
  **HL-205** added `track_features` fingerprints (numpy, imported directly, confined to
  `library/fingerprint.py` + `library/sonic.py`), cosine `getSonicSimilarTracks`/`getSimilarSongs`/
  `getSimilarSongs2`, `findSonicPath`, a background worker and `/api/sonic/*`. **"Sounds like this" and
  "Sonic path" are not in the web UI** — the web UI does not call `/api/sonic/*` at all. See
  `docs/sonic.md`.
- **Phase 5 (shipped member-scoped, not one shared list):** internet radio in the More menu, SSRF
  guards, full Subsonic radio CRUD; every member owns their own stations.
- **Phase 6 (shipped):** podcasts — feed subscribe and the Subsonic podcast endpoints, episode storage
  under a 5 GB per-member budget (**HL-209**) with an opt-in per-channel retention policy (**HL-234**:
  keep the newest N / N days, audited purge in the worker, `storage.pruned` report — see
  `docs/podcast-retention.md`). There is no podcast page in the web UI; the Subsonic surface is the
  client.
- **Phase 7:** chat inside party rooms only (distinct from Discuss), `party_messages`, chat endpoints.
- **Open:** Phase 3 (jukebox) and Phase 7 (chat) on the Subsonic surface, and the in-app sonic UI
  (Phase 4's server half shipped as **HL-205**).
- **Dropped:** music videos entirely. **Out:** re-encoding/HLS, user administration, server-sound-card jukebox.
