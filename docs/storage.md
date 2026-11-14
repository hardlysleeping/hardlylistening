# Storage: local, bucket, mirror, cache, migration and restore

The default is **local** and nothing below runs unless an administrator turns a bucket on. A
server with `storage_mode = local` is exactly the server it was before this feature existed.

Three ideas share this ground and are deliberately separate:

| idea | what it is | where |
| --- | --- | --- |
| **where the bytes live** | a file under the library root, or an object in the bucket | `library/storage.py` |
| **how an existing library gets there** | one administrator-triggered migration, then a persistent queue drained by a worker | `library/uploads.py`, `storage_uploads` |
| **what a client sees** | the same 200/206/416 response either way | `library/playback.py` |

## Settings

| setting | default | meaning |
| --- | --- | --- |
| `storage_mode` | `local` | `local` or `s3`. An unknown value is an error, never a fallback |
| `storage_endpoint` | `` | path-style S3 endpoint (B2, MinIO, AWS) |
| `storage_bucket` | `` | bucket name |
| `storage_region` | `us-east-1` | SigV4 region |
| `storage_prefix` | `hardlylistening/` | every object lives under this prefix, so another app's objects are never touched |
| `storage_cache_bytes` | `0` | bytes of bucket objects kept on disk for playback; `0` streams from the bucket |

The **credential is never a setting.** `HARDLYLISTENING_S3_ACCESS_KEY` and
`HARDLYLISTENING_S3_SECRET_KEY` come from the process environment only; `GET /api/storage` reports
`credential: environment` or `missing`, never a key.

## Mirror, stated exactly

The mode is **mirror**: `storage.put` writes the object, `verify` HEADs it, and the local original
stays. Nothing in this code base deletes a local original after an upload, and nothing deletes a
bucket object because a local file went away. A missing local file is recorded `missing` in the
queue and playback falls back to the object — which is also the deletion-scope rule:

* deleting a *local* file removes the local copy only;
* the object survives, and `POST /api/storage/restore` puts it back;
* `storage_cache_bytes` eviction deletes **cache** files only, never anything under the library
  root.

Remote-with-cache is this same mirror with a cache budget: the canonical copy is the object, the
local file is a cache, and a local delete is not a data-loss event. Nothing chooses that mode for
you and nothing switches modes automatically.

## Migrating a library that already exists

```sh
curl -b 'session' -H 'Origin: https://music.example' -X POST \
     https://music.example/api/storage/migrate
```

`uploads.migrate()`:

* refuses **before writing a row** when the mode is local or the bucket cannot be built (missing
  credential, bad endpoint): one refusal with the reason, not ten thousand failed attempts;
* walks `tracks` with a non-empty `file`, derives the canonical key, and inserts a `pending` row
  per file;
* counts `queued`, `skipped` and `missing` (no file, outside the root, unreadable path) and
  records the run — who asked, when, the counts — in `storage_migrations`;
* is **safe to run again**: `pending` keys are skipped, `failed`/`missing` keys whose file exists
  go back to `pending` with attempts reset;
* **checks what it already stored.** Every `stored` row is HEADed: an object that answers 404, or
  whose size no longer matches what was uploaded, goes back to `pending` so the worker uploads it
  again. A vanished object is found by the migration an administrator asked for, not by a
  member's play. The least recently verified rows are checked first, and `verified`/`repaired`
  are counted in the run record;
* aborts with the reason on any other bucket answer (403, 5xx): that is the bucket refusing, not
  an object that is lost, and requeueing every row for it would bury the real problem;
* takes an optional `limit` that caps **each pass** (queueing and verification), so a very large
  library is walked in deliberate batches rather than one long sweep.

Draining is not part of the request. `uploads.start()` is already running in `boot()`, and every
row is `pending` → `stored` (or `failed` after five attempts, with the reason kept). Because the
queue is SQLite, **a restart mid-migration resumes**: the rows are the progress. A migration that
was interrupted is not restarted from zero, and nothing is uploaded twice — an existing `stored`
row short-circuits `offer()` and `migrate()`.

Progress is `uploads.summary()`: `pending`, `stored`, `failed`, `missing`.

## Restoring one object

```sh
curl -b 'session' -H 'Origin: https://music.example' -H 'Content-Type: application/json' \
     -d '{"track_id": 41}' https://music.example/api/storage/restore
# or {"key": "Artist/Album/track.flac"}
```

