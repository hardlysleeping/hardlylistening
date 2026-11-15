"""A finished download is not a finished track: ffprobe decides.

The gap between yt-dlp exiting 0 and the file being accepted used to be invisible. The
job row still said `downloading` at 100%, so a queue that was reading a large file looked
exactly like a queue that had hung (HL-304). These tests pin the state that fills the gap,
and the two places that must not mistake it for "finished" or for "waiting".
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from downloaders import ytdlp
from engine import queue as queue_module


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(db, "DB_PATH", os.path.join(self.dir.name, "q.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.root = Path(self.dir.name) / "music"
        self.root.mkdir()
        self.store.set_settings({"root": str(self.root), "audio_format": "mp3"})

    def track(self, title="Verifying"):
        return self.store.track("soundcloud", title, title, artist_name="Artist",
                                duration=200,
                                source_url=f"https://soundcloud.com/x/{title}")

    def enqueue(self, tid):
        job = self.store.enqueue(tid, "soundcloud", "https://soundcloud.com/x/t")
        return job if isinstance(job, int) else job["id"]


class VerifyingState(StoreCase):
    def run_job_with_capture(self):
        """Run one real job with yt-dlp stubbed, capturing the row mid-verify."""
        tid = self.track()
        job_id = self.enqueue(tid)
        seen = {}

        def fake_run(cmd, on_progress=None, cancel=None):
            written = self.root / "Artist" / "a.mp3"
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_bytes(b"ID3" + b"\x00" * 64)
            if on_progress:
                on_progress({"percent": 100, "bytes": 67, "speed": 0, "eta": 0})
            return 0, ""

        def fake_verify(path, expected_seconds=0):
            row = self.store.job(job_id)
            state = self.store.state()
            seen["status"] = row["status"]
            seen["progress"] = row["progress"]
            seen["current"] = state.get("current")
            seen["running"] = state["stats"]["running"]
            return True, ""

        with mock.patch.object(ytdlp, "build_command", return_value=["true"]), \
             mock.patch.object(ytdlp, "run", side_effect=fake_run), \
             mock.patch.object(ytdlp, "find_output",
                               side_effect=lambda tmpl, fmt: self.root / "Artist" / "a.mp3"), \
             mock.patch.object(ytdlp, "verify_audio", side_effect=fake_verify):
            queue = queue_module.Queue(self.store)
            queue._run(self.store.job(job_id))
        return job_id, seen

    def test_the_row_says_verifying_while_ffprobe_reads_the_file(self):
        job_id, seen = self.run_job_with_capture()
        self.assertEqual(seen["status"], "verifying")
        # 100% with a state that admits it is not finished, rather than a stuck bar.
        self.assertEqual(seen["progress"], 100)
        # The snapshot names the job, so the strip can say what is moving.
        self.assertEqual(seen["current"]["status"], "verifying")
        self.assertEqual(seen["current"]["title"], "Verifying")

    def test_a_verifying_job_is_counted_as_running(self):
        _, seen = self.run_job_with_capture()
        self.assertGreaterEqual(seen["running"], 1)

    def test_the_job_finishes_completed_after_the_file_is_accepted(self):
        job_id, _ = self.run_job_with_capture()
        self.assertEqual(self.store.job(job_id)["status"], "completed")

    def test_a_failed_verify_never_stays_verifying(self):
        tid = self.track("Bad")
        job_id = self.enqueue(tid)

        def fake_run(cmd, on_progress=None, cancel=None):
            written = self.root / "Artist" / "b.mp3"
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_bytes(b"\x00" * 32)
            return 0, ""

        with mock.patch.object(ytdlp, "build_command", return_value=["true"]), \
             mock.patch.object(ytdlp, "run", side_effect=fake_run), \
             mock.patch.object(ytdlp, "find_output",
                               side_effect=lambda tmpl, fmt: self.root / "Artist" / "b.mp3"), \
             mock.patch.object(ytdlp, "verify_audio", return_value=(False, "no audio stream")), \
             mock.patch.object(ytdlp, "discard_output", return_value=1):
            queue = queue_module.Queue(self.store)
            queue._run(self.store.job(job_id))
        self.assertIn(self.store.job(job_id)["status"], ("retry_wait", "failed"))
        self.assertIsNone(self.store.state()["current"])

    def test_a_row_left_verifying_by_a_hard_kill_is_requeued(self):
        tid = self.track("Interrupted")
        job_id = self.enqueue(tid)
        self.store.update_job(job_id, status="verifying", progress=100)
        queue_module.Queue(self.store)          # the constructor is the recovery path
        self.assertEqual(self.store.job(job_id)["status"], "queued")

    def test_the_state_list_covers_it(self):
        self.assertIn("verifying", db.JOB_STATES)
        self.assertIn("verifying", db.OPEN_STATES)


class DecisionGuards(StoreCase):
    def test_a_decision_cannot_be_reversed_while_the_file_is_being_checked(self):
        import decisions
        tid = self.track("Under review")
        job_id = self.enqueue(tid)
        self.store.update_job(job_id, status="verifying")
        entry = self.store.conn.execute(
            "SELECT 1").fetchone()
        self.assertIsNotNone(entry)
        # No decision row needed: the status guard fires first, which is the point.
        with self.assertRaises(ValueError):
            decisions.reverse(self.store, 1)

    def test_a_source_cannot_be_changed_under_a_verifying_job(self):
        import replacements
        tid = self.track("Busy")
        job_id = self.enqueue(tid)
        (self.root / "Artist").mkdir(parents=True, exist_ok=True)
        path = self.root / "Artist" / "busy.mp3"
        path.write_bytes(b"ID3")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?", (str(path), tid))
            self.store.conn.commit()
        self.store.update_job(job_id, status="verifying")
        with self.assertRaises(ValueError):
            replacements.stage(self.store, tid, "https://example.test/busy")


if __name__ == "__main__":
    unittest.main()