# Playback

Card: **PLAYER-004**. `player/shell/playback.js` is the playback layer of the player shell: the
media session the operating system reads, the transport commands it sends back, the position it
draws, and the scrobble the server is owed. It is a classic script with no build step, no
dependency and no storage, and it is loaded by the shell page next to the app's own scripts.

It is **not a second player**. The audio element (`#player-audio`), the queue (`libQueue`,
`libIndex`, `playingId`), the shuffle order and the repeat mode all belong to `web/` and stay
there. This file owns the four things the assembled app cannot state for itself, and drives
everything else through an adapter.

## What the OS gets

| field | where it comes from | notes |
| --- | --- | --- |
| title | the playing row's `title` | `Untitled` when the server names nothing |
| artist | the row's `artist_name` | |
| album | the row's `album`, else its set | the release when the server named one, otherwise the playlist the song was pulled from — the same rule the player's own second line uses, because 410 of 937 rows in a real library have no release and those rows were never blank on the lock screen |
| artwork | the row's `artwork`, made absolute | one entry, `512x512`, with an explicit `type` — WebKit draws no artwork entry that has no declared type, leaving an empty square on the lock screen |
| position | `setPositionState` | `duration`, `playbackRate` and `position`; pushed as the position changes, throttled to about 1 Hz |
| playback state | `playbackState` | `playing` or `paused`, from the element's own `paused` |

The tag is published when the track changes **and** on the element's own milestones —
`loadedmetadata`, `playing`, `emptied`, `canplay`, `play`, `pause`, `seeked`, `durationchange`.
That is not belt-and-braces: a skip detaches the old resource, WebKit answers with `emptied` and
drops the tag, and a head unit that latched the now-playing block never asks for it again. The
app already learned this; the milestones are the same ones it publishes on.

## Transport

| action | what it does |
| --- | --- |
| `play` | the app's element: `play()`; a rejected promise is swallowed because a skip that lands mid-play rejects the previous one |
| `pause` | the element: `pause()` |
| `nexttrack` | one step forward through the app's queue (`stepTrack(1)`) |
| `previoustrack` | before three seconds: one step back. After three seconds: a restart, which is a seek — the queue does not move |
| `seekto` | the element's `currentTime`, clamped to `[0, duration]` |
| `seekbackward`, `seekforward` | **explicitly cleared (`null`), never claimed** |

The last row is a trade-off, and it is the same one the app makes: registering *either* of the
`seekbackward`/`seekforward` pair makes the platform draw a ±10 s seek layout **instead of** the
skip pair, so a lock screen that could skip would only scrub. `seekto` does not do that — it only
makes the timeline scrubbable — so the seek the card asks for arrives through the timeline, and
the skip buttons survive. A platform that offers no timeline scrubbing therefore offers no seek
from this layer, which is the honest cost.

A seek never touches the queue: the row, the order and the `playingId` are exactly what they were,
so scrubbing cannot leave the queue out of step, cannot drop a row, and cannot report a second
play.

## Shuffle and repeat

Shuffle reorders **only what is still to come**. The head of the queue, up to and including the
song that is playing, is untouched, so the current track stays current and its index does not
move. Turning shuffle off puts the waiting songs back in the order they had before (ranked
against a snapshot taken when shuffle was switched on); songs queued after that keep their place
at the end. The reorder is written back through the app's own `commitPlaybackQueue()`, so its
queue drawer, its save state and its stored order all agree — one order, not two.

Repeat is `off` | `all` | `one`:

* **one** — `ended` seeks the same track to 0 and plays it again. Nothing in the queue is touched
  and the play is not re-reported: a restart is not a new play.
* **all** — at the last row, one step forward. The app's own step wraps, so there is no second
  path for "the end" to get wrong.
* **off** — at the last row, the player stops where it is.

A song that is playing but is not one of the queued ones (`libIndex` is −1) hands over to the
queue's first song at its end, which is what the app's own step already does; only running out of
a queue the song is actually in stops the player.

## Scrobbling

