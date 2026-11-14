# UI components — HardlyListening

Read this before changing the player, the queue, or anything they share.

## Where the code lives

| File | Size | Owns |
| --- | --- | --- |
| `web/index.html` | ~356 KB | All markup and the remaining inline script (state, layouts, sorting, playback). No component CSS |
| `web/styles/app.css` | small | The one stylesheet entry point: layer order and imports only |
| `web/styles/*.css` | — | `tokens`, `base`, `layout`, `motion`, `utilities` (see `docs/frontend-css.md`) |
| `web/styles/components/*.css` | — | One file per surface; the ownership contract is `docs/frontend-css.md` |
| `web/preferences.js` | small | Likes/pins wrappers around the cards' controls |
| `web/genres.js`, `web/lyrics.js`, `web/lifecycle.js` | small | Genre chips, the lyrics reading (word timing, the editor) and reload-on-new-version |
| `web/js/repo-discovery.js` | ~30 KB | Releases, the follows and the artist/release pages (`JS-030`). A plain classic script loaded before the inline one; every name stays global, so nothing else had to change |
| `web/js/social-feed.js` | ~17 KB | The Feed's two readings of one play log — Activity (the day cards, the boost pin) and Popular (the community chart) — plus the member profile page and its listening numbers (`JS-031`). Same shape as the file above |
| `web/js/library.js` | ~60 KB | The library's drawing layer: grouping, the songs/albums/artists renderers and the detail-view editors (`JS-032`). Reads the library's state, which is still inline |
| `web/js/playback.js` | ~26 KB | The playback layer (`PLAYER-004`): the media session the OS reads, the transport commands it sends back, the position, shuffle/repeat state and the scrobble. It is the **only** writer of `navigator.mediaSession` and the only `ended` advance; it drives the app's own audio element and queue through an adapter |

There is no build step: the files above are what the server serves. A change is live on
reload; there is nothing to compile. Components read the entry point's imports; never add a
second stylesheet to `index.html`.

## Player components

All of these have their markup and behaviour in `web/index.html`; their styling is in
`web/styles/components/player.css` (queue: `playback-queue.css`).

