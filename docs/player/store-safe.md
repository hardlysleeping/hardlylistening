# The store-safe assertion

Purpose: make "the player ships no Repo acquisition code" a fact about **the built tree** rather
than a claim about the list that built it. Card **PLAYER-007**.

`player/assemble.mjs` states a promise in two lists: `WEB_ALLOW` (what the player copies from
`web/`) and `EXCLUDED` (what it deliberately leaves out, with the reason). A list is a promise
and a promise is not proof — one line adds a file back, and inlining a module's contents into a
file that *is* on the list defeats the list entirely. So `tests/test_player_store_safe.py` runs
the assembly into a temporary directory and asserts over what came out.

## What the assertion checks

Every needle is read from the tree at test time; nothing below is a remembered list.

| check | where the answer comes from | a tree that fails it |
| --- | --- | --- |
| no `EXCLUDED` path is present | `assemble.mjs --list` → `excluded` | a file copied under its excluded name |
| no assembled file names an acquisition endpoint | every `/api/…` literal in `web/js/repo-discovery.js` | the module, or its URLs, pasted into any shipped file |
| the entry document adds none of them | the same endpoints, counted in `web/index.html` and in the output | a route the assembly introduced |
| no `web/` file acquisition needs is present | the closure: `web/` files naming an endpoint only the module uses | the module present under another name or path |
| no reference dangles | every `<script src>`, `<link href>` and CSS `@import` in the tree | a page asking for a file the build left out |
| the receipt explains the difference | the receipt's `dropped` against `EXCLUDED` | a drop with no policy reason, a rewrite with no receipt, a silent edit |
| the tree is smaller by exactly the drops | output bytes vs source bytes minus the receipt's `removed` text | a file quietly changed on the way in |
| no acquisition vocabulary in the text | a phrase list (see below) | copy that offers a source page as a library download |
| the receipt describes the tree | `sha256` and `bytes` per file | a receipt that is not about the bytes on disk |

Measured on 2026-11-12 at `eaf55d0`: **33 assembled files** (34 with the receipt) totalling
**700 898 bytes** of content, assembled from **701 344 bytes** of sources — the difference being
the **446 bytes** of entry-document references the receipt says were dropped (six of them: the
acquisition module, the web-app manifest and icon, and the Party, Mixer and Swipe links). `web/`
is 908 147 bytes; the rest of that difference is files the player never listed — the mixer, the
login and signup pages, the service worker, the sheets nothing imports.

The endpoint rule has one deliberate exception. `web/index.html` is carried as the player's entry
document, and its **inline** script is the host's: it already calls `/api/subscriptions` and
`/api/feed/…` because it is the whole application, not the player. Those names are inherited,
not added, so the test allows the entry document to carry exactly the module endpoints it carried
in `web/index.html`, **in the same number**, and allows no other file to name any of them. The
module's own endpoints — `/api/tracks`, `/api/track/queue`, `/api/catalogue` — are absent
everywhere, entry document included.

The vocabulary check is a phrase list, not the word "download": `download to library`,
`download album`, `queue for download`, `joins your library`, and so on. The app's own markup
says "Download folder" and "Download ZIP" about a member's library, which is Library vocabulary
and stays until PLAYER-003 replaces the surface; a check on the bare word would be either
vacuous or false.

### The negative controls

The assertions have to be shown to bite, not merely to pass:

* `node player/assemble.mjs --out <tmp> --include-web js/repo-discovery.js` must exit 1, naming
  the module and the policy — the guard in `assemble.mjs`.
* Independently of that guard, the audit is run over a **copied, doctored tree**: the module
  copied back in, and the entry document asking for the excluded manifest again. The audit must
  report the excluded file, the endpoint closure, the dangling reference and the vocabulary. If
  that control ever passes silently, the checks above prove nothing.

## How it runs in the build pipeline

The test is the gate. Its exact command:

```sh
python3 -m unittest tests.test_player_store_safe -v
```

It assembles with `assemble.mjs --out <temporary directory>` (so `TMPDIR` is respected and
`player/www/` is never touched), walks the result once, and exits non-zero on any finding. It
runs in the same place as the rest of the suite (`python3 -m unittest discover -s tests`); this
repository has no CI configuration, so the gate is that command on the machine making the
release. If a runner is added, this test belongs on it unchanged — it needs node and the Python
standard library, nothing else.

The build itself is the first line of defence, not the last: `tauri.conf.json`'s
`beforeBuildCommand` runs `node assemble.mjs` before Tauri collects `player/www`, and that script
refuses a forbidden path in the plan and again in the finished tree. The test exists because a
build can be run without the test, and because the refusal only knows the names it was told.

## What a reviewer looks at in a release

```sh
python3 -m unittest tests.test_player_store_safe -v      # 10 tests, OK
node player/assemble.mjs --out /tmp/player-www           # assemble by hand
find /tmp/player-www -type f | sort                      # 33 assembled files + the receipt
python3 -c 'import json;print(json.load(open("/tmp/player-www/.assembly.json"))["dropped"])'
du -sb /tmp/player-www web                                # the tree is smaller than web/
grep -rn "repo-discovery\|/api/tracks\|/api/catalogue" /tmp/player-www
```

The last line should match only `.assembly.json`, and only the `dropped` entry that records the
exclusion. The receipt ships inside the bundle on purpose: it is the record of what was left out
and why, and a store reviewer reading it sees a decision rather than a hole.

## What the assertion cannot prove

* **It reads code and markup, not behaviour.** A fetch whose URL is built at runtime
  (`'/api/' + 'tracks'`, a string in a JSON payload, a redirect from an allowed host) passes
  every check here. What would close it: a policy at the layer that actually makes requests — a
  `security.csp` `connect-src` that allows only the Subsonic server the person typed, or a
  Tauri HTTP scope — so the shell *cannot* reach a host endpoint even if some file asked. Neither
  is built; `csp` is `null` today (PLAYER-003).
* **The entry document still names host routes.** `/api/subscriptions` and `/api/feed/…` are in
  its inline script, inherited from `web/index.html`. The test pins the count so the assembly
  cannot add any, but it does not remove them: inside the player those calls have no server until
  PLAYER-003 points the library at Subsonic endpoints. Saying otherwise would be a claim about a
  tree that does not exist yet.
* **Vocabulary is a phrase list.** It catches the words acquisition uses about itself; it does
  not catch a synonym, and it deliberately ignores "download" where the app means a member's own
  library. What would close it: assert over the player's own routed page once PLAYER-003 gives it
  one, instead of over the host's markup.
* **Allowed files can grow a dependency.** The endpoint closure is computed from the endpoints
  `repo-discovery.js` uses *today*; a future allow-listed file could call acquisition through a
  route this module never mentions. What would close it: keep the closure rule (a `web/` file
  naming an acquisition-only endpoint fails), and derive the acquisition-only set from Repo's
  route ownership in [`../server-domains.md`](../server-domains.md) §5 rather than from one
  module.
* **`--include-web` is a test hook.** The negative control proves the guard fires and that the
  audit catches a doctored tree; it does not prove no build invocation passes the flag. A release
  review is the place that is checked (`beforeBuildCommand` is `node assemble.mjs`, no flags).
* **It says nothing about the packaged bundle.** Tauri runs the same script before collecting
  `player/www`, but this test never opens an `.app`, `.deb` or `.apk`. The Rust crate is likewise
  uncompiled — see [`../player.md`](../player.md) for what is verified and why.