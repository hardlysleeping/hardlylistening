# UI components — HardlyListening

Read this before changing the player, the queue, or anything they share.

## Where the code lives

| File | Size | Owns |
| --- | --- | --- |
| `web/index.html` | ~332 KB | All markup and the remaining inline script (state, layouts, sorting, playback). No component CSS |
| `web/styles/app.css` | small | The one stylesheet entry point: layer order and imports only |
| `web/styles/*.css` | — | `tokens`, `base`, `layout`, `motion`, `utilities` (see `docs/frontend-css.md`) |
| `web/styles/components/*.css` | — | One file per surface; the ownership contract is `docs/frontend-css.md` |
| `web/preferences.js` | small | Likes/pins wrappers around the cards' controls |
| `web/genres.js`, `web/lyrics.js`, `web/lifecycle.js` | small | Genre chips, lyrics, reload-on-new-version |
| `web/js/repo-discovery.js` | ~30 KB | Releases, the follows and the artist/release pages (`JS-030`). A plain classic script loaded before the inline one; every name stays global, so nothing else had to change |
| `web/js/social-feed.js` | ~12 KB | Feed = Activity: the day cards, the boost pin, the member profile dialog (`JS-031`). Same shape as the file above |
| `web/js/library.js` | ~60 KB | The library's drawing layer: grouping, the songs/albums/artists renderers and the detail-view editors (`JS-032`). Reads the library's state, which is still inline |

There is no build step: the files above are what the server serves. A change is live on
reload; there is nothing to compile. Components read the entry point's imports; never add a
second stylesheet to `index.html`.

## Player components

All of these have their markup and behaviour in `web/index.html`; their styling is in
`web/styles/components/player.css` (queue: `playback-queue.css`).

