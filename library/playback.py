"""Handing a member the bytes: which file, which slice, or an honest refusal.

HTTP lives in the handler; this module decides *what* to serve. That split is what lets the web
player, the mixer, the public share page and the Subsonic surface all stream identically without
any of them owning Range arithmetic or the "file is missing" rule.

No transcode here: this module never encodes. Seeking is a byte range on whatever file it is
given — the stored track, or a transcode the Subsonic surface produced first.

A track whose bytes live in a bucket is the same decision with a different reader: the size comes
from a HEAD, the slice from a ranged GET, and the headers the client sees are identical. That is
deliberate — no client learns which storage mode the server is in, and none has to.
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

    __slots__ = ('path', 'start', 'end', 'size', 'status', 'content_type', 'reader')

    def __init__(self, path, start, end, size, status, content_type, reader=None):
        self.path, self.start, self.end = path, start, end
        self.size, self.status, self.content_type = size, status, content_type
        # A remote object has no path: `reader(start, end)` yields its bytes instead.
        self.reader = reader

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
        if self.reader is not None:
            yield from self.reader(self.start, self.end)
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
    if resolve(store, track_id) is not None:
        return True
    return plan_object(store, track_id) is not None


def plan(store, track_id, range_header=None):
    """Decide the response for one request, or None when there is no file to serve.

    Semantics preserved exactly from the original handler: an unparsable or absent header serves
    the whole file with 200; a satisfiable range serves 206; anything past the end is 416.

    A local file always wins. Only when this disk has nothing to serve does the bucket get asked,
    which is also what makes mirror mode's local copy a fast path rather than a second mode.
    """
    path = resolve(store, track_id)
    if path is not None:
        return plan_path(path, range_header)
    return plan_object(store, track_id, range_header, cache=True)


def plan_object(store, track_id, range_header=None, content_type=None, cache=False):
    """The same response for a track whose bytes live in a bucket rather than on this disk.

    The size comes from a HEAD and the slice from a ranged GET, so the headers a client sees do
    not depend on where the bytes are. A misconfigured bucket raises (it is not silently treated
    as "no file"); a key or object that simply is not there is None, like a missing file.

    `cache=True` is only for a real request: when a cache budget is configured and the object fits
    it, the first request fills a local copy and this answer is the local one. `available()` asks
    with the default, so answering "would this play" never downloads a track.

    A cached copy is answered **before** the HEAD, from the cache metadata, so a repeat play costs
    no network at all. The row is revalidated after `storage.REVALIDATE` seconds with one HEAD: a
    size that still matches refreshes the row and costs no body fetch.
    """
    from library import storage
    adapter = storage.build(store)
    if getattr(adapter, 'mode', 'local') != 's3':
        return None
    track = store.track_by_id(int(track_id))
    if not track or not track.get('file'):
        return None
    try:
        key = storage.key_for(store, track['file'])
    except storage.StorageError:
        return None
    ctype = content_type or mimetypes.guess_type(key)[0] or 'audio/mpeg'
    if cache:
        try:
            hit = storage.cached_copy(store, key, max_age=storage.REVALIDATE)
        except (storage.StorageError, OSError):
            hit = None
        if hit is not None:
            try:
                return plan_path(hit, range_header, ctype)
            except OSError:
                # Evicted between the lookup and the stat: fall through to the bucket.
                pass
    try:
        size = adapter.size(key)
    except storage.StorageError:
        return None
    if size <= 0:
        return None
    if cache:
        try:
            cached = storage.ensure_cached(store, adapter, key, size)
        except (storage.StorageError, OSError):
            # A cache that cannot be filled is not a failed request: the ranged read below is
            # exactly what this server did before the cache existed.
            cached = None
        if cached is not None:
            return plan_path(cached, range_header, ctype)
    start, end, status = span(size, range_header)
    return Stream(None, start, end, size, status, ctype,
                  reader=lambda first, last: adapter.iter_range(key, first, last))


def span(size, range_header):
    """The (start, end, status) triple for one request size.

    Local files and bucket objects both use this, so a 416 means the same thing in both modes.
    """
    if size <= 0:
        return 0, -1, 416
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
            return start, end, 416
        status = 206
    return start, end, status


def plan_path(path, range_header=None, content_type=None):
    """The same decision for a file already resolved — a transcode the server produced, say.

    Range arithmetic lives here so every caller writes headers identically; the caller owns
    deciding *which* file and what it is called.
    """
    size = path.stat().st_size
    ctype = content_type or mimetypes.guess_type(path.name)[0] or 'audio/mpeg'
    start, end, status = span(size, range_header)
    return Stream(path, start, end, size, status, ctype)