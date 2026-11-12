"""One song, one download.

A recording can reach the library twice: followed for a second provider, or the
same link added again. The queue links the second row to the file already on disk
rather than fetching an identical copy, and a delete never unlinks a file that a
surviving row still points at.
"""
import os
import tempfile
import unittest
import wave
from pathlib import Path

import deletion
from db import Store


def tone(path: Path, value: int = 1) -> Path:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes([value, 0]) * 32000)
    return path


class Twins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "music"
        self.root.mkdir()
        self.store = Store(base / "fixture.db")
        self.addCleanup(self.store.conn.close)
        self.store.set_settings({"root": str(self.root)})

    def add(self, provider, ident, title, artist, file="", isrc=""):
        track_id = self.store.track(provider, ident, title, artist_name=artist, isrc=isrc)
        if file:
            with self.store.lock:
                self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?",
                                        (str(file), track_id))
                self.store.conn.commit()
        return track_id

    def test_the_same_title_and_artist_is_recognised(self):
        have = self.add("apple", "1", "Song", "Artist", tone(self.root / "a.wav"))
        other = self.add("soundcloud", "2", "song ", " ARTIST")
        twin = self.store.downloaded_twin(self.store.track_by_id(other))
        self.assertEqual(twin["id"], have)

    def test_a_row_is_never_its_own_twin(self):
        only = self.add("apple", "1", "Song", "Artist", tone(self.root / "a.wav"))
        self.assertIsNone(self.store.downloaded_twin(self.store.track_by_id(only)))

    def test_a_row_without_a_file_is_not_a_twin(self):
        self.add("apple", "1", "Song", "Artist")
        other = self.add("soundcloud", "2", "Song", "Artist")
        self.assertIsNone(self.store.downloaded_twin(self.store.track_by_id(other)))

    def test_isrc_beats_a_different_title(self):
        have = self.add("apple", "1", "Song (Radio Edit)", "Artist",
                        tone(self.root / "a.wav"), isrc="US1234567890")
        other = self.add("soundcloud", "2", "Song", "Someone Else",
                         isrc="us1234567890")
        twin = self.store.downloaded_twin(self.store.track_by_id(other))
        self.assertEqual(twin["id"], have)

    def test_deleting_a_playlist_keeps_a_file_another_row_still_uses(self):
        shared = tone(self.root / "shared.wav")
        keep = self.add("apple", "1", "Song", "Artist", shared)
        gone = self.add("soundcloud", "2", "Song", "Artist", shared)
        playlist = self.store.playlist("List", "apple", "apple:list")
        self.store.add_to_playlist(playlist, gone, 1)
        report = deletion.plan(self.store, "playlist", playlist)
        self.assertEqual(report["files"], [])
        self.assertEqual(report["bytes"], 0)
        self.assertEqual(self.store.track_by_id(keep)["file"], str(shared))


if __name__ == "__main__":
    unittest.main()
