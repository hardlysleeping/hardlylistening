"""Bytes: audio streams, ranged reads, artwork, downloads and the static tree.

These are the methods that write more than a JSON body, and each one owns a decision a generic
sender would get wrong. `send_stream` answers a range request with 206 and the right
`Content-Range`, or the whole file with 200. `stream_audio` refuses a missing file rather than
answering an empty 200. `art_image` serves from the art store and falls back to a generated
placeholder. `static` resolves a name inside the web tree and never outside it.

Nothing here decides whether a person may ask: that already happened in `boundary()`.
"""

from __future__ import annotations

import json
import mimetypes
import re
from urllib.parse import parse_qs, urlparse

import paths
from security import Denied
from .constants import PLAYBACK_LOG_MAX
from .runtime import boot


class Media:
    def browser_download(self, path, store):
        from library.browser_downloads import archive, local_path, safe_name, selection, track_name
        from urllib.parse import quote
        import os
        single = re.fullmatch(r'/api/track/(\d+)/download', path)
        playlist = re.fullmatch(r'/api/playlist/(\d+)/download', path)
        if not (single or playlist or path == '/api/library/download'):
            return False
        try:
            if single:
                track = store.track_by_id(int(single[1]))
                media = local_path(store, track)
                if media is None:
                    self.json({'error': 'Unavailable: only existing local library files can be downloaded.'}, 404)
                    return True
                handle = media.open('rb')
                name = track_name(track, media, with_id=False)   # a single file needs no id prefix
                ctype = mimetypes.guess_type(media.name)[0] or 'application/octet-stream'
            else:
                query = parse_qs(urlparse(self.path).query)
                title, tracks = selection(store, 'playlist' if playlist else 'ids', int(playlist[1]) if playlist else query.get('ids', [''])[0])
                handle = archive(store, tracks, title)
                name = safe_name(title) + '.zip'
                ctype = 'application/zip'
        except ValueError:
            self.json({'error': 'Invalid or unavailable selection'}, 400)
            return True
        except OSError:
            self.json({'error': 'Local files unavailable for download'}, 404)
            return True
        with handle:
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Disposition', 'attachment; filename="download' + ('.zip' if ctype == 'application/zip' else '') + '"; filename*=UTF-8\'\'' + quote(name, safe=''))
            self.send_header('Content-Length', str(os.fstat(handle.fileno()).st_size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                while chunk := handle.read(65536):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
        return True

    def stream_audio(self, track_id: int) -> None:
        """Serve a library file with Range support — an <audio> deck needs it to
        seek, and seeking is most of what a DJ board does.

        The decision (file, slice, status) is `library.playback`'s; this method only writes it,
        so the mixer, the public share page and Subsonic stream the same way.
        """
        from library import playback
        store, _ = boot()
        stream = playback.plan(store, track_id, self.headers.get("Range"))
        if stream is None:
            self._send(404, b"file is missing", "text/plain")
            return
        self.send_stream(stream)

    def stream_path(self, path, content_type: str | None = None) -> None:
        """Serve a file the server produced itself (a transcode) with the same Range rules."""
        from library import playback
        try:
            stream = playback.plan_path(path, self.headers.get("Range"), content_type)
        except OSError:
            self._send(404, b"file is missing", "text/plain")
            return
        self.send_stream(stream)

    def send_stream(self, stream) -> None:
        """Write one resolved stream: headers first, then the slice the client asked for."""
        self.send_response(stream.status)
        self.send_header("Content-Type", stream.content_type)
        self.send_header("Content-Length", str(stream.length))
        self.send_header("Accept-Ranges", "bytes")
        if stream.status in (206, 416):
            self.send_header("Content-Range", stream.content_range)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        for chunk in stream.chunks():
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

    def art_image(self, key: str) -> None:
        """One cover, by its own digest: the URL a head unit's artwork fetch can use.

        Public on purpose. iOS fetches media-session artwork itself, outside the page's
        session, so a member-only route answers that request with a login redirect — which a
        car draws as no image at all. The digest is the unguessable part. `display()` is
        content-addressed and memoised, so the only work on a miss is one deterministic
        transcode of the canonical copy the store already holds.
        """
        from library import art_store
        image = art_store.display(boot()[0], key)
        if image is None or not image.is_file():
            self._send(404, b"no art", "text/plain; charset=utf-8")
            return
        payload = image.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(payload)

    def record_playback_event(self) -> None:
        """Append one line of playback telemetry. Never a URL, never a cookie, never a body.

        A fixed field list is the point: whatever a client sends, the file holds only what a
        future diagnosis needs, with every free-text value clipped.
        """
        length = self.headers.get('Content-Length', '0')
        if not length.isdigit() or int(length) > 4096:
            raise Denied(413, 'Event exceeds 4 KiB')
        event = self.body()
        if not isinstance(event, dict):
            raise Denied(400, 'Object required')
        line = {}
        for field in ('at', 'trackId', 'event', 'wanted', 'interrupted', 'readyState',
                      'networkState', 'errorCode', 'srcKind'):
            if field in event:
                value = event[field]
                line[field] = value if isinstance(value, (int, float, bool)) else str(value)[:64]
        if not line:
            # `body()` turns anything that is not a JSON object into `{}`; an empty line would
            # be noise in the one file that is supposed to be readable after a drive.
            raise Denied(400, 'No known event field')
        try:
            paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
            log = paths.STATE_DIR / 'playback-events.log'
            if log.is_file() and log.stat().st_size > PLAYBACK_LOG_MAX:
                log.replace(log.with_name(log.name + '.1'))
            with log.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(line, default=str) + '\n')
        except OSError:
            raise Denied(503, 'Playback telemetry is not writable')
        self.json({'ok': True})