| Component | Markup | Behaviour | Notes |
| --- | --- | --- | --- |
| Compact bar | `#player` | `paintPlayer`, `syncPlayButton`, `#pllike`, `#plfull`, `#plqueue` | 112 px tall at every width, drawn to `compact-bar-layers.svg` (402×112); desktop only widens the shell and adds the save slot (the volume toggle lives in the header) |
| Fullscreen overlay | `#player-full` | `openFullPlayer()`, `closeFullPlayer()`, `paintFullPlayer()` | Persistent element, toggled by `.on` + `body.fullplayer`. Sized to fit the viewport: the record's stage is `min(86vw,520px,54vh,calc(100vh - 360px))`, so the open player never scrolls (down to 1024×600 and 360×640) |
| Pull / swipe | inside the overlay | `setFullPull()`, `commitFullPull()`, `retreatFullPull()` | The gesture layer the overlay transition uses. Touch and pen only — a mouse drag on the bar or the pane is a slip, and the bar's own buttons open it on desktop. The pane's pull timers check `fullPullSeq`, so a dismiss still animating out cannot close a pane that was reopened meanwhile |
| Disc | `#pf-disc`, `#pfart` | `paintPfDisc()`, `pfScratchPrepare/Release`, `setCoverArt()` | Rotation is a RAF loop; scratching reads a decoded copy. Double-tapping the cover swaps the record for a full-size sleeve (`#player-full.artmode`); the record is the default and returns when the player closes |
| Visualizer | `#player .plwave-seek` (compact), `#hdr-wave` (header) | envelope builder, `spectrum()`, draw loop | Two different readings. The compact wave is the track's own envelope, drawn on its seek line. The header is a **live spectrum**, not the envelope, drawn as one continuous line rather than bars: log-spaced bands (~35 Hz–16 kHz, at least one FFT bin each) read from an `AnalyserNode` fed by the audio element, cosine-interpolated at five sub-samples per band, ending on the resting line at both edges, and mirrored about the canvas centre as one filled shape with a stroked outline. Three things make it move: a rolling headroom (each frame is normalised against its own loudest band, which falls back 0.9 per frame and is floored at 0.4, so a quiet verse and a chorus both use the height), a 1.35 expansion applied after that normalisation so the band beside a peak drops back toward the centre, and a fast per-band attack/release (0.92 / 0.42) under an `AnalyserNode` smoothing of 0.5. An `1/f` tilt keeps the hats off the floor. It redraws at ~30 fps while that canvas is on screen, against ~11 fps for the envelope. That graph is built only on a mouse-driven desktop — `min-width:900px` **and** `(hover:hover) and (pointer:fine)` **and** `maxTouchPoints === 0` — and only while that canvas is drawn. Width alone is not enough: a phone in landscape is wider than 900 px, and a routed element dies when Safari freezes its context on lock, so the touch check is what keeps background playback intact. No on-screen knobs and no fullscreen style button. `cfg` is still read from `hl.wave.v1` (`enabled`, `bars`, `level`, `smooth`, `mirror`), with `bars` × 1.5 as the phone pitch (96). The compact count is width-proportional — `round(pitch × w / 182)`, clamped 12–512, so 95 at 390 px and 469 at the widest 890 px canvas. The canvas is 65 px tall (72 less a tenth), flush with the bar's bottom edge, so bars stand on the bottom margin and rise through the scrub, which sits 27 px up. The envelope is RMS (not peak) over 512 points, normalised to the track's loudest passage and expanded by `^1.4`; there is no per-track percentile stretch, so quiet and loud sections keep their relative heights |
| Top row | `#appnav` — `.navmain`, `#navquick`, `#navpanel` | `paintNavQuick()`, `positionMoreMenu()`, `askExport()` | Five destinations (Queue, Feed, Repo, Library, More) in one group centred with a `1fr auto 1fr` grid; the secondary pages (Settings for admins, Party, Mixer, Swipe, Export library `.zip`) live in the `#navpanel` menu, fixed-positioned under the More button on a desktop and a bottom sheet on a phone. `#navquick` repeats those pages right-aligned in the row on the Library tab only, and only from 1180 px. Both export controls call `askExport()`, so the ZIP never starts without the confirmation dialog |
| Queue | `#playback-panel`, `#pf-queue-slot` | `showFullQueue()`, `togglePlaybackPanel()` | The slot is `display:none` while closed (a parked translated box still counted towards the overlay's scroll area) and `showFullQueue()` flips it to `flex` before the slide. Opened from the bar it is centred on the viewport, 18 px above the queue tab, so the sheet hangs from the control that opened it. Inside the slot the panel rides the slot down, so every close — footer button, `×`, Escape — goes through `showFullQueue(false)`, which waits for the slot's *transform* (not its shorter opacity transition) before unmounting the panel, and unmounts it without the bar's own exit animation |
| Download queue | `#view-queue` — `#qmain`, `#qsearch`, `#qreview` | `setQueueTab()`, `renderQueue()`, `renderReview()`, `setReviewTab()` | Tabs Queue / Search / Review; Review lists `st.stats.review` and `st.stats.failed` rows grouped by playlist. The candidate panel is `.revbox` — `.review` belongs to the status dot |
| Queue row states | `trackState()` in `web/index.html` | One line per track: `queued`, `resolving`, `downloading` (with a bar), `verifying`, `retry_wait` with its countdown, then the finished states. `verifying` is the gap between yt-dlp exiting 0 and ffprobe accepting the file — it used to read as `downloading 100%`, which is what a hung queue looks like, so it has its own label and no bar |
| Offline shell notice | `#shelfmode` (`web/index.html`), `.shelfbar` in `components/status.css` | `paintShelfMode()`, on `online`/`offline`. Shown only while the app itself is being served from the service worker's kept copy: a stale library presented as live is worse than an empty one |
| Volume | `#plvol` (header, behind `#plvolbtn`), `#pfvol` (fullscreen foot) | `paintVolume()`, `paintVolumeToggle()`, one `input` listener per fader | Desktop only: both paint from `playerAudio`, never from each other; `--vol` drives the accent fill, so at 100% the whole track is accent. The header's speaker sits immediately left of shuffle, opens a 72×18 fader into the space to its left (absolute, so the header never reflows) and stays open across sessions (`hl.compactVolume`). Its slot is also the header's flex spacer, so hiding the button on a phone does not move shuffle |
| Shared state | — | `playingId`, `playerAudio`, `libAll`, `libQueue`, `currentRows` | One source for both surfaces; never duplicate it |
| Offline audio | `#dlg-offline` (user menu), `web/sw.js` | `cachePlayedAudio()`, `offlineEvict()`, `offlineReconcile()`, `clearOfflineAudio()`, `setOfflineBudget()` | Per device, not per account: budget in `hl.offlineBudget`, index in `hl.offlineIndex`, bytes in the worker cache `hardlylistening-audio-v1` — that name is written in both `index.html` and `sw.js`, so change them together |

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
| Player surfaces | `#pfsave` (fullscreen foot, 44 px, 12 px radius) and `#plsave` (compact bar, 34 px, 9 px radius), both painted by `paintPlayerSave()`; box, `.on` green and the red `.armed` border live in `styles/components/player.css` |
| Removed-but-visible state | `.acard.removed`, `.songcard.removed`, `.songrow.removed`, `.covercell.removed` |
| Album card layout | `.songcard` grid in `styles/components/library-cards.css`; album cards override it to `"art art" / "title album" / "artist artist" / "votes save"` so the song count sits on the title line, top-right, instead of sharing the artist line with the taller save button (the count's `justify-self:end` keeps it flush with the button's right edge in the equal-column tiers) |

- **A song** keeps its check while saved; the first click turns it into a minus (armed for
  4 s, disarmed by Escape or a click elsewhere) and the second click removes it. An add flashes
  a drawing check for 900 ms before the minus appears.
- **An album** is saved when *any* of its playable songs is, so the minus removes what is there
  instead of topping up the missing songs. `toggleAlbumSave()` reads the album as rendered, so
  in the Library scope it removes — and puts back — exactly the saved songs the card shows.
- **A removal stays on the page.** Removed track ids go into `libraryPeek`, which
  `scopedTracks()` folds into the Library scope: the card or row keeps rendering, dimmed and
  marked (`Removed from library · + to undo`), and its `+` adds the same songs back. The set is
  dropped by `clearLibraryPeek()` on a tab click, a library view change (`setView`) or a scope
  change (`setLibraryScope`), which is when a removed item is really gone. Navigation into an
  album or playlist detail does not clear it.

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

## Feed — Activity

What people did: plays, additions, playlist edits, joins and renames. One collapsed card per
member per day, five entries inside it, plus a boost pinned at the top for 24 hours.

| Piece | Owner |
| --- | --- |
| Day card (one per member per day) | `actDayCard()`, `actDayLabel()`, `actDaySummary()`, `actGroup()`, `actSongLine()` in `web/js/social-feed.js`; `ACT_REVEAL` is the 5-entry reveal |
| Boost pin | `actCard()` — the expiry countdown is computed from `expires_at`, and the list re-reads itself when the nearest one lapses |
| List and its poll | `loadActivity()`; `#activitylist` is repainted, `activityExpiryTimer` is the boost's re-read |
| Open cards survive a repaint | `openDays` (a `Set` of `member|day`) is re-applied by `bindActivityCards()` |
| Member links, card toggles, "Show n more" | `bindActivityCards()`; a member name opens `openMember()` and `#memberdlg` |
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
| Data | `GET /api/catalogue?watch=<id>&offset=<n>` → `discovery/tracklist.catalogue()` |

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
| List rows (`.songtable`, `.songrow`, `.st`, `.sartist`, `.sdur`, `.send`) | `components/library-row.css` |
| Cards, covers, playlist tiles, folders | `components/library-cards.css` |
| Compact bar and fullscreen player | `components/player.css` |
| Playback queue sheet | `components/playback-queue.css` |
| Download queue / search / review | `components/queue-view.css` |
| Feed, the release list and the artist catalogue | `components/feed.css` |
| Genres dialog | `components/genres.css` |
| Lyrics pane | `components/lyrics.css` |

- **One authoritative rule per selector within its owner.** Duplicate selectors inside one
  file were folded where that was order-safe; what remains are deliberate state and
  breakpoint layers (base rule, then `.playing`/`.open`, then `@media`), so an element is
  changed by editing its rule, not by appending a third copy at the end.
- **The playing row is one border.** `.songrow.playing:not(.songcard)` owns a real
  `border` — 1 px accent at 60% and a 3 px accent right edge — and switches its own separator
  off. It used to fake the edge with an inset `box-shadow` over a pseudo-element separator,
  which is why a neighbouring separator could appear to cross it. Do not reintroduce a
  shadow border or a second drawing layer.
- The fullscreen foot is the worked example of token sizing. `#player-full .pf-foot` controls
  are sized from tokens on `#player-full`:

  ```css
  #player-full{--pf-foot-size:55px;--pf-foot-glyph:30px;--pf-wave-size:44px;--pf-wave-glyph:24px}
  ```

  Like and queue read `--pf-foot-size`/`--pf-foot-glyph`; the style toggle reads
  `--pf-wave-size`/`--pf-wave-glyph`. 55 px is deliberate: it is the box the queue already
  had, so equalising like upwards did not change the row height or move the record.
- Compact bar rules are the `body #player, body #player.on` section of `components/player.css`,
  the authoritative block for that surface. Inside one file the later block still wins, so
  edit the block that owns the element rather than adding another.
- **One bar at every width.** The phone composition is the bar: 112 px tall, cover flush in
  the top-left corner, title and artist centred above the transport, transport centred on the
  bar, seek across the foot with a clock at each end, heart top-right, expand bottom-right.
  The only width-dependent rule is the shell's inset and max width (`min(1072px, 100vw - 48px)`,
  centred, 16 px off the bottom) plus the save slot, which takes the empty band left of the
  heart/expand column. The volume toggle is in the header, not the bar. Do not reintroduce a desktop grid: it changed the arrangement at
  761 px, which read as a different player rather than the same one wider.
