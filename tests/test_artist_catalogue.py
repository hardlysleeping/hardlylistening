"""The artist page's catalogue: every release and every song, stored per follow.

No network: the provider is a fixture, and the store is a temp DB.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TMPDIR", "/tmp")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.base import Provider, Release, TrackRef  # noqa: E402


class FakeArtist(Provider):
    name = "fake"
    direct_audio = False
    patterns = (r"^https?://fake\.test/",)

    calls = {"releases": 0, "tracks": 0}
    fail = False

    def supports_follow(self):
        return True

    def new_releases(self, artist_url, watermark, limit=60):
        type(self).calls["releases"] += 1
        if type(self).fail:
            raise RuntimeError("provider down")
        return [
            Release(provider=self.name, release_id=f"album:{n}", title=f"Album {n}",
                    artist="Fixture", release_type="album", release_date=f"2026-0{n}-01",
                    track_count=2, source_url=f"https://fake.test/album/{n}")
            for n in range(1, 4)
        ]

    def catalogue_tracks(self, artist_url, limit=2000, releases=None):
        type(self).calls["tracks"] += 1
        if type(self).fail:
            raise RuntimeError("provider down")
        # 40 songs an album: 120 in total, so the first page is 60.
        out = []
        for n in range(1, 4):
            for i in range(1, 41):
                out.append(TrackRef(
                    provider=self.name, provider_track_id=f"{n}-{i}",
                    title=f"Song {n}.{i}", artist="Fixture", album=f"Album {n}",
                    track_number=i, duration=200.0,
                    source_url=f"https://fake.test/song/{n}{i}",
                    release_id=f"album:{n}"))
        return out


class FakeApple(FakeArtist):
    """iTunes caps an artist-level song lookup at 200; the rest is per album."""
    name = "apple"
    album_calls = []

    def new_releases(self, artist_url, watermark, limit=60):
        # 40 songs an album, so a 3-song artist-level answer is visibly partial.
        out = super().new_releases(artist_url, watermark, limit)
        for release in out:
            release.track_count = 40
        return out

    def catalogue_tracks(self, artist_url, limit=2000, releases=None):
        # The artist-level lookup returns a few songs off the first album only.
        return [t for t in FakeArtist.catalogue_tracks(self, artist_url, limit, releases)
                if t.album == "Album 1"][:3]

    def tracks(self, url):
        type(self).album_calls.append(url)
        n = url.rstrip("/").split("/")[-1]
        return [TrackRef(provider=self.name, provider_track_id=f"{n}-{i}",
                         title=f"Song {n}.{i}", artist="Fixture", album=f"Album {n}",
                         track_number=i, duration=200.0,
                         source_url=f"https://fake.test/song/{n}{i}")
                for i in range(1, 41)]


class AppleAlbumFill(unittest.TestCase):
    def setUp(self):
        import db
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.store = db.Store(os.path.join(self.dir, "apple.db"))
        self.watch_id = self.store.watch_add("apple", "artist-2", "Fixture",
                                            "https://fake.test/artist/2", 0)
        FakeApple.album_calls = []
        self.patch = patch("providers.base.for_url", return_value=FakeApple())
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.store.conn.close()

    def read(self):
        from discovery import tracklist
        return tracklist.catalogue(self.store, self.watch_id)

    def test_uncovered_albums_are_read_once_and_then_reused(self):
        first = self.read()
        self.assertEqual(first["track_total"], 120)          # 3 albums × 40
        self.assertEqual(len(FakeApple.album_calls), 3,       # album 1 was partial
                         str(FakeApple.album_calls))
        from discovery import tracklist
        tracklist.CATALOGUE_TTL = -1                          # force a refresh
        try:
            second = self.read()
        finally:
            tracklist.CATALOGUE_TTL = 6 * 3600
        self.assertEqual(second["track_total"], 120)
        self.assertEqual(len(FakeApple.album_calls), 3,
                         "a fresh album tracklist must not be re-read")

    def test_an_expired_album_tracklist_is_read_again(self):
        self.read()
        from discovery import tracklist
        tracklist.CATALOGUE_TTL = -1
        tracklist.TRACK_TTL = -1
        try:
            self.read()
        finally:
            tracklist.CATALOGUE_TTL = 6 * 3600
            tracklist.TRACK_TTL = 7 * 24 * 3600
        self.assertEqual(len(FakeApple.album_calls), 6)       # all three again


class ArtistCatalogue(unittest.TestCase):
    def setUp(self):
        import db
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.store = db.Store(os.path.join(self.dir, "catalogue.db"))
        self.watch_id = self.store.watch_add("fake", "artist-1", "Fixture",
                                            "https://fake.test/artist/1", 0)
        FakeArtist.calls = {"releases": 0, "tracks": 0}
        FakeArtist.fail = False
        self.patch = patch("providers.base.for_url", return_value=FakeArtist())
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.store.conn.close()

    def read(self, offset=0, limit=60):
        from discovery import tracklist
        return tracklist.catalogue(self.store, self.watch_id, offset=offset, limit=limit)

    def test_stores_everything_and_pages_the_songs(self):
        first = self.read()
        self.assertEqual([r["title"] for r in first["releases"]],
                         ["Album 3", "Album 2", "Album 1"])   # newest first
        self.assertEqual(first["track_total"], 120)
        self.assertEqual(len(first["tracks"]), 60)
        self.assertEqual(first["tracks"][0]["album"], "Album 1")  # A→Z by album
        self.assertEqual(FakeArtist.calls, {"releases": 1, "tracks": 1})

    def test_second_page_comes_from_the_store(self):
        self.read()
        second = self.read(offset=60)
        self.assertEqual(len(second["tracks"]), 60)
        self.assertEqual(second["track_total"], 120)
        self.assertEqual(second["tracks"][0]["album"], "Album 2")
        self.assertEqual(FakeArtist.calls, {"releases": 1, "tracks": 1},
                         "paging must not re-read the provider")

    def test_a_page_past_the_end_is_empty_not_an_error(self):
        self.read()
        last = self.read(offset=120)
        self.assertEqual(last["tracks"], [])
        self.assertEqual(last["track_total"], 120)

    def test_refresh_replaces_rows_that_disappeared_upstream(self):
        self.read()
        from discovery import tracklist
        tracklist.CATALOGUE_TTL = -1          # force the next read to refresh
        try:
            # Drop album 1 upstream; the store must lose it too, not keep both.
            with patch.object(FakeArtist, "new_releases",
                              return_value=FakeArtist().new_releases("u", 0)[1:]):
                with patch.object(FakeArtist, "catalogue_tracks",
                                  return_value=[t for t in FakeArtist().catalogue_tracks("u")
                                                if t.album != "Album 1"]):
                    again = self.read()
        finally:
            tracklist.CATALOGUE_TTL = 6 * 3600
        self.assertEqual([r["title"] for r in again["releases"]], ["Album 3", "Album 2"])
        self.assertEqual(again["track_total"], 80)
        self.assertNotIn("Album 1", {t["album"] for t in again["tracks"]})

    def test_a_provider_failure_keeps_the_rows_already_stored(self):
        self.read()
        from discovery import tracklist
        tracklist.CATALOGUE_TTL = -1
        FakeArtist.fail = True
        try:
            stale = self.read()
        finally:
            tracklist.CATALOGUE_TTL = 6 * 3600
        self.assertEqual(stale["track_total"], 120)
        self.assertEqual(len(stale["releases"]), 3)

    def test_a_failure_with_nothing_stored_is_an_error(self):
        from discovery import tracklist
        FakeArtist.fail = True
        with self.assertRaises(RuntimeError):
            tracklist.catalogue(self.store, self.watch_id)

    def test_removing_the_follow_removes_its_catalogue(self):
        self.read()
        self.store.watch_remove(self.watch_id)
        self.assertEqual(self.store.artist_releases(self.watch_id), [])
        self.assertEqual(self.store.artist_tracks(self.watch_id)[1], 0)


if __name__ == "__main__":
    unittest.main()