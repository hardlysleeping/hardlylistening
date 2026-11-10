"""Deterministic matching: spelling folds, bad re-uploads lose, channels still count."""
import os
import tempfile
import unittest
from unittest import mock

import db
import ranking
import security
from downloaders import ytdlp


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(db, "DB_PATH", os.path.join(self.dir.name, "m.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        security.Security().schema(self.store)
        ranking.schema(self.store)

    def track(self, artist="Alison Wonderland", title="New Day (Fredrick Remix)"):
        return {"id": 1, "title": title, "artist_name": artist}


class BadMarkers(unittest.TestCase):
    QUERY = "Alison Wonderland New Day"

    def test_slowed_and_nightcore_lose_to_the_plain_upload(self):
        plain = {"artist": "Alison Wonderland", "title": "New Day"}
        slowed = {"artist": "Alison Wonderland", "title": "New Day (slowed + reverb)"}
        self.assertGreater(ytdlp.hit_score(self.QUERY, plain),
                           ytdlp.hit_score(self.QUERY, slowed) * 2)

    def test_asking_for_it_stops_the_penalty(self):
        asked = "Alison Wonderland New Day slowed"
        slowed = {"artist": "Alison Wonderland", "title": "New Day (slowed)"}
        plain = {"artist": "Alison Wonderland", "title": "New Day"}
        self.assertGreaterEqual(ytdlp.hit_score(asked, slowed), ytdlp.hit_score(asked, plain) * 0.5)


class ArtistSpelling(Case):
    def test_history_pools_across_spellings(self):
        ranking.record(self.store, self.track(artist="FREDRICK."),
                       {"url": "https://soundcloud.com/fredrick/x", "artist": "fredrick",
                        "title": "New Day (Fredrick Remix)"})
        taste = ranking.taste(self.store, self.track(artist="Fredrick"))
        self.assertEqual(len(taste), 1)
        trusted = ranking.trusted_uploaders(self.store, self.track(artist="fredrick"))
        self.assertIn("fredrick", trusted)

    def test_channel_trusted_across_artists(self):
        for i in range(3):
            ranking.record(self.store, self.track(artist=f"Artist {i}"),
                           {"url": f"https://soundcloud.com/label/{i}", "artist": "labelchannel",
                            "title": "Song"})
        signals = ranking._signals(self.store, ranking.query_for(self.track(artist="Someone New")))
        self.assertIn("labelchannel", signals["uploaders"])


if __name__ == '__main__':
    unittest.main()
