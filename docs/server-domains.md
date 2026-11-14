# Server domains — who owns what

Purpose: give every feature, table and route **exactly one owner** before the cross-platform
player is built, so the player-facing surface (Library + Playback) can be trusted not to depend
on Repo, acquisition or Social.

This document is the authority for *placement*. Behaviour is unchanged by it. Nothing here moves
in the database; ownership is declared and the code is moved behind service facades one card at a
time (`PLAN.md` § Platform separation).

Measured 2026-11-14 against the live tree (excluding `backups/`): **26 top-level modules, 6
packages, 63 tables created by code (56 exist in the live database, 7 are created on first use),
42 literal routes in `ROUTES` plus the prefix-dispatched subtrees in `boundary()`** (a route that
needs its own method check, a member read or a schemaless body lives there rather than in the
table, so the two numbers are counted differently and neither grows on its own).

---

## 1. The rule

The player must talk to HardlyListening *and* to any other OpenSubsonic server. Therefore:

> **Library and Playback may not depend on Repo, acquisition, Party or Social.**
> HardlyListening-only features are optional capabilities on top.

Allowed edges: `Library → Repo` (resolve playable media), `Playback → Repo` (resolve a file),
`Social → Repo` (track metadata), `Social → Library` (what a member did), `Party → Library`.

Forbidden: `Repo → Social`, `Repo → personal Library state`, `Library → Social`, and
`Playback` depending on anything except Repo + Library + Core.

**The acquisition descriptor is the only acquisition artefact that may cross a boundary.**
`repo/federation.py` builds it from a provider result — provider, the provider's own track id and
the metadata a re-acquisition needs, never a credential, a file path or a member — and
`repo/service.py` is the only way anything reaches that code (§3). It is a shape for mirroring,
not a channel: nothing here opens a socket, and the Player never speaks it (`docs/federation.md`).

---

## 2. Domains

| domain | answers | contract it must honour |
| --- | --- | --- |
| **Core** | who is asking, and may they | knows nothing about music, discovery, Activity or Subsonic |
| **Repo** | what music this server has, or could acquire | serves the shared pool; never reads a member's personal state. The acquisition queue (`engine/queue.py`: job states, retries, backoff, the download workers) is Repo's too — it exists to acquire shared music, and no other domain may depend on it (REPO-013, declaration only; no UI moves with it) |
| **Library** | what *this member* chose to keep | per-member only; no acquisition; no social signal |
| **Playback** | give me the bytes, remember where I am | member scope + Range; never needs Social or Party |
| **Social** | what people are doing | may read Repo/Library; nothing depends on it |
| **Party** | who is listening together, right now | may use Library/Playback; nothing depends on Party |
| **Adapter** | Subsonic/OpenSubsonic protocol | translates; owns no rules, reads Library + Playback only |

---

## 3. Module ownership

Top-level modules (26):