- Anything anchored to the bar's top edge moves with its height: the queue sheet's
  `bottom` and the toast's are set from 112 px + the desktop gap, not from an older 88 px bar.
  The queue sheet adds the tab's own 18 px on top of that (146 px), so the tab stays clickable.
- **The header sits above the sticky tab bar** (`header{z-index:26}` vs `.tabs{z-index:25}`).
  The user menu hangs out of the header onto that row, and a child cannot paint over a
  sibling of its ancestor's stacking context — at 1 the bar covered the menu's first rows and
  swallowed their clicks.
- The compact bar's right column is one stack: the heart (`right:9px`), the save slot
  (`right:11.5px`, 29 px) and the expand button (`right:9px`) all centre 27 px in from the
  bar's right edge.
- The compact wave's box is CSS in the compact bar block (`65 px`, `bottom:0`); only the
  drawing is in `index.html`. Moving the canvas means re-checking `TRACK_Y = h - 27`, the
  scrub's own centre (bottom 19 + half of 16 px), in the draw branch — the track position is
  set by the scrub, not by the canvas height, so shrinking the wave does not move it.
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
  load-time wiring stays in `index.html` after `const SVG`.
- **Semantic class names.** `.st`, `.sartist`, `.salbum`, `.sdur`, `.send` and `.idx` are
  generated and queried by that inline script. Renaming them is a separate pass: search the
  CSS, the script's template strings and the tests before touching one.
- **`#pllike`/`#pflike`.** The compact bar and the fullscreen foot both style them; they are
  meant to be one heart state, so change them together (both rules are in
  `components/player.css`).