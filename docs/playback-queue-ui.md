# Playback queue and playlist editor

## Frontend implementation

- The always-visible Playback queue disclosure opens above the fixed player. Its order is the existing `libQueue`, not a parallel queue. Song context menus retain Play now, Play next and Add to queue and add Add to playlist.
- Dedicated pointer grips support mouse, touch and pen. Pointer cancellation restores the original order. Explicit top/up/down/bottom/remove buttons provide keyboard alternatives. Current audio continues when its row is removed or the queue is cleared.
- Only ordered IDs are stored in `hl.playbackQueue.v1`. Restore filters IDs through loaded playable library rows; it never sets an audio source or starts playback. Empty queues persist. Corrupt/unavailable storage does not block the app.
- Create / edit playlists is available in Repository/Library tools and the queue panel. All members may read; only backend `can_edit` owners see membership controls. Search finds canonical tracks including pending entries, add avoids duplicates, remove retains recordings, and drag/buttons write the complete ordered ID set.
- The editor uses the implemented `/api/playlists` contract. Membership writes include the last read `expected_track_ids`; 409 is surfaced without an automatic retry. Successful mutations are read back from the exact playlist endpoint before reporting saved.
- Party is a normal navigation link to `/party`; the dedicated party page and guest routes remain separate.

## Verification

Run with `NODE_PATH=/home/bazzite/.hermes/cache/scratch/hardlylistening-ui-deps/node_modules`:

```
node tests/test_playback_queue_ui.cjs
node tests/test_song_grid.cjs
python3 -m unittest discover -s tests -p test_user_playlists.py -v
```

Browser fixtures use Chromium at widths 1280 and 390, mock network/media, and do not open the live service or modify its database. Queue/editor tests cover keyboard moves, mouse/touch reorder and cancellation, persistence filtering/no autoplay, clear/remove without interrupting audio, playlist create/add/pending/read-only/conflict/readback, and overflow.

No service restart or audible playback verification was performed. This is browser-local queue persistence, not account-synchronized history or offline audio. Playlist candidates show at most 60 matches per search; search narrows across all loaded canonical tracks. Actual service deployment and cross-device testing remain parent/deployment work.
