"""Swipe feed: Repository songs you do not own, and what each decision does."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
import preferences
import swipe
from security import Security


class SwipeCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "library"
        self.root.mkdir()
        patcher = mock.patch.object(db, "DB_PATH", os.path.join(self.dir.name, "d.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({"root": str(self.root)})
        Security().schema(self.store)
        preferences.schema(self.store)
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role,display_name) VALUES('m1','','member','m1')")
            self.store.conn.commit()
        self.ids = []
        for i in range(5):
            media = self.root / f"song{i}.mp3"
            media.write_bytes(b"x" * 64)
            track_id = self.store.track("soundcloud", f"sw-{i}", f"Song {i}", artist_name="Artist")
            with self.store.lock:
                self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?", (str(media), track_id))
                self.store.conn.commit()
            self.ids.append(track_id)
        # one track with no file: never offered
        self.missing = self.store.track("soundcloud", "sw-missing", "Missing")

    def test_feed_holds_only_playable_unowned_unswiped(self):
        tracks = swipe.candidates(self.store, "m1", 10)
        self.assertEqual(sorted(t["id"] for t in tracks), sorted(self.ids))
        swipe.decide(self.store, "m1", self.ids[0], "drop")
        swipe.decide(self.store, "m1", self.ids[1], "keep")
        left = [t["id"] for t in swipe.candidates(self.store, "m1", 10)]
        self.assertNotIn(self.ids[0], left)
        self.assertNotIn(self.ids[1], left)          # kept = owned
        self.assertEqual(sorted(left), sorted(self.ids[2:]))

    def test_keep_adds_to_library_and_like_also_likes(self):
        swipe.decide(self.store, "m1", self.ids[0], "keep")
        with self.store.lock:
            saved = {r[0] for r in self.store.conn.execute(
                "SELECT track_id FROM listening_tracks WHERE member_id='m1'")}
            favs = {r[0] for r in self.store.conn.execute(
                "SELECT track_id FROM listening_favorites WHERE member_id='m1'")}
        self.assertIn(self.ids[0], saved)
        self.assertNotIn(self.ids[0], favs)
        swipe.decide(self.store, "m1", self.ids[1], "like")
        with self.store.lock:
            saved = {r[0] for r in self.store.conn.execute(
                "SELECT track_id FROM listening_tracks WHERE member_id='m1'")}
            favs = {r[0] for r in self.store.conn.execute(
                "SELECT track_id FROM listening_favorites WHERE member_id='m1'")}
        self.assertIn(self.ids[1], saved)
        self.assertIn(self.ids[1], favs)

    def test_drop_adds_nothing(self):
        swipe.decide(self.store, "m1", self.ids[0], "drop")
        with self.store.lock:
            saved = self.store.conn.execute(
                "SELECT COUNT(*) n FROM listening_tracks WHERE member_id='m1'").fetchone()["n"]
            favs = self.store.conn.execute(
                "SELECT COUNT(*) n FROM listening_favorites WHERE member_id='m1'").fetchone()["n"]
        self.assertEqual((saved, favs), (0, 0))

    def test_stats_and_bad_input(self):
        swipe.decide(self.store, "m1", self.ids[0], "keep")
        swipe.decide(self.store, "m1", self.ids[1], "like")
        swipe.decide(self.store, "m1", self.ids[2], "drop")
        result = swipe.stats(self.store, "m1")
        self.assertEqual((result["keep"], result["like"], result["drop"]), (1, 1, 1))
        self.assertEqual(result["left"], len(self.ids) - 3)
        with self.assertRaises(ValueError):
            swipe.decide(self.store, "m1", self.ids[3], "maybe")
        with self.assertRaises(ValueError):
            swipe.decide(self.store, "m1", self.missing, "keep")
        # Swiping the same card twice just replaces the decision.
        swipe.decide(self.store, "m1", self.ids[2], "keep")
        self.assertEqual(swipe.stats(self.store, "m1")["drop"], 0)

    def test_swipes_are_per_member(self):
        swipe.decide(self.store, "m1", self.ids[0], "drop")
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role,display_name) VALUES('m2','','member','m2')")
            self.store.conn.commit()
        self.assertIn(self.ids[0], [t["id"] for t in swipe.candidates(self.store, "m2", 10)])


if __name__ == "__main__":
    unittest.main()
