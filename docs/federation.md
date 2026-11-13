# Federation — the descriptor, and what it is not

Status: **the safe artefact exists; the mirror does not.** `FED-MIRROR-001` is built (the
acquisition descriptor, `repo/federation.py`) and `FED-001` is built as far as it honestly can be
(the dependency direction is asserted, the node identity is generated and stored; asymmetric peer
authentication is explicitly not — see §3). Nothing here opens a socket, talks to a peer, or writes
a descriptor to disk.

The shape of the feature is fixed by `docs/server-domains.md`: **Repo owns acquisition, Library
owns member state, Social owns the Feed and the Player speaks neither.** Federation is a way for
one node's Repo to obtain shared music — or the knowledge of it — from another node. It is never a
way to reach a member's library, a play, a vote or a session.

---

## 1. The acquisition descriptor

A descriptor answers one question: *which provider result should be asked for again?*

```
{"album":"Selected Ambient Works","artist":"Aphex Twin","duration":293.0,
 "provider":"soundcloud","provider_track_id":"123456","source_url":"https://…",
 "title":"Xtal","version":1}
```

(One line in practice — that is wrapped for reading.) It carries exactly:

| field | why it is there |
| --- | --- |
| `version` | a reader refuses a format it does not know, before trusting the rest |
| `provider` | which provider produced the result (`soundcloud`, `apple`, `youtube`, `bandcamp`, …) |
| `provider_track_id` | the provider's own id, so a search is not needed to find it again |
| `title`, `artist`, `album` | what re-acquisition needs to recognise the same recording |
| `duration` | the length the file is verified against (`yt-dlp` verification, `FED-MIRROR-002`) |
| `source_url` | the provider page, when the provider gives one; empty means "resolve by search" |

Everything else a provider result may hold — artwork, ISRC, track number, release id — is
**accepted and not carried**. It is safe, and it is not needed to acquire the track.

### The rules, stated because a caller should not have to read the code

1. **Allow-list.** Any input key that is not one of the fields above (or the plain aliases a
   provider result already uses: `engine`, `source_id`, `url`, `webpage_url`, `uploader`) refuses
   to build. Nothing is dropped-and-forgotten: a scrubber that silently trims is one edit away
   from trimming the wrong thing, and the caller cannot see what it lost.
2. **Credentials and paths refuse to build.** A field whose *name* tokenises to cookie, header,
   token, password, passphrase, secret, session, credential, auth, API key, signature, nonce, or
   to file/path/dir/filename/localfile/cwd/outtmpl/root makes `repo.federation.build` raise
   `UnsafeDescriptor`. The exception carries the **field names only** — never the values — so the
   refusal can be logged without logging the thing it refused.
3. **Names are judged, values are not.** "Password" is a legal song title and an illegal field
   name. The only value that is inspected is `source_url`, because it is the only field that could
   describe a file instead of a page; it must be `http://` or `https://` or empty.
4. **The wire is untrusted.** `parse()` re-runs every check — version, allow-list, required
   fields, text length (`MAX_TEXT`), finite non-negative `duration`, URL scheme — and returns the
   canonical dict. A peer cannot hand this node a descriptor that says "read /music/x.mp3".
5. **Canonical encoding.** `dumps()` is UTF-8 JSON with sorted keys, `,`/`:` separators and
   `allow_nan=False`. Two nodes that agree on a descriptor agree on its bytes, which is what makes
   a descriptor hashable and comparable without a schema negotiation.
6. **No disk, no credentials.** Building, encoding and parsing are pure. A descriptor is never
   written anywhere by this module; `cache/federation/` is for the mirror's own answers
   (`FED-MIRROR-003`), not for acquisition artefacts.

### Round trip

`build(result) → dumps → parse → equal dict → dumps → identical bytes`, asserted in
`tests/test_federation.py` against a real `providers.base.TrackRef` and against a raw provider
mapping. The same file asserts that an input carrying cookies, headers, tokens, passwords or a
local path refuses, and that the refusal does not contain the value.

### What the descriptor is not

* Not a library row: it carries no member, no save, no play count, no favourite.
* Not a queue job: it carries no attempt count, no resolved audio URL, no error, no retry state.
* Not a media locator: a URL is the provider's *page*, never a file on anybody's disk.
---

## 2. Where the code may live (`FED-001`)

Repo owns acquisition, so the descriptor lives in `repo/federation.py` — inside the package whose
owner it is, not in a top-level `federation.py` that would become a seventh domain.

`repo/service.py` is the only module in the tree allowed to import it. `tests/test_federation.py`
parses every Python file and fails if the importers are anything other than
`{'repo/service.py'}`, so:

* the composition root reaches federation through Repo like it reaches everything else;
* `subsonic.py`, `library/playback.py`, `lyrics.py`, `mixer_room.py`, `shares.py` and `listening.py`
  — the modules a player's request actually reaches — cannot speak it, and the test names them so a
  failure says which surface broke;
* a future `sdk/` or `player/` package is covered the day it appears, without anyone remembering to
  add it to a list;
* the shipped web client contains neither "federation" nor "acquisition_descriptor" (it talks HTTP).

The dependency is measured from the AST, not from `sys.modules`: a lazy import inside a function
runs only when the function does, and this has to hold before the first request.

## 3. Node identity — what is built, and what is not

`repo/federation.node_id()` and `node_secret()` generate the node's identity under
`paths.IDENTITY_DIR` on first use. Nothing calls them at boot: an install that never federates has
no key material, and a fresh install's `identity/` stays empty (asserted).

| file | contents | mode | rule |
| --- | --- | --- | --- |
| `identity/node-id` | 32 lowercase hex characters, one line | `0644` | public — a peer has to be able to name this node |
| `identity/node-secret` | 32 random bytes, base64url, one line | `0600` | private; never logged, never in a descriptor, never sent |

The rules:

* **Generated once.** `O_EXCL` creation means two processes starting together agree on one
  identity; an existing file is never rewritten. An existing `node-id` (including one written by
  hand, as `tests/test_migration.py` does) is kept.
* **Never regenerated.** An empty `node-secret` is a `RuntimeError`, not a new key: a lost secret
  is a lost identity, and silently replacing it would let the node keep answering under a name it
  no longer proves. Re-keying is an operator action — remove the file.
* **Never printed.** The module has no logging and no disk cache of its own; the only way out is a
  return value, and only `node_id()` is on the `repo.service` facade. `node_secret()` is internal.
* **The directory is the boundary.** `IDENTITY_DIR` is `0700`, created by `paths.ensure()`, named
  by `backup` and carried across a move by the migration test.

**Not built, and not pretended:** an asymmetric keypair and a peer signature/verification scheme.
The stdlib has no Ed25519 or ECDSA, no new dependency was allowed for this card, and a homemade
signature would be worse than an explicit gap. So the secret is opaque symmetric key material of
the right size (256 bits) whose scheme is still to be chosen, and `FED-001`'s remaining work is:
choose the scheme, sign descriptors or mirror requests with it, and decide how peers exchange or
pin each other's public halves. That work belongs with the mirror cards
(`FED-MIRROR-002`/`003`), which are what would actually need authentication.
