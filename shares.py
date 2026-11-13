"""Public share links (Phase 2) and their no-login player.

A share is a capability URL: 32 random bytes, kept beside its SHA-256 so the member
who made it can be shown the same link again (HL-207). Lookup still goes through the
hash. It exposes exactly one track, album or playlist and nothing about its owner or
the wider library. Every share expires (24 hours by default; the caller may choose a
later expiry, capped at 30 days). Members and admins may mint links; party guests and
signed-out visitors may not.
"""
from __future__ import annotations

import hashlib
import html
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlparse

from security import Denied

DEFAULT_TTL = 24 * 3600
MAX_TTL = 30 * 24 * 3600
BLANK_PNG = None  # imported lazily from subsonic to keep this module import-light


def schema(store):
    """Idempotent mirror of the db.SCHEMA tables, for stores opened before this release."""
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_bookmarks (
          member_id TEXT NOT NULL, track_id INTEGER NOT NULL,
          position_ms INTEGER NOT NULL DEFAULT 0, comment TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL, updated REAL NOT NULL,
          PRIMARY KEY (member_id, track_id));
        CREATE TABLE IF NOT EXISTS listening_shares (
          id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
          token TEXT NOT NULL DEFAULT '',
          member_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('track','album','playlist')),
          target TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL, expires REAL NOT NULL,
          hits INTEGER NOT NULL DEFAULT 0);
        ''')
        # A store opened before HL-207 has the table without the token column; CREATE
        # TABLE IF NOT EXISTS will not add it, and this mirror is the only schema path
        # shares.py owns.
        present = {row[1] for row in store.conn.execute(
            'PRAGMA table_info(listening_shares)')}
        if 'token' not in present:
            store.conn.execute(
                "ALTER TABLE listening_shares ADD COLUMN token TEXT NOT NULL DEFAULT ''")
        store.conn.commit()


def iso(seconds):
    value = float(seconds or 0)
    if value <= 0:
        return '1970-01-01T00:00:00.000Z'
    return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(value))


def album_key(artist, album):
    return 'al_' + hashlib.sha256(
        json.dumps([artist or '', album or ''], ensure_ascii=True).encode()).hexdigest()


def parse_expiry(value, now=None):
    """Accept epoch seconds/milliseconds or ISO-8601; return a future timestamp."""
    now = time.time() if now is None else now
    if value in (None, ''):
        return now + DEFAULT_TTL
    if isinstance(value, bool):
        raise Denied(400, 'Invalid expiry')
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 10 ** 11:                 # milliseconds
            stamp /= 1000.0
    else:
        text = str(value).strip()
        if text.isdigit():
            return parse_expiry(int(text), now)
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            raise Denied(400, 'Expiry must be ISO-8601 or epoch seconds')
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        stamp = parsed.timestamp()
    if stamp <= now:
        raise Denied(400, 'Expiry must be in the future')
    if stamp > now + MAX_TTL:
        raise Denied(400, 'Expiry may be at most 30 days away')
    return stamp


def _track_rows(store, share):
    """The shared item's tracks, read from the catalogue, never a member's scope."""
    kind, target = share['kind'], share['target']
    with store.lock:
        if kind == 'track':
            try:
                track_id = int(target)
            except ValueError:
                return []
            row = store.conn.execute('SELECT * FROM tracks WHERE id=?', (track_id,)).fetchone()
            return [dict(row)] if row else []
        if kind == 'playlist':
            try:
                playlist_id = int(target)
            except ValueError:
                return []
            return [dict(r) for r in store.conn.execute(
                'SELECT t.* FROM tracks t JOIN playlist_tracks p ON p.track_id=t.id '
                'WHERE p.playlist_id=? ORDER BY p.position,p.track_id', (playlist_id,))]
        rows = [dict(r) for r in store.conn.execute(
            'SELECT * FROM tracks ORDER BY artist_name,album,track_number,id')]
    return [r for r in rows if album_key(r.get('artist_name'), r.get('album')) == target]


def share_target(store, kind, target):
    """Resolve and validate a share target. Returns the canonical target string."""
    if kind == 'track':
        try:
            track_id = int(target)
        except (TypeError, ValueError):
            raise Denied(400, 'Track id must be an integer')
        if not store.conn.execute('SELECT id FROM tracks WHERE id=?', (track_id,)).fetchone():
            raise Denied(404, 'Track not found')
        return str(track_id)
    if kind == 'playlist':
        try:
            playlist_id = int(target)
        except (TypeError, ValueError):
            raise Denied(400, 'Playlist id must be an integer')
        if not store.conn.execute('SELECT id FROM playlists WHERE id=?', (playlist_id,)).fetchone():
            raise Denied(404, 'Playlist not found')
        return str(playlist_id)
    if kind == 'album':
        if not any(album_key(r[0], r[1]) == target for r in store.conn.execute(
                'SELECT DISTINCT artist_name,album FROM tracks')):
            raise Denied(404, 'Album not found')
        return target
    raise Denied(400, 'kind must be track, album or playlist')


def tracks_for(store, share):
    return _track_rows(store, share)


def cover_for(store, share):
    kind = share['kind']
    rows = _track_rows(store, share)
    for row in rows:
        if row.get('artwork'):
            return row['artwork']
    if kind == 'album' and rows:
        return ''
    if kind == 'playlist':
        try:
            row = store.conn.execute('SELECT artwork FROM playlists WHERE id=?',
                                     (int(share['target']),)).fetchone()
            if row and row['artwork']:
                return row['artwork']
        except (ValueError, sqlite3.Error):
            pass
    return ''


def create(store, member, kind, target, note='', expires=None, security=None):
    schema(store)
    if not member or member.get('role') not in ('member', 'admin'):
        raise Denied(403, 'Only members and admins may create shares')
    kind = str(kind or '').strip().lower()
    target = share_target(store, kind, target)
    if not isinstance(note, str) or len(note) > 500:
        raise Denied(400, 'Note may be at most 500 characters')
    now = time.time()
    expires_at = parse_expiry(expires, now)
    token = secrets.token_urlsafe(32)        # 32 random bytes
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.lock:
        cursor = store.conn.execute(
            'INSERT INTO listening_shares'
            '(token_hash,token,member_id,kind,target,note,created,expires) '
            'VALUES(?,?,?,?,?,?,?,?)',
            (digest, token, member['id'], kind, target, note.strip(), now, expires_at))
        store.conn.commit()
        share_id = cursor.lastrowid
    return {'id': share_id, 'token': token, 'kind': kind, 'target': target,
            'note': note.strip(), 'created': now, 'expires': expires_at,
            'url': public_url(security, token)}


def public_url(security, token):
    origin = ''
    try:
        origin = (security.config()[2] if security else '').rstrip('/')
    except Exception:
        origin = ''
    return origin + '/s/' + token


def lookup(store, token):
    if not token or len(token) > 256:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.lock:
        row = store.conn.execute(
            'SELECT * FROM listening_shares WHERE token_hash=? AND expires>?',
            (digest, time.time())).fetchone()
    return dict(row) if row else None


def _owned(store, member, share_id):
    with store.lock:
        row = store.conn.execute('SELECT * FROM listening_shares WHERE id=?',
                                 (share_id,)).fetchone()
    if not row:
        raise Denied(404, 'Share not found')
    if row['member_id'] != member['id'] and member.get('role') != 'admin':
        raise Denied(403, 'Only the share owner may change it')
    return dict(row)


def update(store, member, share_id, note=None, expires=None):
    share = _owned(store, member, share_id)
    fields, values = [], []
    new_note, new_expires = share['note'], share['expires']
    if note is not None:
        if not isinstance(note, str) or len(note) > 500:
            raise Denied(400, 'Note may be at most 500 characters')
        new_note = note.strip()
        fields.append('note=?')
        values.append(new_note)
    if expires is not None:
        new_expires = parse_expiry(expires)
        fields.append('expires=?')
        values.append(new_expires)
    if not fields:
        raise Denied(400, 'Nothing to update')
    values.append(share['id'])
    with store.lock:
        store.conn.execute('UPDATE listening_shares SET ' + ', '.join(fields) + ' WHERE id=?', values)
        store.conn.commit()
    share.update({'note': new_note, 'expires': new_expires})
    return share


def delete(store, member, share_id):
    share = _owned(store, member, share_id)
    with store.lock:
        store.conn.execute('DELETE FROM listening_shares WHERE id=?', (share['id'],))
        store.conn.commit()
    return share


def listing(store, member, security):
    schema(store)
    now = time.time()
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            'SELECT * FROM listening_shares WHERE member_id=? AND expires>? ORDER BY created DESC,id DESC',
            (member['id'], now))]
    # The URL comes back for the member who owns the share. A row created before the
    # token was kept has no URL to show: `recreate` says so instead of handing back
    # `/s/` and looking broken.
    out = []
    for r in rows:
        token = r.get('token') or ''
        out.append({'id': r['id'], 'kind': r['kind'], 'target': r['target'],
                    'note': r['note'], 'created': iso(r['created']),
                    'expires': iso(r['expires']), 'hits': r['hits'],
                    'url': public_url(security, token) if token else '',
                    'recreate': not token})
    return out


def render_page(store, share, rows, token):
    title = 'Shared music'
    if len(rows) == 1:
        title = rows[0].get('title') or title
    elif share['kind'] == 'album' and rows:
        title = rows[0].get('album') or title
    elif share['kind'] == 'playlist':
        try:
            with store.lock:
                row = store.conn.execute('SELECT title FROM playlists WHERE id=?',
                                         (int(share['target']),)).fetchone()
            title = (row and row['title']) or title
        except (ValueError, sqlite3.Error):
            pass
    items = []
    for row in rows:
        src = '/s/%s/stream?track=%s' % (quote(token, safe=''), row['id'])
        items.append(
            '<li><div class="t">%s</div><div class="a">%s</div>'
            '<audio controls preload="none" src="%s"></audio></li>' % (
                html.escape(row.get('title') or ''), html.escape(row.get('artist_name') or ''),
                html.escape(src, quote=True)))
    body = '\n'.join(items) or '<li>This share has no playable tracks.</li>'
    cover = '/s/%s/cover' % quote(token, safe='')
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>%s</title>
<style>
body{margin:0;background:#101014;color:#eee;font:16px system-ui,sans-serif}
main{max-width:42rem;margin:0 auto;padding:2rem 1rem}
h1{font-size:1.4rem;margin:0 0 .25rem}
.note{color:#aaa;margin:0 0 1.5rem}
img{width:100%%;max-width:20rem;border-radius:.75rem;display:block;margin:0 0 1.5rem}
ul{list-style:none;padding:0;margin:0}
li{padding:.9rem 0;border-bottom:1px solid #26262e}
.t{font-weight:600}.a{color:#aaa;font-size:.9rem;margin:.15rem 0 .5rem}
audio{width:100%%}
footer{color:#777;font-size:.8rem;margin-top:2rem}
</style></head><body><main>
<img src="%s" alt="">
<h1>%s</h1>
<p class="note">%s</p>
<ul>%s</ul>
<footer>This private link expires %s.</footer>
</main></body></html>''' % (html.escape(title), html.escape(cover, quote=True),
                             html.escape(title), html.escape(share['note'] or ''),
                             body, html.escape(iso(share['expires'])))


