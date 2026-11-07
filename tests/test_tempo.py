"""Tempo analysis: worker behaviour, manual correction, and the HTTP surface.

Detection itself is measured separately (tests/test_analysis is not a thing here
because the real check is against real audio); these tests use a fake detector so
they stay fast and never decode the owner's library.
"""
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import db
from library import tempo


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hl-tempo-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        # db.DB_PATH is resolved at import time, so patching the environment here
        # would silently point the store at the owner's real library. Patch the
        # module attribute instead — that is the only thing Store() reads.
        self.path = mock.patch.object(db, "DB_PATH", self.dir / "t.db")
        self.path.start()
        self.addCleanup(self.path.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        # two real files so the worker's is_file() check passes
        self.files = []
        for index in range(2):
            path = self.dir / f"t{index}.mp3"
            path.write_bytes(b"\xff\xfb" + b"\x00" * 4096)
            self.files.append(path)
        for index, path in enumerate(self.files):
            with self.store.lock:
                self.store.conn.execute(
                    "INSERT INTO tracks(provider, provider_track_id, title, artist_name, file, added) "
                    "VALUES('local', ?, ?, 'A', ?, 1)", (f"k{index}", f"Track {index}", str(path)))
                self.store.conn.commit()
        self.ids = [row[0] for row in self.store.conn.execute("SELECT id FROM tracks ORDER BY id")]

    def wait_idle(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not tempo.status()["running"]:
                return True
            time.sleep(0.02)
        return False


class WorkerTest(Base):
    def test_batch_writes_bpm_and_reports_progress(self):
        fake = {"bpm": 128.0, "confidence": 0.8, "phase": 0.25}
        with mock.patch.object(tempo, "detect", return_value=fake) as detector:
            started = tempo.start(self.store, self.ids)
            self.assertTrue(started["started"])
            self.assertTrue(self.wait_idle(), "worker finished")
        self.assertEqual(detector.call_count, 2)
        rows = self.store.conn.execute(
            "SELECT id, bpm, bpm_source, bpm_phase FROM tracks ORDER BY id").fetchall()
        self.assertEqual([row[1] for row in rows], [128.0, 128.0])
        self.assertEqual([row[2] for row in rows], ["auto", "auto"])
        self.assertEqual([row[3] for row in rows], [0.25, 0.25])
        status = tempo.status()
        self.assertEqual(status["done"], 2)
        self.assertFalse(status["running"])

    def test_manual_tempo_is_never_overwritten(self):
        with self.store.lock:
            self.store.conn.execute(
                "UPDATE tracks SET bpm = 87, bpm_source = 'manual' WHERE id = ?", (self.ids[0],))
            self.store.conn.commit()
        candidates = tempo.candidates(self.store)
        self.assertNotIn(self.ids[0], candidates, "manual row is not a candidate")
        self.assertIn(self.ids[1], candidates)
        with mock.patch.object(tempo, "detect", return_value={"bpm": 174.0, "confidence": 1, "phase": 0}):
            tempo.start(self.store, self.ids)
            self.assertTrue(self.wait_idle())
        kept = self.store.conn.execute("SELECT bpm, bpm_source FROM tracks WHERE id = ?",
                                       (self.ids[0],)).fetchone()
        self.assertEqual((kept[0], kept[1]), (87, "manual"))

    def test_detector_failure_marks_auto_empty_not_crash(self):
        with mock.patch.object(tempo, "detect", side_effect=RuntimeError("boom")):
            tempo.start(self.store, self.ids[:1])
            self.assertTrue(self.wait_idle())
        row = self.store.conn.execute("SELECT bpm, bpm_source FROM tracks WHERE id = ?",
                                      (self.ids[0],)).fetchone()
        self.assertEqual((row[0], row[1]), (0, ""), "a failed read leaves no fake tempo")

    def test_missing_file_is_skipped(self):
        self.files[0].unlink()
        with mock.patch.object(tempo, "detect", return_value={"bpm": 120.0, "confidence": 1, "phase": 0}) as detector:
            tempo.start(self.store, self.ids)
            self.assertTrue(self.wait_idle())
        self.assertEqual(detector.call_count, 1)
        self.assertGreaterEqual(tempo.status()["failed"], 1)


class ManualTest(Base):
    def _bpm(self, track_id):
        row = self.store.conn.execute("SELECT bpm, bpm_source FROM tracks WHERE id = ?",
                                      (track_id,)).fetchone()
        return (row[0], row[1])

    def test_number_double_halve_and_clear(self):
        temper = tempo.set_manual(self.store, self.ids[0], 128)
        self.assertEqual(temper["bpm"], 128)
        self.assertEqual(self._bpm(self.ids[0]), (128, "manual"))
        tempo.set_manual(self.store, self.ids[0], None, "double")
        self.assertEqual(self._bpm(self.ids[0]), (256, "manual"))
        tempo.set_manual(self.store, self.ids[0], None, "halve")
        self.assertEqual(self._bpm(self.ids[0]), (128, "manual"))
        tempo.set_manual(self.store, self.ids[0], None, "clear")
        self.assertEqual(self._bpm(self.ids[0]), (0, ""))

    def test_out_of_range_rejected_and_folded(self):
        self.assertIn("error", tempo.set_manual(self.store, self.ids[0], 900))
        self.assertIn("error", tempo.set_manual(self.store, self.ids[0], 5))
        self.assertIn("error", tempo.set_manual(self.store, self.ids[0], 700))
        self.assertIn("error", tempo.set_manual(self.store, 999999, 120))

    def test_double_folds_a_fast_track_back_into_range(self):
        tempo.set_manual(self.store, self.ids[0], 174)
        folded = tempo.set_manual(self.store, self.ids[0], None, "double")
        self.assertEqual(folded["bpm"], 174, "348 folds back to 174 rather than leaving the range")

    def test_unknown_track(self):
        self.assertIn("error", tempo.set_manual(self.store, 424242, 120))


if __name__ == "__main__":
    unittest.main()
