# Sonic analysis: fingerprints, neighbours, a path (HL-205)

Tempo shipped first (`library/analysis.py`, `library/tempo.py`, `tracks.bpm*`) and Auto DJ already
reads it. This is the other half: what a track *sounds* like, as a vector, so "more like this" and
"walk from A to B" are arithmetic instead of tags.

**numpy is a real dependency of this half**, imported directly — no guarded probe, no pure-Python
fallback (owner's decision, 2026-11-14). It is confined to `library/fingerprint.py` and
`library/sonic.py`; nothing else in the tree imports it, the module is imported lazily inside the
routes that need it, and the rest of the server stays stdlib, so there is still no build step.
Install for the service interpreter with:

```
pip3 install --user --break-system-packages numpy
```

## The fingerprint

One file, one 60-number vector (`library/fingerprint.py`):

| step | what happens |
| --- | --- |
| decode | ffmpeg → mono 22.05 kHz PCM, the same decode the tempo detector uses |
| frames | 1024-sample windows every 512 samples (~23 ms), quieter than 0.1 % of the loudest frame dropped |
| spectrum | Hann window, rfft magnitude |
| block 1 | 48 triangular mel bands, 40 Hz–8 kHz, each frame's mean removed, then averaged over frames |
| block 2 | 12 pitch classes (chroma) over 65 Hz–2 kHz, same per-frame centring, then averaged |
| shape | the two blocks are centred and scaled **separately** (so loudness and the loudness balance between the blocks cannot decide the answer), concatenated, L2-normalised |

Two properties are deliberate:

* **deterministic** — the same bytes give the same list, so a stored vector and a fresh one compare
  equal. That is what lets `fingerprint.VERSION` be bumped to invalidate everything instead of
  drifting silently.
* **silence is not a fingerprint** — a file with no sound returns `None` and is stored as a `dims=0`
  marker, meaning "analysed, nothing there". A zero vector would score 0 against everything and
  quietly look like a track that belongs nowhere; the marker instead means it is skipped by
  similarity and never handed to the worker again.

Measured on the tone fixtures in `tests/test_sonic.py`: the same tone with light noise `0.54`, a
tone a few cents away `0.999`, a tone an octave and a half away `−0.19`. Ordering is the point — a
pure tone is the degenerate case for this maths, and real music never is.

## Storage

```sql
CREATE TABLE track_features (
  track_id INTEGER PRIMARY KEY,
  vector   BLOB NOT NULL DEFAULT x'',
  dims     INTEGER NOT NULL DEFAULT 0,
  version  INTEGER NOT NULL DEFAULT 0,
  analysed REAL NOT NULL DEFAULT 0
);
```

Owned by `library/sonic.py` because it is its only writer. Not in `core/db.py`, so a database that
has never analysed anything simply has no table until a sonic request creates it.

## Neighbours, and a path

* `similar(store, id, limit)` — cosine over every analysed vector, seed excluded, most similar
  first. An **unanalysed** track is enqueued for the worker and answered **empty**; an empty answer
  that fills in later is honest, invented neighbours are not.
* `path(store, from, to, hops)` — a greedy walk: step to the nearest unvisited neighbour, stop at the
  target, the hop cap, or when nothing unvisited is left. Greedy rather than shortest because the
  point is a *listenable* sequence, and the visited set plus cap make it always terminate. Either
  end being unanalysed is a `ValueError` at the module level and a `409` over HTTP — not an empty
  path.

## The worker

`start(store, ids=None, limit, force)` queues ids (or everything without a current fingerprint) and
returns immediately; one daemon thread fingerprints them and reports `running/total/done/skipped/
failed/current/percent`. `force` re-does everything (`re-analyse`). Nothing decodes inside a
request. The lock is a `threading.RLock` because `start()` builds its answer out of `status()`.

## The surfaces

| surface | answers |
| --- | --- |
| `getSimilarSongs`, `getSimilarSongs2`, `getSonicSimilarTracks` (Subsonic) | songs the caller may see, by cosine neighbours; `count` 1–100, default 20 |
| `findSonicPath` (Subsonic) | the walk as songs, `startId`/`endId`, `hops` ≤ 50 |
| `GET /api/sonic/status` | worker state plus `analysed` |
| `GET /api/sonic/similar/<id>` | `{track_id, similar: [{id,title,artist,album,duration,score}]}` |
| `GET /api/sonic/path?from=&to=&hops=` | `{path: [row…]}`, `409` when an end is unanalysed |
| `POST /api/sonic/analyse` (`ids`, `limit`, `force`) | the worker's own answer; a **granted** route, like every other one that does work |

An unknown id is Subsonic error `70` (`400` on the app API). `getSonicSimilarTracks` and
`findSonicPath` left `NOT_IMPLEMENTED` with this card; `docs/subsonic-compatibility.md` cell 13
records it. The sonic endpoints are the only part of the Subsonic surface that imports numpy, and
they import it inside their own branch — every other request stays stdlib.

## What is **not** built

The in-app half: there is **no "Sounds like this" control under a track, no "Sonic path" in the row
menu, and no UI for the analyse action**. The web UI does not call `/api/sonic/*` at all. That is
stated here rather than implied, because the plan's Phase 4 listed it and this card did not build it.

## Where it lives

| piece | file |
| --- | --- |
| the vector (numpy's only home) | `library/fingerprint.py` |
| schema, neighbours, path, the worker | `library/sonic.py` |
| the four Subsonic endpoints | `subsonic.py` |
| `/api/sonic/*` and the grant on `analyse` | `server.py` |
| tests (39) | `tests/test_sonic.py` |