| Component | Markup | Behaviour | Notes |
| --- | --- | --- | --- |
| Compact bar | `#player` | `paintPlayer`, `syncPlayButton`, `#pllike`, `#plqueue` | 112 px tall at every width, drawn to `compact-bar-layers.svg` (402×112); desktop only widens the shell and adds the save slot. The transport's last two buttons are `#plmute` and `#plvolmore` (see Volume). **Surface:** `body #player.on.has-art` is the last `has-art` rule in the sheet and owns the glass — `rgba(16,16,20,.72)` → `rgba(12,12,17,.72)` over its own backdrop blur, so the page still shows through. `#player.has-art::before/::after` carry the fullscreen player's two art layers (blurred wash, then a brighter screened copy masked to two corner blooms), each drawn at a lower opacity than fullscreen and clipped to the bar's outline with `clip-path:inset(0 round var(--cbar-r) …)` — 14 px at phone width, 16 px from 761 px where the bar's radius changes. The clip is what keeps a blurred rectangle from appearing around the floating bar; the drift animation is deliberately not used here because it is transform-driven and a transform would carry the clip out with it |
| Swipe page bar | `#player` in `web/swipe.html` (a second instance of the same markup) | `web/swipe.js`: `paintBar`, `play`, `stopPlay`, `togglePlay`, `paintTime`, `paintVolume`, `placeVolumeMenu` | The library's bar, with only what a one-song deck can honour: art, title/artist, prev/play/next, mute + `#plvolmore`, scrub + clocks. No queue, no fullscreen, no heart/save, no repeat/shuffle, so `#plmeta` may use the band they reserve (`right:14px`). It loads `styles/components/player.css` **before** `swipe.css`, which is this page's own sheet: same rules, same specificity, resolved by order — the reverse order silently loses every narrowing rule. Two narrowings: the bar is held to the deck's column (`min(520px,100vw - 32px)` from 761 px) and the scrub draws a native 3 px track filled by `--bar-played`, because this page builds no `.plwave-seek` canvas. `body:has(#player.on) main` adds the bar's height to `main`'s bottom padding, or the action row sits under it. The bar appears on the tap that starts a song and leaves with it — dropping the playing card stops playback. Prev restarts after 3 s in, else replays the previous audition; next auditions the card underneath **without deciding it** |
| Fullscreen overlay | `#player-full` | `openFullPlayer()`, `closeFullPlayer()`, `paintFullPlayer()` | Persistent element, toggled by `.on` + `body.fullplayer`. Sized to fit the viewport: the record's stage is `min(86vw,520px,54vh,calc(100vh - 360px))`, so the open player never scrolls (down to 1024×600 and 360×640) |
| Pull / swipe | inside the overlay | `setFullPull()`, `commitFullPull()`, `retreatFullPull()` | The gesture layer the overlay transition uses. Touch and pen only — a mouse drag on the bar or the pane is a slip, and the bar's own buttons open it on desktop. The pane's pull timers check `fullPullSeq`, so a dismiss still animating out cannot close a pane that was reopened meanwhile |
| Disc | `#pf-disc`, `#pfart` | `paintPfDisc()`, `pfScratchPrepare/Release`, `setCoverArt()` | Rotation is a RAF loop; scratching reads a decoded copy. Double-tapping the cover swaps the record for a full-size sleeve (`#player-full.artmode`); the record is the default and returns when the player closes |
| Visualizer | `#player .plwave-seek` (compact), `#hdr-wave` (header) | envelope builder, `spectrum()`, draw loop | Two different readings. The compact wave is the track's own envelope and the compact scrub's drawing. The header is a **live spectrum**, not the envelope, drawn as one continuous line rather than bars: log-spaced bands (~35 Hz–16 kHz, at least one FFT bin each) read from an `AnalyserNode` fed by the audio element, cosine-interpolated at five sub-samples per band, ending on the resting line at both edges, and mirrored about the canvas centre as one filled shape with a stroked outline. Three things make it move: a rolling headroom (each frame is normalised against its own loudest band, which falls back 0.9 per frame and is floored at 0.4, so a quiet verse and a chorus both use the height), a 1.35 expansion applied after that normalisation so the band beside a peak drops back toward the centre, and a fast per-band attack/release (0.92 / 0.42) under an `AnalyserNode` smoothing of 0.5. An `1/f` tilt keeps the hats off the floor. It redraws at ~30 fps while that canvas is on screen, against ~11 fps for the envelope. That graph is built only on a mouse-driven desktop — `min-width:900px` **and** `(hover:hover) and (pointer:fine)` **and** `maxTouchPoints === 0` — and only while that canvas is drawn. Width alone is not enough: a phone in landscape is wider than 900 px, and a routed element dies when Safari freezes its context on lock, so the touch check is what keeps background playback intact. No on-screen knobs and no fullscreen style button. `cfg` is still read from `hl.wave.v1` (`enabled`, `bars`, `level`, `smooth`, `mirror`), with `bars` × 1.5 as the phone pitch (96). The compact count is width-proportional — `round(pitch × w / 182)`, clamped 12–512, so 95 at 390 px and 469 at the widest 890 px canvas. The canvas is 49 px tall (three quarters of the old 65), flush with the bar's bottom edge, so bars stand on the bottom margin and grow up through the rectangle without reaching the title line. The envelope is RMS (not peak) over 512 points, normalised to the track's loudest passage and expanded by `^1.4`; there is no per-track percentile stretch, so quiet and loud sections keep their relative heights |
| Compact scrub | `#plseek`, `.plwave-seek` | `markScrub()`, `progress()`, the `seek` branch of the draw loop | The range input is the control and the only keyboard/screen-reader surface, drawn as nothing and stretched over the wave: `left:85px;right:95px;bottom:0;height:49px`, transparent track, 0×0 thumb, `touch-action:none`, `z-index:1` under `.pctrl` (2), so the transport buttons that overlap the top of the rectangle keep their own presses. A press or drag anywhere in the rectangle seeks; a `:focus-visible` accent outline is the only visible sign of the control, and the page's swipe guard already skips `input[type=range]`, so a horizontal drag never changes view. Feedback is canvas-only: `markScrub()` sets `scrubAt` from `progress()` on `pointerdown`/`input`/`pointerup`, and `scrubHeld` (true from `pointerdown` until `pointerup`/`pointercancel`/`lostpointercapture`) gates the 260 ms clear, so a held finger keeps its playhead and a keyboard seek fades; bars up to the finger are lit, the unplayed bars lift from alpha 0.55 to 0.82, and a 2 px light (`#eef1f6`) playhead spans `0.575 × canvas height` standing on the base — light, not accent, because the bars either side of it are already accent. With no decoded envelope yet the same logic draws a flat 3 px line on the base plus the same 21 px playhead |
| Compact clocks | `#pltime`, `#pldur` | painted with the position | `.ptime` is `bottom:4px` (resting on the wave's base, the line the bars stand on), 44 px wide, `#pltime{left:40px}` / `#pldur{right:50px}` so each sits 1 px clear of its edge of the wave rectangle. Measured at 390: boxes y 776–792, wave x 100–280, 1 px gaps, no overlap with mute; at 1280 (bar bottom 784): y 764–780, wave x 190–1080, 1 px gaps |
| Top row | `#appnav` — `.navmain`, `#navpanel` | `positionMoreMenu()`, `askExport()`, `openDjExport()` | Five destinations (Queue, Feed, Repo, Library, More) in one group centred with a `1fr auto 1fr` grid; the secondary pages (Settings for admins, Party, Mixer, Swipe, Export library `.zip`, Send to a DJ app) live in the `#navpanel` menu, fixed-positioned under the More button on a desktop and a bottom sheet on a phone. Both export controls (`#exportbtn` and the Library page's own) call `askExport()`, so the ZIP never starts without the confirmation dialog; `#djexportbtn` opens `#dlg-export` (see *Export dialog* below). The right-hand cluster this row used to carry is gone — the pages live only in the panel |
| Queue | `#playback-panel`, `#pf-queue-slot` | `showFullQueue()`, `togglePlaybackPanel()` | The slot is `display:none` while closed (a parked translated box still counted towards the overlay's scroll area) and `showFullQueue()` flips it to `flex` before the slide. Opened from the bar it is centred on the viewport, 18 px above the queue tab, so the sheet hangs from the control that opened it. Inside the slot the panel rides the slot down, so every close — footer button, `×`, Escape — goes through `showFullQueue(false)`, which waits for the slot's *transform* (not its shorter opacity transition) before unmounting the panel, and unmounts it without the bar's own exit animation |
| Download queue | `#view-queue` — `#qmain`, `#qsearch`, `#qreview` | `setQueueTab()`, `renderQueue()`, `renderReview()`, `setReviewTab()` | Tabs Queue / Search / Review; Review lists `st.stats.review` and `st.stats.failed` rows grouped by playlist. The candidate panel is `.revbox` — `.review` belongs to the status dot |
| Queue row states | `trackState()` in `web/index.html` | One line per track: `queued`, `resolving`, `downloading` (with a bar), `verifying`, `retry_wait` with its countdown, then the finished states. `verifying` is the gap between yt-dlp exiting 0 and ffprobe accepting the file — it used to read as `downloading 100%`, which is what a hung queue looks like, so it has its own label and no bar |
| Offline shell notice | `#shelfmode` (`web/index.html`), `.shelfbar` in `components/status.css` | `paintShelfMode()`, on `online`/`offline`. Shown only while the app itself is being served from the service worker's kept copy: a stale library presented as live is worse than an empty one |
| Volume | `#plvol` (header, behind `#plvolbtn`), `#plvolmore` + `#plvolmenu` (compact bar), `#pfvol` (fullscreen, its own centred row under the foot) | `paintVolume()`, `paintVolumeToggle()`, `placeVolumeMenu()`, `closeVolumeMenu()`, one `input` listener per fader | Desktop only: both paint from `playerAudio`, never from each other; `--vol` drives the accent fill, so at 100% the whole track is accent. The header's speaker sits immediately left of shuffle, opens a 72×18 fader into the space to its left (absolute, so the header never reflows) and stays open across sessions (`hl.compactVolume`). Its slot is also the header's flex spacer, so hiding the button on a phone does not move shuffle. The bar's `#plvolmore` is painted in the boot icon block beside `#plmute` — it lived inside a fader's `input` handler, so it booted as an empty 30×30 box — and sits immediately right of `#plmute` (`#player #plvolmore{order:2}`) and opens `#plvolmenu`, a `position:fixed` 40×124 glass pill whose rail is `position:absolute` at `left/top:50%` with `translate(-50%,-50%) rotate(-90deg)` (as a grid item its 88 px width became the grid track, which pushed the rail past the pill's right edge) 10 px above the button and centred on it (measured offset 0 at 390/430/1280; `color-mix` panel at 93% + a 14 px backdrop blur, hairline border at 62% of `--line`, a 3 px rail whose unfilled half is white at 12% and a 13 px white thumb that gains an accent halo only on hover/focus/drag): opacity + `translateY(6px) scale(.96)` over 140/200 ms, closed by an outside click, Escape or a resize, and hidden with the button under `.no-volume`. The opener is a two-wave speaker (`SVG.volumeUp`) while the mute flag keeps the one-wave `SVG.volume` and the crossed `SVG.mute`, so the three are told apart at a glance. The fullscreen fader is not part of the foot row: in the right group it made that group wider than its lane and it pushed into the queue, so it sits on a row of its own, centred under the foot, desktop only |
| Shared state | — | `playingId`, `playerAudio`, `libAll`, `libQueue`, `currentRows` | One source for both surfaces; never duplicate it |
| AutoDJ | `#pf-autodj` (fullscreen), `#autodj-tab` (compact bar) | `setAutoDj()`, `autoDjLoad()`, `autoDjPick()`, `autoDjEnsure()`, `autoDjMatch()`, `autoDjRate()` | Picks the next song by tempo and tempo-matches it. The flag is per member (`listening_playback`, `POST /api/listening/playback`), so it follows a phone from a desk. Scoring = chance − tempo distance (octave-folded), so a set drifts instead of looping one groove; the current song and the last 12 played are never picked. Three picks are kept queued ahead, so the queue panel and the prefetch agree. **There is one media element and there must stay one**: the incoming song gets a `playbackRate` nudge on `playerAudio` within ±6 % (`preservesPitch`), and the transition is an ordinary track change. An earlier version crossfaded in a second `<audio>`; a phone in a car then had two media elements fighting the platform session — the head unit dropped, the title flipped between the song and the app name, and audio did not come back. Never add a second playing element to this page. The compact tab is a status light above the bar's top-right (the queue grip's twin), `pointer-events:none`, absent while AutoDJ is off |
| Offline audio | `#dlg-offline` (user menu), `#shelfmode`, `web/sw.js` | `cachePlayedAudio()`, `keepArtwork()`, `offlineEvict()`, `offlineReconcile()`, `clearOfflineAudio()`, `setOfflineBudget()`, `paintOfflineMarks()` | Per device, not per account: budget in `hl.offlineBudget`, index (`{bytes, at, art}`) in `hl.offlineIndex`, bytes in the worker cache `hardlylistening-audio-v1`, covers in `hardlylistening-artwork-v1`, filled by `keepArtwork()` only for kept songs and read (never filled) by the worker — both names are written in `index.html` **and** `sw.js`, so change them together. A kept song keeps its cover: rows ask our `/api/art/<digest>` route (immutable, cache-first in the worker) and fall back to the provider URL only until the digest is known; `/api/track/<id>/artwork` is network-first with the kept copy as the offline answer. The row's `Kept` badge is `.kept-badge` in `components/library-row.css`, toggled by `paintOfflineMarks()` after every library paint; eviction frees the song's cover with it and shows a toast with the bytes freed; turning the budget off empties both caches |

### Party room deck bar

The party page is its own document with its own stylesheet — `web/party.html`, `web/party.js`,
`web/party.css` — so it does not use `web/styles/`. `#deckbar` is the room's one leased speaker:
`#deckstate` names the device for everyone in the room ("Host's device — playing"), while
`#deckactions` — play/pause `#decktoggle` and the room fader `#deckgain` — is members only, since
guests cannot play the catalog. `paintDeck()` is the only writer of that bar; the host applies the
gain to its own `audio.volume` and reports the position on the aux-heartbeat.

## The library's drawing layer

`web/js/library.js` turns the library's state into markup: the grouping, every renderer
(songs list / cards / grid, albums, artists, playlists), and the detail-view editors behind
them, including the two-step delete controls. It is a classic script loaded before the inline
one, so it reads `lib`, `libQueue`, `libView`, `libSort` and `detail` at call time rather than
holding them.

| Piece | Owner |
| --- | --- |
| Grouping | `groupTracks()`, `SORT_BY_VIEW` — one sort at a time, a direction per column |
| Songs views | `renderSongs()`, `songRow()`, `songCard()`, `songTile()`, `headCell()`, `songHead()` |
| Album and artist walls | `renderAlbums()`, `albumCard/Tile/Row()`, `renderArtists()`, `artistCard()` |
| Playlists | `playlistCard()`, `renderPlaylistsView()` |
| Detail views | `renderAlbumDetail()`, `renderPlaylistDetail()`, `renderArtistDetail()` and their editors |
| Delete controls | `deleteAlbum()`, `deleteTrack()`, `deletePlaylistFiles()` — two clicks, `armButton()` |

What is *not* in it: the library's state (`lib`, `libQueue`, `playingId`), the view/layout/
sort choices and their saved values, the density control, and the boot/poll that calls
`renderLibrary()`. Those stay in `index.html`; the next split moves them.

## Library filter row

The Filter button (`#filterbtn`) opens `#libfilters` — sort, sort direction, playlist, and
the Genres chip. The row is `display:none` until `html.filters-open`.

| Piece | Owner |
| --- | --- |
| Row layout and its selects | `web/styles/components/library-controls.css` |
| Open state and centring | the same file, `html.filters-open #view-library .libfilters` |
| Genres chip | created by `web/genres.js`, appended to `#libfilters`; **placement** in `library-controls.css`, the chip's own look in `genres.css` |
| Filtering | `Genres.matches()` in `renderLibrary()`; the popup markup and stylesheet are `web/genres.js` / `styles/components/genres.css` |

The chip opens a modal (`#genres-pop`) of taxonomy checkboxes. Selection is multi-select
with OR semantics, and it narrows `rows` before Albums / Artists / Songs / Playlists and
their drill-downs are built, so every section honours it. The filter is session state, not
a stored preference.

## Library save toggle

One square button per song, per album card and per album row: `+` when the item can be added,
`−` once it is in the library, and both keep the same 32 px box so nothing reflows.

| Piece | Owner |
| --- | --- |
| Button markup and states | `listeningSaveControl()`, `albumSaveControl()` in `web/index.html` |
| Click routing | `saveControlClick()` → `toggleListeningSave()` / `toggleAlbumSave()` |
| Styling | `.ghost.lsave` in `styles/components/library-row.css` (the row is the base surface) with card-specific geometry in `library-cards.css` |
| Player surfaces | `#pfsave` (fullscreen foot, the same 55 px box as the other foot glyphs) and `#plsave` (compact bar, in the empty band immediately left of the heart), both painted by `paintPlayerSave()`; box, `.on` green and the red `.armed` border live in `styles/components/player.css` |
| Removed-but-visible state | `.acard.removed`, `.songcard.removed`, `.songrow.removed`, `.covercell.removed` |
| Album card layout | `.songcard` grid in `styles/components/library-cards.css`; album cards override it to `"art art" / "title album" / "artist artist" / "votes save"` so the song count sits on the title line, top-right, instead of sharing the artist line with the taller save button (the count's `justify-self:end` keeps it flush with the button's right edge in the equal-column tiers) |

- **A song** keeps its check while saved; the first click turns it into a minus (armed for
  4 s, disarmed by Escape or a click elsewhere) and the second click removes it. An add flashes
  a drawing check for 900 ms before the minus appears.
- **An album** is saved when *any* of its playable songs is, so the minus removes what is there
  instead of topping up the missing songs. `toggleAlbumSave()` reads the album as rendered, so
  in the Library scope it removes — and puts back — exactly the saved songs the card shows.
- **A compact card keeps its actions until it is opened.**
  `html[data-cardstyle="compact"] .songcard:not(.open)` hides `.rowactions` together with the
  artist, album, votes, clock, the tempo badge and the download/play pair, so a closed compact
  card carries no save chip at any width — at 390 px as much as at 1280 px. Tapping the card
  opens it and the chip is there with the rest of the row.
- **On a card the tempo is read off the artwork.** `songRow()` draws the badge inside `.thumb`
  (`.songcard .thumb .bpm`, absolutely at `var(--chip)` from the top-left, 9 px in a 26×15
  chip, the same glass as the play-next control it shares the corner with) for both card
  styles, and the tag column skips it so the number is never printed twice. The table keeps
  its own badge in `.listtags` beside the source glyph: a row has no cover to stand it on.
- **A collapsed compact card's cover opens the card, not the album.** The page's cover
  handler steps aside while `libCardsCompact && libSongLayout === 'cards'` and the card is
  not `.open`, so the card's own toggle gets the click; once it is open the cover means the
  album it came from — the same door the table's rows have.
- **A removal stays on the page.** Removed track ids go into `libraryPeek`, which
  `scopedTracks()` folds into the Library scope: the card or row keeps rendering, dimmed and
  marked (`Removed from library · + to undo`), and its `+` adds the same songs back. The set is
  dropped by `clearLibraryPeek()` on a tab click, a library view change (`setView`) or a scope
  change (`setLibraryScope`), which is when a removed item is really gone. Navigation into an
  album or playlist detail does not clear it.

## Page header rows

Every top-level page in the app opens with the same row, `.libtop` from
`components/library-controls.css`: a 29 px `<h2>` (`.libtop h2`; the 21 px phone rule
above it is overtaken by this later one, which is why every page's title measures 29 px), an
optional `.libcount`, and
`.libtools` for actions aligned right.

| Page | Header |
| --- | --- |
| Library / Repository | `#libtitle` + `#libcount` + pin/search in `.libtools`; the title is also the detail page's name (Repository, Album, Playlist) |
| Feed | `#feedtop` — bare `<h2>Feed</h2>`; hidden with `#feedtabs` while a member profile page is open |
| Queue | bare `<h2>Queue</h2>` above `#qsegtabs`; the counts stay in the `#qstrip` chips below, where each belongs to its own chip |

The **Mixer** is its own document (`web/dj.html`, served at `/dj`) with its own inline
styles and no shared header. It opens with a `‹ Back` link to `/`, and its brand is a link
for the same reason: the mixer is a page you leave. Its desktop board is
`grid-template-columns:1fr 136px 1fr` — the middle rail has to hold the crossfader, the
three meters and the `A 50 · B 50` readout, which is why the rail is wider than it looks
like it needs to be (the readout is `white-space:nowrap` on purpose).

## Playlist cards

A playlist is a rectangle of the records inside it. `renderPlaylistsView()` in
`web/index.html` draws a `.plgrid` of `.plcard`s; each card holds `playlistCollage(arts,
title)` — a mosaic that fills the card's width at 4:3 — then the title, `done/total`, owner
and the ZIP / save controls.

| Piece | Owner |
| --- | --- |
| Grid and card layout | `.plgrid` / `.plcard` in `styles/components/library-cards.css` |
| Mosaic steps | `playlistCollage()` in `web/index.html` and the `.collage` / `.collage.cN` rules in `library-cards.css` |
| Dynamic cards | `dynCard()` in `web/preferences.js` — same markup, so they follow the mosaic too |

- The mosaic steps in whole rectangles: 12 (4×3), 9 (3×3), 8 (4×2), 6 (3×2), 4, 3, 2, 1
  covers. Counts are chosen from the largest step that fits, so no step leaves a blank cell.
- Covers are de-duplicated first: a playlist with one album repeated still spends its tiles
  on distinct art, and a playlist with no art shows its initials rather than a black box.
- **Favorites** (yours) is added in the Library scope; **Everyone's Favorites** is the
  Repository's list and is added in the shared scope only. Neither is a stored playlist.

## Repository sections

The shared Repository is three sections in one view: **Browse** (the pool), **Releases**
(what the followed artists have put out) and **Subscribed** (the follows themselves). Feed
is Activity only — what people did — because a release is catalogue, not activity.

| Piece | Owner |
| --- | --- |
| Section row `#repotabs` | `setRepoTab()`, `paintRepoTab()` in `web/js/repo-discovery.js`; it is a plain `.seg`, styled with the other library controls (`#repotabs` in `components/library-controls.css`); state in `hl.repoTab` |
| Panes `#repopane-browse` / `-releases` / `-subscribed` | `paintRepoTab()` hides the two that are not on screen; `renderLibrary()` paints Browse, `renderFeed()` Releases, `renderSubscriptions()` Subscribed |
| Heading, song count, import target | `paintRepoTab()` sets `#libtitle` to the section and hides `#libcount` and `#dropzone` outside Browse |
| The personal Library | one list, so the row and the two extra panes do not exist there (`activeRepoTab()` answers `browse`) |

The new-releases badge (`#feedcount`) rides on the Repo tab, not on Feed.

### Release groups — the fold

Releases is one `.feedgroup` per artist: a head row (disc, name, new-count pill, latest
date, subscribe button) and, under it, either the open body of `.feedrel` cards or the
collapsed `peek` — the first three cards cut at the waist behind a strip.

| Piece | Owner |
| --- | --- |
| Head, body, peek | `feedGroupRow()` in `web/js/repo-discovery.js`; `openFeedArtists` holds which groups are open |
| The fold `.feedgfold` | always on screen at the head's edge, 30 px, rotates 180° while open; `toggleGroup()` keeps its `aria-expanded`, `aria-label` ("Show 4 releases from …") and title in step with the head's |
| The strip `.feedgmore` | "Show N releases" — the whole strip is the tap target and opens the group |
| The rest of the row | portrait and name open the artist's catalogue page; `data-follow-url` subscribes; on ≤640 px the head's plain date is hidden (`.feedgmeta > span:not(.pill)`) because it squeezed the name |

Both the fold and the strip are real buttons, so a keyboard reaches the fold and a phone
gets a visible target instead of a bare glyph under a faded preview.

### Subscribed — one card per follow

`renderSubscriptions()` paints `.subrow` cards: a 40 px disc, the identity column (name,
then a meta line of provider, last check, interval, and the `new`/`paused` pills), and a
control column (sync, Pause/Resume, Unsubscribe). It is a grid, so a long name ellipses in
its own lane instead of pushing the controls off the row; on ≤640 px the controls drop to
a full-width line below the identity.

`data-admin-only` belongs on the buttons, never on their container: the attribute is
`display:revert !important`, which flattens a flex or grid parent into a block. The
subscribe form `.followbox` is admin-only through `html.is-admin .followbox{display:flex}`
for the same reason.

## Feed — Activity and Popular

One play log, two readings. **Activity** is what people did: plays, additions, playlist edits,
joins and renames — one collapsed card per member per day, five entries inside it, plus a
boost pinned at the top for 24 hours. **Popular** is the chart built from the same log across
every member, ranked by plays. Both sections are chosen with the Repository's own row
(`nav.seg`), and the chart's window with it again; there is no second selector style.

The Feed opens with the Library page's own header row — `.libtop` with the title "Feed"
(`#feedtop`), same heading and spacing as `#libtitle` — then the section row. A member
profile is a sub-page of the Feed, so it hides both while it is open and puts them back on
close (`memberShowView()` / `memberClosePage()`).

| Piece | Owner |
| --- | --- |
| Page header `#feedtop` | A bare `.libtop` (`<h2>Feed</h2>`); no count, because each section counts its own rows |
| Section row `#feedtabs` | Two `nav.seg` buttons, `#ftab-activity` / `#ftab-popular`; `setFeedTab()` and `paintFeedTab()` in `web/js/social-feed.js`, state in `hl.feedTab`. `openFeedTab()` is what the view switch calls, so the Feed comes back on the section it was left on |
| Chart window `#feedperiods` | The same row again, `data-period` on each of its five buttons; `setPopularPeriod()`, `paintChartPeriod()`, state in `hl.chartPeriod`. Calendar windows, not rolling ones: today from local midnight, this week from Monday, this month from the 1st, this year from January 1st — plus **All time**, which has no start at all (`stats.calendar_window`). Day/week/month/year are calendar questions because a chart is read that way; the profile's own numbers stay rolling |
| The chart itself | `GET /api/popular?period=day|week|month|year|all&limit=` → `stats.popular_tracks()`, the one read over `listen_events` with no member filter; `loadPopular()` paints `#popularlist`, `popularRow()` is one ranked row (cover, title, `n×`), `popularArt()` prefers the library's stored digest |
| One answer at a time | `popularAsked` — a later request wins, so a slow answer for the window you left never repaints the one you are on |
| Day card (one per member per day) | `actDayCard()`, `actDayLabel()`, `actDaySummary()`, `actGroup()`, `actSongLine()` in `web/js/social-feed.js`; `ACT_REVEAL` is the 5-entry reveal |
| Boost pin | `actCard()` — the expiry countdown is computed from `expires_at`, and the list re-reads itself when the nearest one lapses |
| List and its poll | `loadActivity()`; `#activitylist` is repainted, `activityExpiryTimer` is the boost's re-read |
| Open cards survive a repaint | `openDays` (a `Set` of `member|day`) is re-applied by `bindActivityCards()` |
| Member links, card toggles, "Show n more" | `bindActivityCards()`; a member name opens `openMember()`, which reveals `#memberpage` inside `#view-feed` (nav key `m`, `memberNavState()`/`memberNavPush()`) — a page, not a dialog, so the Back button and the tab bar behave normally |
| Listening numbers in the member dialog | `statsBody()`, `statsList()`, `statsSongRow()`, `setStatsPeriod()`; `mdStats` holds the open member, period and payload; two document listeners make a stats row a play target |
| Boosting | `boostTrack()` → `POST /api/boost`, then the list re-reads |

## Artist catalogue page

The release page (`#feedartistpage`, inside `#repopane-releases`) is a catalogue, not a
"latest releases" list: every album the provider lists, and the songs behind them. Albums and Songs are two
tabs behind the Library's icon switch (`#fapTabs`), the layout switch (`#fapLayout`) applies
to both, and both sit on one row above the pane.

| Piece | Owner |
| --- | --- |
| Page shell, tabs, panes, filter | `openArtistPage()`, `artistTabsHtml()`, `setArtistTab()`, `paintArtistPanes()` in `web/js/repo-discovery.js`; tab state in `hl.artistTab` |
| Album pane (cards / covers / rows) | `artistAlbumsHtml()`, `artistAlbumCard/Tile/Row()`, `renderedArtistAlbums` |
| Song pane (cards / covers / rows) | `paintArtistSongs()`, `artistSongsHtml()`; layout state in `hl.artistLayout` |
| Song count and paging | `#fap-song-meter`, `#fap-foot`, `paintArtistFooter()`, `loadMoreArtistSongs()` (Songs tab only) |
| Filter | `#fapsearch` in `.fahead`, right-aligned on the artist's name row; filters albums by title/date and songs by title/album/artist |
| Footer layout | `#fap-song-meter` and `.fafoot` in `styles/components/feed.css` |
| Data | `GET /api/catalogue?watch=<id>&offset=<n>` → `repo/catalogue.catalogue()` |

Storage is per follow, in two tables: `artist_releases` and `artist_tracks` (`db.py`). The
provider is read again after `CATALOGUE_TTL` (6 h); between reads the page is served from
the store, so opening it twice costs nothing. One page is `PAGE` = 60 songs, A→Z by album
then track number; "Load more" asks for the next offset and appends. Pages are cached in
`catalogueCache` as `watch:offset`.

Apple's artist-level song lookup returns at most 200 tracks and ignores `offset`, so an
album that comes back short of its declared `trackCount` is read on its own through
`provider.tracks()`. Those album rows are reused for `TRACK_TTL` (7 days) and each refresh
is bounded by `ALBUM_FETCH_CAP` albums. DJ mixes are the exception: Apple returns the
collection row and no track rows at all, so they stay counted but unlisted.

## Settings — the trash

A delete moves the file into the library's `.trash/` folder instead of erasing it (HL-306). The
Settings dialog is the only surface that says how much is in there and the only place it can be
thrown away.

| Piece | Owner |
| --- | --- |
| `#trash-status` in the Settings dialog | `fillSettings()` paints `stats.trash_count` / `stats.trash_bytes` as `N files · X MB`, or `Empty`. It is read when the dialog opens, so it is a snapshot, not a live counter |
| Empty trash | `emptyTrash()` → `POST /api/trash/empty` behind a `confirm()` that says this is the delete that cannot be undone; it refreshes and repaints the field. Erasing frees the bytes; the disk ceiling will do it on its own, oldest-first and one log line per file |
| Restoring instead | "Show deleted songs" (`openDeleted()`) restores the tombstone, and that restore now renames the file straight out of the trash — no download, even for an import whose source is gone |
| Who deleted what | the same dialog, `#deleted-audit` → `loadDeletionAudit()` → `GET /api/deletions` (admin only). One line per delete: the name, what was deleted, how many tracks/files/bytes, and when. The list is not trimmed like the log, so it answers "who deleted that?" long after the tombstones are restored |

## Export dialog — the download and the drive (HL-228)

One dialog, because both hand-offs answer the same question first: **which scope**. `#dlg-export`
("Send to a DJ app", opened from the More menu's `#djexportbtn`) hands the DJ app a playlist file;
the drive section writes the audio onto a removable drive. `docs/usb-export.md` is the server half.

| Piece | Owner |
| --- | --- |
| `#dlg-export` markup | `web/index.html`, one `<form method="dialog">`; the CSS is the `#dlg-export` block at the end of `components/dialogs.css` |
| Scope | `#expscope`, filled on **open** by `openDjExport()` from `scopedPlaylists()` (never at parse time, so it is empty in the markup). Both hand-offs read it |
| Download | `#expgo` + `refreshExportLink()` → `/api/library/export`; it is an `<a download>` because the browser has to be handed a real link, so it carries `a.btn` styling scoped to this dialog rather than looking like a bare underlined word |
| Drive plan | `#usbpath`, `#usbcheck` → `POST /api/library/usb/plan` → `usbPaintPlan()` into `#usbplan` (a `role="status"` grid of plain paragraphs) |
| Drive copy | `#usbgo` → `POST /api/library/usb/start` → `usbPaintJob()` + `usbPoll()` on `GET /api/library/usb` every 800 ms until the job ends. Progress comes from the job row, never from this tab's own guess |
| `#usbgo` visibility | shown only by a plan that `fits` and has tracks; hidden and disabled while a copy runs, shown again when it ends |

Two rules, both about not lying:

* **Every control inside `#dlg-export` is `type="button"`.** The dialog is a `<form method="dialog">`,
  so a bare `<button>` submits it and closes it — Check would dismiss the dialog it was asked in.
* **A plan describes one scope of one folder, so it is dropped when either changes** (`#expscope`
  `change`, `#usbpath` `input`). Leaving a checked plan on screen while the fields describe
  different work is the failure this prevents. A refused folder is the plan's own answer (200 with
  `blocked`), not an error toast: the reason belongs beside the field.

## CSS ownership

The main app loads exactly one stylesheet, `web/styles/app.css`, which imports one file per
surface under `@layer tokens, base, layout, components, utilities`. `polish.css`, the old
`index.html` inline `<style>` block, and the standalone `preferences.css` / `genres.css` /
`lyrics.css` sheets are gone; their rules live in the owning component. The full contract —
layer rules, what belongs in `utilities.css`, and why a rule cannot be appended to another
file — is `docs/frontend-css.md`.

Where a component lives:

| Surface | File |
| --- | --- |
| Header, brand, spectrum slot, volume widget, tabs, More menu | `components/app-chrome.css` |
| Library header, filter row, sort/scope/layout/density controls | `components/library-controls.css` |
| List rows (`.songtable`, `.songrow`, `.st`, `.sartist`, `.sdur`, `.send`) | `components/library-row.css`; the tempo badges (`.listtags .bpm` for rows, `.songcard .thumb .bpm` for cards) live here too |
| Cards, covers, playlist tiles, folders | `components/library-cards.css` |
| Compact bar and fullscreen player | `components/player.css` |
| Playback queue sheet | `components/playback-queue.css` |
| Download queue / search / review | `components/queue-view.css` |
| Feed, the release list and the artist catalogue | `components/feed.css`; the Feed's two selector rows (`#feedtabs`, `#feedperiods`) only take their margins there — the look is `nav.seg`'s |
| Member profile page (`#memberpage`) | `components/member.css` |
| Genres dialog | `components/genres.css` |
| Lyrics reading, its tool row and word timing | `components/lyrics.css` (`#lyrics-overlay` lives inside `.pf-disc-shell`, so its width is the disc column, not the viewport); the editor dialog `#dlg-lyrics-edit` is in `components/dialogs.css` |

- **One authoritative rule per selector within its owner.** Duplicate selectors inside one
  file were folded where that was order-safe; what remains are deliberate state and
  breakpoint layers (base rule, then `.playing`/`.open`, then `@media`), so an element is
  changed by editing its rule, not by appending a third copy at the end.
- **The lyrics reading opens from `#pflyrics`, not from the bar.** The full player's foot
  carries the Lyrics button (row: Cue, equalizer | queue | save, heart, Lyrics — the words live inside
  `.pf-end`, because a direct child of `.pf-foot` wraps to a second grid row). The
  compact bar keeps a hidden `#pllyrics` only because `$("#pflyrics").onclick` forwards to it
  and `lyrics.js` binds it; the bar is a fixed 402×112 art-directed layer with no free slot
  beside the transport, so a second visible entry point there would be clipped. `#pllyrics`
  must keep existing as an element — removing it breaks the forward.
- **The lyrics tool row is one shape at every width.** Its container is the disc column, so a
  viewport breakpoint would describe the wrong width; the status line takes a full row and the
  buttons sit under it, on a glass backing so 10 px type stays readable over the disc's own
  labels. The close button is pushed to the strip's right edge.
- **The playing row is one border.** `.songrow.playing:not(.songcard)` owns a real
  `border` — 1 px accent at 60% and a 3 px accent right edge — and switches its own separator
  off. It used to fake the edge with an inset `box-shadow` over a pseudo-element separator,
  which is why a neighbouring separator could appear to cross it. Do not reintroduce a
  shadow border or a second drawing layer.
- **A queue row has no `×`.** The row content is one `.pq-body` panel and `.pq-remove` sits
  behind its right edge, revealed by a left swipe (`--pq-shift` drives both the body's slide
  and the panel's `translateX(calc(100% + var(--pq-shift)))`). The clear button in the drawer
  header is the only other way to empty the queue, and the ⋯ menu keeps Remove for a keyboard.
  Both gestures around a row are tracked on the window, not on the row or the list: a swipe
  or a drop that ends outside `#playback-items` otherwise leaves the row stuck. Reordering is
  vertical only (`dy > 3 && dy >= dx`) — the first few horizontal pixels used to be claimed
  by the drag, which meant a swipe could never begin.
- **A detail page has no toolbar band.** `#view-library.detail-open .liblayout` hides the
  row only when it holds neither `#gridstylebtn` nor `#playlist-edit-btn` (the `:has()`
  guard); otherwise the empty 40 px row plus its 14 px margin sat between the section nav and
  Back. 12 px — the nav's own margin — is the intended gap.
- **The fullscreen art wash is fullscreen only.** `#player-full.has-art::before/::after`
  carry the colour wash; the compact bar keeps its glass `background`/`box-shadow` and nothing
  else. The `::after` shine had still been shared with `#player`, even after the wash itself
  was scoped; that painted the semi-transparent rectangle across and beyond the compact bar.
  The bar's *fill* still matches the fullscreen surface's tone: `body #player.on.has-art` is
  the last surface rule in the sheet and carries `#101014`→`#0c0c11` at 72 % alpha. Edit that
  rule, not the base gradient above it — the bar always has art, so the base block never shows.
- **The saved theme is a stylesheet, not a fetch.** `/theme.css` (`Handler.theme_css`) answers
  `:root:root{--user-base;--user-accent;--user-trim}` for the session cookie and the stock
  three otherwise; it is linked in `<head>` before any script, so a cold load paints the
  member's colours first. It writes only the inputs — `styles/tokens.css` mixes `--panel`,
  `--panel2` and `--line` from them — and it reads the session with `Security.peek()`, which
  neither raises nor counts against the verification budget. The `fetch` in the inline script
  stays as the in-session fallback; a second source of the *same* three values is fine, a
  second source of *different* ones is not.
- The fullscreen foot is the worked example of token sizing. `#player-full .pf-foot` controls
  are sized from tokens on `#player-full`:

  ```css
  #player-full{--pf-foot-size:55px;--pf-foot-glyph:30px;--pf-wave-size:44px;--pf-wave-glyph:24px}
  ```

  One rule sizes the three glyph buttons (`#pflike`, `#pfqueue`, `#pfeq`): a 55 px box from
  `--pf-foot-size` with a 27 px EQ drawing; the style toggle still reads
  `--pf-wave-size`/`--pf-wave-glyph`. The row is one flat, centred line in this order —
  Cue, EQ, queue, save, heart — with the heart last on the right. Cue has its own rule because
  `.iconbtn` pins `width:34px`: it is a word pill (`height:var(--pf-foot-size)`, `width:auto`,
  `padding:0 17px`, 12 px radius), never an icon. The heart is 1.15× the other glyphs
  (`font-size:calc(var(--pf-foot-glyph)*1.15)`), so like reads as the row's anchor rather than
  its equal. The volume fader is 180 px: it has a centred row of its own under the foot, so the
  width is the fader's own business — the older 90 px existed because a 130 px fader's hit area
  reached over the heart's corner in the shared row, and the test now checks overlap rather than
  left/right order.
- **The fullscreen play button carries the track's progress as a ring.** `#player-full
  #pfplay::after` is a conic gradient — `--accent` to `calc(var(--pf-progress) * 1turn)`, the
  remainder the same accent at 22% — cut to a 3 px band by a radial mask, `inset:-5px`,
  `pointer-events:none`, so the ring never steals a press. `--pf-progress` (0–1) is set by
  `paintSeekFill()` from the same `#pfseek` value as the fullscreen seek line, so the two can
  never disagree.
- Compact bar rules are the `body #player, body #player.on` section of `components/player.css`,
  the authoritative block for that surface. Inside one file the later block still wins, so
  edit the block that owns the element rather than adding another.
- **One bar at every width.** The phone composition is the bar: 112 px tall, cover flush in
  the top-left corner, title and artist centred above the transport, transport centred on the
  bar, seek across the foot with a clock at each end, heart top-right, expand bottom-right.
  The only width-dependent rule is the shell's inset and max width (`min(1072px, 100vw - 48px)`,
  centred, 16 px off the bottom) plus the save slot, which takes the empty band left of the
  heart/expand column. The header's volume toggle is separate from the bar's own speaker
  (`#plvolmore`). Do not reintroduce a desktop grid: it changed the arrangement at
  761 px, which read as a different player rather than the same one wider.
- Anything anchored to the bar's top edge moves with its height: the queue sheet's
  `bottom` and the toast's are set from 112 px + the desktop gap, not from an older 88 px bar.
  The queue sheet adds the tab's own 18 px on top of that (146 px), so the tab stays clickable.
- **The header sits above the sticky tab bar** (`header{z-index:26}` vs `.tabs{z-index:25}`).
  The user menu hangs out of the header onto that row, and a child cannot paint over a
  sibling of its ancestor's stacking context — at 1 the bar covered the menu's first rows and
  swallowed their clicks.
- **The profile menu hangs off the chip, not the bar.** `#who` and `#usermenu` share
  `.userwrap{position:relative}`, and `placeUserMenu()` picks the edge: a chip on the left
  half opens the panel rightward from its left edge, a chip on the right half opens it
  leftward under its right edge, and either placement is clamped inside the viewport — a
  chip that wraps to the right end of a narrow bar would otherwise push the panel off
  screen. Do not go back to a fixed `right:16px` on `.usermenu`: on a phone the chip wraps
  to the left of its own row, and the panel landed nowhere near it.
- The compact bar's right column is one stack: the heart (`right:9px`) and the expand
  button (`right:9px`) centre 26 px in from the bar's right edge, and the save slot sits
  under the heart on the time line (`right:11.5px;bottom:13px`, 29 px wide) on that same
  centre line; `#plmeta` insets its right side by 90 px so a long title never runs under the
  heart.
- **The second line ranks the artist over the release.** The artist never shrinks or
  ellipsises while there is any room left; the release takes whatever is left and scrolls
  inside its own box when that is too short for it (`fitMarquee`). Only a name wider than
  the whole line scrolls itself, and then the row switches to `#plsub.artist-only`
  (`justify-content:flex-start`) so a centred overflow cannot clip the start of the name.
  `paintPlayerSub` measures the bar's own width, not the release's, for its one retry —
  the release can legitimately be squeezed to 0 px, and testing it would retry forever.
- The compact wave's box is CSS in the compact bar block (`49 px`, `bottom:0`); only the
  drawing is in `index.html`. There is no slider track or thumb any more: `#plseek` is drawn
  as nothing over the same rectangle, so the canvas' own base is the only line — the clocks
  and the flat fallback line rest on it too. Moving the canvas means re-checking
  `TRACK_Y = h - 3` in the `seek` branch (3 px line, standing on the bottom edge), not the
  old `h - 27`.
- **The transport is one button wider since the volume button.** `body #player.on .ptrans`
  carries `margin:0 -6px 0 0` to pull the row back onto the bar's centre line: the play
  button measured 198 vs 195 at 390, 218 vs 215 at 430 and 643 vs 640 at 1280. Remove it and
  the whole transport sits 3 px right of centre.
- The header's `#hdr-wave` is the only surface that reads a real `AnalyserNode`. Keep that
  gated to a mouse-driven desktop (width **and** pointer **and** no touch points): it is the
  one thing that routes the `<audio>` element through Web Audio, and a frozen context on a
  locked phone is what stops the music. The context is also resumed on play, on returning to
  the page and on every tick, so a suspended graph can never outlive its own output.

## Adding or changing a button

1. Find the owning component file from the table in `docs/frontend-css.md`, then grep the
   selector in that file. If it appears more than once there, the later block wins —
   consolidate instead of adding another.
2. Size it from an existing token or add one on the owning component, never a bare pixel
   override appended later.
3. Keep the element's id and click handler. Other code looks elements up by id.
4. Run `node tests/visual/snap.cjs` and `node tests/visual/library.cjs`, and look at every
   changed screenshot. A diff outside the element you touched is a regression, not a
   rounding error.

## Visual baselines

`tests/visual/snap.cjs` renders the player from the real markup, CSS and paint functions
against a stub API, freezes animation, and compares screenshots for three states
(`compact`, `fullscreen`, `queue`) at 390×844 and 1280×800. The comparing states carry no
saved song, so the add/remove control is absent from them; check it by hand.

`tests/visual/library.cjs` does the same for the surfaces the CSS refactor touches hardest:
`list`, `cards` and `list-playing` at 1280×800, plus `list` and `cards` at 390×844. The
playing state is what catches the row's border, its right edge, and a separator painting
over it.

```bash
node tests/visual/snap.cjs            # compare against tests/visual/baseline/
node tests/visual/snap.cjs --update   # accept a deliberate change
node tests/visual/snap.cjs --selfcheck # prove the baselines are byte-stable
node tests/visual/library.cjs         # compare against tests/visual/library/
```

Changed states write `<state>.actual.png` next to the baseline for inspection.

## Known debt

- **`index.html` is still a large inline script.** Markup, styling and behaviour can be
  reviewed independently now that the CSS has left, and the release/follow/artist half has
  moved to `web/js/repo-discovery.js`, Activity to `web/js/social-feed.js`, and the library's
  drawing layer to `web/js/library.js`; the rest is one block. A further split is a separate
  change: a classic script cannot use a `const` from a later script at load time, so
  anything that needs `SVG` at load has to keep running from the inline script, after it.
- **Semantic class names.** `.st`, `.sartist`, `.salbum`, `.sdur`, `.send` and `.idx` are
  generated and queried by that inline script. Renaming them is a separate pass: search the
  CSS, the script's template strings and the tests before touching one.
- **`#pllike`/`#pflike`.** The compact bar and the fullscreen foot both style them; they are
  meant to be one heart state, so change them together (both rules are in
  `components/player.css`).