`<server>/rest/scrobble.view` with `u`, `p`, `v`, `c`, `f=json`, the track `id`, the position in
milliseconds as `time`, and `submission`. The credentials are the ones `connect.js` is already
holding in memory for the session; the module keeps no copy of its own and writes nothing to
disk. With no session there is no URL and nothing is sent.

The play is submitted **once per track**, at the first of:

* half the track's duration, or four minutes — `min(240, duration / 2)` — whichever is first, as
  the protocol says; an unknown duration uses the 240 s ceiling, because half of nothing is not a
  threshold;
* the track reaching its end, whatever the position.

Seeking backwards and playing forward again does not submit a second time: the flag is keyed to
the track, not to the position, and only a track change resets it. An `ended` after a submission
sends nothing.

The *now playing* notification (`submission=false` at the start) is **off by default**. This
server's `scrobble`/`reportPlayback` handler records a play for every call it receives and ignores
`submission`, so a start ping would count every song twice. `attach({announce: true})` turns it on
for a server that honours the flag.

## Backgrounded, and what actually keeps playing

The short version: **the audio element keeps playing; JavaScript may not be running.** A mobile
WebView is free to freeze or throttle a backgrounded page, and timers and animation frames are
the first things it takes.

So nothing in this module is scheduled on a timer. There is no `setInterval`, no `setTimeout`, no
animation frame, no deferred advance. Every decision is taken from an event the media element
emits (`timeupdate`, `ended`, `seeked`, `play`, `pause`, `loadedmetadata`) or from the element's
own state when the page becomes visible again. A page that was suspended for two minutes
therefore resumes with **nothing to replay**: no queue of stale decisions exists.

What this guarantees:

* a suspended page cannot advance the queue behind the user's back, because advancing is an event
  handler and events are delivered in order, once;
* a page that was hidden across the scrobble threshold reports that play **once**, on resume, from
  the element's real `currentTime` — and a second resume reports nothing;
* the transport is re-claimed on every resume (`visibilitychange` → visible, `pageshow`), because
  WebKit drops action handlers when the session deactivates and the lock screen would otherwise
  fall back to the platform's ±10 s layout.

What it does **not** guarantee, stated plainly:

* a missed `ended` is not reconstructed. If the page resumes and the element has ended, the play
  is reported and nothing is advanced — guessing here would either skip a song or double-play one.
  In practice the element and the page are frozen together, so there is no gap to reconstruct;
* the position published while hidden is only as fresh as the last `timeupdate` the page was
  allowed to handle. The first resume push corrects it from `currentTime`;
* whether audio keeps playing at all is the **platform's** decision, not this file's: an audio
  session / foreground service declared in the platform project. A frozen process stops the sound,
  and no script can prevent that.

## The queue the app already has

`PlayerPlayback.appQueue()` binds the module to the app's own globals — it reads `libQueue` and
`playingId` **by name**, not as properties of `window`, because those are `let` declarations in
the app's classic scripts and live in the global lexical scope. `window.libQueue` is `undefined`;
a second queue grown from that mistake would look like it worked and be empty.

| adapter call | the app's own call |
| --- | --- |
| `row()`, `ids()`, `index()` | `libQueue`, `playingId`, `libAll` |
| `setOrder(ids)` | replaces `libQueue` and calls `commitPlaybackQueue()` |
| `play(id)` | `playFromLibrary(id)` |
| `step(delta)` | `stepTrack(delta)` |

`setOrder` refuses a partial order (an id the app does not have) instead of applying it: a
queue that silently drops a song is worse than an order that did not change.

## Wiring

This card adds one file to `player/shell/`, which **is** the assembly's shell allow-list, so it
ships without an edit to `player/assemble.mjs`. Everything else is handed over: line numbers are
from `eaf55d0`, and none of it may be applied by this card, because `web/` belongs to PLAYER-003
and the shipped page to whoever owns the shell document.

The shape of it: the module becomes the **only** writer of `navigator.mediaSession`, of the
`ended` advance, and of shuffle/repeat state. Two owners fight over the handlers and every
re-claim undoes the other's; two `ended` listeners advance two songs per end.

