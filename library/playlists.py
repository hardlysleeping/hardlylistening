"""Disposable playlist views. SQLite owns membership; audio is never copied."""
import json
import os
from pathlib import Path
import re
import tempfile

MARKER = '.hardlylistening-playlist.json'
FILES = ('playlist.m3u8', 'playlist.txt')


def line(value):
    return ' '.join(str(value or '').split())


def folder(title, pid):
    slug = re.sub(r'[/\\<>:"|?*\x00-\x1f]', '_', line(title)).strip(' .')
    return f'{slug[:100] or "Playlist"}--{int(pid)}'


def atomic(path, text):
    if path.is_symlink():
        raise OSError(f'refusing symlink: {path}')
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    fd, name = tempfile.mkstemp(prefix='.projection-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as out:
            out.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def owned(directory):
    try:
        return (not directory.is_symlink() and
                not (directory / MARKER).is_symlink() and
                json.loads((directory / MARKER).read_text()) == {'managed_by': 'hardlylistening', 'version': 1})
    except (OSError, ValueError):
        return False


def clean(directory):
    if not owned(directory):
        return
    for name in (*FILES, MARKER):
        path = directory / name
        if path.is_file() and not path.is_symlink():
            path.unlink()
    try:
        directory.rmdir()
    except OSError:
        pass  # User files are never ours to remove.


def sync(store):
    """One locked snapshot, atomic file replacement; no writes during state polls."""
    with store.lock:
        root = Path(store.setting('root')).expanduser() / 'Playlists'
        if root.is_symlink():
            raise OSError('refusing symlink playlist root')
        playlists = [dict(r) for r in store.conn.execute('SELECT * FROM playlists')]
        tracks = store.library(include_pending=True)
        by_playlist = {}
        for track in tracks:
            for membership in track['playlists']:
                by_playlist.setdefault(membership['id'], []).append((membership['position'], track))
        wanted = set()
        if playlists:
            root.mkdir(parents=True, exist_ok=True)
        for playlist in playlists:
            directory = root / folder(playlist['title'], playlist['id'])
            wanted.add(directory.name)
            if directory.exists() and not owned(directory):
                raise OSError(f'refusing unmanaged playlist folder: {directory}')
            directory.mkdir(exist_ok=True)
            atomic(directory / MARKER, json.dumps({'managed_by': 'hardlylistening', 'version': 1}))
            m3u = ['#EXTM3U']
            manifest = [line(playlist['title']), 'SQLite playlist: ' + str(playlist['id']), '']
            for index, (_, track) in enumerate(sorted(by_playlist.get(playlist['id'], []), key=lambda item: (item[0], item[1]['id'])), 1):
                label = line(track['artist_name']) + ' — ' + line(track['title'])
                path = Path(track['file']) if track['file'] else None
                relative = os.path.relpath(path, directory) if path else ''
                # M3U cannot safely represent newline-containing filenames.
                playable = track['playable'] and not any(c in relative for c in '\r\n\x00')
                status = track['status']
                manifest.append(f'{index}. [{status}] {label} (track {track["id"]})' +
                                (f' | {line(relative)}' if playable else ' | pending/missing: no playable entry'))
                if playable:
                    m3u.extend([f'#EXTINF:{int(track["duration"] or -1)},{label}', relative])
            atomic(directory / 'playlist.m3u8', '\n'.join(m3u) + '\n')
            atomic(directory / 'playlist.txt', '\n'.join(manifest) + '\n')
        if root.exists():
            for directory in root.iterdir():
                if directory.is_dir() and directory.name not in wanted:
                    clean(directory)
        previous = getattr(store, '_projection_root', None)
        if previous and previous != root and previous.exists():
            for directory in previous.iterdir():
                if directory.is_dir():
                    clean(directory)
        store._projection_root = root
