"""Numeric-title repair: which rows qualify, what replaces them, and that the
repair runs off the request thread without touching files."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import db
from library import metadata_repair as repair


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hl-titles-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.patch = mock.patch.object(db, "DB_PATH", self.dir / "t.db")
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.files = []
        for index in range(3):
            path = self.dir / f"{index + 1:02d} - 228950764{index}.mp3"
            path.write_bytes(b"\x00" * 2048)
            self.files.append(path)
        rows = [("2289507640", "2289507640"), ("2289507641", "2289507641"),
                ("Real Title", "2289507642")]
        for (title, provider_id), path in zip(rows, self.files):
            with self.store.lock:
                self.store.conn.execute(
                    "INSERT INTO tracks(provider, provider_track_id, title, artist_name, album, file, added) "
                    "VALUES('soundcloud', ?, ?, '', '', ?, 1)", (provider_id, title, str(path)))
                self.store.conn.commit()
        self.ids = [row[0] for row in self.store.conn.execute("SELECT id FROM tracks ORDER BY id")]

    def wait(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not repair.status()["running"]:
                return True
            time.sleep(0.02)
        return False


class IdDetection(unittest.TestCase):
    def test_numeric_titles_are_ids(self):
        self.assertTrue(repair.looks_like_id("2289507644"))
        self.assertTrue(repair.looks_like_id("", "1234"))
        self.assertTrue(repair.looks_like_id("1234", "1234"))
        self.assertFalse(repair.looks_like_id("Peaches (TIGER DROOL Bootleg)"))

    def test_filename_fallback_drops_the_track_number(self):
        self.assertEqual(repair._from_filename(Path("06 - Shut Up and Drive.mp3")),
                         "Shut Up and Drive")
        self.assertEqual(repair._from_filename(Path("06 - 2289507644.mp3")), "")


class RepairFlow(Base):
    def test_preview_counts_without_writing(self):
        result = repair.preview(self.store)
        self.assertEqual(result["count"], 2, "only the two id-titled rows qualify")
        self.assertEqual(self.store.conn.execute(
            "SELECT title FROM tracks WHERE id = ?", (self.ids[0],)).fetchone()[0], "2289507640")

    def test_repair_fills_titles_from_tags_and_leaves_real_ones_alone(self):
        tags = {"title": "Real Song Name", "artist": "Real Artist"}
        with mock.patch.object(repair, "_tags", return_value=tags):
            started = repair.repair_async(self.store)
            self.assertTrue(started["started"])
            self.assertTrue(self.wait())
        status = repair.status()
        self.assertEqual(status["changed"], 2)
        rows = [tuple(r) for r in self.store.conn.execute(
            "SELECT title, artist_name FROM tracks ORDER BY id")]
        self.assertEqual(rows[0], ("Real Song Name", "Real Artist"))
        self.assertEqual(rows[1], ("Real Song Name", "Real Artist"))
        self.assertEqual(rows[2], ("Real Title", ""), "a genuine title is never replaced")

    def test_no_usable_name_leaves_the_row_untouched(self):
        # "01 - 2289507640.mp3" with no tags gives nothing better than the id, and
        # inventing a name would be worse than leaving it for the owner.
        with mock.patch.object(repair, "_tags", return_value={}):
            repair.repair_async(self.store)
            self.assertTrue(self.wait())
        first = self.store.conn.execute(
            "SELECT title FROM tracks WHERE id = ?", (self.ids[0],)).fetchone()[0]
        self.assertEqual(first, "2289507640")
        self.assertGreaterEqual(repair.status()["skipped"], 1)

    def test_missing_file_is_counted_not_guessed(self):
        self.files[0].unlink()
        with mock.patch.object(repair, "_tags", return_value={"title": "X"}):
            repair.repair_async(self.store)
            self.assertTrue(self.wait())
        self.assertGreaterEqual(repair.status()["missing_file"], 1)

    def test_second_start_while_running_is_refused(self):
        with mock.patch.object(repair, "_tags", side_effect=lambda p: (time.sleep(0.05) or {"title": "Slow"})):
            first = repair.repair_async(self.store)
            second = repair.repair_async(self.store)
            self.assertTrue(first["started"])
            self.assertFalse(second["started"])
            self.assertTrue(self.wait())


if __name__ == "__main__":
    unittest.main()
