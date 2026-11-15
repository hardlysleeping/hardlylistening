"""Undoing a hand-picked source.

Two things went wrong that this pins down: a wrong option was the only option
offered, and picking it was permanent. Undo has to put the track back into
review *and* stop the pick from teaching the ranking — an accidental click must
not become a rule.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
import ranking


class UndoPickCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "library"
        self.root.mkdir()
        patcher = mock.patch.object(db, "DB_PATH", os.path.join(self.dir.name, "d.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({"root": str(self.root)})

    def make_job(self, *, pinned=1, status="queued", url="https://example.test/one"):
        track_id = self.store.track("demo", "t-" + url.rsplit("/", 1)[-1],
                                    "I Need To Feel", artist_name="X", duration=180)
        job_id = self.store.enqueue(track_id, "demo")
        self.store.update_job(job_id, status=status, pinned=pinned, resolved_url=url)
        return track_id, job_id

    # -- the record ---------------------------------------------------------

    def test_pick_is_recorded_and_then_forgotten(self):
        track = {"title": "I Need To Feel", "artist_name": "X"}
        query = ranking.query_for(track)
        candidate = {"url": "https://example.test/one", "title": "Some other song",
                     "artist": "Y", "duration": 180}
        self.assertTrue(ranking.record(self.store, track, candidate))
        self.assertEqual(len(ranking.history(self.store, query)), 1)

        self.assertTrue(ranking.forget(self.store, query, "https://example.test/one"))
        self.assertEqual(ranking.history(self.store, query), [])
        # Forgetting twice is not an error, it just reports nothing was there.
        self.assertFalse(ranking.forget(self.store, query, "https://example.test/one"))

    def test_forget_leaves_other_picks_alone(self):
        track = {"title": "Song", "artist_name": "A"}
        q = ranking.query_for(track)
        for url in ("https://example.test/a", "https://example.test/b"):
            ranking.record(self.store, track, {"url": url, "title": "Song", "artist": "A",
                                               "duration": 100})
        ranking.forget(self.store, q, "https://example.test/a")
        left = [row["url"] for row in ranking.history(self.store, q)]
        self.assertEqual(left, ["https://example.test/b"])

    # -- what the row says --------------------------------------------------

    def test_state_reports_which_sources_were_picked_by_hand(self):
        track_id, job_id = self.make_job(pinned=1, status="review")
        playlist_id = self.store.playlist("P", "demo", "https://example.test/p")
        self.store.add_to_playlist(playlist_id, track_id, 1)
        job = [t for p in self.store.state()["playlists"] for t in p["tracks"]
               if t["id"] == track_id][0]
        self.assertEqual(job["pinned"], 1)

        self.store.update_job(job_id, pinned=0)
        job = [t for p in self.store.state()["playlists"] for t in p["tracks"]
               if t["id"] == track_id][0]
        self.assertEqual(job["pinned"], 0)

    # -- the undo itself ----------------------------------------------------

    def test_undo_returns_the_track_to_review_and_forgets_the_pick(self):
        """The shape the route must produce, checked without an HTTP request."""
        track_id, job_id = self.make_job()
        track = self.store.track_by_id(track_id)
        ranking.record(self.store, track, {"url": "https://example.test/one",
                                          "title": "Wrong", "artist": "Y", "duration": 180})
        query = ranking.query_for(track)

        ranking.forget(self.store, query, "https://example.test/one")
        self.store.update_job(job_id, resolved_url="", pinned=0, status="review",
                              attempts=0, next_retry_at=0, error_message="")

        job = self.store.job_for_track(track_id)
        self.assertEqual(job["status"], "review")
        self.assertEqual(job["resolved_url"], "")
        self.assertEqual(job["pinned"], 0)
        self.assertEqual(ranking.history(self.store, query), [])
        # The track itself survives; only the choice is gone.
        self.assertIsNotNone(self.store.track_by_id(track_id))

    def test_undo_is_refused_while_the_job_is_running(self):
        """The route must not yank a job out from under the downloader."""
        import server
        seen = {}
        for index, status in enumerate(("downloading", "resolving")):
            track_id, job_id = self.make_job(status=status,
                                             url=f"https://example.test/run{index}")
            handler = object.__new__(server.Handler)
            handler.body = lambda: {"delete_file": True}
            handler.json = lambda payload, code=200: seen.update(payload=payload, code=code)
            with mock.patch.object(server, "boot", return_value=(self.store, mock.Mock())):
                server.Handler.do_track_unpick(handler, track_id)
            self.assertEqual(seen["code"], 409)
            self.assertIn("stop", seen["payload"]["error"].lower())
            self.assertEqual(self.store.job_for_track(track_id)["status"], status)

    def test_undo_drops_the_file_only_when_asked(self):
        import server
        track_id, _job_id = self.make_job(pinned=1)
        audio = self.root / "x.mp3"
        audio.write_bytes(b"ID3" + b"\0" * 64)
        self.store.conn.execute("UPDATE tracks SET file=?, size=? WHERE id=?",
                                (str(audio), audio.stat().st_size, track_id))
        self.store.conn.commit()

        seen = {}
        handler = object.__new__(server.Handler)
        handler.body = lambda: {"delete_file": True}
        handler.json = lambda payload, code=200: seen.update(payload=payload, code=code)
        with mock.patch.object(server, "boot", return_value=(self.store, mock.Mock())):
            server.Handler.do_track_unpick(handler, track_id)

        self.assertTrue(seen["payload"]["ok"])
        self.assertFalse(audio.exists(), "the wrong download must actually go")
        self.assertEqual(seen["payload"]["status"], "review")
        self.assertEqual(self.store.track_by_id(track_id)["file"], "")

    def test_undo_keeps_the_file_when_not_asked_to_delete_it(self):
        import server
        track_id, _job_id = self.make_job(pinned=1)
        audio = self.root / "y.mp3"
        audio.write_bytes(b"ID3" + b"\0" * 32)
        self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?", (str(audio), track_id))
        self.store.conn.commit()

        seen = {}
        handler = object.__new__(server.Handler)
        handler.body = lambda: {}
        handler.json = lambda payload, code=200: seen.update(payload=payload, code=code)
        with mock.patch.object(server, "boot", return_value=(self.store, mock.Mock())):
            server.Handler.do_track_unpick(handler, track_id)

        self.assertTrue(audio.exists())
        self.assertEqual(seen["payload"]["removed_file"], "")

if __name__ == "__main__":
    unittest.main()
