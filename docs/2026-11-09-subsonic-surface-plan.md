# Plan — Subsonic surface: completeness, bookmarks, shares, jukebox, sonic, radio, podcasts, chat

Status (2026-11-13): Phase 0, 1, 2, 3, 6 and 7 shipped; Phase 5 shipped as the Subsonic CRUD only
(see that section for what is not built). Phase 4 is half done: tempo ships and Auto DJ reads it;
the fingerprint/similarity half is HL-205 and is the only part that needs numpy.
Decisions are locked; do not re-open them while building.

## Locked decisions

1. **Jukebox target = the party host's browser.** The VPS has no sound card; the deck is one device that
   claims the room and heartbeats.
2. **Shares cover track, album and playlist** from day one.
3. **Shares expire in 24 hours**, fixed. **Only members and admins can share** — guests cannot mint links.
4. **Music videos are dropped.** No `--video` pipeline, no `getVideos`/`getVideoInfo`. `getCaptions`
   answers empty.
5. **Radio is member-scoped**: every member owns their own stations. The first draft of this plan
   said one shared list for the whole install; that was re-decided when accounts landed, because a
   station's `stream_url` can embed a credential and a personal list should not be readable, editable
   or deletable by another member. The Subsonic protocol carries no owner field on a station, so each
   caller simply gets their own set.
6. **Audio analysis may use numpy** (and any dependency that helps). The pure-python rule applies only to
   the existing tempo worker, which stays as it is.
7. **Phase 0 ships as one release.**

Still out of scope: re-encoding (`hls`, and any target format other than the stored file), user
administration (`createUser`, `updateUser`, `deleteUser`, `changePassword`), jukebox-as-server-sound-card.

The OpenSubsonic transcoding extension (`getTranscodeDecision`, `getTranscodeStream`) **is** served,
but only as direct play: the decision evaluates the client's direct-play profiles against the stored
container, codec and average bitrate; a client that accepts the file gets those exact bytes with
Range seeking, and one that would need a re-encode gets `canTranscode: false` with a reason. A time
`offset` is refused, because without an encoder it cannot be honoured.

Radio is member-scoped as decision 5 now says: `listening_radio_stations` carries `member_id`, and a
member only ever sees, changes or deletes their own stations (`tests/test_subsonic_phase34.py`
`test_radio_is_member_scoped`). There is no admin view of another member's stations, and no way to
promote a station to the install; an operator who wants a shared list would need a new decision,
not a flag.

## Phase 0 — everything that maps onto data we already have (one release)

Client-facing completeness; no new state beyond two small tables.

- `song` gains `contentType`, `suffix`, `size`, `path`, `created`, `coverArt`, `starred`, `genre`, `bpm`.
- Artwork & structure: `getCoverArt` (302 to the stored artwork URL, 1×1 PNG fallback), `getIndexes`,
  `getMusicDirectory` (root → albums, artist → albums, album → songs, plus a Radio folder in Phase 5).
- Stars: `star`, `unstar` (song ids and album ids, repeated parameters allowed), `getStarred`, `getStarred2`
  — mapped to `listening_favorites` and `listening_album_likes`.
- Ratings: `setRating` → new `listening_ratings(member_id, track_id, rating)`.
- Plays: `scrobble`, `reportPlayback`, `getNowPlaying` → new `listening_plays(member_id, track_id, plays,
  last_played)`; powers `getTopSongs` and the `frequent` album list.
- Queue: `savePlayQueue`, `savePlayQueueByIndex`, `getPlayQueue`, `getPlayQueueByIndex` → existing
  `nowplaying` state (already written by the web player). `getNowPlaying` reports the same member
  state as one entry: the song fields plus `username`, `minutesAgo` (the real age of that write),
  `playerId` and `playerName` (the device the request authenticated as). Nothing is playing on the
  server, and a track the member no longer has in scope produces no entry rather than a guess.
- Lists & search: remaining `getAlbumList2` types (`newest`, `recent`, `random`, `frequent`, `highest`),
  legacy `getAlbumList`, `search`, `search2`, `getRandomSongs`.
- Genres: `getGenres`, `getSongsByGenre` from `genre_labels`.
- Lyrics: `getLyrics`, `getLyricsBySongId` via the existing lyrics module.
- Info blocks: `getArtistInfo`, `getArtistInfo2`, `getAlbumInfo`, `getAlbumInfo2` (empty biography rather
  than an error), `getSimilarSongs`, `getSimilarSongs2` (same artist/genre until Phase 4 lands).
- Plumbing: `getOpenSubsonicExtensions` (advertise `formPost`, `songLyrics`), `getScanStatus`,
  `startScan` (no-op that reports idle), `getUsers`/`getUser` (self only), `getAvatar`.
- Remaining unsupported endpoints answer Subsonic error 30 with a plain message, so a client never hangs.

