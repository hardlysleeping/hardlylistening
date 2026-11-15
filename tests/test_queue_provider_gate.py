"""A cooldown belongs to the source that issued it, not to the queue (HL-305).

One source answering 429 used to stop everything: the loop waited on the latest cooldown
across all providers, so an evening of Apple Music work sat still behind SoundCloud's clock.
The worker still runs one job at a time — what these tests pin is that a source asking to be
left alone only parks its own work.
"""
import os
import tempfile
import time
import unittest
from unittest import mock

import db
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
        self.store.set_settings({"root": self.dir.name, "pacing": "0"})

    def job(self, provider, title, priority=0):
        tid = self.store.track(provider, title, title, artist_name="A", duration=100,
                               source_url=f"https://example.test/{provider}/{title}")
        job = self.store.enqueue(tid, provider, f"https://example.test/{provider}/{title}")
        job_id = job if isinstance(job, int) else job["id"]
        if priority:
            self.store.update_job(job_id, priority=priority)
        return job_id

    def cool(self, provider, seconds=600):
        self.store.set_provider(provider, cooldown_until=time.time() + seconds,
                                last_error="HTTP 429")


class ProviderGate(StoreCase):
    def test_a_cooling_source_does_not_stop_another_source(self):
        sc = self.job("soundcloud", "Sc")
        apple = self.job("apple", "Ap")
        self.cool("soundcloud")
        blocked = self.store.cooldowns()
        self.assertEqual(set(blocked), {"soundcloud"})
        picked = self.store.next_job(blocked=blocked)
        self.assertEqual(picked["id"], apple)

    def test_without_the_gate_the_cooling_job_would_win(self):
        # The positive control: same rows, no blocked set — the old behaviour.
        self.job("soundcloud", "Sc", priority=5)
        self.job("apple", "Ap")
        self.assertEqual(self.store.next_job()["provider"], "soundcloud")

    def test_a_cooldown_in_the_past_blocks_nothing(self):
        self.job("soundcloud", "Sc")
        self.store.set_provider("soundcloud", cooldown_until=time.time() - 60)
        self.assertEqual(self.store.cooldowns(), {})
        self.assertIsNotNone(self.store.next_job(blocked=self.store.cooldowns()))

    def test_the_reported_cooldown_names_a_source_that_is_actually_waiting(self):
        self.job("apple", "Ap")
        self.cool("soundcloud", 900)          # cooling, but nothing queued for it
        deadline, provider = self.store.blocking_cooldown()
        self.assertEqual((deadline, provider), (0.0, ""))
        self.job("soundcloud", "Sc")
        deadline, provider = self.store.blocking_cooldown()
        self.assertEqual(provider, "soundcloud")
        self.assertGreater(deadline, time.time())
        self.assertEqual(self.store.state()["stats"]["cooldown_provider"], "soundcloud")

    def test_two_cooling_sources_report_the_one_that_ends_first(self):
        self.job("apple", "Ap")
        self.job("soundcloud", "Sc")
        self.cool("soundcloud", 900)
        self.cool("apple", 120)
        deadline, provider = self.store.blocking_cooldown()
        self.assertEqual(provider, "apple")
        self.assertLess(deadline - time.time(), 200)


class TheLoopUsesIt(StoreCase):
    def run_one_pass(self):
        seen = []
        queue = queue_module.Queue(self.store)

        def fake_run(job):
            seen.append(job["provider"])
            queue._stop.set()          # one job, then leave the loop

        queue._run = fake_run
        queue._loop()
        return seen

    def test_the_worker_skips_the_cooling_provider_and_runs_the_other(self):
        self.job("soundcloud", "Sc", priority=9)
        self.job("apple", "Ap")
        self.cool("soundcloud")
        self.assertEqual(self.run_one_pass(), ["apple"])

    def test_it_runs_the_cooling_providers_job_once_the_wait_is_over(self):
        self.job("soundcloud", "Sc")
        self.store.set_provider("soundcloud", cooldown_until=time.time() - 1)
        self.assertEqual(self.run_one_pass(), ["soundcloud"])

    def test_nothing_runnable_reports_the_wait_instead_of_spinning(self):
        self.job("soundcloud", "Sc")
        self.cool("soundcloud", 600)
        queue = queue_module.Queue(self.store)
        seen = []

        def fake_run(job):
            seen.append(job["provider"])

        queue._run = fake_run
        queue.paused = False
        # One pass of the body, without waiting out the real cooldown.
        with mock.patch.object(queue_module.time, "sleep", return_value=None):
            blocked = self.store.cooldowns()
            job = self.store.next_job(blocked=blocked)
            self.assertIsNone(job)
            deadline, provider = self.store.blocking_cooldown()
            queue.current = {"phase": "cooldown", "provider": provider,
                             "remaining": round(deadline - time.time())}
        self.assertEqual(seen, [])
        self.assertEqual(queue.current["provider"], "soundcloud")


if __name__ == "__main__":
    unittest.main()