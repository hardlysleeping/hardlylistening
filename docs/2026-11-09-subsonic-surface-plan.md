# Plan — Subsonic surface: completeness, bookmarks, shares, jukebox, sonic, radio, podcasts, chat

Status: Phase 0, 1, 2, 5 and 6 shipped. Phase 3 (jukebox), 4 (sonic analysis) and 7 (chat) open.
Decisions are locked; do not re-open them while building.

## Locked decisions

1. **Jukebox target = the party host's browser.** The VPS has no sound card; the deck is one device that
   claims the room and heartbeats.
2. **Shares cover track, album and playlist** from day one.
3. **Shares expire in 24 hours**, fixed. **Only members and admins can share** — guests cannot mint links.
4. **Music videos are dropped.** No `--video` pipeline, no `getVideos`/`getVideoInfo`. `getCaptions`
   answers empty.
5. **Radio is one shared list** for the whole install (not per member).
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

Radio shipped **member-scoped**, not install-wide: `radio_stations` carries `member_id`, and a member
only ever sees, changes or deletes their own stations. Locked decision 5 was not implemented that way.

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
  `nowplaying` state (already written by the web player).
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

- `listening_shares(id, token_hash, member_id, kind('track'|'album'|'playlist'), target, note, created,
  expires, hits)`; expiry always `created + 24h`.
- Routes: `POST /api/shares`, `DELETE /api/shares/<id>`, `GET /s/<token>` (no-login player),
  `GET /s/<token>/stream` (Range), `GET /s/<token>/cover`.
- Token: 32 random bytes, only the hash stored; per-token rate limit; `noindex`, `no-referrer`;
  the page exposes only the shared item, never the owner's name or their wider library.
- Guests are refused (403) — the party session cookie is not enough to mint a share.
- Subsonic: `createShare`, `getShares`, `updateShare` (expiry/description), `deleteShare`.
- No storage cost: shares stream originals.

## Phase 3 — Jukebox inside Party mode

- Room state gains `deck` (device id, heartbeat, position, volume, playing) beside the voting queue.
- The party host page can "Run the room on this device": heartbeat every 5 s with position; a missed
  heartbeat releases the deck and the room falls back to per-guest playback.
- Control: `get`, `status`, `set` (index), `start`, `stop`, `skip`, `add`, `clear`, `remove`, `shuffle`,
  `setGain` via `POST /api/party/<id>/jukebox`, restricted to the deck owner or an admin.
- Party UI: deck bar, "controlled by <name>'s phone", queue actions that act on the deck.
- Subsonic `jukeboxControl` maps 1:1 onto those actions.
- Risk: two decks claiming at once, and advance/vote races — one atomic claim, one advancing write.

## Phase 4 — Audio analysis: BPM, sonic similarity, sonic path

BPM already works (`library/analysis.py` beat-grid fit, `library/tempo.py` worker, `bpm/bpm_conf/
bpm_source/bpm_at` columns). This phase adds the sonic half, with numpy allowed.

- `library/fingerprint.py`: ffmpeg decode → log-mel/chroma/spectral-contrast vector (~64 dims, L2 normalised),
  stored in `track_features(track_id PRIMARY KEY, vector BLOB, version, analysed)`.
- `library/sonic.py`: cosine neighbours for `getSonicSimilarTracks` and a real `getSimilarSongs*`;
  `findSonicPath` = greedy nearest-neighbour walk with hop cap and visited set (always terminates).
- One background worker (never in a request), admin "Analyse audio" action with progress, automatic enqueue
  for new downloads, and a "re-analyse" escape hatch.
- API: `/api/sonic/similar/<id>`, `/api/sonic/path?from=&to=`, `/api/sonic/status`.
- UI: "Sounds like this" under a track, "Sonic path" in the row menu, BPM where it already appears.

## Phase 5 — Internet radio (one shared list)

- `radio_stations(id, name, stream_url, homepage, created, created_by)` — install-wide, admin-editable,
  members may add.
- "Radio" in the More menu (beside Party/Mixer/Swipe): list + add, play through the audio deck.
  Nothing is downloaded, nothing counts against quota.
- Guards: http/https only, no private/localhost targets (SSRF), HEAD probe before save, ICY title when present.
- Subsonic: `getInternetRadioStations`, `createInternetRadioStation`, `updateInternetRadioStation`,
  `deleteInternetRadioStation`, and a "Radio" folder in `getMusicDirectory`.

## Phase 6 — Podcasts

- `podcast_feeds(id, url, title, artwork, created, last_checked)`, `podcast_episodes(id, feed_id, guid, title,
  published, duration, audio_url, file, state)`; the list is shared like radio.
- "Podcasts" in the More menu: subscribe by URL, newest-first episodes, explicit per-episode download,
  progress, play through the deck.
- Storage: own `member_podcast_gb` cap (5 GB default) with oldest-first pruning; quota checked before queueing.
- Subsonic: `getPodcasts`, `getNewestPodcasts`, `getPodcastEpisode`, `downloadPodcastEpisode`, `refreshPodcasts`,
  `createPodcastChannel`, `deletePodcastChannel`, `deletePodcastEpisode`.

## Phase 7 — Chat (party room, not Discuss)

Discuss (`social_comments`) is per-item commentary with votes; Subsonic chat is a room timeline. They stay
separate; merging would put room chatter on song pages or strip discussion of its target.

- `party_messages(id, party, actor, body, created, expires)`, expiring with the room.
- Party page: Chat panel beside the queue.
- Subsonic: `addChatMessage`, `getChatMessages` → the member's current room; no room ⇒ error 30 / empty.

## Ordering

Phase 0 (one release) → 1 bookmarks → 2 shares (security review before ship) → 3 jukebox → 4 sonic →
5 radio → 6 podcasts → 7 chat.

## Acceptance per phase

Every phase: Python suite green, a disposable-browser probe for any UI, service restarted with health 200,
`BOARD.md` entry with what changed and what was measured, `BACKLOG.md` items ticked, and the Subsonic
endpoints exercised against a temporary device rather than a real member.