| module | owner | owns |
| --- | --- | --- |
| `server.py` | Core | HTTP dispatch, static, boot — the composition root. It legitimately imports every domain; nothing imports it. |
| `security.py` | Core | an alias of `core.security` (ARCH-009): `import security` returns that module object, the old name is what every module imports |
| `config.py` | Core | an alias of `core.config` (ARCH-009): the two socket options, the two surfaces, the declared media root |
| `auth_admin.py` | Core | local admin bootstrap/recovery |
| `db.py` | Core **+ Repo** | an alias of `core.db` (ARCH-009). Connection and schema migration are Core's; `Store`'s repository accessors over tracks/playlists/downloads are Repo's. The split is ARCH-002; the file does not move. |
| `user_playlists.py` | Library | **the HTTP edge only** (LIB-001): route shape, accepted body keys, rate limit, and the Activity write after the transaction. The domain — validation, ownership, membership — is `library/playlists.py`, and the on-disk M3U projection is `library/playlist_files.py` |
| `search.py` | Repo | provider search; one result into the acquisition queue |
| `deletion.py` | Repo | deleting a shared playlist/album, and the files only it needed |
| `genres.py` | Repo | shared EDM taxonomy (`genre_labels`) + its admin endpoints |
| `ranking.py` | Repo | source-pick learning (`source_picks`) |
| `decisions.py` | Repo | metadata decision audit (`match_decisions`) |
| `replacements.py` | Repo | staged source replacements (`source_replacements`) and their retention: `collect` removes a previous copy once its window passes, marks the row `pruned`, and `reverse` refuses with the date — `docs/replacement-retention.md` |
| `podcasts.py` | Repo | podcast feeds and episode acquisition (`listening_podcast_*`) |
| `listening.py` | Library | device sessions and the request routing — **and today the Subsonic entry point (`rest`, `envelope`, `song`, `key`)**, which is Adapter code still living in a Library file. SUB-010/011 removed its query layer; moving the dispatch itself into `subsonic.py` is the part still open. Saves and folders moved to `library/` in LIB-001 and the three names the queue calls (`want_playlist`, `deliver_track`, `forget_track`) delegate to `library/saves.py` |
| `nowplaying.py` | Library | resume state (`listening_now`) |
| `preferences.py` | Library | favourites, pins, album likes, theme, UI snapshot |
| `zip_export.py` | Library | a member's own library as a ZIP |
| `radio.py` | Library | member-scoped radio stations |
| `shares.py` | Library | capability share links + the no-login player |
| `swipe.py` | Library | a member's triage of Repo songs. A decision writes one `swipe_swipes` row and nothing else: `drop` keeps the song out of that member's feed only, `keep`/`like` also save it to the member's library. No route deletes a `tracks` row |
| `lyrics.py` | Playback | lyrics cache and playback metadata |
| `mixer_room.py` | Playback | mixer audio streams |
| `activity.py` | Social | alias of `social.activity` (SOCIAL-001): the queue, Subsonic and the web routes still import the old path, and `sys.modules` makes it the same module object |
| `social/` | Social | `service.py` is the only entry point (ARCH-005). It owns nothing; each function delegates to the module that holds the query: `activity.py` (the log: `record`, `log_play`, `listen`, `feed`, `day_key`, kinds), `boosts.py` (the 24-hour slot: `boost`, `current_boost`, `active_boosts`, `BOOST_TTL`), `comments.py` (comments, votes, summaries, `annotate`/`album_id`, `validate_target`, `schema`), `likes.py` (a like on a comment), and `discussion.py`, which is a re-export facade over `comments` + `likes` for `social/__init__.py` and `api.py`. `api.py` is the HTTP adapter (`social_*`, `song_boosts`); `profiles.py` stays its own module |
| `profiles.py` | Social | the member profile page (reads Library and Repo) |
| `party.py` | Party | rooms, guests, sessions, presence, votes, skips, chat |
| `subsonic.py` | Adapter | OpenSubsonic surface; translates and encodes, and owns no member state — stars, ratings, plays and bookmarks are `library/service.py`'s, and `schema()` only delegates there (SUB-011). It does own the protocol facts (`PROTOCOL_VERSION`, `EXTENSIONS`, transcode targets) that `/api/v1/capabilities` reports |

Packages:

| package | owner | owns |
| --- | --- | --- |
| `core/` | Core | `security.py` (members, credentials, identities, sessions, invites, rate limits, Origin/CSRF), `config.py` (runtime settings) and `db.py` (connection, schema, migrations). The top-level names are aliases, not copies: each puts this module object into `sys.modules` (ARCH-009). Core must know nothing about discovery, downloads, Activity or Subsonic |

