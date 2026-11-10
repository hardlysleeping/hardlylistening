"""A member's own library as a ZIP: the audio files plus an M3U8 that indexes them.

The M3U8 uses paths *inside* the archive, so the folder works wherever it is unpacked —
that is the point of the archive, not a hand-off list of absolute paths on the server.
"""
from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

_UNSAFE = re.compile(r'[\\/:*?"<>|]+')


def _clean(value: str, fallback: str) -> str:
    text = _UNSAFE.sub(' ', (value or '').strip())
    text = ' '.join(text.split())
    return text[:80] or fallback


def _relative(track: dict, used: set) -> Path:
    artist = _clean(track.get('artist_name'), 'Unknown artist')
    album = _clean(track.get('album'), 'Singles')
    number = track.get('track_number') or 0
    stem = _clean(track.get('title'), 'Track %s' % track.get('id'))
    if number:
        stem = '%02d %s' % (number, stem)
    suffix = Path(track['file']).suffix.lower() or '.mp3'
    candidate = Path(artist) / album / (stem + suffix)
    bump = 2
    while str(candidate).lower() in used:
        candidate = Path(artist) / album / ('%s (%d)%s' % (stem, bump, suffix))
        bump += 1
    used.add(str(candidate).lower())
    return candidate


def build(store, uid: int, playlist_id: int | None = None) -> tuple[str, str, int]:
    """Write the archive to a temp file. Returns (path, download name, track count)."""
    import listening

    tracks = listening.catalog(store, uid)
    if playlist_id is not None:
        wanted = {str(row[0]) for row in store.conn.execute(
            'SELECT track_id FROM listening_playlist_tracks WHERE member_id=? AND playlist_id=?',
            (uid, playlist_id))}
        tracks = [t for t in tracks if str(t['id']) in wanted]
        title = next((row[0] for row in store.conn.execute(
            'SELECT title FROM playlists WHERE id=?', (playlist_id,))), None)
        name = _clean(title or 'playlist', 'playlist')
    else:
        name = 'library'

    used: set[str] = set()
    entries = []
    for track in tracks:
        path = Path(track.get('file') or '')
        if not path.is_file():
            continue
        entries.append((track, path, _relative(track, used)))
    if not entries:
        raise ValueError('Nothing to export yet — save a song first.')

    handle = tempfile.NamedTemporaryFile(prefix='hl-export-', suffix='.zip', delete=False)
    handle.close()
    manifest = ['#EXTM3U', '#PLAYLIST:HardlyListening %s' % name]
    with zipfile.ZipFile(handle.name, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
        for track, path, relative in entries:
            archive.write(path, relative.as_posix())
            seconds = int(track.get('duration') or 0)
            manifest.append('#EXTINF:%d,%s - %s' % (seconds, track.get('artist_name') or '', track.get('title') or ''))
            manifest.append(relative.as_posix())
        archive.writestr(name + '.m3u8', '\n'.join(manifest) + '\n')
    return handle.name, 'hardlylistening-%s.zip' % name, len(entries)
