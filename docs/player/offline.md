# Offline copies

PLAYER-005. A track the connected server offers can be kept on this device, so the next play
starts from disk instead of from the network. This is not Repo acquisition — nothing is searched
for, nothing is fetched from anywhere but the server the person connected, and no second
catalogue is built. It is a cache with a promise: what the dialog says is kept is what the
player can play with the network unplugged.

Two files, split where the testability line falls:

| file | what it decides |
| --- | --- |
| `player/shell/offline.js` | which tracks are kept, the download and its progress, cancellation, the resume, the integrity check, the budget, the eviction order, and handing a kept file to the audio element |
| `player/src-tauri/src/offline.rs` | the per-account directory, the atomic write, the manifest, delete, and how much space is left |

The window has no filesystem, so every byte crosses `invoke` into those commands. That split is
also why the interesting logic is where it can be tested: `player.js` runs under node with a stub
server, while the Rust crate cannot be compiled on the machine this was written on.

## What is kept, and where

```
<app data>/offline/<account>/manifest.json
<app data>/offline/<account>/<track id>.<suffix>        a whole copy
<app data>/offline/<account>/<track id>.<suffix>.part   a download in flight
```

`<account>` is `account_name("<server url>|<username>")`: a readable prefix of lowercase ASCII
plus a 32-bit FNV-1a tag of the whole key, so two accounts whose prefixes collide
(`https://a/b` and `https://a_b`) still get separate directories, and no account key can become
a path. The name is computed identically in the front end and in Rust, and both are tested
against the same table.

`<track id>` is the Subsonic song id, used only after `safe_component` has accepted it: ASCII
alphanumerics, `.`, `-`, `_`, at most 64 bytes, no leading dot. Everything else — a separator, a
drive letter, a NUL, a non-ASCII look-alike slash, `..` — is **refused**, not escaped. An id this
player cannot represent as a name is an id it does not keep, and the caller is told so. The
`<suffix>` is a hint, not a path: everything but ASCII alphanumerics is dropped, and an empty or
overlong hint becomes `bin`.

## The manifest

One JSON file per account, the only index of what is kept:

```json
{
  "version": 1,
  "entries": {
    "842": {
      "file": "842.mp3", "key": "842", "suffix": "mp3",
      "size": 5242880, "expected": 5242880,
      "etag": "\"a91f…\"", "contentType": "audio/mpeg", "title": "…",
      "sourceSize": 31457280, "addedAt": 1731456000000, "lastPlayed": 1731456120000,
      "complete": true
    }
  }
}
```

* `size` is what is on disk; `expected` is what the server declared. They are equal on a
  committed copy.
* `complete: false` is a **pinned partial**: a download that has started but not finished. It
  carries the ETag and declared length of the file it belongs to, so a resume can tell a changed
  file from the same one. Verification, playback, `stats()` and eviction all ignore it — a
  partial is a download in flight, not a copy this device can offer.
* `file` is written by the module and re-sanitised on every use. A name that arrived as JSON is a
  claim, not permission: `entry_file` rebuilds a refused name from `key` and `suffix`, and every
  path is checked with `inside(dir, path)` after it is built.

The manifest is written to `manifest.json.part` and renamed, so a reader never sees half of one.
A manifest that exists but cannot be parsed is treated as empty: the copies become unreferenced
bytes the person can clear, rather than files this code silently deletes.

## The download

1. **Already kept?** `verify()` the copy. If it is whole, the download is a `touch` (so it is the
   last eviction candidate) and no request is made.
2. **Where did the last attempt stop?** `offline_begin` returns the length of the `.part`. It
   also creates the file, so a directory that cannot be written is a failure before a byte of
   audio is fetched.
3. **Fetch with `Range: bytes=<n>-`.** Every attempt after the first carries the offset. A reply
   must be `206` with a `Content-Range` starting at `n`. A `200` means the server ignored the
   header and sent the file from zero, so the partial is **thrown away** and the download starts
   again rather than stitching two beginnings together. A `Content-Range` that starts anywhere
   else is the same answer.
4. **Pin the file.** The first response's ETag and total length are recorded against the partial
   (`offline_pin`). On a resume, an ETag that differs from the pinned one — or a total that
   differs — is a different file: the partial goes and the download restarts.
5. **Write at the offset.** Chunks are 256 KiB, base64 over IPC, and the store refuses a chunk
   whose offset is not exactly the current length. No gap, no overlap, no gap that reads as
   silence in the middle of a track.
6. **Resume or stop.** A body that ends early is resumed from where it stopped. Three
   interruptions in a row and the download stops, leaving the `.part` for the next attempt. A
   body that never reaches the declared length is never committed: `offline_commit` refuses
   unless the bytes on disk are exactly the declared size.
7. **Commit.** `<name>.part` is renamed onto `<name>` — atomic on unix, target-removed-then-renamed
   on Windows — and the manifest entry is written.

**Integrity is size first, and ETag where the server offers one.** The length is checked against
`Content-Length` (or the `Content-Range` total) before the commit, and again on every play:
`verify()` compares the manifest's `size` with the file on disk, and a copy that is shorter than
the manifest says is truncated. A truncated copy is **dropped**, not played — an audio element
will happily stop in the middle and call it the end of the song. A server that declares no length
at all is written down as such: the file is what it sent.

`sourceSize` (the `size` a Subsonic reply puts on a song) is kept for information only. It is the
size of the *stored* file, and `stream.view` may legitimately hand back a transcode of it, so
using it as an integrity check would reject every correctly downloaded MP3 of a FLAC.

