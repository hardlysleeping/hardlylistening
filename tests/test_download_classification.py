"""A downloaded album or single is a release, never a playlist.

`Queue.ingest` groups the work of one link in `playlists`, because that row is also what
the download queue draws its cards from. The container's `kind` is what separates a set
somebody pasted from a release somebody downloaded; the library and the playlist editor
have to read it, or an album and a one-song single turn up as playlists of their own.
"""
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from db import Store
from engine.queue import Queue
from providers.base import Provider, Release, TrackRef
from security import Security
import server
import user_playlists


class FixtureProvider(Provider):
    """Answers a fixed set of links; nothing is fetched."""
    name = "fixture"

    def __init__(self, releases):
        self.releases = releases

    def resolve(self, url):
        release = self.releases[url]
        release.tracks = [
            TrackRef(provider=self.name, provider_track_id=f"{release.release_id}:{n}",
                     title=f"Song {n}", artist="Fixture", album=release.title,
                     track_number=n, source_url=f"{release.source_url}/{n}")
            for n in range(1, release.track_count + 1)]
        return release


class DownloadClassification(unittest.TestCase):
    LINKS = (("album", "album", 3), ("single", "single", 1),
             ("track", "track", 1), ("set", "playlist", 2))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        base = Path(self.tmp.name)
        (base / "music").mkdir()
        self.db_path = base / "fixture.db"
        self.open_store()
        releases = {}
        for slug, kind, count in self.LINKS:
            url = f"https://fixture.example/{slug}"
            releases[url] = Release(provider="fixture", release_id=slug,
                                    title=f"Fixture {slug}", artist="Fixture",
                                    release_type=kind, track_count=count, source_url=url)
        self.queue = Queue(self.store)
        self.env = patch("providers.base.for_url", lambda url: FixtureProvider(releases))
        self.env.start()
        self.addCleanup(self.env.stop)

    def tearDown(self):
        self.close_store()
        self.tmp.cleanup()

    # -- helpers --
    def open_store(self):
        self.store = Store(self.db_path)
        Security().schema(self.store)
        self.store.set_settings({"root": str(Path(self.tmp.name) / "music")})

    def close_store(self):
        self.store._projection_timer.cancel()
        self.store._projection_timer.join()
        self.store.conn.close()

    def reopen(self):
        self.close_store()
        self.open_store()

    def ingest(self, slug):
        return self.queue.ingest(f"https://fixture.example/{slug}")

    def kind(self, slug):
        return self.store.conn.execute(
            "SELECT kind FROM playlists WHERE source_url=?",
            (f"https://fixture.example/{slug}",)).fetchone()["kind"]

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body)

    def call(self, method, path, body=None):
        with patch.dict(os.environ, {"HARDLYLISTENING_AUTH_REQUIRED": "0"}), \
                patch.object(server, "boot", return_value=(self.store, self.queue)):
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            try:
                request = Request(
                    f"http://127.0.0.1:{http.server_port}{path}", method=method,
                    data=None if body is None else json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"})
                with urlopen(request) as response:
                    return json.loads(response.read())
            finally:
                http.shutdown()
                http.server_close()

    # -- the bug --
    def test_a_release_keeps_its_own_kind(self):
        for slug in ("album", "single", "track"):
            self.ingest(slug)
            self.assertEqual(self.kind(slug), slug)
        self.ingest("set")
        self.assertEqual(self.kind("set"), "url")

    def test_library_lists_only_a_set_as_a_playlist(self):
        for slug, _, _ in self.LINKS:
            self.ingest(slug)
        library = self.get("/api/library")
        self.assertEqual([p["title"] for p in library["playlists"]], ["Fixture set"])
        # The download queue still groups every batch: this is a classification fix,
        # not a way to hide work that is still in flight.
        state = self.get("/api/state")
        self.assertEqual(sorted(p["title"] for p in state["playlists"]),
                         sorted(f"Fixture {slug}" for slug, _, _ in self.LINKS))

    def test_playlist_editor_lists_only_a_set(self):
        for slug, _, _ in self.LINKS:
            self.ingest(slug)
        self.assertEqual([p["title"] for p in user_playlists.listing(self.store, "u1")],
                         ["Fixture set"])

    def test_a_pasted_album_or_single_is_not_a_playlist(self):
        # Pasted links take the same route as a Feed or artist-page button.
        added = self.post("/api/add", {"url": "\n".join(
            f"https://fixture.example/{slug}" for slug in ("album", "single", "set"))})
        self.assertEqual(len(added["added"]), 3)
        self.assertEqual([self.kind(slug) for slug in ("album", "single", "set")],
                         ["album", "single", "url"])
        self.assertEqual([p["title"] for p in self.get("/api/library")["playlists"]],
                         ["Fixture set"])

    def test_a_row_an_older_ingest_misfiled_is_reclassified_on_boot(self):
        # Rows already in a library were all filed as 'url', so the source link and the
        # release it points at are what say whether such a row was ever a playlist.
        album = self.store.playlist("Fixture album", "fixture",
                                    "https://fixture.example/album")
        self.store.release("fixture", "album", "Fixture album", release_type="album",
                           source_url="https://fixture.example/album")
        self.store.playlist("Old set", "fixture", "https://fixture.example/old")
        self.store.release("fixture", "old", "Old set", release_type="playlist",
                           source_url="https://fixture.example/old")
        self.reopen()
        kinds = dict(self.store.conn.execute("SELECT id,kind FROM playlists"))
        self.assertEqual(kinds[album], "album")
        self.assertEqual(list(kinds.values()).count("url"), 1)
        self.assertEqual([p["title"] for p in user_playlists.listing(self.store, "u1")],
                         ["Old set"])


if __name__ == "__main__":
    unittest.main()