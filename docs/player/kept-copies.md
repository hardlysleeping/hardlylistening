# Kept copies

A phone on a bad connection cannot stream, so the player keeps the bytes it was already allowed to
play and plays them from its own folder. The command layer is `player/src-tauri/src/keep.rs`; the
page's half is `player/ui/src/lib/keep/store.svelte.ts`; the player asks it first in
`player/ui/src/lib/player/store.svelte.ts` (`useLocalSource`).

`docs/player/store-safe.md` states the rule this does not break: **no address ever comes from the
page.** A keep command takes a song id; Rust resolves the origin from the bound account, exactly as
`subsonic_source` does.

## The four decisions

| decision | why |
| --- | --- |
| the word is **kept**, not *downloaded* or *offline* | it is the host web UI's own word for the same idea (`.kept-badge`, "Kept on this device"), and `offline_*`/`downloadUrl`/`/download` stay banned by the store-safe gate so the removed P4 feature cannot return under a new name |
| the bytes come from `/rest/stream`, not `/rest/download` | a plain stream is what the play path already asks for, so a keep is the same request the speaker would have made; `/download` is refused even though the server implements it |
| playback is a **loopback URL** | an `<audio>` element needs a URL, not bytes. `http://127.0.0.1:<port>/<id>` is this process's own listener, so the page never learns a file path, no plugin is needed, and `tauri.conf.json`'s existing `media-src … http:` already allows it. `NSAllowsLocalNetworking` (already in the iOS plist) covers the ATS side |
| removal lives in the **Kept list**, not on a song row | a row's control is a one-tap fetch; the same cell also removing a copy would put a lost song one mis-tap away |

## What is real, and measured here

One deadline to name first: a fetch uses a fifteen-minute ceiling, not the shared client's
thirty seconds. That client timeout is a JSON call's deadline, and a 10 MB track at 100 KB/s would
be killed at 30 s — never kept, on exactly the connection this feature is for.

The pure half and a real socket, in `cargo test` on this machine (13 tests):

* the index round-trips through `index.json`, is written through a `.tmp` and renamed, and a
  missing or corrupt file reads as an empty index rather than an error — the files are still on
  disk, and a person who lost the index has lost names, not music;
* one row per song: keeping the same song twice replaces its row;
* `safe_id` refuses anything that could leave the folder (`../etc/passwd`, `a/b`, a 97-character
  id) and `file_name`/`suffix_from` keep only an alphanumeric extension;
* `parse_range` answers the single-range form a media element sends — `bytes=0-99`, `bytes=100-`,
  `bytes=-50`, a clipped end — and refuses an impossible one; anything it cannot read falls back to
  the whole file rather than guessing at a slice;
* over a real `TcpListener`: `200` with `Accept-Ranges: bytes` and the bytes, `206` with
  `Content-Range: bytes 2-4/10` and exactly those three bytes, `416` with `bytes */10`, and `404`
  for a song the index does not hold.

The page's half (17 vitest cases): the list is replaced from the index rather than merged, a held
song is never fetched twice, one refusal stops a run instead of fetching the rest, a refusal sets
`error` rather than pretending, `sourceFor` is not even asked about a song it does not hold, a held
copy that cannot be read falls back to the stream, remove drops the row without re-reading, and the
progress event moves the bar and ignores a straggler from a fetch that already finished. The player
itself is pinned to **kept copy first**: a kept song plays without the server answering, a song that
is not held streams as before, and a kept copy that cannot be resolved streams instead of failing.

## What is not verified, and why

* **No IPA has been built with this.** The Rust half compiles and its tests pass in `huibuild`, but
  iOS loading `http://127.0.0.1:<port>/<id>` in the WebView's own `<audio>` is unverified until one
  Codemagic dispatch. This host cannot build the shell (no WebKitGTK dev packages, no Xcode).
* **The native engine is still absent** (`PLATFORM_PLAYER_PRESENT` is `false`), so a kept copy plays
  through the WebView. Background playback therefore rests on the iOS recipe's
  `UIBackgroundModes: [audio]`, not on a platform player.
* **No budget and no eviction.** A kept copy lives until it is removed; there is no size cap and
  nothing ages out. The Kept list prints the total so a person can see what it is costing, and that
  is the whole policy.
* **Downloads do not resume.** A fetch writes `<id>.part` and renames on success, so a killed fetch
  leaves a `.part` that is overwritten by the next attempt rather than continued.