"""A member's own library as a ZIP: the audio files plus an M3U8 that indexes them.

The M3U8 uses paths *inside* the archive, so the folder works wherever it is unpacked —
that is the point of the archive, not a hand-off list of absolute paths on the server.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

_UNSAFE = re.compile(r'[\\/:*?"<>|]+')

# A DJ application has to be able to open every file in the archive. Opus, Ogg and
# FLAC are fine for Mixxx but invisible to Serato and rekordbox, so anything that is
# not already DJ-safe is converted to MP3 on its way into the ZIP. The stored file is
# never touched and never re-encoded on disk — the archive is a delivery copy.
DJ_SAFE = {'.mp3', '.m4a', '.aac', '.wav'}
EXPORT_MP3_BITRATE = '320k'


def _archived_name(relative: Path) -> Path:
    """The name the file wears inside the archive."""
    return relative if relative.suffix.lower() in DJ_SAFE else relative.with_suffix('.mp3')


def _to_mp3(source: Path, target: Path) -> None:
    if not shutil.which('ffmpeg'):
        raise ValueError('ffmpeg is not installed, so %s cannot be converted to MP3' % source.name)
    done = subprocess.run(
        ['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-map', '0:a:0',
         '-c:a', 'libmp3lame', '-b:a', EXPORT_MP3_BITRATE,
         '-map_metadata', '0', '-id3v2_version', '3', str(target)],
        capture_output=True, text=True, timeout=300)
    if done.returncode != 0 or not target.is_file() or not target.stat().st_size:
        lines = (done.stderr or '').strip().splitlines()
        raise ValueError('could not convert %s to MP3 for export: %s'
                         % (source.name, lines[-1] if lines else 'ffmpeg failed'))


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
    stage = Path(tempfile.mkdtemp(prefix='hl-export-audio-'))
    manifest = ['#EXTM3U', '#PLAYLIST:HardlyListening %s' % name]
    try:
        prepared = []
        for index, (track, path, relative) in enumerate(entries):
            archived = _archived_name(relative)
            source = path
            if archived != relative:                 # not DJ-safe: one delivery copy
                source = stage / ('%04d.mp3' % index)
                _to_mp3(path, source)
            prepared.append((source, archived))
        with zipfile.ZipFile(handle.name, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
            for (track, path, relative), (source, archived) in zip(entries, prepared):
                archive.write(source, archived.as_posix())
                seconds = int(track.get('duration') or 0)
                manifest.append('#EXTINF:%d,%s - %s' % (seconds, track.get('artist_name') or '', track.get('title') or ''))
                manifest.append(archived.as_posix())
            archive.writestr(name + '.m3u8', '\n'.join(manifest) + '\n')
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return handle.name, 'hardlylistening-%s.zip' % name, len(entries)