**Aliases are not copies.** `sys.modules["db"] is core.db`, and the same for `config` and
`security`. A `from core.db import *` shim looks equivalent and is not: it splits the attributes, so
`mock.patch.object(db, "DB_PATH")` patches an object the server never reads. That mistake let one
full test run write to the live library (`tests/test_core_aliases.py` now pins the identity), and
`db.connect()` refuses the install's own database from a test process (`tests/test_db_guard.py`).
| `engine/` | Repo | acquisition queue and scheduler |
| `downloaders/` | Repo | yt-dlp invocation and AI match helpers |
| `providers/` | Repo | provider adapters (SoundCloud, Apple Music, generic) |
| `repo/` | Repo | `service.py`: the only entry point (ARCH-002) — snapshot, artists, releases, subscriptions, catalogue; beside it `repository.py` (the artist page), `catalogue.py` (releases and tracklists), `subscriptions.py` (following an artist), `acquisition.py` (how work enters the queue), `api.py` (the HTTP-shaped helpers), `federation.py` (the acquisition descriptor and the node identity — nothing outside the package may import it except `service.py`, `mirror.py`), `discovery.py`, `mirror.py` (REPO-014) |
| `discovery/` | Repo | **aliases only** (`sys.modules`, not copies): `artist_profile.py` → `repo.repository`, `tracklist.py` → `repo.catalogue`, `subscriptions.py` → `repo.subscriptions`. The implementation moved into `repo/` in REPO-014; the old paths stay because tests and the facade import them |
| `library/` | **Repo today, Library tomorrow** | `service.py` (the player's reads and personal writes), `playback.py`, and from LIB-001 `saves.py` (saved scope + download intents), `folders.py` (the personal tree), `playlists.py` (member playlists), `playlist_files.py` (the M3U/JSON projection), `api.py` (the HTTP adapter for the two listening paths), `storage.py` (where a recording's bytes live: local by default, an S3-compatible bucket opt-in — it owns the key namespace, so one track row names the same object in either mode; also the bounded playback cache under `paths.STORAGE_CACHE_DIR`, whose eviction can never reach the media root); `uploads.py` (the mirror queue: which finished file is waiting for the bucket, how many times it has tried and why it stopped; plus `migrate()` — the administrator-requested walk of an existing library — `restore()` — a verified object back to the root — and the `storage_migrations` record of who asked and when); `funkwhale.py` (the optional publish step: one finished track uploaded to an instance, then the upload polled until it reports a track, because "accepted" is not "delivered"); plus artwork, art store, id3 art, imports, import rules, metadata repair, playlist backup, quota, source health, tempo analysis and DJ/browser export — see §7 |
| `acquisitions.py` | **its own** (a root module, like `extensions.py`) | who may start work: one grant row per member, and a `may_acquire()` that reads the grant and never a role. Membership, player access and administration do not imply it; the first run grants existing administrators once (`granted_by='migration'`) so an upgrade does not lock the operator out. Admin surface: `GET`/`POST /api/acquisitions` |
| `extensions.py` | **its own** (a root module, like `stats.py` and `shares.py`) | the opt-in extension host's bookkeeping: manifest validation, the closed capability set, the install → enable → revoke → remove lifecycle. It deliberately **executes nothing** — the loader is the module below — so what is permitted can be tested and revoked without a process. `acquisition` is reserved, never granted here (EXT-ACQUIRE owns it) |
| `extension_host.py` | **its own** (root module) | the executing half of HL-218: one enabled extension per run in a child process (`python3 -I -B`, cwd = the extension's directory, environment of `PATH`/`HOME`/locale only), with a line-delimited JSON protocol. Every call is mediated against the stored grant *at that moment* (`library.read` → `library.service.catalog` projected to ten fields with **no `file` and no `source_url`**; `social.read` → `social.service.feed`; `network` → declared hosts, https, no redirects, 2 MiB cap), and every call and run lands in `extension_calls`/`extension_runs` (bounded). `library.write`, `social.write` and `background` are **refused by name** — grants the host does not offer yet. **Not a sandbox**: same uid and filesystem, so an extension is trusted code and enabling it is the decision. `extension_shim.py` is the child half and imports nothing from this application |
| `tools/` | none | operator scripts, not imported by the server |
| `edge-fallback/` | infrastructure | Cloudflare Worker, no Python domain |

---

## 4. Table ownership

Declared only. Access from another domain goes through the owner's service, never by opening a
second connection or writing the table directly.

**Core (9)** — `members`, `member_credentials`, `member_identities`, `browser_sessions`,
`browser_invites`, `signup_invites`, `member_requests`, `settings`, `logs`

**Repo (21)** — `artists`, `artist_art`, `artist_releases`, `artist_tracks`, `releases`, `tracks`,
`playlists`, `playlist_tracks`, `member_items` (who added a shared item), `member_playlists` (who
owns a shared playlist), `download_jobs`, `watches`, `watch_items`, `feed_items`, `provider_state`,
`genre_labels`, `source_picks`, `source_replacements`, `match_decisions`,
`listening_podcast_channels`, `listening_podcast_episodes`

**Library (21)** — `listening_tracks`, `listening_playlists`, `listening_playlist_tracks`,
`listening_folders`, `listening_folder_tracks`, `listening_favorites`, `listening_album_likes`,
`listening_pins`, `listening_theme`, `listening_ui`, `listening_bookmarks`, `listening_plays`,
`listening_ratings`, `listening_now`, `listening_devices`, `library_intents`,
`listening_radio_stations`, `listening_shares`, `swipe_swipes`, `library_trash` (files a delete
moved aside), `library_deletions` (who deleted what; never pruned)

**Playback (1)** — `lyrics_cache`

**Social (5)** — `activity`, `social_comments`, `social_comment_likes`, `social_votes`,
`song_boosts`

**Party (8)** — `party_rooms`, `party_entries`, `party_sessions`, `party_guests`, `party_presence`,
`party_votes`, `party_skips`, `party_chat`

Created lazily on first use, so absent from a quiet database: `listening_plays`,
`listening_ratings`, `listening_radio_stations`, `listening_podcast_channels`,
`listening_podcast_episodes`, `listening_ui`, `swipe_swipes`.

### Schema-creation sites do not match ownership yet

`db.py` creates `listening_bookmarks`, `listening_shares`, `library_trash` and `library_deletions`
(Library), `activity` and `song_boosts` (Social). `security.py` creates `member_items` and `member_playlists` (Repo). `shares.py` also
creates `listening_bookmarks`/`listening_shares` defensively. All of it is `CREATE TABLE IF NOT
EXISTS`, so moving the statements is a code move, not a migration — do it when the owning service
is created, not as a sweep.

### Two pairs that look alike and are not

| personal (Library) | public/social |
| --- | --- |
| `listening_favorites` — my star | `social_votes`, `song_boosts` — what others see |
| `listening_album_likes` — my saved album | `party_votes` — the room's decision |
| `playlists`, `playlist_tracks` — the shared pool (Repo) | `listening_playlists`, `listening_playlist_tracks` — my copy (Library) |

Never merge these, in schema, in queries or in UI copy.

---

## 5. Route ownership

`server.py` holds 42 literal patterns in `ROUTES` plus the prefix subtrees in `boundary()`.
Prefixes are assigned here;
a new route takes its owner from this table rather than from the module that happens to handle it.

| owner | routes |
| --- | --- |
| **Core** | `/api/health`, `/api/state`, `/api/settings`, `/api/members*`, `/api/member/*`, `/api/auth/*`, `/api/account*`, `/api/signup*`, `/api/signup-invite`, `/api/admin/signup-invite`, `/theme.css` (the session's `--user-*` colours as a stylesheet: the one page asset that is per-member, linked from `<head>` so the first paint is right) |
| **Repo** | `/api/federation` (read-only mirror status), `/api/tracks`, `/api/add`, `/api/pause`, `/api/cooldown/clear`, `/api/failed/retry`, `/api/queue/*`, `/api/import`, `/api/import/upload`, `/api/artist`, `/api/artist/art*`, `/api/catalogue`, `/api/feed*`, `/api/subscriptions*`, `/api/search`, `/api/playlist/(id)/{apple-backup,requeue,remove,verify}`, `/api/release/(id)/remove`, `/api/track/(id)/{artwork,bpm,move,retry,skip,source,unpick}`, `/api/track/queue`, `/api/library` (shared snapshot), `/api/library/{grab,move,verify,tempo,titles}`, `/api/genres*`, `/api/decisions*`, `/api/replacements/collect` (collect previous media whose retention window has passed — `docs/replacement-retention.md`), `/api/tempo/status`, `/api/titles/status` |
| **Library** | `/api/listening/*`, `/api/storage` (admin read: mode/bucket/region/prefix and whether the configuration is usable — never the credential — plus upload totals, recent migrations and the cache report), `/api/storage/migrate` and `/api/storage/restore` (admin, CSRF: queue the existing library; put one object back on disk verified), `/api/shares*`, `/api/swipe*`, `/api/library/export`, `/api/library/usb/plan` and `/api/library/usb/start` (a removable drive: the plan, then the copy — `docs/usb-export.md`), `GET /api/library/usb` (job status), `/api/library/download`, `/api/track/(id)/download`, `/api/playlist/(id)/download`, `/api/art/(hash)` (the cover a head unit fetches: capability URL, anonymous on purpose) |
| **Playback** | `/api/track/(id)/audio`, `/api/mixer`, `/api/mixer/stream`, `/api/playback/events` (bounded telemetry, member-scoped) |
| **Social** | `/api/activity`, `/api/activity/listen`, `/api/social*`, `/api/boost`, `/api/profile` |
| **Party** | `/api/party*` |
| **Extensions** | `/api/extensions` (list installed + on disk + recent runs), `/api/extensions/install`, `/api/extensions/(id)/{enable,revoke,remove,run}`, `/api/extensions/(id)/calls` — administrator-only, CSRF-checked; installation names a directory an administrator already placed under `paths.EXTENSIONS_DIR` and never accepts code over HTTP |
| **Acquisition grants** | `GET`/`POST /api/acquisitions` (list, grant, revoke — administrator-only) |
| **Adapter** | `/rest/*.view` (every Subsonic endpoint) |

### The `/api/library` trap

`/api/library` is the **shared Repo snapshot**, while `/api/listening/*` is the **personal
Library**. The words are inverted at the HTTP layer. Do not "fix" the URLs — clients and the web
UI depend on them; treat this table as the mapping. The versioned APIs (`/api/v1/repo/*`,
`/api/v1/library/*`) exist precisely so a new client is not forced to learn the inversion.

Existing routes stay as compatibility adapters when a service facade lands. No frontend contract
changes in this refactor.

The versioned surface is documented separately in [`docs/api-v1.md`](api-v1.md): the envelope,
every route, the service function it delegates to, and the auth rule each one follows.
`api/v1.py` is the adapter; `README`-style route lists should point there rather than duplicating
it.

**Built so far (ARCH-002, ARCH-005, ARCH-006).** `repo/service.py` and `social/service.py` are
delegating facades, and `server.py` now reaches Repo and Social only through them:
`/api/state`, `/api/artist`, `/api/tracks`, `/api/catalogue`, `/api/subscriptions` and every
subscription action call `repo.service`; `/api/activity`, `/api/member/<id>`, `/api/boost` and
`/api/activity/listen` call `social.service`. The social endpoints go through `social/api.py`, the
adapter that was `dispatch` inside `social.py`. `social.py` became the `social/` package with its
old names re-exported, so `import social` still works.

The facades are pinned to the functions underneath by tests rather than trusted, because the
delegation was meant to change who calls whom and nothing else.

**Isolation is measured (ARCH-007).** `tests/test_isolation.py` refuses the import of a whole
package with a `sys.meta_path` finder and then asks the rest of the server to work: with `social`
unimportable, `/api/track/<id>/audio` still answers 206 with the right `Content-Range`; with
`discovery` unimportable, the Library routes still answer and `/api/health` stays up. The failing
route is the only thing that fails. One honest gap is pinned there too: `do_GET` has no catch-all,
so a route that raises drops its connection instead of answering 5xx — the process survives, but
the client sees a reset.

**The last Core → Social call is gone.** `security.py` used to call `activity.record` on join and
rename, which put the Feed inside the sign-in path. Core now publishes `events.MEMBER_JOINED` /
`events.MEMBER_RENAMED` (`events.py`); `social.service.subscribe_events()` attaches Activity to them
from the composition root (`server.boot()`), and the mapping from Core's vocabulary to Activity's
kinds lives in Social. Publishing never raises and needs no subscribers, so a server without Social
still boots and still lets people in — the Feed rows simply do not happen. `tests/test_events.py`
pins both halves, and `security.py` naming `activity` at all fails the suite.

---

## 6. Known inversions (measured, not suspected)

| edge | where | why it is wrong | fix |
| --- | --- | --- | --- |
| ~~Core → Social~~ | closed: `security.py` publishes `events.MEMBER_JOINED` / `MEMBER_RENAMED`; `social.service.subscribe_events()` turns them into Activity rows at boot | Core must not know Social exists; a Social bug must not break sign-in | done — `events.py` + `tests/test_events.py` |
| Library → Social | `preferences.py` uses `social.album_id` and `social.validate_target` | album identity is metadata, not a social concept | move both to a Core/Library metadata helper (ARCH-003) |
| Library → Adapter | `shares.py` imports `subsonic.BLANK_PNG` | a domain reaching into the protocol adapter for a constant | move the constant to a shared asset module (SUB-010) |
| Library → Adapter | `listening.py` contains `rest()`/`envelope()` and imports `subsonic` | the adapter lives in a Library file | SUB-010/011 took the queries out of it; the remaining step is moving the dispatch itself into `subsonic.py` |
| Core+Repo in one file | `db.py` creates schema for every domain and holds repository accessors | ownership is unclear at the point of change | schema stays (it is the migration point); accessors move behind `repo/service.py` (ARCH-002) |

Everything else in the import graph already points the allowed way. `server.py` importing every
domain is correct: it is the composition root.

---

## 7. The `library/` package name

`library/` currently holds **Repo-side** code: artwork, art store, id3 art, imports, import rules,
metadata repair, playlist backup, quota, source health, tempo analysis, DJ/browser exports
(including preparation of a removable drive, `usb_export.py` — `docs/usb-export.md`),
playlist views. The personal-Library facades (`library/service.py`, `library/playback.py`) will
join that package because the plan names them there.

That is a deliberate compromise, and the debt is the name: files in `library/` that are not the
personal library. Rule: **do not mass-rename**. As each helper is touched, it moves behind
`repo/service.py` (or into `repo/`) — one file at a time, with its tests.

---

## 8. Decisions this document makes (confirm or overrule)

1. **Party is its own domain**, a sibling of Social, not a part of it. It owns realtime room state
   and will own the jukebox deck (HL-204); folding it into Social would make the player's
   dependency graph lie.
2. **`shares.py` is Library**, not Social. A public capability link to my own tracks is personal
   media exposure; "social shares" in the plan means ActivityPub-style, which does not exist yet.
3. **Podcasts are Repo.** Feed subscription is discovery, episode storage is acquisition with a
   cap (HL-209). The Subsonic podcast endpoints read them through Repo.
4. **Radio is Library.** Stations are per-member listening state, not shared pool content.
5. **Swipe is Library**, reading Repo. It is per-member triage, which fits the allowed
   `Library → Repo` edge; putting it in Repo would create forbidden `Repo → Library` writes.
6. **`search.py` is Repo.** Provider search and "download this result" are acquisition.
7. **`zip_export.py` is Library**, `browser_downloads.py` is Repo (it resolves shared media
   paths), and playback resolves files *through* Repo — `Playback → Repo` is the allowed edge.

---

## 9. Review checklist

Apply to any change that adds a module, table or route:

- [ ] It appears in exactly one row of §3, §4 and §5.
- [ ] Its imports obey §1 — no `Repo → Social`, no `Library → Social`, no `Playback → Social`.
- [ ] It opens no connection of its own and writes no other domain's table directly.
- [ ] A new route's owner is taken from §5, not from the file that was convenient.
- [ ] If it is a player-facing capability, it works with Social and Party absent.
- [ ] Federation code is reached through `repo/service.py`; no `web/`, Player or Adapter module
      imports it, and an acquisition descriptor carries no credential and no file path.
- [ ] Personal state (favourites, pins, plays) and social signal (votes, boosts) are not merged.

## 10. Re-measuring this document

```
ls *.py | wc -l                                             # top-level modules
sqlite3 hardlylistening.db "select count(*) from sqlite_master where type='table' and name not like 'sqlite_%';"
grep -rh "CREATE TABLE IF NOT EXISTS" --include=*.py . | grep -v backups | wc -l
grep -rhoE '"/api/[^"]*"' server.py | sort -u | wc -l        # route literals
```

If those numbers move, update §3–§5 in the same commit.

---

## 11. Runtime boundary — the server is the application

The Python core is the **canonical runtime**. Everything else is a client of it.

| artefact | what it is | may depend on | must never contain |
| --- | --- | --- | --- |
| `server` (this repo's Python core) | the application. Runs directly, under systemd, or in Docker | Python + the OS | a GUI toolkit, a WebView, a desktop shell, a window |
| `web` (`web/`) | the full frontend served by the server | HTTP only | server internals |
| Desktop shell | an optional GUI controller **and** client | the server, its HTTP API, and the `web/` it serves | server-domain rules, a second implementation of anything in §3–§5 |
| Player | an independent OpenSubsonic client | `/rest/*` + `/api/v1/capabilities` | any Repo acquisition code, and any federation descriptor |

Rules that follow:

* **Desktop depends on Server; Server never depends on Desktop.** No import, no shared module, no
  build step in either direction that makes the core need a GUI to start.
* The core must start and serve with no display, no session, no Tauri, no Node toolchain —
  enforced by `tests/test_headless.py` and `tests/test_host_dependency.py`
  (`docs/desktop/dependency.md`).
  A headless host runs the core alone and still exposes the web UI, OpenSubsonic and the API.
* The GUI adds its own memory and CPU while it is open. Closing it must not be what stops the
  server, and starting it must not be the only way to start the server.
* Packaging (`server/`, `web/`, `desktop/`, `player/`) is a **layout target for the builds**, not a
  repo reorganisation demanded today. The hard part is the dependency direction above, which is
  testable without moving a single file.
* **The Player never speaks federation.** Repo federation attaches to `repo/service.py` only;
  `subsonic.py`, `library/playback.py`, `lyrics.py`, `mixer_room.py`, `shares.py` and the shipped
  web client import none of it. `tests/test_federation.py` parses the modules to keep that true,
  so a future `sdk/` or `player/` tree cannot reach the descriptor by accident either.