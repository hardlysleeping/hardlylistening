"""Replace the cover inside an MP3 without touching a byte of audio.

The downloader embeds the uploader's full-size picture in every file. The library
keeps one copy of each distinct image and a 640 px copy inside each track, so the
surgery has to be exact: every frame this module does not own is copied verbatim,
the tag is padded back to its original length so the audio never shifts, and the
caller can compare the bytes after the tag before replacing the file.

Deliberately narrow. Anything unusual (ID3v2.2, unsynchronised tags, extended
headers, per-frame unsynchronisation, MP4) raises Unsupported and the file is left
alone — a missed saving is cheap, a mangled tag is not.
"""

from __future__ import annotations


class Unsupported(Exception):
    """A tag layout this module will not rewrite."""


def _synchsafe(raw: bytes) -> int:
    value = 0
    for byte in raw:
        value = (value << 7) | (byte & 0x7F)
    return value


def _to_synchsafe(value: int) -> bytes:
    return bytes(((value >> 21) & 0x7F, (value >> 14) & 0x7F,
                  (value >> 7) & 0x7F, value & 0x7F))


def _frame_size(raw: bytes, version: int) -> int:
    return _synchsafe(raw) if version == 4 else int.from_bytes(raw, "big")


def _frame_length(length: int, version: int) -> bytes:
    return _to_synchsafe(length) if version == 4 else length.to_bytes(4, "big")


def split(raw: bytes):
    """(version, frames, tag_end). Frames keep their original bytes, header included."""
    if len(raw) < 10 or raw[:3] != b"ID3":
        return None
    version, revision, flags = raw[3], raw[4], raw[5]
    if version not in (3, 4):
        raise Unsupported(f"ID3v2.{version} is not rewritten")
    if flags & 0x80:
        raise Unsupported("unsynchronised tag")
    if flags & 0x40:
        raise Unsupported("extended header")
    if flags & 0x10:
        raise Unsupported("footer")
    end = 10 + _synchsafe(raw[6:10])
    if end > len(raw):
        raise Unsupported("truncated tag")
    frames, cursor = [], 10
    while cursor + 10 <= end:
        ident = raw[cursor:cursor + 4]
        if ident[:1] == b"\x00":
            break                                  # padding starts here
        if not ident.isalnum():
            raise Unsupported(f"odd frame id {ident!r}")
        if version == 4 and raw[cursor + 9] & 0x02:
            raise Unsupported("per-frame unsynchronisation")
        length = _frame_size(raw[cursor + 4:cursor + 8], version)
        if length <= 0 or cursor + 10 + length > end:
            break
        frames.append((ident, raw[cursor:cursor + 10 + length]))
        cursor += 10 + length
    return version, frames, end


def covers(raw: bytes) -> list[tuple[str, bytes]]:
    """Every attached picture as (mime, image bytes), without shelling out to ffmpeg."""
    tag = split(raw)
    if tag is None:
        return []
    pictures = []
    for ident, frame in tag[1]:
        if ident != b"APIC":
            continue
        payload = frame[10:]
        if len(payload) < 4:
            continue
        encoding = payload[0]
        mime_end = payload.find(b"\x00", 1)
        if mime_end < 0:
            continue
        mime = payload[1:mime_end].decode("latin1", "replace")
        cursor = mime_end + 1 + 1                 # skip picture type
        if encoding in (1, 2):                    # UTF-16 description
            while cursor + 1 < len(payload) and payload[cursor:cursor + 2] != b"\x00\x00":
                cursor += 2
            cursor += 2
        else:
            cursor = payload.find(b"\x00", cursor) + 1
        if 0 < cursor < len(payload):
            pictures.append((mime, payload[cursor:]))
    return pictures


def apic(jpeg: bytes, version: int) -> bytes:
    """A front-cover APIC frame pointing at one JPEG."""
    payload = b"\x00" + b"image/jpeg\x00" + b"\x03" + b"\x00" + jpeg
    return (b"APIC" + _frame_length(len(payload), version) + b"\x00\x00" + payload)


def embed(raw: bytes, jpeg: bytes) -> bytes | None:
    """New file bytes with `jpeg` as the only cover, or None when it already is.

    The tag keeps its original length whenever the new picture fits, so the audio
    stays at the same offset and can be compared byte for byte.
    """
    tag = split(raw)
    if tag is None:
        raise Unsupported("no ID3v2 tag to hold a picture")
    version, frames, end = tag
    kept = [frame for ident, frame in frames if ident != b"APIC"]
    old = [frame for ident, frame in frames if ident == b"APIC"]
    frame = apic(jpeg, version)
    if len(old) == 1 and old[0] == frame:
        return None                                # already exactly this cover
    body = b"".join(kept) + frame
    # 1 KB of slack for a later tag edit. The old picture's space is *not* kept: a
    # 1.2 MB cover replaced by a 60 KB one has to actually free the 1.1 MB, which
    # means the audio moves up — and is compared byte for byte before the swap.
    size = len(body) + 1024
    if size > end - 10:
        size = len(body)
    padding = b"\x00" * (size - len(body))
    return b"ID3" + raw[3:6] + _to_synchsafe(size) + body + padding + raw[end:]


def audio(raw: bytes) -> bytes:
    """The media bytes, i.e. everything after the tag."""
    tag = split(raw)
    return raw[tag[2]:] if tag else raw