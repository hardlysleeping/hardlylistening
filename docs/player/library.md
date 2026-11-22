# The player's library

> **Retired in P4 (2026-11-14).** The two modules this document describes — `player/shell/api-bridge.js`
> and `player/shell/library-boot.js` — are deleted. They wrapped `window.fetch`, answered a list of
> `/api/*` routes from `/rest/*`, and grafted `web/index.html` into the player's window so the
> library *was* the app's page. The player is now its own Svelte build (`player/ui/`), and its
> library is `player/ui/src/lib/library/store.svelte.ts` over the typed Subsonic client in
> `player/ui/src/lib/subsonic/`: real routes, real types, lazy pages, and no `/api/*` name anywhere
> in the build (`tests/test_player_store_safe.py` asserts that over `dist`). The reasoning below is
> kept because it is the record of *why* the bridge existed and what a Subsonic server can and
> cannot answer — not because any of it is still running.

The player serves the app's own `web/` tree and nothing else behind it. That tree calls `/api/…`
on the server it was written against, and inside the player there is no server: no Python, no
download queue, no library on disk. This is the answer to that gap.

Two files, both classic scripts, both in `player/shell/` so the assembly ships them:

| file | job |
| --- | --- |
| `api-bridge.js` | wraps `window.fetch` once and answers an explicit list of `/api/*` routes from `/rest/*` |
| `library-boot.js` | waits for the connect screen's session, installs the bridge, then writes `index.html` into the same window |

