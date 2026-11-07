"""The queue page can only show progress if the snapshot carries it.

Two things matter here and both have silently been wrong before:

* ``state()["current"]`` has to name the job that is actually running, with its
  live numbers — the row-level "downloading" label alone cannot tell a moving
  download from a hung queue.
* ``POST /api/track/<id>/retry`` has to say whether it queued anything, otherwise
  the button looks broken whenever the job was not eligible.

Tests patch ``db.DB_PATH``; the env var does nothing because the path is resolved
at import (see the 0.9.7 test-DB incident).
"""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import db
from downloaders import ytdlp


SCHEMA_MARK = None


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(db, "DB_PATH",
                                    os.path.join(self.dir.name, "q.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())

    def track(self, title="T", artist="A", provider="soundcloud"):
        return self.store.track(provider, title, title, artist_name=artist,
                                duration=200,
                                source_url=f"https://soundcloud.com/x/{title}")


class CurrentJob(StoreCase):
    def test_empty_queue_has_no_current(self):
        self.assertIsNone(self.store.state()["current"])

    def test_running_job_is_reported_with_its_numbers(self):
        tid = self.track("Moving", "Artist")
        job = self.store.enqueue(tid, "soundcloud", "https://soundcloud.com/x/Moving")
        pid = job if isinstance(job, int) else job.get("id")
        self.store.update_job(pid, status="downloading", started=time.time(),
                              progress=42.7, bytes=3120000, speed=845000.0,
                              eta=97, attempts=1)
        cur = self.store.state()["current"]
        self.assertIsNotNone(cur)
        self.assertEqual(cur["track_id"], tid)
        self.assertEqual(cur["title"], "Moving")
        self.assertEqual(cur["artist"], "Artist")
        self.assertEqual(cur["status"], "downloading")
        self.assertAlmostEqual(cur["progress"], 42.7)
        self.assertEqual(cur["bytes"], 3120000)
        self.assertAlmostEqual(cur["speed"], 845000.0, places=3)
        self.assertEqual(cur["eta"], 97)
        self.assertEqual(cur["attempts"], 1)
        self.assertEqual(cur["max_attempts"], 4)

    def test_resolving_counts_as_running(self):
        tid = self.track("Looking", "Artist")
        job = self.store.enqueue(tid, "apple", "https://music.apple.com/x")
        pid = job if isinstance(job, int) else job.get("id")
        self.store.update_job(pid, status="resolving", started=time.time())
        self.assertEqual(self.store.state()["current"]["status"], "resolving")

    def test_queued_and_failed_are_not_current(self):
        for status in ("queued", "failed", "retry_wait", "review", "completed"):
            tid = self.track(f"Track {status}", "Artist")
            job = self.store.enqueue(tid, "soundcloud", f"https://soundcloud.com/x/{status}")
            pid = job if isinstance(job, int) else job.get("id")
            self.store.update_job(pid, status=status)
        self.assertIsNone(self.store.state()["current"])

    def test_the_newest_started_job_wins(self):
        old = self.track("Old", "Artist")
        new = self.track("New", "Artist")
        a = self.store.enqueue(old, "soundcloud", "u1")
        b = self.store.enqueue(new, "soundcloud", "u2")
        apid = a if isinstance(a, int) else a.get("id")
        bpid = b if isinstance(b, int) else b.get("id")
        self.store.update_job(apid, status="downloading", started=time.time() - 60)
        self.store.update_job(bpid, status="downloading", started=time.time())
        self.assertEqual(self.store.state()["current"]["title"], "New")


class TrackRows(StoreCase):
    def test_rows_carry_what_the_ui_prints(self):
        tid = self.track("Row", "Artist")
        pid = self.store.playlist("Set", "soundcloud", "https://soundcloud.com/set/1")
        self.store.add_to_playlist(pid, tid, 1)
        job = self.store.enqueue(tid, "soundcloud", "https://soundcloud.com/x/Row")
        jid = job if isinstance(job, int) else job.get("id")
        self.store.update_job(jid, status="retry_wait", attempts=2, max_attempts=4,
                              next_retry_at=time.time() + 31)
        row = self.store.state()["playlists"][0]["tracks"][0]
        self.assertEqual(row["status"], "retry_wait")
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(row["max_attempts"], 4)
        self.assertEqual(row["started"], 0)
        self.assertGreater(row["next_try"], time.time())

    def test_completed_row_reports_size_for_the_done_label(self):
        tid = self.track("Done", "Artist")
        pid = self.store.playlist("Set", "soundcloud", "https://soundcloud.com/set/2")
        self.store.add_to_playlist(pid, tid, 1)
        job = self.store.enqueue(tid, "soundcloud", "https://soundcloud.com/x/Done")
        jid = job if isinstance(job, int) else job.get("id")
        self.store.update_job(jid, status="completed")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET size=7188735 WHERE id=?", (tid,))
            self.store.conn.commit()
        row = self.store.state()["playlists"][0]["tracks"][0]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["size"], 7188735)


class StrongReview(StoreCase):
    """A 90%+ candidate should not need a human click — and the page must know how
    many rows that is, so it can offer one button instead of 18.

    Candidates are scored for real (`hit_score`), not stubbed: the count has to
    mean what the ranking means, and the ranking was wrong before this.
    """

    def review_track(self, title, pool_specs):
        tid = self.track(title, "Artist", provider="apple")
        job = self.store.enqueue(tid, "apple", "https://music.apple.com/x")
        jid = job if isinstance(job, int) else job.get("id")
        pool = []
        for index, (artist, candidate_title) in enumerate(pool_specs):
            hit = {"url": f"https://soundcloud.com/x/{title}-{index}",
                   "artist": artist, "title": candidate_title, "duration": 200}
            hit["match"] = ytdlp.hit_score(f"Artist {title}", hit)
            pool.append(hit)
        self.store.update_job(jid, status="review", resolved_url="", error_type="review",
                              match=max((c["match"] for c in pool), default=0),
                              candidates=json.dumps(pool))
        return tid, jid, pool

    def test_only_rows_a_strong_candidate_answers_are_counted(self):
        # Exact artist+title = 1.0. A different song by a different artist is low.
        self.review_track("Exact Song", [("Artist", "Exact Song")])
        self.review_track("Other Song", [("Nobody", "Something Else Entirely")])
        self.assertEqual(self.store.state()["stats"]["review_strong"], 1)

    def test_just_under_the_line_is_not_counted(self):
        tid, jid, pool = self.review_track("Almost Song", [("Artist", "Almost Song")])
        # Nudge the only candidate under 0.90 the way a near-miss looks.
        pool[0]["artist"] = "Artist "
        pool[0]["title"] = "Almost Songz"
        pool[0]["match"] = 0.899
        self.store.update_job(jid, candidates=json.dumps(pool), match=0.899)
        self.assertEqual(self.store.state()["stats"]["review_strong"], 0)

    def test_failed_rows_are_not_counted_even_with_a_strong_pool(self):
        tid, jid, pool = self.review_track("Failed Song", [("Artist", "Failed Song")])
        self.store.update_job(jid, status="failed")
        self.assertEqual(self.store.state()["stats"]["review_strong"], 0)

    def test_route_queues_the_strongest_candidate_only(self):
        import server
        strong_tid, strong_job, pool = self.review_track(
            "Strong Song", [("Wrong Person", "Strong Song (Someone Else Remix)"),
                            ("Artist", "Strong Song"), ("Artist", "Strong Song [Live]")])
        weak_tid, weak_job, _ = self.review_track(
            "Weak Song", [("Nobody", "A Different Song Altogether")])
        queue = mock.Mock()
        handler = mock.Mock()
        handler.json = mock.Mock()
        with mock.patch.object(server, "boot", return_value=(self.store, queue)):
            server.Handler.do_queue_auto(handler)
        payload = handler.json.call_args[0][0]
        self.assertEqual(payload["applied"], 1, "only the row with a real strong match")
        self.assertEqual(payload["considered"], 2)
        queue.wake.assert_called_once()
        after = self.store.job(strong_job)
        self.assertEqual(after["status"], "queued")
        self.assertTrue(after["resolved_url"].endswith("Strong Song-1"), after["resolved_url"])
        self.assertEqual(after["pinned"], 1)
        self.assertEqual(self.store.job(weak_job)["status"], "review")
        self.assertEqual(self.store.state()["stats"]["review_strong"], 0)


class RetryPayload(unittest.TestCase):
    """The route's answer is what makes the button legible, so pin its shape."""

    def test_retry_reports_queued_true_when_it_requeues(self):
        import server
        store = mock.Mock()
        store.job_for_track.return_value = {"id": 5, "status": "failed",
                                            "provider": "soundcloud",
                                            "source_url": "u", "max_attempts": 4}
        store.enqueue.return_value = True
        store.lock = mock.MagicMock()
        store.lock.__enter__ = mock.Mock()
        store.lock.__exit__ = mock.Mock()
        handler = mock.Mock()
        handler.json = mock.Mock()
        handler.body = mock.Mock(return_value={})
        with mock.patch.object(server, "boot", return_value=(store, mock.Mock())):
            server.Handler.do_track_retry(handler, 7)
        payload = handler.json.call_args[0][0]
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["queued"])
        self.assertEqual(payload["max_attempts"], 4)

    def test_retry_reports_queued_false_when_nothing_eligible(self):
        import server
        store = mock.Mock()
        store.job_for_track.return_value = {"id": 5, "status": "downloading"}
        handler = mock.Mock()
        handler.json = mock.Mock()
        handler.body = mock.Mock(return_value={})
        with mock.patch.object(server, "boot", return_value=(store, mock.Mock())):
            ret = server.Handler.do_track_retry(handler, 7)
        if ret is not None:
            handler.json(ret)
        payload = handler.json.call_args[0][0]
        self.assertFalse(payload["queued"])


if __name__ == "__main__":
    unittest.main()
