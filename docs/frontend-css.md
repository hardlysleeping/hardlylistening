# Frontend CSS — HardlyListening

The main app has **one stylesheet entry point** and **one owner per surface**. This file is
the contract; `docs/UI_COMPONENTS.md` records which component each element belongs to.

## The tree

```text
web/index.html            <link rel="stylesheet" href="/styles/app.css">   ← the only one
web/styles/
├── app.css               layer order + imports. Contains no rules.
├── tokens.css            design values (:root custom properties)
├── base.css              element defaults, shared button variants, element-level enforcement
├── layout.css            page shell: html/body, main, the space the player reserves
├── motion.css            every @keyframes in the app, in one file
├── utilities.css         genuine utilities only (see below)
└── components/
    ├── app-chrome.css        header, brand, spectrum slot, volume widget, tabs, More menu
    ├── library-controls.css  library header, filter row, sort/scope/layout/density controls
    ├── library-row.css       list row (.songtable/.songrow/.st/.sartist/.sdur/.send/.idx…)
    ├── library-cards.css     cards: .songcard/.acard/.plcard/cover cells/folder cards
    ├── social.css            votes, comments, bubbles, art overlays
    ├── player.css            compact bar (#player) and fullscreen (#player-full)
    ├── playback-queue.css    #playback-panel: the queue sheet behind the player
    ├── queue-view.css        #view-queue: download queue, search, review
    ├── feed.css              Feed tab and the artist catalogue page
    ├── artist.css            artist profile rows
    ├── activity.css          activity feed
    ├── dialogs.css           dialog shells and their frames
    ├── status.css            banners, toasts, empty states, drop zone, counts
    ├── preferences.css       like/pin presentation, including the pinned strip
    ├── genres.css            the Genres dialog only
    └── lyrics.css            the lyrics pane only
```

`web/party.css` and `web/swipe.css` are **separate surfaces** and stay that way. They do not
import `app.css`, and `app.css` does not import them.

One shared primitive is deliberately parked with the list row: the song-card chrome that both
`.songrow` and `.songcard` use — `.thumb`, `.st`, `.splay`, `.lsave` — lives in
`library-row.css`, because the row is the base surface those controls were drawn for.
Card-only layout (grid areas, cover cells, playlist mosaics, folder cards) is in
`library-cards.css`. If you need to change one of those shared controls, change it in
`library-row.css` and then check a card, not only a row: `node tests/visual/library.cjs`.

## Layers

```css
@layer tokens, base, layout, components, utilities;
```

Order replaces specificity between layers: a component rule beats every base rule regardless
of selector weight, and a utility beats every component rule. Two consequences that have
already bitten:

- **An `!important` in an earlier layer beats a later layer's normal declaration.** The iOS
  16 px field guard is `!important` on purpose, so it lives in `utilities.css` — the last
  layer — which lets it beat component field sizing while still losing to the few component
  rules that are deliberately `!important` (the small library search field).
- **Moving a rule between files can change which rule wins.** That is why the migration was
  verified element-by-element (`tests/_csstdiff.cjs`) rather than by eye.

## Rules

1. **One component owns its CSS.** Find the owning file in the tree above and edit the rule
   there. If a selector is missing, add it to the owner; do not add a file.
2. **Do not append fixes to another file.** No `overrides.css`, `fixes.css`,
   `mobile-fixes.css`, `desktop.css`, `polish2.css`. There is no second stylesheet for the
   main app, and one should not be reintroduced.
3. **Do not put component CSS back into `index.html`.** The page has no `<style>` block.
4. **Modify an existing component rule instead of adding a later override.** A new rule for a
   selector that already exists in the same file is a chain, not a fix.
5. **Responsive rules stay with their component.** `@media` blocks belong in the owner file,
   next to the rule they modify, not in a global mobile section.
6. **Do not use utilities as component patches.** `utilities.css` is for genuine utilities:
   the iOS field guard, hidden-state enforcement, reduced motion. A utility that exists to
   correct one component is a component rule in the wrong place.
7. **Do not fake a visible border with a second drawing layer** unless the design genuinely
   needs two edges. The playing row is the worked example: it owns one real border, and its
   separator is switched off, so no neighbouring separator can cross the edge. An inset
   `box-shadow` plus a pseudo-element separator is what it replaced.
8. **Keep ids and handlers.** Element ids are looked up by inline script and by tests.

## The one exception: enforced element rules

`base.css` carries the element-level enforcement that used to be inline in `index.html`:
`[data-admin-only]{display:none!important}`, `html.is-admin [data-admin-only]`, the scrollbar
styling, `::selection`, and reduced-motion. These target elements, not a component, and they
are the only `!important` declarations outside `utilities.css` and a handful of hidden-state
rules (`[hidden]` enforcement in the component that shows the element).

## Verifying a CSS change

Nothing is built; `web/` is served as-is, so a change is live on reload.

```bash
node tests/visual/snap.cjs      # player: compact, fullscreen, queue at 390 / 1280
node tests/visual/library.cjs   # library: list, cards, playing row at 1280 / 390
HL_OLD_WEB=/tmp/oldweb node tests/_csstdiff.cjs list   # computed style vs the 0.16.6 tree
python3 -m unittest tests.test_pwa   # stylesheet tree complete + precached
```

`tests/_csstdiff.cjs` is the oracle for "did moving this rule change any computed style": it
renders the same markup against two CSS trees and compares computed styles element by element.
Point `HL_OLD_WEB` at a checkout of the last release that shipped the old stylesheets; without
one it prints `skipped` and exits 0.

```bash
git archive v0.16.6 web | tar -x -C /tmp/oldweb --strip-components=1
HL_OLD_WEB=/tmp/oldweb node tests/_csstdiff.cjs list-playing 390
```

A difference that is not the change you meant is a regression.

Inspecting the screenshot matters as much as the pixel count: a diff box tells you *where*,
not whether the result is right. Accept a deliberate change by moving `<state>.actual.png`
over the baseline (or run the suite with `--update` when every changed state is intended).