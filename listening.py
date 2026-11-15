"""The player-facing surface: device sessions and the read-only Subsonic subset.

The saved scope and the folder tree moved into the Library package (LIB-001): `library/saves.py`,
`library/folders.py` and `library/playlists.py` hold the rules, `library/api.py` the two HTTP
adapters, and this module keeps what is genuinely the surface — device credentials, the request
routing and the `/rest/` XML/JSON envelope. The names below stay importable because the queue and
`server.py` call them (`want_playlist`, `deliver_track`, `forget_track`).

Device secrets are random, returned once and SHA256-hashed at rest. This milestone supports
password authentication (p), not legacy MD5 salt/token auth. No acquisition or network IO.
"""
import hashlib
import hmac
import json
import secrets
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse
from security import SECURITY, Denied
from library import folders as library_folders
from library import playback, saves as library_saves, service


def schema(store, security):
    security.schema(store)
    library_saves.schema(store)
    library_folders.schema(store)
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_devices (
          id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id),
          label TEXT NOT NULL, digest TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
          -- Subsonic's MD5 token auth sends md5(password + salt), which cannot be checked
          -- against `digest`: a SHA-256 cannot be turned back into the password. Devices
          -- created from now on also keep the secret in the Subsonic `enc:` hex form so a
          -- token-only client can connect. NULL means password auth only; see SECURITY.md.
          token_secret TEXT,
          created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        ''')
        # Devices that predate token auth keep a NULL secret and stay password-only.
        columns = {r[1] for r in store.conn.execute('PRAGMA table_info(listening_devices)')}
        if 'token_secret' not in columns:
            store.conn.execute('ALTER TABLE listening_devices ADD COLUMN token_secret TEXT')
        store.conn.commit()


def want_playlist(store, member_id: str, playlist_id: int) -> int:
    """Downloading from the Feed also puts the release in the library (`library/saves.py`)."""
    return library_saves.want_playlist(store, member_id, playlist_id)


def deliver_track(store, track_id: int) -> int:
    """The queue wrote this track: hand it to everyone who pressed Download."""
    return library_saves.deliver_track(store, track_id)


def forget_track(store, track_id: int) -> None:
    """The download ended without a file; drop the promise rather than save it."""
    return library_saves.forget_track(store, track_id)


def browser(handler, store, security):
    if handler.command == 'POST':
        handler.close_connection = True
    if urlparse(handler.path).path == '/api/listening/now':
        import nowplaying
        return nowplaying.dispatch(handler, store, security)
    if urlparse(handler.path).path.startswith('/api/listening/preferences') \
            or urlparse(handler.path).path in ('/api/listening/pin-order', '/api/listening/eq',
                                               '/api/listening/playback',
                                               '/api/listening/cues'):
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
    if path in ('/api/listening/saves', '/api/listening/folders'):
        from library import api as library_api
        call = library_api.saves if path.endswith('/saves') else library_api.folders
        return call(handler, store, security)
    with store.lock:
        if handler.command == 'GET':
            if path == '/api/listening/devices':
                rows = [dict(r) for r in store.conn.execute(
                    'SELECT id,label,revoked,created FROM listening_devices WHERE member_id=? ORDER BY created,id', (uid,))]
                handler.json({'devices': rows})
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
            store.conn.execute('INSERT INTO listening_devices(id,member_id,label,digest,token_secret) VALUES(?,?,?,?,?)',
                               (identity, uid, label.strip(), hashlib.sha256(password.encode()).hexdigest(),
                                password.encode().hex()))
            store.conn.commit()
            handler.json({'username': identity, 'password': password, 'token_auth': True})
            return
        if path == '/api/listening/devices/revoke':
            store.conn.execute('UPDATE listening_devices SET revoked=1 WHERE id=? AND member_id=?', (str(body.get('id', '')), uid))
        else:
            raise Denied(404, 'No such endpoint')
        store.conn.commit()
    handler.json({'ok': True})


def catalog(store, uid):
    """Kept as a name for callers; the query itself belongs to the library service."""
    return service.catalog(store, uid)


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
    import subsonic
    data = {'status': 'failed' if error else 'ok', 'version': subsonic.PROTOCOL_VERSION,
            'type': subsonic.SERVER_TYPE, 'serverVersion': subsonic.SERVER_VERSION,
            'openSubsonic': True}
    data.update({'error': {'code': error[0], 'message': error[1]}} if error else (payload or {}))
    # A request can be refused before its parameters are parsed, so the error still has to
    # honour the format the client asked for in the query string.
    fmt = params.get('f') or (parse_qs(urlparse(handler.path).query).get('f') or ['xml'])[0]
    if fmt == 'json':
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
        endpoint = urlparse(handler.path).path.removeprefix('/rest/').removesuffix('.view')
        client_info = None
        if handler.command == 'POST':
            handler.close_connection = True
            length = int(handler.headers.get('Content-Length', '0'))
            encoding = handler.headers.get('Content-Type', '').split(';')[0]
            if encoding == 'application/json':
                # The transcoding extension carries the client's playback capabilities in a
                # JSON body. Every other POST here stays a form.
                if endpoint != 'getTranscodeDecision':
                    raise Denied(400, 'A JSON body is only read by getTranscodeDecision')
                if length > 65536:
                    raise Denied(400, 'Request too long')
                try:
                    client_info = json.loads(handler.rfile.read(length).decode() or '{}')
                except (ValueError, UnicodeError):
                    raise Denied(400, 'Invalid JSON body')
                if not isinstance(client_info, dict):
                    raise Denied(400, 'Invalid JSON body')
            else:
                if length > 16384:
                    raise Denied(400, 'Request too long')
                if encoding != 'application/x-www-form-urlencoded':
                    raise Denied(400, 'Form encoding required')
                raw = handler.rfile.read(length)
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
        if endpoint == 'getTranscodeDecision' and handler.command != 'POST':
            raise Denied(400, 'getTranscodeDecision requires POST with a JSON client profile')
        schema(store, security)
        security.limit(security.client_ip(handler), 'device-auth', 300)
        with store.lock:
            device = store.conn.execute('''SELECT d.*,m.role FROM listening_devices d JOIN members m ON m.id=d.member_id
              WHERE d.id=?''', (params.get('u', ''),)).fetchone()
            password = params.get('p', '')
            if password.startswith('enc:'):
                password = bytes.fromhex(password[4:]).decode()
            authorised = False
            if device and password:
                authorised = hmac.compare_digest(
                    device['digest'], hashlib.sha256(password.encode()).hexdigest())
            if not authorised:
                # Subsonic token auth: t = md5(password + s). Only a device that kept its
                # secret in `token_secret` can answer that; older rows stay password-only.
                token, salt = params.get('t', ''), params.get('s', '')
                secret = device['token_secret'] if device else None
                if token and salt and secret:
                    try:
                        expected = hashlib.md5(bytes.fromhex(secret) + salt.encode()).hexdigest()
                    except ValueError:
                        expected = ''
                    authorised = hmac.compare_digest(expected, token.lower())
            if (not device or device['revoked'] or device['role'] not in ('member', 'admin')
                    or not authorised):
                raise Denied(401, 'Invalid device credentials')
            uid = device['member_id']
            security.limit(uid, 'device-requests', 240)
            import subsonic
            import nowplaying
            tracks = catalog(store, uid)
            try:
                import genres
                genres.annotate(store, tracks)
            except Exception:
                pass
            import subsonic as _subsonic
            _subsonic.schema(store)
            starred = service.starred(store, uid)
            ratings = service.ratings(store, uid)
            bookmarks = service.bookmarks(store, uid)
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
            import preferences
            preferences.schema(store)
            playlists = service.playlists(store, uid)
            links = service.playlist_tracks(store, uid)
            favorites = service.favorites(store, uid)
            playlists.append({'id': preferences.FAVORITES_ID, 'title': 'Favorites'})
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
            if not playback.available(store, selected['id']):
                raise Denied(404, 'Media unavailable')
            # A `format` on a plain stream is the client asking for a re-encode: classic
            # Subsonic clients never call the decision endpoint. `download` always hands over
            # the stored file — a download is the file, not a rendition of it.
            wanted = params.get('format', '') if endpoint == 'stream' else ''
            if wanted.lower() in ('raw', 'original'):
                wanted = ''
            ceiling = params.get('maxBitRate', '0')
            target = subsonic.transcode_target(None, wanted, ceiling) if wanted else None
            if wanted and target is None:
                raise Denied(430, 'This server cannot encode %s' % wanted)
            if target and subsonic.should_encode(selected, target, ceiling):
                encoded = subsonic.encode(store, selected['id'], target)
                if encoded is None:
                    raise Denied(404, 'Media unavailable')
                handler.stream_path(encoded, subsonic.MUSIC_TYPES.get(target['suffix'], 'audio/mpeg'))
                return
            handler.stream_audio(selected['id'])
            return
        if endpoint == 'streamPodcastEpisode':
            # Only a kept copy is served here: the original enclosure is already handed to
            # the client as `streamUrl`, so the reason to ask this server is that it holds it.
            import podcasts
            held, kind = podcasts.held_file(store, uid, params.get('id', ''))
            handler.stream_path(held, kind)
            return
        if endpoint == 'getTranscodeStream':
            # Two kinds of decision lead here: direct play, which serves the stored file byte
            # for byte, and a re-encode, which is produced on demand and then served with the
            # same Range rules. Range seeking is the player's job either way.
            if params.get('mediaType') != 'song':
                raise Denied(430, 'Only song media is served by this server')
            try:
                offset = int(params.get('offset') or 0)
            except ValueError:
                raise Denied(400, 'Invalid offset')
            if offset > 0:
                raise Denied(430, 'Seek with HTTP Range on the stream, not with an offset')
            resolved = subsonic.resolve_transcode_params(params.get('transcodeParams'), uid)
            if resolved is None or str(resolved[0]) != params.get('mediaId', ''):
                raise Denied(400, 'transcodeParams is unknown, expired, or for another track')
            track_id, target = resolved
            selected = next((dict(t) for t in tracks if t['id'] == track_id), None)
            if selected is None or not playback.available(store, track_id):
                raise Denied(404, 'Media unavailable')
            if target is None:
                handler.stream_audio(track_id)
                return
            encoded = subsonic.encode(store, track_id, target)
            if encoded is None:
                raise Denied(404, 'Media unavailable')
            handler.stream_path(encoded, subsonic.MUSIC_TYPES.get(target['suffix'], 'audio/mpeg'))
            return
        payload = subsonic.handle(endpoint, params, multi, {
            'store': store, 'uid': uid, 'username': params.get('u', ''), 'role': device['role'],
            'handler': handler, 'security': security, 'client': client_info,
            'tracks': tracks, 'songs': songs, 'albums': albums, 'artists': artists,
            'favorites': favorites, 'playlists': playlists, 'links': links, 'art': artwork,
            'find': find, 'page': page, 'playlist': playlist,
            'device_label': device['label'],
            'now': lambda: nowplaying.get(store, uid),
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
