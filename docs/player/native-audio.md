# Native audio: who owns the bytes, the queue and the lock screen

Card: **PLAYER-004**. Two engines implement `AudioEngine` (`player/ui/src/lib/player/engine.ts`):
the WebView's `<audio>`, and — on a phone — the platform's own player behind Rust.

The difference that matters is not where the sound comes from. It is that on a locked phone
**there is no JavaScript to call `advance()`**, so the queue has to belong to the player.

| | `WebAudioEngine` | `NativeAudioEngine` |
|---|---|---|
| `kind` | `web` | `native` |
| `ownsQueue` | `false` | `true` |
| who advances | the store, on `ended` | the platform player, which reports `track` |
| what the page states | one stream URL per track | ids and an order, once |
| where a credential lives | in that URL | in Rust, and in the platform player's memory |
| who holds the lock-screen session | the WebView's `navigator.mediaSession`, claimed by `media.ts` | the platform, by construction |

The store follows this in four places (`player/ui/src/lib/player/store.svelte.ts`), and each has a
test in `src/lib/player/native.test.ts`:

- `#start` never calls `source()` on a queue-owning engine — it calls `playAt(index)`.
- `next()` / `previous()` are `skip(±1)`. The three-second "previous restarts" rule belongs to
  whoever owns the playhead, so the store does not apply it twice.
- `advance()` — wired to `ended` — only sets `playing = false`. A queue-owning player has already
  advanced by the time it says `ended`; that word means the order ran out.
- the `track` event **updates state only** (`#follow`). Issuing a `playAt` there would restart the
  track the person is hearing.

## The commands, and what crosses them

| page call | arguments | Rust |
|---|---|---|
| `native_audio_available` | — | `bool`; `false` on every build today |
| `native_audio_queue` | `ids: string[]`, `repeat` | replaces the order |
| `native_audio_play` | `index` | start that position |
| `native_audio_skip` | `direction: 1 \| -1` | next / previous |
| `native_audio_repeat` | `repeat` | change the mode without touching the order |
| `native_audio_resume` / `native_audio_pause` | — | transport |
| `native_audio_seek` | `seconds` | jump |
| `native_audio_volume` / `native_audio_mute` | `volume` / `muted` | level |
| `native_audio_stop` | — | stop and forget the order |

No command takes a URL, an address or a credential, on any platform. That is the same rule as
`docs/player/transport.md`, applied to audio: Rust resolves each id to a stream address when it
builds the platform queue, so **the credential-in-the-page that `<audio>` needs does not exist on
this path** — the remainder P2 left behind.

`setQueue` semantics, which is what makes a queue edit safe mid-playback: **the player keeps the
current item if it is still in the new order, and starts from the beginning otherwise.** `playAt`
is the explicit "start this one". Changing the order therefore costs the current track's playhead
position, and `toggleShuffle` accepting that restart is a recorded ruling, not an accident.

## The channel

One event, one name: `hl:native-audio`, with `kind` as the discriminator.

```json
{ "kind": "track", "index": 3 }
{ "kind": "position", "position": 12.5, "duration": 200.0 }
{ "kind": "playing", "playing": true }
{ "kind": "ended" }
{ "kind": "error", "message": "the file is gone" }
```

