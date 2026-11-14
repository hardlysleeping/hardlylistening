"""Exports that a DJ application can actually open.

Two formats, both plain text over the existing library rows:

* **m3u8** — the lowest common denominator. VLC, Mixxx, Traktor, Engine DJ and
  most players read it, and relative sibling paths survive a folder move.
* **Rekordbox XML** — Rekordbox, and the import path most other apps can convert
  from. It carries a collection plus the playlist tree.

Only files that exist on this machine are exported; a row whose audio is gone
would import as a dead reference in the DJ app.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

M3U8 = "m3u8"
REKORDBOX = "rekordbox"


def _suffix(path: Path) -> str:
    return path.suffix.lstrip(".").upper() or "MP3"


def _kind(path: Path) -> str:
    suffix = _suffix(path)
    return f"{suffix} File" if suffix else "File"


def _split_title(track: dict) -> tuple[str, str]:
    return (track.get("artist_name") or "").strip(), (track.get("title") or "").strip()


def collection(store, playlist_id: int | None = None) -> tuple[list[dict], list[dict], str]:
    """Return (tracks, playlists, title) restricted to files that still exist."""
    with store.lock:
        rows = [dict(r) for r in store.conn.execute(
            "SELECT id,title,artist_name,album,duration,file,track_number FROM tracks"
            " WHERE file<>'' ORDER BY artist_name, album, track_number, id")]
        playlists = [dict(r) for r in store.conn.execute(
            "SELECT id,title,position FROM playlists ORDER BY position, id")]
        links = [dict(r) for r in store.conn.execute(
            "SELECT playlist_id, track_id, position FROM playlist_tracks"
            " ORDER BY playlist_id, position")]
    keep, title = [], "HardlyListening library"
    for row in rows:
        path = Path(row["file"])
        if not path.is_file():
            continue
        if playlist_id is not None:
            if not any(link["playlist_id"] == playlist_id and link["track_id"] == row["id"] for link in links):
                continue
        row["path"] = path
        keep.append(row)
    if playlist_id is not None:
        match = next((p for p in playlists if p["id"] == playlist_id), None)
        if match is None:
            raise ValueError("unknown playlist")
        title = match["title"]
        playlists = [match]
        links = [link for link in links if link["playlist_id"] == playlist_id]
    return keep, [dict(p, tracks=[l for l in links if l["playlist_id"] == p["id"]]) for p in playlists], title


def m3u8(store, playlist_id: int | None = None) -> tuple[str, str]:
    tracks, _, title = collection(store, playlist_id)
    lines = ["#EXTM3U", f"#PLAYLIST:{title}"]
    for track in tracks:
        artist, name = _split_title(track)
        label = f"{artist} - {name}" if artist else name
        lines.append(f"#EXTINF:{int(round(track.get('duration') or 0))},{label}")
        lines.append(str(track["path"]))
    return ("\n".join(lines) + "\n", title + ".m3u8")


def rekordbox(store, playlist_id: int | None = None) -> tuple[str, str]:
    tracks, playlists, title = collection(store, playlist_id)
    ids = {track["id"]: index + 1 for index, track in enumerate(tracks)}
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<DJ_PLAYLISTS Version="1.0.0">',
           f'  <PRODUCT Name="HardlyListening" Version="1.0" Company="HardlyListening"/>',
           f'  <COLLECTION Entries="{len(tracks)}">']
    for track in tracks:
        artist, name = _split_title(track)
        location = "file://localhost" + quote(str(track["path"]))
        attrs = [
            f'TrackID="{ids[track["id"]]}"',
            f'Name={quoteattr(name)}',
            f'Artist={quoteattr(artist)}',
            f'Album={quoteattr(track.get("album") or "")}',
            f'TotalTime="{int(round(track.get("duration") or 0))}"',
            f'Kind={quoteattr(_kind(track["path"]))}',
            f'Location={quoteattr(location)}',
        ]
        out.append("    <TRACK " + " ".join(attrs) + "/>")
    out += ["  </COLLECTION>", "  <PLAYLISTS>",
            f'    <NODE Type="0" Name="ROOT" Count="{len(playlists)}">']
    for playlist in playlists:
        members = [link for link in playlist["tracks"] if link["track_id"] in ids]
        out.append(f'      <NODE Name={quoteattr(playlist["title"])} Type="1" KeyType="0" Entries="{len(members)}">')
        for link in members:
            out.append(f'        <TRACK Key="{ids[link["track_id"]]}"/>')
        out.append("      </NODE>")
    out += ["    </NODE>", "  </PLAYLISTS>", "</DJ_PLAYLISTS>", ""]
    return ("\n".join(out), title + ".rekordbox.xml")


def usb_document(tracks: list[dict], title: str) -> str:
    """Rekordbox XML for files written to a drive by `usb_export` (HL-228).

    The same document shape as `rekordbox()`, but each row is a file *on the drive* and
    carries what a CDJ reads from the XML: `AverageBpm` when the tempo is known, and the
    member's hot cues as `POSITION_MARK`s. There is no `Tonality`: this app has no key
    detection, and an empty attribute would claim otherwise. `TotalTime` is 0 for the same
    reason — the length was never measured here, so it is not invented.
    """
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<DJ_PLAYLISTS Version="1.0.0">',
           '  <PRODUCT Name="HardlyListening" Version="1.0" Company="HardlyListening"/>',
           f'  <COLLECTION Entries="{len(tracks)}">']
    for index, track in enumerate(tracks, 1):
        location = "file://localhost" + quote(str(track["target"]))
        attrs = [
            f'TrackID="{index}"',
            f'Name={quoteattr(track.get("title") or "")}',
            f'Artist={quoteattr(track.get("artist") or "")}',
            f'Album={quoteattr(track.get("album") or "")}',
            f'TotalTime="{int(round(track.get("duration") or 0))}"',
            f'Kind={quoteattr((track.get("kind") or "MP3") + " File")}',
            f'Location={quoteattr(location)}',
        ]
        bpm = round(float(track.get("bpm") or 0), 1)
        if bpm > 0:
            attrs.append(f'AverageBpm="{bpm}"')
        cues = track.get("cues") or []
        if not cues:
            out.append("    <TRACK " + " ".join(attrs) + "/>")
            continue
        out.append("    <TRACK " + " ".join(attrs) + ">")
        for cue in cues:
            slot = int(cue.get("slot") or 0)
            start = round(float(cue.get("position") or 0), 3)
            out.append(f'      <POSITION_MARK Name={quoteattr(f"Cue {slot}")} Type="0"'
                       f' Start="{start}" Num="{slot}"/>')
        out.append("    </TRACK>")
    out += ["  </COLLECTION>", "  <PLAYLISTS>",
            '    <NODE Type="0" Name="ROOT" Count="1">',
            f'      <NODE Name={quoteattr(title)} Type="1" KeyType="0" Entries="{len(tracks)}">']
    for index in range(1, len(tracks) + 1):
        out.append(f'        <TRACK Key="{index}"/>')
    out += ["      </NODE>", "    </NODE>", "  </PLAYLISTS>", "</DJ_PLAYLISTS>", ""]
    return "\n".join(out)


def build(store, fmt: str, playlist_id: int | None = None) -> tuple[str, str]:
    if fmt == M3U8:
        return m3u8(store, playlist_id)
    if fmt == REKORDBOX:
        return rekordbox(store, playlist_id)
    raise ValueError("unsupported format")
