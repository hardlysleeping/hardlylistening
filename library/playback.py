"""Handing a member the bytes: which file, which slice, or an honest refusal.

HTTP lives in the handler; this module decides *what* to serve. That split is what lets the web
player, the mixer, the public share page and the Subsonic surface all stream identically without
any of them owning Range arithmetic or the "file is missing" rule.

No transcode here: this module never encodes. Seeking is a byte range on whatever file it is
given — the stored track, or a transcode the Subsonic surface produced first.
"""
import mimetypes
import re
from dataclasses import dataclass

CHUNK = 65536
RANGE = re.compile(r'bytes=(\d*)-(\d*)')


class Stream:
    """A resolved response: everything the header block needs, plus the bytes after `start`.

    `status` is 200, 206 or 416. A 416 still carries a size, because the refusal has to tell the
    client what the real length is (`Content-Range: bytes */size`).
    """

    __slots__ = ('path', 'start', 'end', 'size', 'status', 'content_type')

    def __init__(self, path, start, end, size, status, content_type):
        self.path, self.start, self.end = path, start, end
        self.size, self.status, self.content_type = size, status, content_type

    @property
    def length(self) -> int:
        return 0 if self.status == 416 else self.end - self.start + 1

    @property
    def content_range(self) -> str:
        return f'bytes */{self.size}' if self.status == 416 else f'bytes {self.start}-{self.end}/{self.size}'

    def chunks(self):
        """Yield the body. A client that disappears mid-stream stops the read, not the server."""
        if self.status == 416:
            return
        remaining = self.length
        with self.path.open('rb') as handle:
            handle.seek(self.start)
            while remaining > 0:
                chunk = handle.read(min(CHUNK, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk


def resolve(store, track_id):
    """The stored file for a track, or None when it is missing or outside the media root."""
    from library.browser_downloads import local_path
    track = store.track_by_id(int(track_id))
    return local_path(store, track) if track else None


def available(store, track_id) -> bool:
    """Whether a stream would succeed. Cheaper than building a plan when only the answer matters."""
    return resolve(store, track_id) is not None


def plan(store, track_id, range_header=None):
    """Decide the response for one request, or None when there is no file to serve.

    Semantics preserved exactly from the original handler: an unparsable or absent header serves
    the whole file with 200; a satisfiable range serves 206; anything past the end is 416.
    """
    path = resolve(store, track_id)
    if path is None:
        return None
    return plan_path(path, range_header)


def plan_path(path, range_header=None, content_type=None):
    """The same decision for a file already resolved — a transcode the server produced, say.

    Range arithmetic lives here so every caller writes headers identically; the caller owns
    deciding *which* file and what it is called.
    """
    size = path.stat().st_size
    ctype = content_type or mimetypes.guess_type(path.name)[0] or 'audio/mpeg'
    start, end, status = 0, size - 1, 200
    match = RANGE.match((range_header or '').strip())
    if range_header and match:
        if match.group(1):
            start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
        elif match.group(2):
            start = max(0, size - int(match.group(2)))
        if start >= size or start > end:
            return Stream(path, start, end, size, 416, ctype)
        status = 206
    return Stream(path, start, end, size, status, ctype)