"""Bounded metadata-only LRCLIB lookup and read-only canonical LRC sidecars."""
import hashlib
import json
import math
import re
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LIMIT = 512 * 1024


def parse_lrc(text):
    """LRC line timing; positive offset advances displayed timestamps (milliseconds)."""
    offsets = re.findall(r'\[offset:([+-]?\d{1,9})\]', text, re.I)
    offset = int(offsets[-1]) / 1000 if offsets else 0
    lines = []
    for raw in text.splitlines():
        stamps = list(re.finditer(r'\[(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?\]', raw))
        if not stamps:
            continue
        body = raw[stamps[-1].end():].strip()
        for stamp in stamps:
            fraction = stamp[3] or ''
            seconds = int(stamp[1]) * 60 + int(stamp[2]) + (int(fraction) / 10 ** len(fraction) if fraction else 0)
            lines.append({'time': max(0, seconds - offset), 'text': body})
    return sorted(lines, key=lambda line: line['time'])


def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(value or '')).casefold() if c.isalnum())


def matches(track, data):
    if not isinstance(data, dict):
        return False
    if not norm(track.get('title')) or not norm(track.get('artist_name')):
        return False
    if norm(track['title']) != norm(data.get('trackName', '')) or norm(track['artist_name']) != norm(data.get('artistName', '')):
        return False
    if track.get('album') and norm(track['album']) != norm(data.get('albumName', '')):
        return False
    try:
        duration = float(track.get('duration') or 0)
        candidate = float(data.get('duration') or 0)
        if duration and (not math.isfinite(candidate) or abs(duration - candidate) > 3):
            return False
    except (ValueError, TypeError):
        return False
    return True


def payload(data, source):
    synced = data.get('syncedLyrics')
    plain = data.get('plainLyrics')
    lines = parse_lrc(synced) if isinstance(synced, str) else []
    if data.get('instrumental') is True:
        return {'status': 'instrumental', 'source': source, 'lines': [], 'text': ''}
    if lines:
        return {'status': 'synced', 'source': source, 'lines': lines, 'text': plain if isinstance(plain, str) else '\n'.join(x['text'] for x in lines)}
    if isinstance(plain, str) and plain.strip():
        return {'status': 'plain', 'source': source, 'lines': [], 'text': plain}
    return {'status': 'not_found', 'source': source, 'lines': [], 'text': ''}


def lookup(track):
    query = {'artist_name': track.get('artist_name') or '', 'track_name': track.get('title') or '', 'album_name': track.get('album') or ''}
    if track.get('duration'):
        query['duration'] = str(track['duration'])
    request = Request('https://lrclib.net/api/get?' + urlencode(query), headers={'User-Agent': 'HardlyListening/1.0 (lyrics lookup)', 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('Lyrics response too large')
        data = json.loads(raw)
        return payload(data, 'LRCLIB') if matches(track, data) else payload({}, 'LRCLIB')
    except HTTPError as exc:
        if exc.code == 404:
            return payload({}, 'LRCLIB')
        return {'status': 'unavailable', 'source': 'LRCLIB', 'lines': [], 'text': ''}
    except (OSError, ValueError, TypeError):
        return {'status': 'unavailable', 'source': 'LRCLIB', 'lines': [], 'text': ''}


def get(store, identity):
    from library.browser_downloads import local_path
    with store.lock:
        row = store.conn.execute('SELECT * FROM tracks WHERE id=?', (identity,)).fetchone()
        if row is None:
            return None
        track = dict(row)
    audio = local_path(store, track)
    if audio:
        sidecar = audio.with_suffix('.lrc').resolve()
        root = __import__('pathlib').Path(store.setting('root')).expanduser().resolve()
        if root in sidecar.parents and sidecar.is_file():
            try:
                with sidecar.open('rb') as handle:
                    raw = handle.read(LIMIT + 1)
                if len(raw) <= LIMIT:
                    text = raw.decode('utf-8-sig')
                    return payload({'syncedLyrics': text, 'plainLyrics': '\n'.join(line for line in text.splitlines() if not line.startswith('['))}, 'Local LRC')
            except (OSError, UnicodeError):
                pass
    key = hashlib.sha256(json.dumps([track.get(k) for k in ('artist_name', 'title', 'album', 'duration')], ensure_ascii=False).encode()).hexdigest()
    # Isolated additive migration; no mutations to media, tracks, jobs or membership.
    with store.lock:
        store.conn.execute('CREATE TABLE IF NOT EXISTS lyrics_cache (cache_key TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT NOT NULL)')
        cached = store.conn.execute('SELECT fetched,payload FROM lyrics_cache WHERE cache_key=?', (key,)).fetchone()
        if cached and time.time() - cached['fetched'] < 86400:
            return json.loads(cached['payload'])
    result = lookup(track)
    with store.lock:
        store.conn.execute('INSERT OR REPLACE INTO lyrics_cache VALUES (?,?,?)', (key, time.time() if result['status'] != 'unavailable' else time.time() - 86340, json.dumps(result)))
        store.conn.commit()
    return result

# autodeploy smoke test
