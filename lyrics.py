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

# `[mm:ss.xx]` starts a line; `<mm:ss.xx>` inside that line starts a word. Enhanced LRC puts
# the second inside the body, which is why the line body has to be cut before words are read.
LINE_STAMP = re.compile(r'\[(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?\]')
WORD_STAMP = re.compile(r'<(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?>')


def _clock(minutes, seconds, fraction):
    fraction = fraction or ''
    return (int(minutes) * 60 + int(seconds)
            + (int(fraction) / 10 ** len(fraction) if fraction else 0))


def parse_lrc(text):
    """LRC line timing; positive offset advances displayed timestamps (milliseconds).

    Word timing is the same file with `<mm:ss.xx>` marks inside a line's body. A line that has
    them carries `words` (`[{'time', 'text'}]`, in order); a line that does not is exactly the
    shape it always was, so a plain LRC and every existing reader are unchanged.
    """
    offsets = re.findall(r'\[offset:([+-]?\d{1,9})\]', text, re.I)
    offset = int(offsets[-1]) / 1000 if offsets else 0
    lines = []
    for raw in text.splitlines():
        stamps = list(LINE_STAMP.finditer(raw))
        if not stamps:
            continue
        body = raw[stamps[-1].end():]
        marks = list(WORD_STAMP.finditer(body))
        # The marks are timings, not words: the printed line must not show them.
        printed = WORD_STAMP.sub('', body).strip()
        words = []
        for index, mark in enumerate(marks):
            end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
            piece = body[mark.end():end].strip()
            if piece:
                words.append({'time': max(0, _clock(*mark.groups()) - offset), 'text': piece})
        for stamp in stamps:
            line = {'time': max(0, _clock(*stamp.groups()) - offset), 'text': printed}
            if words:
                line['words'] = [dict(word) for word in words]
            lines.append(line)
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
        return {'status': 'instrumental', 'source': source, 'lines': [], 'text': '', 'word_timed': False}
    if lines:
        return {'status': 'synced', 'source': source, 'lines': lines,
                'text': plain if isinstance(plain, str) else '\n'.join(x['text'] for x in lines),
                'word_timed': any(line.get('words') for line in lines)}
    if isinstance(plain, str) and plain.strip():
        return {'status': 'plain', 'source': source, 'lines': [], 'text': plain, 'word_timed': False}
    return {'status': 'not_found', 'source': source, 'lines': [], 'text': '', 'word_timed': False}


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
        exc.close()
        if exc.code == 404:
            return payload({}, 'LRCLIB')
        return {'status': 'unavailable', 'source': 'LRCLIB', 'lines': [], 'text': ''}
    except (OSError, ValueError, TypeError):
        return {'status': 'unavailable', 'source': 'LRCLIB', 'lines': [], 'text': ''}


# A member's correction lives against the track, not against the provider row it replaced, so
# a new provider copy, a refreshed cache or a redeploy cannot undo it. One row per track: the
# library is shared, so the correction is shared, and the row records who made it.
EDITS = """CREATE TABLE IF NOT EXISTS lyrics_edits (
  track_id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  payload TEXT NOT NULL,
  member_id TEXT NOT NULL DEFAULT '',
  member_name TEXT NOT NULL DEFAULT '',
  updated REAL NOT NULL
)"""


def _edit(store, identity):
    with store.lock:
        store.conn.execute(EDITS)
        row = store.conn.execute('SELECT payload FROM lyrics_edits WHERE track_id=?',
                                 (identity,)).fetchone()
    return json.loads(row['payload']) if row else None


def save_edit(store, identity, text, member=None):
    """Store a member's correction; None when the track does not exist.

    The text is the lyrics themselves, in LRC form, so an edited line keeps its timestamp and
    word timings survive if the member left the `<mm:ss.xx>` marks in place. A correction with
    no timestamps is stored as plain lyrics rather than refused.
    """
    text = str(text or '')
    if len(text.encode('utf-8')) > LIMIT:
        raise ValueError('That is more lyrics than this app keeps (512 KB).')
    if not text.strip():
        raise ValueError('Give the lyric lines to save.')
    with store.lock:
        if store.conn.execute('SELECT 1 FROM tracks WHERE id=?', (identity,)).fetchone() is None:
            return None
    plain = '\n'.join(line for line in text.splitlines() if not LINE_STAMP.search(line))
    result = payload({'syncedLyrics': text, 'plainLyrics': plain}, 'Edited copy')
    result['editor'] = str((member or {}).get('display_name') or '')
    result['edited'] = time.time()
    with store.lock:
        store.conn.execute(EDITS)
        store.conn.execute('INSERT OR REPLACE INTO lyrics_edits VALUES (?,?,?,?,?,?)',
                           (identity, text, json.dumps(result), str((member or {}).get('id') or ''),
                            result['editor'], result['edited']))
        store.conn.commit()
    return result


def clear_edit(store, identity):
    """Drop the correction and answer with what the track says now; None when it is gone."""
    with store.lock:
        store.conn.execute(EDITS)
        known = store.conn.execute('SELECT 1 FROM tracks WHERE id=?', (identity,)).fetchone()
        store.conn.execute('DELETE FROM lyrics_edits WHERE track_id=?', (identity,))
        store.conn.commit()
    if known is None:
        return None
    return get(store, identity)


def get(store, identity):
    from library.browser_downloads import local_path
    with store.lock:
        row = store.conn.execute('SELECT * FROM tracks WHERE id=?', (identity,)).fetchone()
        if row is None:
            return None
        track = dict(row)
    # A member's correction outlives a reload and a provider refresh, and it is stored against
    # the track rather than against the copy it replaced. It wins over the sidecar too: somebody
    # editing in the app is a later, more specific statement than the file on disk.
    edited = _edit(store, identity)
    if edited:
        return edited
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
