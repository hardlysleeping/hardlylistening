# Working on HardlyListening

Before changing UI, read `docs/UI_COMPONENTS.md`. It records which file owns each player
component, which CSS block is authoritative for which element, the shared design tokens, and
how to verify a change.

Non-negotiables:

- Keep element ids and click handlers; other code looks elements up by id.
- Edit the authoritative CSS block for an element. Do not append another override.
- Run `node tests/visual/snap.cjs` after any UI change and inspect every changed screenshot.
  A diff outside the element you touched is a regression.
- Nothing here is built: `web/` is served as-is, so a change is live on reload.