def public(handler, store, security):
    """Serve /s/<token>, /s/<token>/stream and /s/<token>/cover without login."""
    schema(store)
    path = urlparse(handler.path).path
    parts = path.split('/')
    token = parts[2] if len(parts) > 2 else ''
    security.limit('share:' + hashlib.sha256(token.encode()).hexdigest(), 'share-access', 120)
    share = lookup(store, token)
    if share is None:
        handler._send(404, b'Share not found or expired', 'text/plain; charset=utf-8')
        return
    action = parts[3] if len(parts) > 3 else ''
    rows = _track_rows(store, share)
    if action == 'stream':
        wanted = (parse_qs(urlparse(handler.path).query).get('track') or [''])[0]
        allowed = {str(row['id']): row for row in rows}
        chosen = wanted or (str(rows[0]['id']) if rows else '')
        if chosen not in allowed:
            handler._send(404, b'Track not in this share', 'text/plain; charset=utf-8')
            return
        handler.stream_audio(int(chosen))
        return
    if action == 'cover':
        art = cover_for(store, share)
        if art:
            handler.send_response(302)
            handler.send_header('Location', art)
            handler.send_header('Cache-Control', 'public, max-age=86400')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return
        from subsonic import BLANK_PNG as blank
        handler._send(200, blank, 'image/png')
        return
    if action:
        handler._send(404, b'Not found', 'text/plain; charset=utf-8')
        return
    with store.lock:
        store.conn.execute('UPDATE listening_shares SET hits=hits+1 WHERE id=?', (share['id'],))
        store.conn.commit()
    handler._send(200, render_page(store, share, rows, token).encode(), 'text/html; charset=utf-8')


