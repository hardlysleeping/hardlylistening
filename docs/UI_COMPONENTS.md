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
| Compact bar | `#player` | `paintPlayer`, `syncPlayButton`, `#pllike`, `#plfull`, `#plqueue` | 112 px tall, drawn to `compact-bar-layers.svg` (402×112) |
| Fullscreen overlay | `#player-full` | `openFullPlayer()`, `closeFullPlayer()`, `paintFullPlayer()` | Persistent element, toggled by `.on` + `body.fullplayer` |
| Pull / swipe | inside the overlay | `setFullPull()`, `commitFullPull()`, `retreatFullPull()` | The gesture layer the overlay transition uses |
| Disc | `#pf-disc`, `#pfart` | `paintPfDisc()`, `pfScratchPrepare/Release` | Rotation is a RAF loop; scratching reads a decoded copy |
| Visualizer | `#pfwave`, `#pfwaveknobs`, `.pfwave-aura`, `#plwave-seek` | envelope builder + draw loop | Knobs: bars, level, smooth, mirror, enabled |
| Queue | `#playback-panel`, `#pf-queue-slot` | `showFullQueue()`, `togglePlaybackPanel()` | One panel at a time with the style knobs |
| Volume | `#plvol` (bar), `#pfvol` (fullscreen foot) | `paintVolume()`, one `input` listener per fader | Desktop only: both paint from `playerAudio`, never from each other; `--vol` drives the accent fill |
| Shared state | — | `playingId`, `playerAudio`, `libAll`, `libQueue`, `currentRows` | One source for both surfaces; never duplicate it |

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
against a stub API, freezes animation, and compares screenshots for four states
(`compact`, `fullscreen`, `queue`, `knobs`) at 390×844 and 1280×800.

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