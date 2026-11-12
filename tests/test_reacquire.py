"""Re-acquiring the back catalogue: source stream in, old media kept.

The pass must be resumable, must never transcode, and must leave the library
browsable (canonical paths, not uuid staging folders).
"""
import io
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import reacquire
import replacements
from db import Store
from engine.queue import Queue


def wav(value: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes([value, 0]) * 32000)
    return output.getvalue()


class Pass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "music"
        self.root.mkdir()
        self.store = Store(Path(self.tmp.name) / "fixture.db")
        self.addCleanup(self.store.conn.close)
        self.store.set_settings({"root": str(self.root), "audio_format": "wav"})
        self.queue = Queue(self.store)          # never started

    def add(self, title="Song", artist="Artist", cls="mp3-only",
            origin="https://example.test/track", duration=3.0):
        track_id = self.store.track("apple", title, title, artist_name=artist)
        job_id = self.store.enqueue(track_id, "apple", origin)
        path = self.root / artist / "Singles" / f"{title}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav(1))
        with self.store.lock:
            self.store.conn.execute(
                "UPDATE tracks SET file=?, size=?, duration=? WHERE id=?",
                (str(path), path.stat().st_size, duration, track_id))
            self.store.conn.commit()
        self.store.update_job(job_id, status="completed", resolved_url=origin)
        return {"track_id": track_id, "title": title, "artist": artist,
                "duration": duration, "origin": origin, "class": cls, "abr": 128.0}

    def fake(self, cmd, **kwargs):
        path = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "wav"))
        path.write_bytes(wav(2))
        return 0, ""

    def run_pass(self, work, **kwargs):
        with patch("replacements.ytdlp.run", side_effect=self.fake):
            return reacquire.run(self.store, self.queue, work, pause=0, **kwargs)

    def test_plan_keeps_sources_and_drops_dead_or_finished(self):
        good = self.add("Good")
        dead = self.add("Dead", cls="dead")
        done = self.add("Done", origin="https://example.test/done")
        replacements.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO source_replacements (track_id,job_id,created,old_file,old_url,"
                "old_pinned,new_url,new_file,status) VALUES (?,?,?,?,?,?,?,?, 'completed')",
                (done["track_id"], 1, 0, "x", "old", 0, done["origin"], "y"))
            self.store.conn.commit()
        work = reacquire.plan(self.store, [good, dead, done])
        self.assertEqual([item["track_id"] for item in work], [good["track_id"]])

    def test_a_track_with_no_file_is_not_in_the_plan(self):
        item = self.add("Gone")
        Path(self.store.track_by_id(item["track_id"])["file"]).unlink()
        self.assertEqual(reacquire.plan(self.store, [item]), [])

    def test_the_swap_lands_at_a_canonical_path_and_keeps_the_old_media(self):
        item = self.add("Song")
        old = self.root / "elsewhere" / "Song.wav"
        old.parent.mkdir(parents=True, exist_ok=True)
        Path(self.store.track_by_id(item["track_id"])["file"]).rename(old)
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?",
                                    (str(old), item["track_id"]))
            self.store.conn.commit()
        seen = self.run_pass([item])
        self.assertEqual(seen["done"], 1)
        track = self.store.track_by_id(item["track_id"])
        new = Path(track["file"])
        self.assertNotIn(".replacements", str(new))
        self.assertEqual(new, self.root / "Artist" / "Singles" / "Song.wav")
        self.assertNotEqual(new, old)
        self.assertEqual(new.read_bytes(), wav(2))
        self.assertFalse(old.exists())                  # nothing left looking like an orphan
        # Parked outside the tree, filed under the replacement, ready for reverse.
        parked = list((self.root / ".replacements" / "retained").rglob("*.wav"))
        self.assertEqual(len(parked), 1)
        self.assertEqual(parked[0].read_bytes(), wav(1))
        with self.store.lock:
            row = dict(self.store.conn.execute(
                "SELECT status, old_file FROM source_replacements").fetchone())
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["old_file"], str(old))

    def test_when_the_canonical_name_is_the_old_file_it_moves_aside(self):
        item = self.add("Song", cls="native-opus")
        canonical = (self.root / "Artist" / "Singles" / "Song.wav")
        old = Path(self.store.track_by_id(item["track_id"])["file"])
        self.assertEqual(canonical, old)
        seen = self.run_pass([item])
        self.assertEqual(seen["done"], 1)
        track = self.store.track_by_id(item["track_id"])
        self.assertEqual(Path(track["file"]), canonical)
        self.assertEqual(canonical.read_bytes(), wav(2))
        parked = list((self.root / ".replacements" / "retained").rglob("*.wav"))
        self.assertEqual(len(parked), 1)
        self.assertEqual(parked[0].read_bytes(), wav(1))
        with self.store.lock:
            row = dict(self.store.conn.execute(
                "SELECT status, old_file FROM source_replacements").fetchone())
        self.assertEqual(row["old_file"], str(canonical))

    def test_the_sweep_finds_nothing_to_adopt_after_a_swap(self):
        from library import reconcile
        item = self.add("Song")
        self.run_pass([item])
        self.assertEqual(reconcile.orphans(self.store), [])

    def test_a_second_run_does_nothing(self):
        item = self.add("Song")
        self.run_pass([item])
        self.assertEqual(reacquire.plan(self.store, [item]), [])

    def test_the_worker_takes_a_swap_only_while_the_queue_is_empty(self):
        item = self.add("Song")
        self.queue._reacquire_work = [item]
        with patch("replacements.ytdlp.run", side_effect=self.fake):
            self.assertTrue(self.queue._reacquire_one())   # did one track
            self.assertFalse(self.queue._reacquire_one())  # nothing left
        self.assertEqual(self.queue._reacquire_failures, 0)
        self.assertEqual(Path(self.store.track_by_id(item["track_id"])["file"]).read_bytes(),
                         wav(2))

    def test_a_failed_swap_counts_toward_the_outage_stop(self):
        item = self.add("Song")
        self.queue._reacquire_work = [item]
        with patch("replacements.ytdlp.run", side_effect=RuntimeError("provider down")):
            self.assertTrue(self.queue._reacquire_one())
        self.assertEqual(self.queue._reacquire_failures, 1)

    def test_the_pass_stops_after_consecutive_failures(self):
        items = [self.add(f"Song {n}", origin=f"https://example.test/{n}") for n in range(4)]
        with patch("replacements.ytdlp.run", side_effect=RuntimeError("provider down")):
            seen = reacquire.run(self.store, self.queue, items, pause=0, max_failures=2)
        self.assertEqual(seen["failed"], 2)
        self.assertEqual(seen["done"], 0)
        self.assertIn("source looks down", seen["stopped"])


if __name__ == "__main__":
    unittest.main()