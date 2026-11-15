"""Every audio file in the library belongs to a row.

A file with no row is invisible: the UI cannot show it, the queue cannot know it
exists, and the next retry writes another copy of the same song. The sweep adopts
those files instead of leaving them behind.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from db import Store
from library import reconcile


def make_mp3(path: Path, title: str = "Song", artist: str = "Artist",
             album: str = "Album", seconds: int = 3, tone: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={tone}:duration={seconds}",
         "-c:a", "libmp3lame", "-b:a", "128k",
         "-metadata", f"title={title}", "-metadata", f"artist={artist}",
         "-metadata", f"album={album}", str(path)], check=True)
    return path


class Adoption(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "music"
        self.root.mkdir()
        self.store = Store(base / "fixture.db")
        self.addCleanup(self.store.conn.close)
        self.store.set_settings({"root": str(self.root)})

    def rows(self, **where):
        clause = " AND ".join(f"{key}=?" for key in where) or "1"
        return [dict(row) for row in self.store.conn.execute(
            f"SELECT * FROM tracks WHERE {clause}", tuple(where.values()))]

    def test_a_file_no_row_points_at_becomes_a_visible_track(self):
        path = make_mp3(self.root / "loose" / "song.mp3")
        seen = reconcile.sweep(self.store)
        self.assertEqual(seen["imported"], 1)
        track = self.rows(provider="local")[0]
        self.assertEqual(track["file"], str(path))
        self.assertEqual(track["size"], path.stat().st_size)
        self.assertEqual(track["title"], "Song")
        self.assertEqual(track["artist_name"], "Artist")

    def test_the_sweep_is_idempotent(self):
        make_mp3(self.root / "song.mp3")
        reconcile.sweep(self.store)
        again = reconcile.sweep(self.store)
        self.assertEqual(again, {})
        self.assertEqual(len(self.rows()), 1)

    def test_a_file_that_belongs_to_an_empty_row_is_linked_not_duplicated(self):
        track_id = self.store.track("apple", "42", "Song", artist_name="Artist")
        split = self.store.enqueue(track_id, "apple", "apple:42")
        self.store.update_job(split, status="failed", error_type="retryable",
                              error_message="yt-dlp wrote no file")
        path = make_mp3(self.root / "Artist" / "Album" / "Song.mp3")
        seen = reconcile.sweep(self.store)
        self.assertEqual(seen["linked"], 1)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["file"], str(path))
        job = dict(self.store.conn.execute(
            "SELECT status, error_message FROM download_jobs").fetchone())
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["error_message"], "")

    def test_a_file_a_row_already_owns_is_left_alone(self):
        owned = make_mp3(self.root / "owned.mp3", title="Owned")
        track_id = self.store.track("apple", "1", "Owned", artist_name="Artist")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?",
                                    (str(owned), track_id))
            self.store.conn.commit()
        self.assertEqual(reconcile.sweep(self.store), {})
        self.assertEqual(self.rows()[0]["file"], str(owned))

    def test_a_second_encoding_of_an_owned_song_is_reported_not_added(self):
        owned = make_mp3(self.root / "a" / "Song.mp3", tone=440)
        self.store.track("apple", "1", "Song", artist_name="Artist")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=?, duration=3 WHERE id=1",
                                    (str(owned),))
            self.store.conn.commit()
        make_mp3(self.root / "b" / "Song (2).mp3", tone=660)
        seen = reconcile.sweep(self.store)
        self.assertEqual(seen["duplicate"], 1)
        self.assertEqual(len(self.rows()), 1)

    def test_a_different_length_version_of_a_known_song_is_its_own_track(self):
        owned = make_mp3(self.root / "a" / "Song.mp3", seconds=3)
        self.store.track("apple", "1", "Song", artist_name="Artist")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=?, duration=3 WHERE id=1",
                                    (str(owned),))
            self.store.conn.commit()
        make_mp3(self.root / "b" / "Song extended.mp3", seconds=9)
        reconcile.sweep(self.store)
        self.assertEqual(len(self.rows()), 2)

    def test_media_retained_by_a_swap_is_not_adopted(self):
        import replacements
        path = make_mp3(self.root / "Artist" / "Album" / "Song.mp3")
        replacements.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO source_replacements (track_id,job_id,created,old_file,old_url,"
                "old_pinned,new_url,new_file,status) VALUES (1,1,0,?,'u',0,'u',?,'completed')",
                (str(path), str(self.root / "Artist" / "Album" / "Song.m4a")))
            self.store.conn.commit()
        self.assertEqual(reconcile.orphans(self.store), [])
        self.assertEqual(reconcile.sweep(self.store), {})

    def test_a_missing_row_file_is_reunited_with_its_row(self):
        track_id = self.store.track("apple", "7", "Gone", artist_name="Artist")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?",
                                    (str(self.root / "missing.mp3"), track_id))
            self.store.conn.commit()
        path = make_mp3(self.root / "Gone.mp3", title="Gone")
        seen = reconcile.sweep(self.store)
        self.assertEqual(seen["linked"], 1)
        self.assertEqual(self.rows()[0]["file"], str(path))


if __name__ == "__main__":
    unittest.main()