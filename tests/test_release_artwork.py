"""One cover per release, from the library read to the moment a download lands.

A song grabbed from the Feed used to keep whatever picture its own file carried,
so two tracks off one album could show two different covers — and a release
whose tracks had no artwork URL showed a frame pulled from each file instead of
the album's cover. These rules are pinned here:

* a real release's artwork is the cover every track in it reports,
* a playlist is a set, not an album, so its cover never stamps its songs,
* a track with no artwork still falls back to the cover embedded in its file,
* finishing a download fills an empty artwork column from the release, and
  never overwrites a cover that is already there.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db

COVER = "https://is1-ssl.mzstatic.com/image/thumb/Music211/album/600x600bb.jpg"
OTHER = "https://i1.sndcdn.com/artworks-other-large.jpg"


class Case(unittest.TestCase):
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

    def release(self, key, kind, artwork=COVER):
        return self.store.release("apple", key, f"Release {key}",
                                  release_type=kind, artwork=artwork)

    def track(self, key, *, release_id=None, artwork="", on_disk=True, album="Album"):
        tid = self.store.track("apple", key, f"Song {key}", artist_name="Artist",
                               album=album, artwork=artwork, release_id=release_id)
        if on_disk:
            path = self.root / "Artist" / album / f"{key}.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 2048)
            with self.store.lock:
                self.store.conn.execute(
                    "UPDATE tracks SET file=?, size=2048 WHERE id=?",
                    (str(path), tid))
                self.store.conn.commit()
        return tid

    def row(self, track_id):
        return next(r for r in self.store.library(include_pending=True)
                    if r["id"] == track_id)

    def test_every_track_reports_the_release_cover(self):
        release = self.release("album-7", "album")
        first = self.track("a", release_id=release)
        second = self.track("b", release_id=release)
        self.assertEqual(self.row(first)["artwork"], COVER)
        self.assertEqual(self.row(second)["artwork"], COVER)

    def test_playlist_cover_never_stamps_a_song(self):
        release = self.release("set-1", "playlist")
        own = self.track("c", release_id=release, artwork=OTHER)
        bare = self.track("d", release_id=release)
        self.assertEqual(self.row(own)["artwork"], OTHER)
        # No artwork anywhere: the cover embedded in that file is the fallback.
        self.assertEqual(self.row(bare)["artwork"],
                         f"/api/track/{bare}/artwork")

    def test_undownloaded_playlist_track_has_no_cover(self):
        release = self.release("set-2", "playlist")
        bare = self.track("e", release_id=release, on_disk=False)
        self.assertEqual(self.row(bare)["artwork"], "")

    def test_download_queue_shows_the_release_cover(self):
        release = self.release("album-8", "album")
        first = self.track("f", release_id=release)
        second = self.track("g", release_id=release, on_disk=False)
        playlist_id = self.store.playlist("Release album-8", "apple",
                                          "https://music.apple.com/album/8",
                                          artwork=COVER)
        self.store.add_to_playlist(playlist_id, first, 1)
        self.store.add_to_playlist(playlist_id, second, 2)
        playlist = next(p for p in self.store.state()["playlists"]
                        if p["id"] == playlist_id)
        covers = {t["id"]: t["artwork"] for t in playlist["tracks"]}
        self.assertEqual(covers[first], COVER)
        self.assertEqual(covers[second], COVER)

    def test_finishing_a_download_fills_an_empty_cover(self):
        release = self.release("album-9", "album")
        tid = self.track("h", release_id=release)
        self.assertEqual(self.store.track_by_id(tid)["artwork"], "")
        self.assertEqual(self.store.fill_release_artwork(tid), COVER)
        self.assertEqual(self.store.track_by_id(tid)["artwork"], COVER)

    def test_finishing_a_download_keeps_an_existing_cover(self):
        release = self.release("album-10", "album")
        tid = self.track("i", release_id=release, artwork=OTHER)
        self.assertEqual(self.store.fill_release_artwork(tid), OTHER)

    def test_finishing_a_download_ignores_a_playlist_cover(self):
        release = self.release("set-3", "playlist")
        tid = self.track("j", release_id=release)
        self.assertEqual(self.store.fill_release_artwork(tid), "")


if __name__ == "__main__":
    unittest.main()