Acceptance: every endpoint in the green list answers 200 with well-formed XML/JSON for a device whose member
has saved songs; the full Python suite passes; one release, service restarted, health 200.

## Phase 1 — Bookmarks

- `listening_bookmarks(member_id, track_id, position_ms, comment, created, updated)`.
- Player: "Bookmark here" beside the seek bar, bookmark list in the song menu, resume prompt on open.
- Subsonic: `getBookmarks`, `createBookmark`, `deleteBookmark`, `song.bookmarkPosition`.
- Library gets a "Continue listening" rail when a bookmark is older than the current session.

## Phase 2 — Public shares (24 h, members and admins only)

- `listening_shares(id, token_hash, token, member_id, kind('track'|'album'|'playlist'), target, note,
  created, expires, hits)`; expiry always `created + 24h`.
- Routes: `POST /api/shares`, `DELETE /api/shares/<id>`, `GET /s/<token>` (no-login player),
  `GET /s/<token>/stream` (Range), `GET /s/<token>/cover`.
- Token: 32 random bytes, kept beside its SHA-256 so the member who minted it can be shown the
  same URL again (HL-207); lookup is still by hash. A row from before that has `token=''` and the
  listing says `recreate` rather than showing a broken `/s/`. Per-token rate limit; `noindex`,
  `no-referrer`; the page exposes only the shared item, never the owner's name or their wider
  library. Whoever can read the row could already read its target, so the token adds no reach —
  it only stops the link from being lost the moment the dialog closes.
- Guests are refused (403) — the party session cookie is not enough to mint a share.
- Subsonic: `createShare`, `getShares`, `updateShare` (expiry/description), `deleteShare`.
- No storage cost: shares stream originals.

## Phase 3 — Jukebox inside Party mode

Shipped 2026-11-13 (HL-204). The ownership question is the aux-host lease that already existed:
`party.control()` holds a 30-second `host_until` with a hashed `host_token`
(`claim`/`heartbeat`/`next`/`release`), so there is exactly one deck and a client steers it rather
than becoming a second speaker.