They are wired from `connect.html`, after `connect.js` (see [Wiring](#wiring)). Nothing here is
built: no bundler, no dependency, no `import`. `api-bridge.js` is loaded as text, and the same
file can be exercised under node with `fetch` stubbed — which is what
`tests/test_player_library.cjs` does.

## Three rules

1. **An explicit route table.** Only the routes below are answered. Everything else under
   `/api/` is refused with a 404 and a sentence naming why — never a catch-all that returns `{}`
   or `[]`. An empty list is indistinguishable from an empty library, and the person looking at
   it would believe it.
2. **A Subsonic error is an error.** An envelope with `status: "failed"` or an `error` object
   leaves the bridge as an HTTP failure carrying the server's own sentence and the protocol
   code, mapped to an HTTP status the app's `response.ok` checks already understand (30 → 501,
   40 → 401, 50 → 403, 70 → 404, 10 → 400, everything else → 502). It is never flattened into
   an empty result.
3. **No password in a URL the page renders, and none in the log.** Ordinary calls authenticate
   with `u`/`p`, exactly as the connect screen does — they are fetches, not URLs on screen.
   Streams and downloads are opened by the media element and by `<a download>`, so they carry
   the protocol's own token form (`u`, `t = md5(password + salt)`, `s`). Cover art is fetched by
   the bridge and handed over as a `blob:` URL, so a rendered `<img src>` carries no credential
   at all. `PlayerApi.log()` records the endpoint and the status, never a query string.

## The routes

| method | route | answered from |
| --- | --- | --- |
| GET | `/api/library` | `getAlbumList2` (paged, 500 at a time) → `getAlbum` per album, plus `getPlaylists`/`getPlaylist` for the per-track playlist list |
| GET | `/api/playlists` | the composed library's playlist index (`id`, `title`, `can_edit`, `track_ids`) |
| POST | `/api/playlists` | `createPlaylist` |
| GET | `/api/playlists/:id` | the composed index, one playlist |
| POST | `/api/playlists/:id` | `updatePlaylist`, append-only (see below) |
| GET | `/api/listening/saves` | `getStarred2.song` — saved songs are stars |
| POST | `/api/listening/saves` | `star` / `unstar` for `kind: "track"` |
| GET | `/api/listening/folders` | `{ "folders": [] }` — empty by fact, see below |
| GET | `/api/listening/now` | `getPlayQueue` |
| POST | `/api/listening/now` | `savePlayQueue` |
| GET | `/api/track/:id/audio` | `stream` — the server's bytes, passed through |
| GET | `/api/track/:id/download` | `download` — the server's file, passed through |
| POST | `/api/activity/listen` | `scrobble` |
| GET | `/api/bookmarks` | `getBookmarks` |
| GET | `/api/search` | `search3` — the songs this server holds |
| POST | `/api/search` | refused: the player does not download music |

Notes that matter in practice:

* **`/api/library` is a walk, not a call.** This server implements no `getSongs`, so the bridge
  lists albums and then reads each album's songs. Albums that vanish between the listing and the
  read are skipped (they are gone, not an error); anything else fails the whole read, because a
  partially-read library presented as complete is the lie rule 1 exists to prevent. If a server
  answers code 30 for `getAlbumList2`, the bridge walks `getArtists` → `getArtist` → `getAlbum`
  instead rather than reporting an empty library.
* **The snapshot is 30 seconds old at most.** A Subsonic server has no change feed. The composed
  library is reused for 30 s and in-flight reads are deduplicated, so the app's three-second poll
  does not turn into a library walk every three seconds.
* **Covers arrive late on purpose.** Artwork is fetched in the background, four at a time, and
  `artwork` is resolved when the answer is written — so covers appear on the next poll without
  waiting for the snapshot to expire.
* **Ids.** The app writes ids into `onclick` and `data-id`, so they must be numbers. A numeric
  server id passes through as itself; anything else is mapped to a synthetic id above any
  plausible row id (`2 000 000 000` for songs, `1 000 000 000` for playlists) and remembered for
  the session. A row's `album_id` is the server's album id: the app groups albums by it, and an
  opaque key is what it expects.
* **Playlists can only grow.** Subsonic can add a song to the end of a playlist and remove one;
  it cannot express an order. A save that only appends is forwarded to `updatePlaylist`; a
  reorder or a removal is refused with 501 and the reason. A reorder that silently did nothing
  would be worse than one that says why. (This was the bridge's rule for the app's own save
  button. The player's Playlists tab writes through the Subsonic client directly and removes by
  position — see [Playlist writes](#playlist-writes).)
* **Folders are empty by fact.** `personal folders` are the host's own tables; Subsonic's tree
  is artist/album. The app calls `/api/listening/saves` and `/api/listening/folders` together,
  so an error there would take the whole Library scope down. An empty *list of folders* is the
  truth, unlike an empty library.

## Playlist writes

The player's Playlists tab is the one screen that writes. It sends three calls, all of them the
protocol's own:

| what the person did | what the client sends |
| --- | --- |
| made a playlist | `createPlaylist` with a name, then opens the row the answer carried |
| renamed it | `updatePlaylist` with `name` |
| added what is playing | `updatePlaylist` with `songIdToAdd` |
| removed a row | `updatePlaylist` with `songIndexToRemove`, the position that row was drawn at |

`player/ui/src/lib/subsonic/types.ts` types the edit as a union of exactly those three, so a
caller cannot half-apply a change it never showed the person. The transport's parameters are one
flat map, so a song is added one at a time: adding five songs is five calls.

On this server a playlist made from a client is **the same playlist the web editor writes**. The
canonical `playlists`/`playlist_tracks` rows are written through `library/playlists.py::write`,
then saved into the caller's own scope (`library/saves.py::set_saved`), which is the set
`getPlaylists` reads. An edit re-snapshots that scope (`library/saves.py::resync_playlist`) so the
next read shows the edit instead of waiting for the playlist to be saved again. Ownership,
the optimistic `expected` membership and the one transaction stay in the domain module; the REST
handler only parses and records the Activity event.

Refusals are named, and each is a sentence rather than a silent no-op:

* a duplicate add, a position that is not in the playlist, or a call that changes nothing;
* `comment` — this server has no comment column, and accepting it would be a silent lie;
* `favorites` — computed from the member's stars, so it is refused as *not editable* (code 40),
  not as a bad id;
* another member's playlist — not theirs to write (code 40), and not theirs to read either.

Three limits are deliberate:

* **No reorder.** `updatePlaylist` carries add and remove, and no order; expressing one would mean
  replacing the whole membership, which the protocol has no parameter for.
* **No delete.** Nothing on any surface deletes a manual playlist today, and the host's own delete
  is not a list-only operation: `deletion.py::apply` deletes every track no other playlist still
  references and then moves those files to the trash, so deleting a playlist can take the audio
  with it. A delete here has to wait for an explicit ownership flag on `getPlaylists` and a design
  that says whether the songs stay.
* **One song per write.** See above — the transport carries one value per parameter.

## What is refused, and why that is the answer

The app is a whole host front end, so it asks for far more than a player can answer. Each of
these is refused by name, with the reason it cannot exist here — a control that hits one says
something true instead of quietly doing nothing:

| route | why it is refused |
| --- | --- |
| `/api/state` | the queue, the log and the download counters are the host's own snapshot; there is no download queue here |
| `/api/health` | the host's health probe watches the host's build for an update; a player is a client |
| `/api/library/download`, `/api/playlist/:id/{download,apple-backup}` | a ZIP is assembled by the host; the player streams and saves nothing |
| `/api/add`, `/api/search` (POST), `/api/import*`, `/api/library/{grab,move,verify,export}` | acquisition and the host's own files |
| `/api/track/:id/{source,skip,retry,move,delete,unpick,restore,candidates}` | the host's download queue |
| `/api/playlist/:id/{remove,requeue,verify}`, `/api/release/:id/remove` | the host's own deletions and re-downloads |
| `/api/artist*` | artist pictures and profiles are the host's store |
| `/api/subscriptions*` | podcast subscriptions are the host's own feed store; this player reads one music server |
| `/api/boost`, `/api/social*`, `/api/activity*`, `/api/feed*` | the activity feed arrives with its own card |
| `/api/{settings,preferences,profile,members,member,account,admin,auth,signup-invite}*` | accounts and server settings are the host backend's |
| `/api/{queue,pause,cooldown,decisions,deletions,deleted,trash}*` | the download queue and its history |
| `/api/listening/{devices,ui,theme,preferences,pin-order,export,eq,playback,cues}*` | the host's own member state — the equaliser, the playback session and a track's cue points are rows in the host's database |
| `/api/stats*` | listening statistics are counted by the host's database; a Subsonic server keeps no such history |

`tests/test_player_library.cjs` reads every `/api/…` literal out of `web/index.html`,
`web/js/library.js`, `web/js/playback-queue.js`, `web/js/social-feed.js` and `web/lifecycle.js`
and asserts each one is either answered or refused — so a route that nobody mapped cannot reach
the generic 404 without a test failing.

## Wiring

`connect.html` is PLAYER-001's file; this card does not edit it. It needs two lines, after the
existing `connect.js`, in this order:

```html
<script src="./connect.js"></script>
<script src="./api-bridge.js"></script>
<script src="./library-boot.js"></script>
```

That is the whole wiring. The anchor is the existing `<script src="./connect.js"></script>` at
the end of `<body>`; both new tags go immediately after it, bridge first. They belong to the
shell's own allow-list already (`player/shell/*` *is* the list), so the assembly ships them with
no change to `player/assemble.mjs`. The stylesheets stay as they are: `index.html` is assembled
with the app's own CSS, so the library looks like the library.

## How the page changes hands

`library-boot.js` polls `PlayerConnect.current()` (a `MutationObserver` on `#connect-msg`, with an
interval as the fallback). That value only becomes non-null after `/rest/ping.view` proved the
server answers, so nothing here runs on a server that has not been vouched for.

Then, in order:

1. `PlayerApi.install()` wraps `window.fetch`, so the library page's very first request is
   already answered from `/rest/*`.
2. `localStorage` gets `hl.view = "library"` and `hl.scope = "shared"` **only if unset** — the app
   replays that tab on load, and the default would otherwise be the (nonexistent) download queue.
   A choice the person already made is kept.
3. `index.html` is fetched as text and written into **this same window** with
   `document.open()` / `document.write()` / `document.close()`.

Why the third step is not a navigation: a navigation discards the JavaScript realm, and the
session lives in memory only (PLAYER-001 deliberately keeps it out of storage; PLAYER-002 brings
the keychain). Writing the page into the same realm keeps `PlayerConnect`, the bridge and its
state. The URL also stays `connect.html` on purpose: a reload then lands back on the gate, which
is a working screen, rather than on a library page with no bridge behind it.

After the write the boot runs one idempotent pass: hide the controls that cannot act here (the
acquisition search tab, the paste-a-link box and its Add button, the host export, the
import/move/check tools, and the links to `party`/`dj`/`swipe`, which the assembly does not
ship), and make sure a line of the app's own `.shelfbar` exists for a failing server. The
transport and the download links open `/api/track/:id/{audio,download}` themselves, so the boot
patches `HTMLMediaElement.prototype.src` and `HTMLAnchorElement.prototype.href` through
`PlayerApi.rewriteMediaUrl` — a URL substitution, so Range requests still go straight to the
server.

If the library fails to open (missing page, no bridge in the build, a fetch that cannot read
`index.html`), the gate stays on screen and says which of those it was. A second Connect retries;
the failed session is not retried on a timer.

## What is still unproven

* **CORS, for the two loads the page still makes itself.** Every `/rest/` call goes through
  Rust (`subsonic_*`), so the server's origin policy does not apply to the mapping, the error
  codes, the token URLs or the page write. What the page still fetches directly is a cover in an
  `<img>` and a stream in an `<audio>`; those carry no `Origin`-based checks in practice — enough
  for the copies on this server and for Navidrome — but a server that answers `getCoverArt` with
  a redirect to somewhere that refuses the WebView's origin would show the drawn fallback.
* **A real window.** Not on this host: Bazzite is immutable and ships no WebKitGTK development
  packages, so the crate is compiled in the `huibuild` distrobox, which has `webkit2gtk-4.1`,
  `gtk+-3.0` and `libsoup-3.0`, and the window is opened there under Xvfb on X11 with
  `GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_COMPOSITING_MODE=1
  WEBKIT_DISABLE_DMABUF_RENDERER=1`. Every surface in this document has been walked that way
  against a stub OpenSubsonic server, including the empty states and both window widths; what a
  walk cannot settle is the device (the audio engine, ATS and the platform keychain).
* **Playlist writes depend on the server.** This server answers `createPlaylist`/`updatePlaylist`
  (see [Playlist writes](#playlist-writes)); a third-party server that does not leaves the
  controls refusing with its own sentence rather than failing quietly.
* **The visualiser's scratch buffer** re-fetches the playing stream URL from the element. That is
  a cross-origin fetch; a server without CORS refuses it and the effect is skipped, never
  playback.