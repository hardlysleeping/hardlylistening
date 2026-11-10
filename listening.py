"""Personal saved scope and read-only Subsonic subset. No acquisition/network IO.

Device secrets are random, returned once and SHA256-hashed at rest. This first
milestone supports password authentication (p), not legacy MD5 salt/token auth.
Saving a playlist snapshots its current membership; later catalog additions do
not silently expand a member's scope. Removing a save never deletes media.
"""
import hashlib
import hmac
import json
import secrets
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse
from security import SECURITY, Denied
from library.browser_downloads import local_path


def schema(store, security):
    security.schema(store)
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_devices (
          id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id),
          label TEXT NOT NULL, digest TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
          created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS listening_tracks (
          member_id TEXT NOT NULL REFERENCES members(id),
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          PRIMARY KEY(member_id,track_id));
        CREATE TABLE IF NOT EXISTS listening_playlists (
          member_id TEXT NOT NULL REFERENCES members(id),
          playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
          PRIMARY KEY(member_id,playlist_id));
        CREATE TABLE IF NOT EXISTS listening_playlist_tracks (
          member_id TEXT NOT NULL, playlist_id INTEGER NOT NULL,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position INTEGER NOT NULL,
          PRIMARY KEY(member_id,playlist_id,track_id),
          FOREIGN KEY(member_id,playlist_id) REFERENCES listening_playlists(member_id,playlist_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS listening_folders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
          parent_id INTEGER, name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
          created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(member_id,id),
          FOREIGN KEY(member_id,parent_id) REFERENCES listening_folders(member_id,id) ON DELETE CASCADE);
        -- A download that should join someone's library once the file is
        -- written. Filled when Download is pressed on the Feed, emptied by the
        -- queue: a queued row would list a song that cannot play yet, and a
        -- failed one would list a song that never will.
        CREATE TABLE IF NOT EXISTS library_intents (
          member_id TEXT NOT NULL REFERENCES members(id),
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          PRIMARY KEY(member_id,track_id));
        CREATE TABLE IF NOT EXISTS listening_folder_tracks (
          member_id TEXT NOT NULL, folder_id INTEGER NOT NULL,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(member_id,folder_id,track_id),
          FOREIGN KEY(member_id,folder_id) REFERENCES listening_folders(member_id,id) ON DELETE CASCADE);
        ''')
        store.conn.commit()


def want_playlist(store, member_id: str, playlist_id: int) -> int:
    """Downloading from the Feed also puts the release in the library.

    Songs the repository already holds go in straight away; the rest wait in
    `library_intents` so the library is never given a placeholder row.
    """
    if not member_id or not playlist_id:
        return 0
    schema(store, SECURITY)
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            'SELECT t.* FROM tracks t JOIN playlist_tracks pt ON pt.track_id=t.id'
            ' WHERE pt.playlist_id=?', (playlist_id,))]
        for track in rows:
            if local_path(store, track):
                store.conn.execute(
                    'INSERT OR IGNORE INTO listening_tracks(member_id,track_id) VALUES(?,?)',
                    (member_id, track['id']))
            else:
                store.conn.execute(
                    'INSERT OR IGNORE INTO library_intents(member_id,track_id) VALUES(?,?)',
                    (member_id, track['id']))
        store.conn.commit()
    return len(rows)


def deliver_track(store, track_id: int) -> int:
    """The queue wrote this track: hand it to everyone who pressed Download."""
    schema(store, SECURITY)
    with store.lock:
        members = [r[0] for r in store.conn.execute(
            'SELECT member_id FROM library_intents WHERE track_id=?', (track_id,))]
        for member_id in members:
            store.conn.execute(
                'INSERT OR IGNORE INTO listening_tracks(member_id,track_id) VALUES(?,?)',
                (member_id, track_id))
        store.conn.execute('DELETE FROM library_intents WHERE track_id=?', (track_id,))
        store.conn.commit()
    return len(members)


def forget_track(store, track_id: int) -> None:
    """The download ended without a file; drop the promise rather than save it."""
    schema(store, SECURITY)
    with store.lock:
        store.conn.execute('DELETE FROM library_intents WHERE track_id=?', (track_id,))
        store.conn.commit()


def browser(handler, store, security):
    if handler.command == 'POST':
        handler.close_connection = True
    if urlparse(handler.path).path == '/api/listening/now':
        import nowplaying
        return nowplaying.dispatch(handler, store, security)
    if urlparse(handler.path).path.startswith('/api/listening/preferences') \
            or urlparse(handler.path).path == '/api/listening/pin-order':
        import preferences
        return preferences.browser(handler, store, security)
    schema(store, security)
    member = security.authenticate(handler, store)
    handler.member = member
    uid = member['id']
    path = urlparse(handler.path).path
    if path == '/api/listening/export.zip':
        if handler.command != 'GET':
            raise Denied(405, 'Method not allowed')
        import os
        from urllib.parse import parse_qs, quote as _quote
        import zip_export
        security.limit(uid, 'export-zip', 6)
        raw = (parse_qs(urlparse(handler.path).query).get('playlist') or [''])[0]
        playlist_id = int(raw) if raw.isdigit() else None
        try:
            archive, name, count = zip_export.build(store, uid, playlist_id)
        except ValueError as error:
            raise Denied(400, str(error))
        size = os.path.getsize(archive)
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/zip')
        handler.send_header('Content-Disposition',
                            'attachment; filename="export.zip"; filename*=UTF-8\'\'' + _quote(name, safe=''))
        handler.send_header('Content-Length', str(size))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.end_headers()
        try:
            with open(archive, 'rb') as source:
                while True:
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
        finally:
            try:
                os.unlink(archive)
            except OSError:
                pass
        handler.close_connection = True
        return
    with store.lock:
        if handler.command == 'GET':
            if path == '/api/listening/devices':
                rows = [dict(r) for r in store.conn.execute(
                    'SELECT id,label,revoked,created FROM listening_devices WHERE member_id=? ORDER BY created,id', (uid,))]
                handler.json({'devices': rows})
            elif path == '/api/listening/saves':
                handler.json({'tracks': [r[0] for r in store.conn.execute('SELECT track_id FROM listening_tracks WHERE member_id=?', (uid,))],
                              'playlists': [r[0] for r in store.conn.execute('SELECT playlist_id FROM listening_playlists WHERE member_id=?', (uid,))],
                              'playlist_tracks': [dict(r) for r in store.conn.execute(
                                  'SELECT playlist_id,track_id,position FROM listening_playlist_tracks WHERE member_id=? ORDER BY playlist_id,position,track_id', (uid,))],
                              'folder_tracks': [dict(r) for r in store.conn.execute(
                                  'SELECT folder_id,track_id,position FROM listening_folder_tracks WHERE member_id=? ORDER BY folder_id,position,track_id', (uid,))]})
            elif path == '/api/listening/folders':
                folders = [dict(r) for r in store.conn.execute(
                    'SELECT id,parent_id,name,position FROM listening_folders WHERE member_id=? ORDER BY position,id', (uid,))]
                handler.json({'folders': folders})
            else:
                raise Denied(404, 'No such endpoint')
            return
        if handler.command != 'POST':
            raise Denied(405, 'Method not allowed')
        security.limit(uid, 'listening-write', 30)
        body = handler.body()
        if not isinstance(body, dict):
            raise Denied(400, 'Object required')
        if path == '/api/listening/devices':
            label = body.get('label', 'Player')
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
                raise Denied(400, 'Label must be 1–80 characters')
            if store.conn.execute('SELECT count(*) FROM listening_devices WHERE member_id=? AND revoked=0', (uid,)).fetchone()[0] >= 20:
                raise Denied(400, 'Revoke an existing device first')
            identity, password = 'hl_' + secrets.token_hex(16), secrets.token_urlsafe(32)
            store.conn.execute('INSERT INTO listening_devices(id,member_id,label,digest) VALUES(?,?,?,?)',
                               (identity, uid, label.strip(), hashlib.sha256(password.encode()).hexdigest()))
            store.conn.commit()
            handler.json({'username': identity, 'password': password})
            return
        if path == '/api/listening/devices/revoke':
            store.conn.execute('UPDATE listening_devices SET revoked=1 WHERE id=? AND member_id=?', (str(body.get('id', '')), uid))
        elif path == '/api/listening/saves':
            kind, identity, saved = body.get('kind'), body.get('id'), body.get('saved')
            if kind not in ('track', 'playlist') or type(identity) is not int or identity < 1 or type(saved) is not bool:
                raise Denied(400, 'kind, positive integer id and boolean saved required')
            table = 'tracks' if kind == 'track' else 'playlists'
            if not store.conn.execute('SELECT id FROM ' + table + ' WHERE id=?', (identity,)).fetchone():
                raise Denied(404, 'Not found')
            target = 'listening_' + table
            column = kind + '_id'
            if saved:
                store.conn.execute(f'INSERT OR IGNORE INTO {target}(member_id,{column}) VALUES(?,?)', (uid, identity))
                if kind == 'playlist':
                    store.conn.execute('INSERT OR IGNORE INTO listening_playlist_tracks SELECT ?,playlist_id,track_id,position FROM playlist_tracks WHERE playlist_id=?', (uid, identity))
            else:
                store.conn.execute(f'DELETE FROM {target} WHERE member_id=? AND {column}=?', (uid, identity))
        elif path == '/api/listening/folders':
            action = body.get('action')
            if action == 'create':
                name, parent = body.get('name'), body.get('parent_id')
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
                    raise Denied(400, 'Folder name must be 1–80 printable characters')
                if parent is not None and (type(parent) is not int or parent < 1 or not store.conn.execute(
                        'SELECT id FROM listening_folders WHERE id=? AND member_id=?', (parent, uid)).fetchone()):
                    raise Denied(404, 'Parent folder not found')
                clean = name.strip()
                if store.conn.execute('SELECT id FROM listening_folders WHERE member_id=? AND parent_id IS ? AND lower(name)=lower(?)',
                                      (uid, parent, clean)).fetchone():
                    raise Denied(409, 'A folder with that name already exists here')
                position = store.conn.execute('SELECT COALESCE(MAX(position),-1)+1 FROM listening_folders WHERE member_id=? AND parent_id IS ?',
                                              (uid, parent)).fetchone()[0]
                cur = store.conn.execute('INSERT INTO listening_folders(member_id,parent_id,name,position) VALUES(?,?,?,?)',
                                         (uid, parent, clean, position))
                folder_id = cur.lastrowid
            elif action in ('rename', 'delete'):
                folder_id, name = body.get('folder_id'), body.get('name')
                if type(folder_id) is not int or folder_id < 1:
                    raise Denied(400, 'Positive folder_id required')
                row = store.conn.execute('SELECT id,parent_id FROM listening_folders WHERE id=? AND member_id=?', (folder_id, uid)).fetchone()
                if not row:
                    raise Denied(404, 'Folder not found')
                if action == 'delete':
                    store.conn.execute('DELETE FROM listening_folders WHERE id=? AND member_id=?', (folder_id, uid))
                else:
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
                        raise Denied(400, 'Folder name must be 1–80 printable characters')
                    clean = name.strip()
                    if store.conn.execute('SELECT id FROM listening_folders WHERE member_id=? AND parent_id IS ? AND id<>? AND lower(name)=lower(?)',
                                          (uid, row['parent_id'], folder_id, clean)).fetchone():
                        raise Denied(409, 'A folder with that name already exists here')
                    store.conn.execute('UPDATE listening_folders SET name=? WHERE id=? AND member_id=?', (clean, folder_id, uid))
            elif action in ('add_track', 'remove_track'):
                folder_id, track_id = body.get('folder_id'), body.get('track_id')
                if type(folder_id) is not int or folder_id < 1 or type(track_id) is not int or track_id < 1:
                    raise Denied(400, 'Positive folder_id and track_id required')
                if not store.conn.execute('SELECT id FROM listening_folders WHERE id=? AND member_id=?', (folder_id, uid)).fetchone():
                    raise Denied(404, 'Folder not found')
                if not store.conn.execute('SELECT id FROM tracks WHERE id=?', (track_id,)).fetchone():
                    raise Denied(404, 'Track not found')
                if action == 'add_track':
                    pos = store.conn.execute('SELECT COALESCE(MAX(position),-1)+1 FROM listening_folder_tracks WHERE member_id=? AND folder_id=?',
                                             (uid, folder_id)).fetchone()[0]
                    store.conn.execute('INSERT OR IGNORE INTO listening_folder_tracks(member_id,folder_id,track_id,position) VALUES(?,?,?,?)',
                                       (uid, folder_id, track_id, pos))
                else:
                    store.conn.execute('DELETE FROM listening_folder_tracks WHERE member_id=? AND folder_id=? AND track_id=?',
                                       (uid, folder_id, track_id))
            else:
                raise Denied(400, 'Unknown folder action')
        else:
            raise Denied(404, 'No such endpoint')
        store.conn.commit()
    handler.json({'ok': True, **({'id': folder_id} if path == '/api/listening/folders' and action == 'create' else {})})


def catalog(store, uid):
    import preferences
    preferences.schema(store)
    rows = store.conn.execute('''SELECT DISTINCT t.* FROM tracks t WHERE t.id IN
      (SELECT track_id FROM listening_tracks WHERE member_id=? UNION
       SELECT track_id FROM listening_playlist_tracks WHERE member_id=? UNION
       SELECT track_id FROM listening_folder_tracks WHERE member_id=? UNION
       SELECT track_id FROM listening_favorites WHERE member_id=?) ORDER BY t.artist_name,t.album,t.track_number,t.id''', (uid, uid, uid, uid))
    return [dict(r) for r in rows]


def key(prefix, *values):
    return prefix + hashlib.sha256(json.dumps(values, ensure_ascii=True).encode()).hexdigest()


def song(t):
    return {'id': str(t['id']), 'title': t['title'], 'artist': t['artist_name'], 'album': t['album'],
            'artistId': key('ar_', t['artist_name']), 'albumId': key('al_', t['artist_name'], t['album']),
            'isDir': False, 'duration': int(t['duration'] or 0), 'track': t['track_number'] or 0,
            'type': 'music'}


def lyrics_for(store, track_id):
    """Raw lyrics payload for a track id: {'text', 'lines', ...}; empty when unavailable."""
    try:
        import lyrics
        return lyrics.get(store, track_id) or {}
    except Exception:
        return {}


def envelope(handler, params, payload=None, error=None):
    data = {'status': 'failed' if error else 'ok', 'version': '1.16.1',
            'type': 'HardlyListening', 'serverVersion': 'scoped-1'}
    data.update({'error': {'code': error[0], 'message': error[1]}} if error else (payload or {}))
    if params.get('f') == 'json':
        handler._send(200, json.dumps({'subsonic-response': data}).encode(), 'application/json')
    else:
        root = ET.Element('subsonic-response', xmlns='http://subsonic.org/restapi')
        def add(node, value):
            for k, v in value.items():
                if isinstance(v, list):
                    for item in v:
                        add(ET.SubElement(node, k), item)
                elif isinstance(v, dict):
                    add(ET.SubElement(node, k), v)
                else:
                    node.set(k, str(v).lower() if isinstance(v, bool) else str(v))
        add(root, data)
        handler._send(200, ET.tostring(root, encoding='utf-8', xml_declaration=True), 'application/xml; charset=utf-8')


def rest(handler, store, security):
    params = {}
    try:
        if len(handler.path) > 16384:
            raise Denied(400, 'Request too long')
        query = parse_qs(urlparse(handler.path).query, keep_blank_values=True, max_num_fields=60)
        if handler.command == 'POST':
            handler.close_connection = True
            if int(handler.headers.get('Content-Length', '0')) > 16384:
                raise Denied(400, 'Request too long')
            if handler.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                raise Denied(400, 'Form encoding required')
            raw = handler.rfile.read(int(handler.headers.get('Content-Length', '0')))
            form = parse_qs(raw.decode(), keep_blank_values=True, max_num_fields=60)
            if set(form) & set(query):
                raise Denied(400, 'Duplicate parameter')
            query.update(form)
        # Subsonic repeats `id` / `songId` for multi-item operations (star, scrobble, queue).
        repeated = {'id', 'songId', 'albumId', 'artistId'}
        if any(len(v) != 1 for k, v in query.items() if k not in repeated):
            raise Denied(400, 'Duplicate parameter')
        params = {k: v[0] for k, v in query.items()}
        multi = {k: v for k, v in query.items() if len(v) > 1}
        if params.get('f', 'xml') not in ('xml', 'json'):
            raise Denied(400, 'Unsupported response format')
        schema(store, security)
        security.limit(handler.client_address[0], 'device-auth', 300)
        with store.lock:
            device = store.conn.execute('''SELECT d.*,m.role FROM listening_devices d JOIN members m ON m.id=d.member_id
              WHERE d.id=?''', (params.get('u', ''),)).fetchone()
            password = params.get('p', '')
            if password.startswith('enc:'):
                password = bytes.fromhex(password[4:]).decode()
            if (not device or device['revoked'] or device['role'] not in ('member', 'admin') or not password or
                not hmac.compare_digest(device['digest'], hashlib.sha256(password.encode()).hexdigest())):
                raise Denied(401, 'Invalid device credentials')
            uid = device['member_id']
            security.limit(uid, 'device-requests', 240)
            import subsonic
            tracks = catalog(store, uid)
            try:
                import genres
                genres.annotate(store, tracks)
            except Exception:
                pass
            import subsonic as _subsonic
            _subsonic.schema(store)
            with store.lock:
                starred = {r[0] for r in store.conn.execute(
                    'SELECT track_id FROM listening_favorites WHERE member_id=?', (uid,))}
                ratings = {r[0]: r[1] for r in store.conn.execute(
                    'SELECT track_id, rating FROM listening_ratings WHERE member_id=?', (uid,))}
                bookmarks = {r[0]: r[1] for r in store.conn.execute(
                    'SELECT track_id, position_ms FROM listening_bookmarks WHERE member_id=?', (uid,))}
            songs, albums, artists, artwork = [], {}, {}, {}
            for t in tracks:
                t['albumId'] = key('al_', t.get('artist_name') or '', t.get('album') or '')
                t['artistId'] = key('ar_', t.get('artist_name') or '')
                s = subsonic.song(t, starred, ratings, bookmarks)
                songs.append(s)
                a = albums.setdefault(s['albumId'], {'id': s['albumId'], 'name': s['album'] or 'Unknown album',
                                                     'artist': s['artist'], 'artistId': s['artistId'],
                                                     'songCount': 0, 'duration': 0,
                                                     'coverArt': s['albumId']})
                a['songCount'] += 1
                a['duration'] += s['duration']
                if t.get('artwork') and s['albumId'] not in artwork:
                    artwork[s['albumId']] = t['artwork']
                artists.setdefault(s['artistId'], {'id': s['artistId'], 'name': s['artist'],
                                                   'albumCount': 0, 'coverArt': s['artistId']})
            for a in albums.values():
                artists[a['artistId']]['albumCount'] += 1
            playlists = [dict(r) for r in store.conn.execute('SELECT p.id,p.title FROM playlists p JOIN listening_playlists s ON p.id=s.playlist_id WHERE s.member_id=? ORDER BY p.id', (uid,))]
            links = list(store.conn.execute('SELECT playlist_id,track_id,position FROM listening_playlist_tracks WHERE member_id=? ORDER BY position,track_id', (uid,)))
            import preferences
            preferences.schema(store)
            favorites = [dict(r) for r in store.conn.execute('SELECT t.* FROM tracks t JOIN listening_favorites f ON t.id=f.track_id WHERE f.member_id=? ORDER BY t.id', (uid,))]
            playlists.append({'id': preferences.FAVORITES_ID, 'title': 'Favorites'})
        endpoint = urlparse(handler.path).path.removeprefix('/rest/').removesuffix('.view')
        def find(rows):
            result = next((r for r in rows if str(r['id']) == params.get('id', '')), None)
            if result is None:
                raise Denied(404, 'Not found')
            return dict(result)
        def page(rows, count='size', offset='offset', default=20):
            n, start_at = int(params.get(count, default)), int(params.get(offset, 0))
            if not 0 <= n <= 500 or not 0 <= start_at <= 1000000:
                raise Denied(400, 'Invalid pagination')
            return rows[start_at:start_at + n]
        def playlist(row, entries):
            by_id = {s['id']: s for s in entries}
            chosen = ([by_id[str(t['id'])] for t in favorites if str(t['id']) in by_id]
                      if row['id'] == preferences.FAVORITES_ID else
                      [by_id[str(link['track_id'])] for link in links
                       if link['playlist_id'] == row['id'] and str(link['track_id']) in by_id])
            return ({'id': str(row['id']), 'name': row['title'], 'public': False,
                     'songCount': len(chosen), 'duration': sum(s['duration'] for s in chosen)}, chosen)
        if endpoint in ('stream', 'download'):
            selected = find(tracks)
            if local_path(store, selected) is None:
                raise Denied(404, 'Media unavailable')
            # Only the scoped id above can reach a file; no transcoding or acquisition.
            handler.stream_audio(selected['id'])
            return
        payload = subsonic.handle(endpoint, params, multi, {
            'store': store, 'uid': uid, 'username': params.get('u', ''), 'role': device['role'],
            'handler': handler, 'security': security,
            'tracks': tracks, 'songs': songs, 'albums': albums, 'artists': artists,
            'favorites': favorites, 'playlists': playlists, 'links': links, 'art': artwork,
            'find': find, 'page': page, 'playlist': playlist,
            'lyrics': lambda track_id: lyrics_for(store, track_id)})
        if payload is not None:
            envelope(handler, params, payload)
    except Denied as exc:
        code = 40 if exc.code in (401, 403) else 70 if exc.code == 404 else 30 if exc.code == 430 else 10
        envelope(handler, params, error=(code, exc.message))
    except (ValueError, UnicodeError):
        envelope(handler, params, error=(10, 'Invalid parameters'))
    except OSError:
        envelope(handler, params, error=(70, 'Media unavailable'))
