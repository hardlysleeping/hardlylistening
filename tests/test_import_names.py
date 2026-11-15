"""A track number in a file name is a position, not a performer.

The library writes `<root>/<Artist>/<Collection>/NN - Title.ext`. When the tags are
missing, reading the raw stem turned that number into an artist called "76" — a
whole shelf of rows whose artist was a track position.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from db import Store
from library import import_files, reconcile


def make_plain_mp3(path: Path, seconds: int = 3) -> Path:
    """A real mp3 with no tags at all: what a stripped download looks like."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "libmp3lame", "-b:a", "128k", "-map_metadata", "-1", str(path)],
        check=True)
    return path


class FileNames(unittest.TestCase):
    def test_a_leading_number_is_the_track_position(self):
        self.assertEqual(
            import_files.from_filename(Path("/x/57 - Krewella - Alive.m4a")),
            ("Krewella - Alive", 57))

    def test_a_title_that_starts_with_a_year_is_not_a_number_prefix(self):
        self.assertEqual(import_files.from_filename(Path("/x/1999.mp3")), ("1999", 0))

    def test_a_leading_number_needs_a_separator(self):
        self.assertEqual(import_files.from_filename(Path("/x/76 Trombones.mp3")),
                         ("76 Trombones", 0))

    def test_the_artist_is_the_folder_under_the_root(self):
        path = Path("/music/Kuhlosul/Shuffle/57 - Alive.mp3")
        self.assertEqual(import_files.artist_folder(path, "/music"), "Kuhlosul")

    def test_a_file_one_level_under_the_root_does_not_guess_an_artist(self):
        path = Path("/music/loose/song.mp3")
        self.assertEqual(import_files.artist_folder(path, "/music"), "")

    def test_a_file_outside_the_root_does_not_guess_an_artist(self):
        path = Path("/elsewhere/Kuhlosul/Shuffle/57 - Alive.mp3")
        self.assertEqual(import_files.artist_folder(path, "/music"), "")

    def test_the_copy_marker_is_not_part_of_the_title(self):
        self.assertEqual(import_files.from_filename(Path("/x/12 - Song (2).opus")),
                         ("Song", 12))
        self.assertEqual(import_files.from_filename(Path("/x/Song (3).mp3")),
                         ("Song", 0))


class UntaggedFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "music"
        self.root.mkdir()
        self.store = Store(base / "fixture.db")
        self.addCleanup(self.store.conn.close)
        self.store.set_settings({"root": str(self.root)})

    def track(self):
        return dict(self.store.conn.execute(
            "SELECT * FROM tracks WHERE provider='local'").fetchone())

    def test_a_numbered_file_takes_its_artist_from_the_folder(self):
        path = make_plain_mp3(self.root / "Kuhlosul" / "Shuffle" / "57 - Alive.mp3")
        self.assertEqual(import_files.probe(path, self.root)["artist"], "Kuhlosul")
        reconcile.sweep(self.store)
        row = self.track()
        self.assertEqual(row["artist_name"], "Kuhlosul")
        self.assertEqual(row["title"], "Alive")

    def test_a_file_with_no_folder_and_no_tags_does_not_make_the_number_the_artist(self):
        path = make_plain_mp3(self.root / "76 - I Believe.mp3")
        meta = import_files.probe(path, self.root)
        self.assertEqual(meta["artist"], "")
        self.assertEqual(meta["title"], "I Believe")
        self.assertEqual(meta["track_number"], 76)

    def test_reconcile_reads_the_numbered_layout_the_same_way(self):
        path = make_plain_mp3(self.root / "Kuhlosul" / "Shuffle" / "57 - Alive.mp3")
        meta = reconcile._probe(path, self.root)
        self.assertEqual((meta["artist"], meta["title"], meta["track_number"]),
                         ("Kuhlosul", "Alive", 57))

    def test_a_copy_marked_two_is_the_same_song_not_a_second_row(self):
        owned = make_plain_mp3(self.root / "Kuhlosul" / "Shuffle" / "57 - Alive.mp3")
        track_id = self.store.track("local", "key", "Alive", artist_name="Kuhlosul")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=?, duration=3 WHERE id=?",
                                    (str(owned), track_id))
            self.store.conn.commit()
        copy = make_plain_mp3(self.root / "Kuhlosul" / "Shuffle" / "57 - Alive (2).mp3")
        seen = reconcile.sweep(self.store)
        self.assertEqual(seen.get("duplicate"), 1)
        self.assertNotIn("imported", seen)
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) FROM tracks").fetchone()[0], 1)

    def test_a_row_already_titled_two_still_matches_its_clean_file(self):
        # What the old sweep left behind: a row whose title kept the marker.
        orphan = make_plain_mp3(self.root / "Kuhlosul" / "Shuffle" / "57 - Alive.mp3")
        track_id = self.store.track("apple", "42", "Alive (2)", artist_name="Kuhlosul")
        self.store.enqueue(track_id, "apple", "apple:42")
        seen = reconcile.sweep(self.store)
        self.assertEqual(seen.get("linked"), 1)
        row = dict(self.store.conn.execute(
            "SELECT file FROM tracks WHERE id=?", (track_id,)).fetchone())
        self.assertEqual(row["file"], str(orphan))


if __name__ == "__main__":
    unittest.main()