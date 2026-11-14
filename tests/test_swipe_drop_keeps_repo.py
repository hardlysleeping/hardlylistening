"""A drop is a note to the feed, not a deletion: the Repository keeps the song.

The Swipe page is a triage of the shared Repository. "Drop" is the member saying "never
show me this again", so the only row it may write is the member's own swipe. The song
itself stays in `tracks`, keeps its file, and stays on offer to every other member —
otherwise one member's taste would quietly empty the shared catalogue.
"""
import unittest

import swipe
import test_swipe


class DropKeepsTheRepository(unittest.TestCase):
    # Reuse the fixture without re-collecting its tests in this module.
    setUp = test_swipe.SwipeCase.setUp

    def test_a_drop_removes_it_from_the_feed_only(self):
        before = [tuple(row) for row in
                  self.store.conn.execute("SELECT * FROM tracks ORDER BY id")]
        left_before = swipe.stats(self.store, "m1")["left"]
        swipe.decide(self.store, "m1", self.ids[0], "drop")

        # The catalogue row is untouched, file and all.
        after = [tuple(row) for row in
                 self.store.conn.execute("SELECT * FROM tracks ORDER BY id")]
        self.assertEqual(before, after)

        # The feed loses exactly that card and remembers the answer.
        self.assertEqual(swipe.stats(self.store, "m1")["left"], left_before - 1)
        self.assertNotIn(self.ids[0], [t["id"] for t in swipe.candidates(self.store, "m1", 10)])
        self.assertEqual(swipe.stats(self.store, "m1")["drop"], 1)

    def test_a_drop_takes_nothing_from_the_member_either(self):
        # Dropping must not read as "un-save": a row already in the library survives it, so
        # a member who drops a song they own does not lose the copy.
        swipe.schema(self.store)
        with self.store.lock:
            self.store.conn.execute("INSERT INTO listening_tracks VALUES(?,?)",
                                    ("m1", self.ids[0]))
            self.store.conn.commit()
        swipe.decide(self.store, "m1", self.ids[0], "drop")
        with self.store.lock:
            saved = {r[0] for r in self.store.conn.execute(
                "SELECT track_id FROM listening_tracks WHERE member_id='m1'")}
        self.assertEqual(saved, {self.ids[0]})

    def test_another_member_is_still_offered_it(self):
        swipe.decide(self.store, "m1", self.ids[0], "drop")
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO members(id,email,role,display_name) VALUES('m2','','member','m2')")
            self.store.conn.commit()
        self.assertIn(self.ids[0], [t["id"] for t in swipe.candidates(self.store, "m2", 10)])


if __name__ == "__main__":
    unittest.main()