- **Room state** carries `deck` beside the queue: `playing`, `gain`, `position`, `host_active`, and
  `controlled_by` (the host's display name). `playing` is true only while a lease is live, so the
  status cannot claim a speaker that is gone. `party_rooms` gains `deck_playing`, `deck_gain`,
  `deck_position`, `deck_updated`; `party_entries` gains `position`. Old rooms migrate through
  guarded `ALTER TABLE`s — the defaults describe a room that never had a deck.
- **Commands** — `get`, `status`, `set`, `start`, `stop`, `add`, `clear`, `remove`, `shuffle`,
  `setGain` over `POST /api/party/<id>/jukebox` (GET is `get`), one transaction each, members only
  (`member_identity`), 90 actions per member per window. `start` is refused 409 when no lease is
  live; `setGain` is 0–1; `remove` takes an index in the room's own order.
- **The order stays the room's.** Waiting entries sort by votes descending, then `position`, then
  id. `set` writes the order a client asked for into `position`, so it is honoured among tracks
  nobody has voted on, and a vote still moves a track up. `shuffle` permutes `position`.
- **Heartbeat** carries the host's playback position (bounded 0–24 h) and whether its audio element
  is playing; `next` resets the position to 0. The deck's position is therefore measured, not
  guessed, and `playing` has one honest source — the device making sound — so a remote `stop`
  really stops it and a local pause is not undone by the next poll.
- **Party UI**: the deck bar (`#deckbar`, `#deckstate`, `#deckactions`, `#decktoggle`,
  `#deckgainrow`, `#deckgain`) names the device — "Host's device — playing" — and every member sees
  who holds it; only members get play/pause and the room fader. The host applies gain to
  `audio.volume`, sends `audio.currentTime` and `!audio.paused` on each heartbeat (and at once on a
  local play/pause), and pauses or resumes when the deck's `playing` says the room should. Guests see the bar but have no
  controls, because guests cannot play the catalog.
- **A lapsed lease** leaves the room with no deck: the bar says so and any member can claim it.
  There is no per-guest speaker fallback — the catalog is member-only.
- **Subsonic `jukeboxControl`** maps 1:1: `status` → `jukeboxStatus`, `get` → `jukeboxPlaylist`
  (the playing track at index 0, then the waiting queue, so `currentIndex` points at it), every
  other action → `jukeboxStatus`. `party.control_room` finds the
  member's room by presence, else a room they created or whose deck they hold; no room ⇒ error 30,
  never a new party invented for the client.
- **Races pinned** by tests: a second claim on a live lease is 409, and an advance whose `expected`
  entry is stale does not move the room (409).
- Proof: `tests/test_party_jukebox.py` (15), `tests/test_subsonic_jukebox.py` (14),
  `tests/test_party_ui.cjs` (deck bar), `node tests/visual/snap.cjs` PASS (the party page is its own
  stylesheet, so the player baselines are untouched).

## Phase 4 — Audio analysis: BPM, sonic similarity, sonic path

BPM works and is in use (`library/analysis.py` beat-grid fit, `library/tempo.py` worker,
`bpm/bpm_conf/bpm_phase/bpm_source/bpm_at` columns; Auto DJ sequences on it, HL-303). This phase
adds the sonic half — the only part that wants numpy.

- `library/fingerprint.py`: ffmpeg decode → log-mel/chroma/spectral-contrast vector (~64 dims, L2 normalised),
  stored in `track_features(track_id PRIMARY KEY, vector BLOB, version, analysed)`.
- `library/sonic.py`: cosine neighbours for `getSonicSimilarTracks` and a real `getSimilarSongs*`;
  `findSonicPath` = greedy nearest-neighbour walk with hop cap and visited set (always terminates).
- One background worker (never in a request), admin "Analyse audio" action with progress, automatic enqueue
  for new downloads, and a "re-analyse" escape hatch.
- API: `/api/sonic/similar/<id>`, `/api/sonic/path?from=&to=`, `/api/sonic/status`.
- UI: "Sounds like this" under a track, "Sonic path" in the row menu, BPM where it already appears.

## Phase 5 — Internet radio (member-scoped)

Shipped: `radio.py`, the Subsonic CRUD only. Not built: the More-menu page, the `getMusicDirectory`
Radio folder, the SSRF address check, a HEAD probe before saving, and ICY titles. The list below
records what exists, then what the original plan asked for and did not ship.

- `listening_radio_stations(id, member_id, name, stream_url, homepage_url, created)`. The owner is
  part of every read, update and delete, so a station id from another member answers error 70, not a
  row.
- Validation is on the value, not the network: absolute `http(s)` only, no userinfo (a credential is
  never persisted), 2048 characters, no control characters or whitespace; name 1–200. Nothing is
  fetched or probed server-side, so a station URL is inert data and cannot be used to reach a private
  address from this server. That is why there is no SSRF guard and no HEAD probe: there is no request
  to guard.
- Nothing is downloaded and nothing counts against quota; playback is the client's own stream.
- Subsonic: `getInternetRadioStations`, `createInternetRadioStation`, `updateInternetRadioStation`,
  `deleteInternetRadioStation`. No "Radio" folder in `getMusicDirectory`, and no web UI — the
  stations are reachable only from a Subsonic client.

## Phase 6 — Podcasts

- `podcast_feeds(id, url, title, artwork, created, last_checked)`, `podcast_episodes(id, feed_id, guid, title,
  published, duration, audio_url, file, state)`; the list is shared like radio.
- "Podcasts" in the More menu: subscribe by URL, newest-first episodes, explicit per-episode download,
  progress, play through the deck.
- Storage: **5 GB per member** (`HARDLYLISTENING_PODCAST_CAP_MB`), counted over the files this server holds
  for that member and checked before and during the copy. **No automatic pruning**: at the cap the download is
  refused with the numbers, and the member deletes a downloaded episode to make room. Deleting an episode
  unlinks its file, so the space really comes back; `getPodcasts.storage` reports the budget and the files.
- Subsonic: `getPodcasts`, `getNewestPodcasts`, `getPodcastEpisode`, `downloadPodcastEpisode`,
  `streamPodcastEpisode` (Range, only for a held copy), `refreshPodcasts`, `createPodcastChannel`,
  `deletePodcastChannel`, `deletePodcastEpisode`.

## Phase 7 — Chat (party room, not Discuss)

Discuss (`social_comments`) is per-item commentary with votes; Subsonic chat is a room timeline. They stay
separate; merging would put room chatter on song pages or strip discussion of its target.

- Shipped as `party_chat(id, party, author, display_name, profile_icon, body, created)`, scoped to a
  24-hour room by construction and keeping the newest 500 rows per room. Name and icon come from the
  server's identity for that actor, so a submitted profile is refused rather than trusted.
- Party page: the Chat panel beside the queue (`web/party.js`), text nodes only.
- Shipped (HL-210): `addChatMessage`, `getChatMessages` map onto the member's current room —
  `party.room_for` reads the newest open room the identity is still present in (`party_presence`,
  the room's own "who is here" rule), `getChatMessages` filters by `since` in milliseconds and
  answers an empty list with no room, `addChatMessage` answers error 30 with no room, the author is
  the authenticated member (a submitted `username` is ignored) and the body keeps the room's
  1–1000-character rule. `tests/test_subsonic_chat.py`.

## Ordering

Phase 0 (one release) → 1 bookmarks → 2 shares (security review before ship) → 3 jukebox → 4 sonic →
5 radio → 6 podcasts → 7 chat.

## Acceptance per phase

Every phase: Python suite green, a disposable-browser probe for any UI, service restarted with health 200,
`BOARD.md` entry with what changed and what was measured, `BACKLOG.md` items ticked, and the Subsonic
endpoints exercised against a temporary device rather than a real member.
