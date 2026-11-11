# UI components — HardlyListening

Read this before changing the player, the queue, or anything they share.

## Where the code lives

| File | Size | Owns |
| --- | --- | --- |
| `web/index.html` | ~444 KB | All markup, a ~51 KB inline `<style>` block, and ~352 KB of inline script |
| `web/polish.css` | ~134 KB | Most styling, in ~886 top-level blocks |
| `web/preferences.js` | small | Likes/pins wrappers around the cards' controls |
| `web/genres.js`, `web/lyrics.js`, `web/lifecycle.js` | small | Genre chips, lyrics, reload-on-new-version |

There is no build step: the files above are what the server serves. A change is live on
reload; there is nothing to compile.

## Player components

All of these are inside `web/index.html` today.

| Component | Markup | Behaviour | Notes |
| --- | --- | --- | --- |
| Compact bar | `#player` | `paintPlayer`, `syncPlayButton`, `#pllike`, `#plfull`, `#plqueue` | 112 px tall at every width, drawn to `compact-bar-layers.svg` (402×112); desktop only widens the shell and adds the save slot (the volume toggle lives in the header) |
| Fullscreen overlay | `#player-full` | `openFullPlayer()`, `closeFullPlayer()`, `paintFullPlayer()` | Persistent element, toggled by `.on` + `body.fullplayer`. Sized to fit the viewport: the record's stage is `min(86vw,520px,54vh,calc(100vh - 360px))`, so the open player never scrolls (down to 1024×600 and 360×640) |
| Pull / swipe | inside the overlay | `setFullPull()`, `commitFullPull()`, `retreatFullPull()` | The gesture layer the overlay transition uses. Touch and pen only — a mouse drag on the bar or the pane is a slip, and the bar's own buttons open it on desktop. The pane's pull timers check `fullPullSeq`, so a dismiss still animating out cannot close a pane that was reopened meanwhile |
| Disc | `#pf-disc`, `#pfart` | `paintPfDisc()`, `pfScratchPrepare/Release`, `setCoverArt()` | Rotation is a RAF loop; scratching reads a decoded copy. Double-tapping the cover swaps the record for a full-size sleeve (`#player-full.artmode`); the record is the default and returns when the player closes |
| Visualizer | `#player .plwave-seek` (compact), `#hdr-wave` (header) | envelope builder, `spectrum()`, draw loop | Two different readings. The compact wave is the track's own envelope, drawn on its seek line. The header is a **live spectrum**, not the envelope: bars are log-spaced bands (~35 Hz–16 kHz, at least one FFT bin each) read from an `AnalyserNode` fed by the audio element, mirrored around the bar's centre, with per-band attack/release and an `1/f` tilt so the hats are not a flat line. That graph is built only on a mouse-driven desktop — `min-width:900px` **and** `(hover:hover) and (pointer:fine)` **and** `maxTouchPoints === 0` — and only while that canvas is drawn. Width alone is not enough: a phone in landscape is wider than 900 px, and a routed element dies when Safari freezes its context on lock, so the touch check is what keeps background playback intact. No on-screen knobs and no fullscreen style button. `cfg` is still read from `hl.wave.v1` (`enabled`, `bars`, `level`, `smooth`, `mirror`), with `bars` × 1.5 as the phone pitch (96). The compact count is width-proportional — `round(pitch × w / 182)`, clamped 12–512, so 95 at 390 px and 469 at the widest 890 px canvas. The canvas is 65 px tall (72 less a tenth), flush with the bar's bottom edge, so bars stand on the bottom margin and rise through the scrub, which sits 27 px up. The envelope is RMS (not peak) over 512 points, normalised to the track's loudest passage and expanded by `^1.4`; there is no per-track percentile stretch, so quiet and loud sections keep their relative heights |
| Queue | `#playback-panel`, `#pf-queue-slot` | `showFullQueue()`, `togglePlaybackPanel()` | The slot is `display:none` while closed (a parked translated box still counted towards the overlay's scroll area) and `showFullQueue()` flips it to `flex` before the slide. Opened from the bar it is centred on the viewport, 18 px above the queue tab, so the sheet hangs from the control that opened it |
| Download queue | `#view-queue` — `#qmain`, `#qsearch`, `#qreview` | `setQueueTab()`, `renderQueue()`, `renderReview()`, `setReviewTab()` | Tabs Queue / Search / Review; Review lists `st.stats.review` and `st.stats.failed` rows grouped by playlist. The candidate panel is `.revbox` — `.review` belongs to the status dot |
| Volume | `#plvol` (header, behind `#plvolbtn`), `#pfvol` (fullscreen foot) | `paintVolume()`, `paintVolumeToggle()`, one `input` listener per fader | Desktop only: both paint from `playerAudio`, never from each other; `--vol` drives the accent fill, so at 100% the whole track is accent. The header's speaker sits immediately left of shuffle, opens a 72×18 fader into the space to its left (absolute, so the header never reflows) and stays open across sessions (`hl.compactVolume`). Its slot is also the header's flex spacer, so hiding the button on a phone does not move shuffle |
| Shared state | — | `playingId`, `playerAudio`, `libAll`, `libQueue`, `currentRows` | One source for both surfaces; never duplicate it |
| Offline audio | `#dlg-offline` (user menu), `web/sw.js` | `cachePlayedAudio()`, `offlineEvict()`, `offlineReconcile()`, `clearOfflineAudio()`, `setOfflineBudget()` | Per device, not per account: budget in `hl.offlineBudget`, index in `hl.offlineIndex`, bytes in the worker cache `hardlylistening-audio-v1` — that name is written in both `index.html` and `sw.js`, so change them together |

## Library filter row

The Filter button (`#filterbtn`) opens `#libfilters` — sort, sort direction, playlist, and
the Genres chip. The row is `display:none` until `html.filters-open`.

| Piece | Owner |
| --- | --- |
| Row layout and its selects | `.libfilters` blocks in the `web/index.html` inline style |
| Open state and centring | `html.filters-open #view-library .libfilters` in `polish.css` |
| Genres chip | created by `web/genres.js`, appended to `#libfilters`; sized by `.libfilters #genresbtn` in `polish.css` |
| Filtering | `Genres.matches()` in `renderLibrary()`; the popup markup and stylesheet are `web/genres.js` / `web/genres.css` |

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
| Styling | `.ghost.lsave` blocks in the `web/index.html` inline style |
| Player surfaces | `#pfsave` (fullscreen foot, 44 px, 12 px radius) and `#plsave` (compact bar, 34 px, 9 px radius), both painted by `paintPlayerSave()`; box, `.on` green and the red `.armed` border live in the `.pfsave` / `.plsave` blocks in `polish.css` |
| Removed-but-visible state | `.acard.removed`, `.songcard.removed`, `.songrow.removed`, `.covercell.removed` |
| Album card layout | `.songcard` grid areas in `polish.css`; album cards override them to `"art art" / "title album" / "artist artist" / "votes save"` so the song count sits on the title line, top-right, instead of sharing the artist line with the taller save button (the count's `justify-self:end` keeps it flush with the button's right edge in the equal-column tiers) |

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
| Grid and card layout | `.plgrid` / `.plcard` blocks in the `web/index.html` inline style |
| Mosaic steps | `playlistCollage()` in `web/index.html` and the `.collage` / `.collage.cN` blocks |
| Dynamic cards | `dynCard()` in `web/preferences.js` — same markup, so they follow the mosaic too |

- The mosaic steps in whole rectangles: 12 (4×3), 9 (3×3), 8 (4×2), 6 (3×2), 4, 3, 2, 1
  covers. Counts are chosen from the largest step that fits, so no step leaves a blank cell.
- Covers are de-duplicated first: a playlist with one album repeated still spends its tiles
  on distinct art, and a playlist with no art shows its initials rather than a black box.
- **Favorites** (yours) is added in the Library scope; **Everyone's Favorites** is the
  Repository's list and is added in the shared scope only. Neither is a stored playlist.

## Artist catalogue page

The Feeds tab's artist page (`#feedartistpage`) is a catalogue, not a "latest releases"
list: every album the provider lists, and the songs behind them. Albums and Songs are two
tabs behind the Library's icon switch (`#fapTabs`), the layout switch (`#fapLayout`) applies
to both, and both sit on one row above the pane.

| Piece | Owner |
| --- | --- |
| Page shell, tabs, panes, filter | `openArtistPage()`, `artistTabsHtml()`, `setArtistTab()`, `paintArtistPanes()` in `web/index.html`; tab state in `hl.artistTab` |
| Album pane (cards / covers / rows) | `artistAlbumsHtml()`, `artistAlbumCard/Tile/Row()`, `renderedArtistAlbums` |
| Song pane (cards / covers / rows) | `paintArtistSongs()`, `artistSongsHtml()`; layout state in `hl.artistLayout` |
| Song count and paging | `#fap-song-meter`, `#fap-foot`, `paintArtistFooter()`, `loadMoreArtistSongs()` (Songs tab only) |
| Filter | `#fapsearch` in `.fahead`, right-aligned on the artist's name row; filters albums by title/date and songs by title/album/artist |
| Footer layout | `#fap-song-meter` and `.fafoot` blocks in the `web/index.html` inline style |
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

## CSS ownership

`polish.css` is still the only stylesheet for the player, and it carries historical
overrides: 209 of its blocks mention a player selector, and several elements are defined
three times. The rule from here on:

- **One authoritative block per element.** If a rule must change, edit the block that owns
  the element; do not append a third override at the end of the file.
- The fullscreen foot is the worked example. `#player-full .pf-foot` controls are sized from
  tokens on `#player-full`:

  ```css
  #player-full{--pf-foot-size:55px;--pf-foot-glyph:30px;--pf-wave-size:44px;--pf-wave-glyph:24px}
  ```

  Like and queue read `--pf-foot-size`/`--pf-foot-glyph`; the style toggle reads
  `--pf-wave-size`/`--pf-wave-glyph`. The old 40 px rule, the 44 px rule and the queue-only
  55 px override are gone. 55 px is deliberate: it is the box the queue already had, so
  equalising like upwards did not change the row height or move the record.
- Compact bar rules belong in the `body #player, body #player.on { … }` section, which is the
  authoritative block for that surface. Earlier `#player` blocks above it are historical.
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

1. Find the element's authoritative block (grep its id in `polish.css`; if it appears more
   than once, the last one wins — consolidate instead of adding another).
