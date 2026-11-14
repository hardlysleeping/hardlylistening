"""Cosine neighbours, a greedy sonic path, and the worker that fills them in (HL-205).

A fingerprint (see `library/fingerprint.py`) is one 60-number vector per track, stored in
`track_features`. Everything here is a read over those vectors: the nearest tracks by cosine
similarity, and a walk from one track towards another. numpy is used directly in both, the same
dependency the fingerprint half has, and nothing outside this pair imports it.

Three rules the tests hold us to:

* **never in a request.** Fingerprinting decodes a whole file with ffmpeg, so it runs in one
  background worker, exactly like tempo. Asking about a track that has not been analysed
  enqueues it and answers empty — an empty list that fills in later is honest; invented
  neighbours are not.
* **silence and failures are recorded, not retried forever.** A file with no sound gets a row
  with `dims=0`, which means "analysed, no fingerprint": it is skipped by similarity and is not
  handed to the worker again on every pass. Bumping `fingerprint.VERSION` is what asks for
  everything to be done again.
* **a path always terminates.** The walk keeps a visited set and a hop cap, so a catalogue
  where the target is not near anything stops early instead of looping.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from library import fingerprint

MAX_BATCH = 1000
VERSION = fingerprint.VERSION

#: Reentrant on purpose: `start()` builds its answer out of `status()` while it holds this
#: lock, and a plain Lock makes that call block forever — taking every later status read
#: (and the request thread holding it) down with it.
_lock = threading.RLock()
_queue: list[int] = []
_thread: threading.Thread | None = None
_state = {"running": False, "total": 0, "done": 0, "skipped": 0, "failed": 0,
          "current": "", "started": 0.0, "finished": 0.0, "error": ""}


def schema(store) -> None:
    """The feature table. Owned here because this module is its only writer."""
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS track_features (
          track_id INTEGER PRIMARY KEY,
          vector BLOB NOT NULL DEFAULT x'',
          dims INTEGER NOT NULL DEFAULT 0,
          version INTEGER NOT NULL DEFAULT 0,
          analysed REAL NOT NULL DEFAULT 0
        );
        ''')
        store.conn.commit()


# -- storage ---------------------------------------------------------------------------------

def _encode(vector) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _decode(blob, dims: int):
    if not blob or int(dims or 0) <= 0:
        return None
    values = np.frombuffer(blob, dtype=np.float32)
    return values if values.size == int(dims) else None


def store_vector(store, track_id: int, vector) -> None:
    """Write one vector (or the `dims=0` marker that means "no fingerprint")."""
    blob = b'' if vector is None else _encode(vector)
    dims = 0 if vector is None else len(vector)
    with store.lock:
        store.conn.execute(
            'INSERT INTO track_features(track_id,vector,dims,version,analysed) '
            'VALUES(?,?,?,?,?) ON CONFLICT(track_id) DO UPDATE SET '
            'vector=excluded.vector,dims=excluded.dims,version=excluded.version,'
            'analysed=excluded.analysed',
            (int(track_id), blob, dims, VERSION, time.time()))
        store.conn.commit()


def analysed_vector(store, track_id: int):
    """The stored fingerprint as a float32 array, or None when there is not one."""
    with store.lock:
        row = store.conn.execute(
            'SELECT vector,dims FROM track_features WHERE track_id=? AND version=?',
            (int(track_id), VERSION)).fetchone()
    if row is None:
        return None
    return _decode(row['vector'], row['dims'])


def count(store) -> int:
    with store.lock:
        row = store.conn.execute(
            'SELECT COUNT(*) AS n FROM track_features WHERE dims>0 AND version=?',
            (VERSION,)).fetchone()
    return int(row['n'] or 0)


def _matrix(store):
    """(ids, vectors) for every analysed track, in id order."""
    with store.lock:
        rows = [dict(row) for row in store.conn.execute(
            'SELECT track_id,vector,dims FROM track_features WHERE dims>0 AND version=? '
            'ORDER BY track_id', (VERSION,))]
    ids, vectors = [], []
    for row in rows:
        values = _decode(row['vector'], row['dims'])
        if values is not None:
            ids.append(int(row['track_id']))
            vectors.append(values)
    if not ids:
        return np.zeros(0, dtype=int), np.zeros((0, fingerprint.DIMS), dtype=np.float32)
    return np.asarray(ids), np.vstack(vectors)


def pending(store, limit: int = MAX_BATCH, force: bool = False) -> list[int]:
    """Track ids with a real file that have no current fingerprint."""
    with store.lock:
        if force:
            rows = store.conn.execute(
                "SELECT id FROM tracks WHERE file<>'' ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        else:
            rows = store.conn.execute(
                "SELECT t.id FROM tracks t LEFT JOIN track_features f ON f.track_id=t.id "
                "WHERE t.file<>'' AND (f.track_id IS NULL OR f.version<>?) "
                "ORDER BY t.id DESC LIMIT ?", (VERSION, limit)).fetchall()
    return [int(row[0]) for row in rows]


def analyse(store, track_id: int) -> str:
    """Fingerprint one track. Returns 'done', 'skipped' (no sound) or 'failed'."""
    with store.lock:
        row = store.conn.execute(
            'SELECT file FROM tracks WHERE id=?', (int(track_id),)).fetchone()
    path = (row['file'] if row else '') or ''
    if not path:
        return 'failed'
    try:
        values = fingerprint.vector(path)
    except fingerprint.FingerprintError:
        return 'failed'
    except Exception:                                       # noqa: BLE001
        return 'failed'
    store_vector(store, track_id, values)
    return 'done' if values is not None else 'skipped'


def analyse_pending(store, limit: int = MAX_BATCH, force: bool = False) -> dict:
    """Fingerprint up to `limit` tracks now. Used by the worker and by tests."""
    report = {'done': 0, 'skipped': 0, 'failed': 0}
    for track_id in pending(store, limit=limit, force=force):
        report[analyse(store, track_id)] += 1
    return report


# -- neighbours ------------------------------------------------------------------------------

def cosine(left, right) -> float:
    """Cosine similarity of two stored vectors. Not normalised inputs are handled."""
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    if a.size == 0 or b.size != a.size:
        return 0.0
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator > 1e-12 else 0.0


def similar(store, track_id: int, limit: int = 20) -> list[dict]:
    """The nearest analysed tracks, most similar first, never including the track itself.

    An unanalysed track is queued and answered empty; nothing is computed for it here.
    """
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 20
    seed = analysed_vector(store, track_id)
    if seed is None:
        with _lock:
            if store_row_exists(store, track_id) and int(track_id) not in _queue:
                _queue.append(int(track_id))
        return []
    ids, vectors = _matrix(store)
    if not ids.size:
        return []
    scores = vectors @ (seed / max(float(np.linalg.norm(seed)), 1e-12))
    order = np.argsort(-scores)
    found = []
    seed_id = int(track_id)
    for index in order:
        candidate = int(ids[index])
        if candidate == seed_id:
            continue
        found.append({'id': candidate, 'score': round(float(scores[index]), 6)})
        if len(found) >= limit:
            break
    return found


def store_row_exists(store, track_id: int) -> bool:
    with store.lock:
        row = store.conn.execute(
            'SELECT 1 FROM tracks WHERE id=?', (int(track_id),)).fetchone()
    return row is not None


def path(store, start: int, end: int, hops: int = 8) -> list[int]:
    """A greedy walk from `start` to `end`: step to the nearest unvisited neighbour.

    Greedy rather than shortest: the point is a listenable sequence ("from this to that"), so
    every step should sound like the one before it. The visited set and `hops` cap make the
    walk finite even when the target sits nowhere near the current track.
    """
    ids, vectors = _matrix(store)
    by_id = {int(value): index for index, value in enumerate(ids)}
    if int(start) not in by_id or int(end) not in by_id:
        missing = [name for name, value in (('start', int(start)), ('end', int(end)))
                   if value not in by_id]
        raise ValueError('%s track has not been analysed yet' % ' and '.join(missing))
    walk = [int(start)]
    visited = {int(start)}
    current = int(start)
    for _step in range(max(1, int(hops))):
        if current == int(end):
            break
        scores = vectors @ (vectors[by_id[current]]
                            / max(float(np.linalg.norm(vectors[by_id[current]])), 1e-12))
        best, best_score = None, None
        for index in np.argsort(-scores):
            candidate = int(ids[index])
            if candidate in visited:
                continue
            best, best_score = candidate, float(scores[index])
            break
        if best is None:
            break
        walk.append(best)
        visited.add(best)
        current = best
    return walk


# -- the worker ------------------------------------------------------------------------------

def status() -> dict:
    with _lock:
        snapshot = dict(_state)
        snapshot['queued'] = len(_queue)
    if snapshot['running'] and snapshot['total']:
        snapshot['percent'] = round(100 * snapshot['done'] / snapshot['total'], 1)
    else:
        snapshot['percent'] = 100.0 if snapshot['total'] and not snapshot['running'] else 0.0
    return snapshot


def queue_ids() -> list[int]:
    with _lock:
        return list(_queue)


def clear_queue() -> None:
    with _lock:
        _queue.clear()


def clear_status() -> None:
    with _lock:
        _queue.clear()
        _state.update({"running": False, "total": 0, "done": 0, "skipped": 0, "failed": 0,
                       "current": "", "started": 0.0, "finished": 0.0, "error": ""})


def _run(store) -> None:
    global _thread
    try:
        while True:
            with _lock:
                if not _queue:
                    break
                track_id = _queue.pop(0)
                _state['current'] = ''
            with store.lock:
                row = store.conn.execute(
                    'SELECT title FROM tracks WHERE id=?', (track_id,)).fetchone()
            _state['current'] = (row['title'] if row else '') or ''
            outcome = analyse(store, track_id)
            with _lock:
                _state[outcome] += 1
    except Exception as exc:                                # noqa: BLE001
        with _lock:
            _state['error'] = str(exc)
    finally:
        with _lock:
            _state.update({'running': False, 'current': '', 'finished': time.time()})
            _thread = None


def start(store, ids=None, limit: int = MAX_BATCH, force: bool = False) -> dict:
    """Queue ids (or every track without a fingerprint) for the worker. Never blocks."""
    global _thread
    chosen = [int(value) for value in (ids or []) if str(value).isdigit()][:MAX_BATCH] \
        or pending(store, limit=limit, force=force)
    with _lock:
        if _state['running']:
            return {**status(), 'started': False, 'reason': 'already running'}
        if not chosen:
            return {**status(), 'started': False, 'reason': 'nothing to analyse'}
        _queue[:] = chosen
        _state.update({"running": True, "total": len(chosen), "done": 0, "skipped": 0,
                       "failed": 0, "current": "", "started": time.time(),
                       "finished": 0.0, "error": ""})
        _thread = threading.Thread(target=_run, args=(store,), name='sonic', daemon=True)
        _thread.start()
    return {**status(), 'started': True, 'count': len(chosen)}