### Cancellation

`start()` returns `{promise, cancel}`. `cancel()` aborts the fetch **and** races the reader, so a
socket that has stopped delivering does not hold the cancel hostage. The partial is kept and the
next attempt begins at the byte this one reached — a cancel is a pause, not a delete. Deleting is
`remove(key)` (the dialog's per-track action) or `removeAll()` ("Remove copies").

## The budget, and the eviction rule

The budget is bytes, set by the person, and `0` means keep nothing. It uses the app's own
vocabulary (`web/index.html`'s Offline audio dialog): the same default of 2 GB, the same "off"
meaning no copies at all, the same status line shape.

Eviction is **oldest played first**: entries ordered by `lastPlayed`, then `addedAt`, then key.
The copy that is **playing is never taken** — `download()` protects the track that is playing and
the track it just kept, and `evict(key)` protects whatever the caller names. If the budget could
only be met by evicting those, the budget stays exceeded until they stop and `over` reports by how
much; that is the honest failure, not deleting the song in someone's ears.

A track larger than the whole budget is **not kept at all** — it is downloaded, refused, and
deleted, with the reason stated. Keeping it would evict the library to hold one file.

The budget itself is a preference, stored under `hl.player.offlineBudget` in the window's own
storage. It is the only thing this feature writes that is not a copy of music. No credential is
stored here: the caller composes the stream URL, so the password never reaches this module.

## Playing a kept copy

`local(track)` answers from the manifest and the file, and **never asks the server** whether the
track is still there. It verifies the length, reads the file in 4 MiB slices, reassembles one
`Uint8Array`, and hands it to `URL.createObjectURL` as a `blob:` URL the audio element can take.
Seeking a local copy is therefore a slice read (`offline_read`), not a range request.

The alternative — the asset protocol — was not taken: it needs `security.assetProtocol` enabled
and a scope in `tauri.conf.json`, and this card does not own that file. The blob costs one copy of
the track in memory; a later card can switch to the asset protocol when it owns the config.

## Free space

`offline_free` answers with `available` and `total` bytes, from `statvfs` on unix (the `libc`
crate, `cfg(unix)` only). On a platform with no such call here it answers `null` **with a
reason**, and the dialog says the budget is the only ceiling rather than showing a number it made
up. A failed `statvfs` is reported the same way.

Writes that run out of space are reported by the operating system's own message, through the
command that failed — the download does not pre-reserve, because the size a Subsonic reply
declares is the stored file's, not the transcode's.

## Permissions

The commands are the app's own (`#[tauri::command]`), so they need **no capability entry**: the
capability system covers plugin commands, and `capabilities/default.json` still grants
`core:default` and nothing else — no `fs`, no `shell`, no Rust-side HTTP. `tests/test_player_offline.py`
asserts that, so a plugin cannot be added to make this work without the test noticing.

## What is unverified, and why

This machine is Bazzite with no `webkit2gtk-4.1`, `gtk+-3.0` or `libsoup-3.0` packages and no
`cargo-tauri`, so `offline.rs` has never been compiled against Tauri, and no window has opened.

What *is* proven:

* the pure block of `offline.rs` — `safe_component`, `safe_suffix`, `file_name`, `account_name`,
  `inside`, `sanitise_text`, `b64_encode`, `b64_decode` — is extracted by
  `tests/test_player_offline.py` and compiled and run with `rustc --test`, against the same
  hostile table the client test uses;
* `rustfmt --check` accepts the whole module;
* every command `offline.js` invokes exists in the module, and every command in the module has a
  caller;
* every `join` in the module takes a value from a sanitiser, and the manifest's own `file` field
  is re-sanitised on use;
* the client's behaviour is run for real: `tests/test_player_offline.cjs` loads `offline.js` into
  a vm, serves ranges from a stub `http` server that drops a body mid-transfer, and drives a real
  temporary directory through a shim of the same commands — resume from the exact byte, a resumed
  file byte-identical to a clean download, a truncated body never committed, a truncated file
  never played, eviction that skips the playing track, a budget that is met, and a play that makes
  no request.

What is **not** proven, and needs a build:

* the command layer against real Tauri IPC — argument names, `Result<Value, String>` rejection,
  and that `Vec<u8>`/base64 travels as expected. Base64 is used because it is version-independent;
  Tauri 2 has a raw-binary IPC path that would be cheaper, and moving to it is a change nobody can
  test here;
* `statvfs` field widths on each target (Linux, macOS, iOS, Android all come through `libc`, which
  is the reason to take the dependency rather than declare the struct by hand);
* `URL.createObjectURL` on a multi-megabyte blob inside the OS WebView, and whether the audio
  element keeps its bytes alive after `release(url)` — the caller must release a URL only when it
  has stopped using the track;
* whether a Subsonic server sends the CORS headers a `Range` request from the WebView's origin
  needs. If it does not, the download fails with the server's answer, and PLAYER-002's
  `tauri-plugin-http` decision covers it along with the keychain;
* iOS ATS still needs an exception before a plain `http://` LAN server answers, for the same
  reason and in the same place as the connect screen.

Two smaller gaps worth naming: a `.part` from a suffix that changed (a server that stops
transcoding) lingers until the track is deleted, and there is no sweep of old partials yet; and a
manifest that is corrupt is treated as empty, which leaves the copies on disk unreferenced rather
than deleted — visible in `stats()` as disk used that the dialog does not account for, and
clearable with "Remove copies".