2. Size it from an existing token or add one on the owning component, never a bare pixel
   override appended later.
3. Keep the element's id and click handler. Other code looks elements up by id.
4. Run `node tests/visual/snap.cjs` and look at every changed screenshot. A diff outside the
   element you touched is a regression, not a rounding error.

## Visual baselines

`tests/visual/snap.cjs` renders the player from the real markup, CSS and paint functions
against a stub API, freezes animation, and compares screenshots for three states
(`compact`, `fullscreen`, `queue`) at 390×844 and 1280×800. The comparing states carry no
saved song, so the add/remove control is absent from them; check it by hand.

```bash
node tests/visual/snap.cjs            # compare against tests/visual/baseline/
node tests/visual/snap.cjs --update   # accept a deliberate change
node tests/visual/snap.cjs --selfcheck # prove the baselines are byte-stable
```

Changed states write `<state>.actual.png` next to the baseline for inspection.

## Known debt

- `polish.css`: ~209 player blocks with repeated selectors (`#player.on` ×7,
  `#player-full .pf-body` ×6, `#player .pctrl` ×5). Each is a future extraction candidate.
- `web/index.html`: one 51 KB inline `<style>` block (9 player blocks) and ~352 KB of inline
  script, so the player's markup, styling and behaviour cannot be reviewed independently.
- The compact bar and the fullscreen foot both style `#pllike`/`#pflike`; they are meant to
  be one heart state, so change them together.