def dispatch(handler, store, security):
    """Member-only JSON API: GET/POST /api/shares, DELETE /api/shares/<id>."""
    schema(store)
    # A party guest bears only the party cookie; that is not a member account.
    if security.cookie(handler, '__Host-hl_party') and not security.cookie(handler, '__Host-hl_session'):
        raise Denied(403, 'Guests cannot create or manage shares')
    member = security.authenticate(handler, store)
    path = urlparse(handler.path).path
    if handler.command == 'GET' and path == '/api/shares':
        handler.json({'shares': listing(store, member, security)})
        return
    if handler.command == 'POST' and path == '/api/shares':
        security.limit(member['id'], 'share-create', 30)
        body = handler.body()
        kind = body.get('kind')
        target = body.get('id', body.get('target'))
        result = create(store, member, kind, target, body.get('note', ''),
                        body.get('expires'), security)
        handler.json({'id': result['id'], 'token': result['token'], 'url': result['url'],
                      'kind': result['kind'], 'expires': iso(result['expires'])})
        return
    if handler.command == 'DELETE' and path.startswith('/api/shares/'):
        raw = path.rsplit('/', 1)[-1]
        if not raw.isdigit():
            raise Denied(400, 'Share id must be an integer')
        delete(store, member, int(raw))
        handler.json({'ok': True})
        return
    raise Denied(404, 'No such endpoint')