The shape is pinned from both sides: `Event` in `src-tauri/src/native_queue.rs` serialises to it
(and `event_payload` is the channel's only writer), and `native-engine.test.ts` asserts the page
reads exactly those fields.

## The rules, as data

`src-tauri/src/native_queue.rs` is pure — no Tauri, no socket, no device — and is tested by
`cargo test` on this machine. It holds the part a platform player must not invent:

- **off** at the last song: stop where it is.
- **all** at the last song: wrap to the first.
- **one** when a track ends: `Repeat(index)`, which on a real player is a seek to 0 and continue.
- **a manual skip moves on even under repeat-one.** Repeat is about the end of a file, not about a
  button.
- **back from the first song restarts it** rather than jumping to the end.
- an index outside the order, an empty queue, a non-finite seek, a volume above 1, a direction
  that is not ±1: refused, by `checked_*`, which the commands call before anything else.

## The lock screen, on the engine that is actually built

`player/ui/src/lib/player/media.ts` is the half of a lock screen the shell cannot supply: a tag,
a transport and a timeline, handed to `navigator.mediaSession`. It exists because the assembled
web app had one (`web/js/playback.js`) and the first-class client — which shares the host's
styles but not its scripts — had none, so a locked phone drew whatever the platform draws for an
unclaimed session: nothing, or a bare ±10 s bar.

`App.svelte` builds it once from the store's own state (`track`, `playing`, `position`,
`duration`, `cover`) and publishes on a single `$effect`. That is deliberate: the session is a
**picture of the player**, never a second player, so a press on a head unit takes the same path
as a press on the docked bar. Clearing on `track === null` is the sign-out case — the last
account's song must not stay on a lock screen a phone is no longer holding.

What it claims, and what it refuses:

| action | effect |
|---|---|
| `play` / `pause` | `player.toggle()`, and only when the intent differs — a platform that repeats a press does not start a track twice |
| `nexttrack` / `previoustrack` | `player.next()` / `player.previous()`, which carry the three-second rule on the browser engine |
| `seekto` | `player.seekTo(seekTime / duration)` — the store's only seek, so the lock screen timeline and the page cannot disagree |
| `seekbackward` / `seekforward` | **cleared (`null`), never claimed** — the same rule the assembled app learned: claiming either replaces the skip pair with ±10 s |

Two properties are taken from `docs/player/playback.md` because they were learned on a real drive
rather than from a specification:

- the tag is re-published **only** when the song or its artwork changes, and the transport is
  re-claimed on every track change, because WebKit drops handlers when a session goes inactive;
- the timeline is pushed at most once a second and never moves backwards without a jump larger
  than `POSITION_JITTER_SECONDS` — a car's elapsed clock must not tick backwards because
  `timeupdate` wobbled.

Artwork is the cover route the client already uses (`<server>/rest/getCoverArt.view?...&t=…`), so
it carries its own credential in the URL: iOS fetches media-session artwork **itself**, outside
the page's session, and a cookie-gated route would answer that fetch with a login redirect, which
a car draws as no image at all.

Nothing here schedules a timer. A suspended page runs no callbacks, so every decision is taken
when the store says something changed; a frozen and resumed page reports the truth instead of a
queue of stale decisions. Keeping the audio alive is the shell's job (`UIBackgroundModes`,
`AVAudioSession`) and is not something a script can promise.

`PLATFORM_PLAYER_PRESENT = false` means the WebAudioEngine is the engine every build runs, so
this is the whole of the lock-screen story today. A queue-owning engine must publish its own
(`MPNowPlayingInfoCenter` / `MediaSessionService`, both already on the list below) — a page that
is not running cannot keep a tag in step with a native playhead.

## What is compiled in, and what is not

`PLATFORM_PLAYER_PRESENT` in `src-tauri/src/native_audio.rs` is **`false`**. So
`native_audio_available` answers `false`, `useNativeAudioIfPresent` leaves the browser engine in
place, and every build plays exactly as it did before P3. The commands exist, check their
arguments, and otherwise refuse with a `config` failure that says why.

**Ruling (P3, this session): the Swift and Kotlin sources are not written yet.** They are postponed
to the first build on a machine that can compile them, and the reason is mechanical: this host has
no `swiftc`, no `xcodebuild`, no `kotlinc` and no Android Gradle plugin (`java` 17 and the SDK's
`adb` are present, the build tools are not), so nothing written here could be compiled, run, or
even type-checked. A file that has never been built is not a feature; it is a claim, and it would
sit in the repository looking finished. What ships instead is the whole seam above — page, store,
Rust state machine, command contract, event contract — plus this list of what the platform side
must do.