`uploads.restore()` writes to `<library root>/<key>` — the same place the track row already names,
so no row changes — through a `<name>.part` staging file, and checks:

* the byte count against the object's own HEAD;
* the SHA-256 against the queue's record when one exists (rows written by `tick()` keep it).

A mismatch unlinks the staging file and raises; nothing half-written is left looking playable. A
key that resolves outside the library root is refused.

## The playback cache

Set `storage_cache_bytes` to a positive budget. On the first play of a track whose local file is
absent:

1. the size comes from a HEAD;
2. if the object fits the whole budget, it is fetched once into
   `<DATA_DIR>/cache/storage/<key>` via a `.part` file, size- and SHA-256-checked, then renamed
   into place;
3. the response is the local copy, so range requests and seeking cost no further body fetches;
4. after writing, least-recently-used cache files are deleted until the total fits the budget.
   The file just written is never the one evicted; an evicted file's metadata row goes with it.

**The metadata is a table, not a guess.** `storage_cache` records each key's size, SHA-256, when
it was filled and when it was last used. A later play is answered from that row and a `stat` —
**no request to the bucket at all**, not even a HEAD.

**Revalidation.** A row last used more than `storage.REVALIDATE` (300 s) ago is treated as a miss
so the caller makes one HEAD. If the object's size still matches, the row is refreshed and the
local copy is served — no body fetch. If it differs, the object is fetched again. An object
replaced in the bucket out of band is therefore noticed within five minutes rather than never.

A hit also moves the file's mtime, because eviction orders by it: a track played all day is not
evicted for having been *filled* long ago.

**Invalidation is immediate when this server changes the bytes at a key**: a new mirror upload
(`uploads.tick`), a `restore()`, and a replacement (`replacements.py`) all drop the cache row and
file. A cached copy of the old object is never served as the new one.

An object **larger than the whole budget is not cached** — it streams from the bucket exactly as
it did before. Pretending to cache it would mean evicting everything else for one track.

`available()` (the Subsonic/`HEAD`-style "would this play" check) always asks without the cache, so
answering that question never downloads a track.

What the cache is **not**: a second library. It lives under the data directory, not the media
root; deleting it loses nothing but a refetch; and no eviction path can reach a track file.

## Previous copies kept for a reverse

A source change parks the file it replaced under `<root>/.replacements/retained/<id>/`, beside
the scratch directory its download used. Both are collected once the replacement is past
`replacements_keep_days` (default 30; `0` keeps everything); a scratch directory a live download
still points at is never touched. The disk ceiling does not collect them early — the trash goes
first — because the copy is an undo the person was told they had. `docs/replacement-retention.md`
is the whole policy.

## API

| route | who | what |
| --- | --- | --- |
| `GET /api/storage` | admin | settings, `ready`/`reason`, `uploads`, `migrations`, `cache` |
| `POST /api/storage/migrate` | admin, CSRF | queue the existing library; rate-limited to 3/minute |
| `POST /api/storage/restore` | admin, CSRF | restore one object by `key` or `track_id` |

There is no web UI for any of this yet; the API is the surface.

## Limits, stated plainly

* **Mirror only.** There is no mode that treats the bucket as the only copy and deletes local
  originals. The cache budget gets close, but the media root is never pruned.
* **One PUT per object**, capped at 5 GiB. Multipart upload is not implemented; a larger file is
  refused by name rather than truncated.
* **A cache is filled on demand**, one whole object at a time, and only if the object fits the
  budget. There is no partial fill and no read-through of a slice.
* **Verification costs one HEAD per stored row.** A `migrate()` run checks every stored object,
  which is a real network cost on a large library; `limit` is what makes that a series of runs
  instead of one. A `pending` row is not verified (there is nothing to verify yet).
* **The cache trusts its metadata between revalidations.** Outside the 300 s window, bytes
  changed directly in the bucket are served stale until the next HEAD; changes made through this
  server invalidate immediately.
* **No cross-process cache lock, and fills are serialised in-process.** Two server processes
  would each fetch a cold object, and within one process a fill blocks another fill. Both copies
  are verified, so the result is correct, not efficient.
* **S3-compatible only.** WebDAV and Google Drive are still carded separately (`HL-223` was never
  their home; see `PLAN.md` § Storage).
* **No bucket deletion path.** Deleting objects is an operator action in the bucket's own
  console; the server never does it.