### 1. `web/index.html` — delete the media-session implementation

Delete these ranges, and nothing else in them:

| lines | what goes |
| --- | --- |
| 3228–3237 | `mediaArtwork` (only `publishMediaSession` used it) |
| 3258–3347 | `publishMediaSession`, `publishPosition`, `publishPlaybackState`, `bindMediaSession`, `republishMediaSession`, and the `pageshow` / `visibilitychange` / `play` / `loadedmetadata` / `playing` / `emptied` / `canplay` / `error` listeners |
| 4825–4847 | `lastPositionPush`, `pushPositionState`, `bindMediaSession();`, the re-claim listeners, the position-state loop, and the `play` / `pause` publish lines |

Keep, because the app's own UI still calls them: `warmArtwork` (3213–3227, used by
`prefetchAhead` at 3367 to warm the next cover), and `realAlbum` / `albumFor` / `playerSub`
(3242–3253), which paint the player's second line.

Keep one line from the range at 4825–4847 — the save button repaint that the module does not do:

```js
playerAudio.addEventListener("play", () => { if (typeof paintPlayerSave === "function") paintPlayerSave(); });
```

### 2. `web/index.html:4810–4816` — delete the inline `ended` handler

```js
playerAudio.addEventListener("ended", () => {
  if (plRepeat === "one"){ playerAudio.currentTime = 0; playerAudio.play().catch(() => {}); return; }
  if (libIndex < libQueue.length - 1){ stepTrack(1); return; }
  if (plRepeat === "all" && libQueue.length){ libIndex = -1; stepTrack(1); return; }
  paintPlayerModes();
});
```

The module replays exactly this from its own `ended` listener, including the hand-over when the
playing row is outside the queue. Leave the `saveNowState(true)` listener on 4822 alone.

### 3. `web/index.html:3564–3571` — `playFromLibrary` publishes through the module

Replace `publishMediaSession(row);` (3565) with `playback.publish();` — it re-tags from the row
`playingId` now names and pushes the position, and it warms nothing, so `warmArtwork(row;` on
3564 stays.

Replace the two lines

```js
  playerAudio.play().catch(() => republishMediaSession());
  publishPlaybackState();
```

with

```js
  playerAudio.play().catch(() => playback.refresh());
```

`refresh()` is the module's own re-claim: sync, claim, re-tag, state, position. The comment above
it (a skip landing while the last `play()` is pending) still describes what it is for.

### 4. `web/index.html:4771–4790` — shuffle and repeat call the module

The buttons stay where they are, so nothing is lost. Replace the *bodies* of `toggleShuffle` and
`cycleRepeat`; the queue reorder and the `localStorage` writes inside them go with the module's
own adapter (`setOrder` → `commitPlaybackQueue`), and the toasts stay:

```js
function toggleShuffle(){
  plShuffle = playback.toggleShuffle();
  paintPlayerModes();
  showToast(plShuffle ? "Shuffling up next" : "Shuffle off — original order restored");
}
function cycleRepeat(){
  plRepeat = playback.cycleRepeat();
  paintPlayerModes();
  showToast(plRepeat === "one" ? "Repeat one song" : plRepeat === "all" ? "Repeat the queue" : "Repeat off");
}
```

`plShuffle` / `plRepeat` stay as the app's mirror of the module's state, because
`paintPlayerModes()` (3634) and `upcomingIds()` (3381) read them. `restoreQueueOrder`,
`shuffleUpNext` and `shuffleBase` (4755–4768) become unused and may be deleted with the module in
place; they must not be called, or the order is shuffled twice.

Consequence, stated rather than buried: shuffle and repeat are no longer written to
`localStorage` (`hl.shuffle`, `hl.repeat` at 3631–3632), so they reset on reload. A shell page
that wants them to survive can call `playback.setShuffle(true)` / `playback.setRepeat(value)`
after `attach`, from its own stored copy.

### 5. The shell page that runs the library — load the module