### What the platform side must implement, in full

1. **iOS (`gen/apple`, not committed):** `Info.plist` `UIBackgroundModes = [audio]` and an
   `AVAudioSession` category of `playback`; one `AVQueuePlayer` (or `AVPlayer` + `AVPlayerItem`
   chain for repeat-one, which `AVQueuePlayer` cannot express) fed from `native_audio_queue`; a
   `Tauri` command impl per row of the table; `MPNowPlayingInfoCenter` and
   `MPRemoteCommandCenter` handlers for play/pause/next/previous/seek; `ended` → `advance()` →
   `track`/`ended` events on `hl:native-audio`.
2. **Android (`gen/android`, not committed):** a `MediaSessionService` with `ExoPlayer`; a
   foreground service with `FOREGROUND_SERVICE_MEDIA_PLAYBACK`; a `MediaSession` with the queue as
   `MediaItem`s; `onMediaItemTransition` → `track`, `onPlaybackStateChanged(STATE_ENDED)` →
   `ended`; the same command table over JNI.
3. **Both:** call `subsonic_source` logic (not the page) for each id, keep the addresses in the
   player's memory only, and emit nothing but the five event shapes above.

`PLATFORM_PLAYER_PRESENT` flips to `true` in the same change that makes those compile, and not
before.

## What is verified, and what is not

Verified on this machine:

- `player/ui`: 167 vitest tests. 17 pin this seam — the ids-and-order message, no URL in any
  message, `track` updating state without starting anything, `skip` delegation, `playAt` from Up
  Next, repeat without a re-push, `ended` stopping rather than advancing, shuffle re-pushing, a
  queue edit re-pushing, the browser path unchanged, a queue handed over on an engine swap, and
  the engine choice answering to `native_audio_available`. 16 pin the media session
  (`media.test.ts`): the claimed and refused actions, the tag and its artwork type, re-tagging
  only on a change, re-claiming on a track change, the three playback states, the one-second
  throttle, the high-water mark, a real backward seek, `seekto` → fraction, and inertness on a
  platform with no session at all.
- the media session was walked in **muted headless Chromium** against the silent-WAV scratch
  server (`player/ui/dist` on a static server): a played row produced
  `title/artist/album` equal to the page's own title line, artwork as `image/jpeg` at `512x512`,
  `playbackState: playing`, a timeline at ~1 s intervals; a synthesised `nexttrack` moved both
  the tag and `#pltitle`; `pause`/`play` reported `paused`/`playing`; `seekto {seekTime: 5}`
  moved the timeline to 5.99 s. Run twice — once with a recording fake in place of
  `navigator.mediaSession`, once against Chromium's **own** session object, which would throw on
  an action name a platform does not accept. Nothing reached the speakers (`--mute-audio`, and
  the fixture is 30 s of digital silence).
- `svelte-check` 0/0, `vite build`, and the three-surface visual parity harness — the engine
  swap does not move a pixel.
- `native_queue.rs`: 15 tests, compiled and run **standalone**; four rules mutation-checked
  (repeat-all not wrapping, a skip reusing `advance`, an unresolved address ignored, the event tag
  renamed) each broke a test.
- `rustfmt --edition 2021 --check` clean on all seven `.rs` files.

Not verified, and not verifiable here:

- `native_audio.rs` has never been compiled: `cargo check` stops at `glib-sys`, because Bazzite
  has no WebKitGTK/GTK development packages. It is twelve thin commands; the logic they call is
  in `native_queue.rs`, which is compiled and tested.
- Nothing has played on a phone. No lock screen, no background suspension, no Bluetooth, no
  Control Center. `PLATFORM_PLAYER_PRESENT = false` is the honest state of that.
- Whether `reqwest`'s address resolution for a queue build behaves the same on iOS and Android
  (the same open question P2 left).