"""The dated play log: the history the stats read.

`listening_plays` keeps lifetime totals and `activity` is the feed's trimmed 4000-row
window; neither can answer "what did I play this month". `listen_events` is the append-only
record, written by the web player and by Subsonic scrobbles.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

import activity
import social.service as social_service
import social
from db import Store
from security import Security


class ListenEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.store = Store(Path(self.tmp.name) / "a.sqlite")
        Security().schema(self.store)
        social.schema(self.store)
        with self.store.lock:
            for uid, name in (("m-ann", "Ann"), ("m-bob", "Bob")):
                self.store.conn.execute(
                    "INSERT INTO members(id,email,role,display_name) VALUES(?,?,'member',?)",
                    (uid, "", name))
            self.store.conn.commit()

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def track(self, title):
        with self.store.lock:
            cur = self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,size,added)"
                " VALUES('soundcloud',?,?,'Artist',0,0)", (title, title))
            self.store.conn.commit()
            return cur.lastrowid

    def rows(self):
        with self.store.lock:
            return [dict(r) for r in self.store.conn.execute(
                "SELECT ts,member_id,track_id,source FROM listen_events ORDER BY ts, id")]

    def test_one_play_is_one_dated_row(self):
        track = self.track("one")
        self.assertTrue(activity.log_play(self.store, "m-ann", track, now=1000.0))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["member_id"], rows[0]["track_id"], rows[0]["source"]),
                         ("m-ann", track, "web"))
        self.assertEqual(rows[0]["ts"], 1000.0)

    def test_a_double_report_inside_the_guard_is_one_play(self):
        track = self.track("one")
        activity.log_play(self.store, "m-ann", track, now=1000.0)
        self.assertFalse(activity.log_play(self.store, "m-ann", track, now=1000.0 + 5))
        self.assertEqual(len(self.rows()), 1)
        # A repeat after the guard is a real second play.
        self.assertTrue(activity.log_play(self.store, "m-ann", track, now=1000.0 + 31))
        self.assertEqual(len(self.rows()), 2)

    def test_plays_are_per_member_and_carry_their_source(self):
        track = self.track("one")
        activity.log_play(self.store, "m-ann", track, source="subsonic", now=1000.0)
        activity.log_play(self.store, "m-bob", track, source="subsonic", now=1000.0)
        activity.log_play(self.store, "m-ann", track, source="web", now=2000.0)
        rows = self.rows()
        self.assertEqual([r["member_id"] for r in rows], ["m-ann", "m-bob", "m-ann"])
        self.assertEqual([r["source"] for r in rows], ["subsonic", "subsonic", "web"])

    def test_the_web_player_writes_the_feed_row_and_the_dated_row(self):
        track = self.track("one")
        social_service.listen(self.store, {"id": "m-ann"}, track)
        self.assertEqual(len(self.rows()), 1)
        with self.store.lock:
            feed = self.store.conn.execute(
                "SELECT COUNT(*) FROM activity WHERE kind='listen'").fetchone()[0]
        self.assertEqual(feed, 1)

    def test_nothing_is_dropped_when_the_feed_window_is_trimmed(self):
        """The feed keeps 4000 rows; the history must outlive that."""
        track = self.track("one")
        base = time.time() - 5000
        with self.store.lock:
            self.store.conn.executemany(
                "INSERT INTO listen_events(ts,member_id,track_id,source) VALUES(?,?,?,'web')",
                [(base + i, "m-ann", track) for i in range(6000)])
            self.store.conn.commit()
        # A play now trims only the feed, never the history.
        activity.log_play(self.store, "m-ann", track, now=base + 6000 + 60)
        self.assertEqual(len(self.rows()), 6001)

    def test_a_missing_member_or_track_is_not_an_error(self):
        track = self.track("one")
        self.assertFalse(activity.log_play(self.store, "", track))
        self.assertFalse(activity.log_play(self.store, "m-ann", 0))
        self.assertEqual(self.rows(), [])