After the app's scripts:

```html
<script src="./playback.js"></script>
<script>
  const playback = PlayerPlayback.attach({
    audio: document.getElementById("player-audio"),
    coverUrl: artHQ,
    onmode: mode => { plShuffle = mode.shuffle; plRepeat = mode.repeat; paintPlayerModes(); },
  });
</script>
```

`player/shell/connect.html` is the *gate*, not the library page, so this belongs to whichever
document PLAYER-003 routes to. It must **not** be added to `web/index.html`, where `./playback.js`
would 404 in the browser build.

`attach` needs nothing else: with no `queue` it binds `appQueue()` (the app's `libQueue`,
`playingId`, `libAll`, `playFromLibrary`, `stepTrack`, `commitPlaybackQueue` by bare name), with no
`mediaSession` it takes `navigator.mediaSession`, with no `report` it posts to the session
`PlayerConnect` is holding, and with no `announce` it stays quiet on track start (see scrobble).

## Tests

```sh
cd <repo>
node tests/test_player_playback.cjs
PYTHONPATH=.:tests python3 -m unittest tests.test_player_playback tests.test_player_shell
```

No dependency is needed for the browser-free test: it uses `vm` from the standard library.

`tests/test_player_playback.cjs` drives the module with a fake `navigator.mediaSession`, a fake
audio element, a fake queue and a recorder in place of the server — no audio file, no network. It
pins the tag on each track change, the transport's queue operations, a monotonic and never
negative position, shuffle keeping the current track, repeat one repeating exactly one, the
scrobble firing once at the threshold and not on a seek-back loop, and a hidden document resuming
without corrupting the queue or double-reporting. The Python test pins the parts a runtime test
cannot see: no timer, no storage, no second player, the protocol's endpoint, the adapter's names,
and the presence of this document.

## Unverified

This machine has no `webkit2gtk-4.1`, `gtk+-3.0` or `libsoup-3.0` development packages and no
`cargo-tauri`, so **no window was opened and no real `<audio>` element was played**. What that
leaves:

* **No device.** No lock screen, notification, car head unit or Bluetooth AVRCP session has seen
  this tag. Whether the artwork is drawn, whether the timeline is scrubbable, and whether the skip
  buttons survive the `seekto` registration are the platform's choices; the code follows the
  rules the app already follows for the same reason, and the reason is written next to it.
* **Platform by platform, what forwards the session is not the same thing.**
  * macOS/iOS WKWebView forwards to the system Now Playing widget (Control Center, Lock Screen,
    CarPlay's media block). iOS stops background audio unless the app's `Info.plist` declares the
    `audio` background mode — that belongs to the generated Xcode project (`gen/apple`, not
    committed), not to this file.
  * Android's Chromium WebView implements `navigator.mediaSession`, but the *system's* media
    notification is posted by the app, not by a bare WebView. Without a native media-session bridge
    in `src-tauri`, Android's lock screen and Bluetooth controls are not guaranteed, and this card
    does not add one.
  * Windows WebView2 forwards the session to the System Media Transport Controls in recent
    versions; the version that ships on a given machine is not something this repository can pin.
  * Desktop WebKitGTK's route to a desktop's media widget (MPRIS) is the platform's own; nothing
    here assumes it exists.
* **Scrobbling is unverified against a live server.** The endpoint, the parameter names and the
  threshold are the protocol's; that this app's handler records a play for every call and ignores
  `submission` is a reading of its code, not a measurement. No request was sent.
* **The transcode path is not exercised.** `getTranscodeStream` serves a direct-play copy or an
  ffmpeg encode with Range support; the player asks for neither yet, because pointing the library
  at the Subsonic endpoints is PLAYER-003. Seeking in a stream is HTTP Range, which is that
  server's job and is tested there, not here.
* **A real library's rows.** The tag is built from `title`/`artist_name`/`album`/`artwork` as
  `web/js/library.js` names them. A Subsonic `song` object uses `artist` and `coverArt`, so
  PLAYER-003's mapping — not this file — is where those names are aligned.