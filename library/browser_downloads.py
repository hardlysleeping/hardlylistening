"""Read-only browser exports. No acquisition, credentials or filesystem paths in manifests."""
from pathlib import Path
import re
import shutil
import tempfile
import zipfile


def safe_name(value, fallback='download'):
    value = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', '_', str(value))
    value = value.strip(' .')[:120]
    return value or fallback


def local_path(store, track):
    """Only DB-selected regular media inside the configured canonical library."""
    if not track or not track.get('file'):
        return None
    root = Path(store.setting('root')).expanduser().resolve()
    path = Path(track['file']).expanduser().resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def selection(store, kind, identity):
    with store.lock:
        if kind == 'playlist':
            row = store.conn.execute('SELECT title FROM playlists WHERE id=?', (identity,)).fetchone()
            if not row:
                raise ValueError('No such playlist')
            tracks = store.conn.execute('SELECT t.* FROM tracks t JOIN playlist_tracks pt ON pt.track_id=t.id WHERE pt.playlist_id=? ORDER BY pt.position,t.id', (identity,)).fetchall()
            return row['title'], [dict(t) for t in tracks]
        ids = identity.split(',')
        if not ids or len(ids) > 5000 or any(not re.fullmatch(r'[0-9]+', i) for i in ids):
            raise ValueError('Select 1–5000 track IDs')
        ids = list(dict.fromkeys(int(i) for i in ids))
        found = {}
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset+500]
            for t in store.conn.execute('SELECT * FROM tracks WHERE id IN (' + ','.join('?' for _ in batch) + ')', batch):
                found[t['id']] = dict(t)
        return 'Selected tracks', [found.get(i, {'id': i, 'title': 'Unknown track', 'file': ''}) for i in ids]


def track_name(track, path, with_id=True):
    # The extension must keep its dot; safe_name strips leading punctuation, so a
    # bare suffix turns ".mp3" into "mp3" and the download lands without a type.
    suffix = re.sub(r'[^A-Za-z0-9.]', '', path.suffix)[:12] or '.audio'
    artist = safe_name(track.get('artist_name') or 'Unknown artist')
    title = safe_name(track.get('title') or 'Track')
    prefix = f"{track['id']} - " if with_id else ''
    return f"{prefix}{artist} - {title}{suffix}"


def archive(store, tracks):
    cache = Path(__file__).resolve().parents[1] / '.cache' / 'browser-downloads'
    cache.mkdir(parents=True, exist_ok=True)
    output = tempfile.TemporaryFile(dir=cache)
    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
            notes = ['Only existing local library files are included. No downloads were queued.']
            for track in tracks:
                path = local_path(store, track)
                if path is None:
                    notes.append(f"Omitted track {track['id']}: unavailable (pending, missing, or outside library).")
                    continue
                try:
                    source = path.open('rb')
                except OSError:
                    notes.append(f"Omitted track {track['id']}: file unavailable.")
                    continue
                with source, z.open(track_name(track, path), 'w', force_zip64=True) as target:
                    shutil.copyfileobj(source, target, 65536)
            z.writestr('MANIFEST.txt', '\n'.join(notes) + '\n')
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
