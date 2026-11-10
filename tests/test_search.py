"""Song search: a hit becomes one pinned download.

The search tab must not re-search for the song it was given a choice about — the job
carries the chosen URL — and pressing Download twice must not queue the file twice.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
import search


class SearchCase(unittest.TestCase):
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
        self.queue = mock.Mock()

    def hit(self, **over):
        base = {
            "url": "https://soundcloud.com/x/atmosphere",
            "title": "Atmosphere", "artist": "FISHER", "duration": 220.5,
            "artwork": "https://i1.sndcdn.com/a.jpg", "engine": "scsearch",
            "source_id": "sc-1", "match": 0.91,
        }
        base.update(over)
        return base

    # -- provider mapping ---------------------------------------------------
    def test_provider_from_engine_and_url(self):
        self.assertEqual(search.provider_for("scsearch", "https://soundcloud.com/a/b"), "soundcloud")
        self.assertEqual(search.provider_for("ytsearch", "https://www.youtube.com/watch?v=1"), "youtube")
        self.assertEqual(search.provider_for("", "https://soundcloud.com/a/b"), "soundcloud")
        self.assertEqual(search.provider_for("", "https://www.youtube.com/watch?v=1"), "youtube")

    # -- reading ------------------------------------------------------------
    def test_find_normalises_hits_and_rejects_short_queries(self):
        with mock.patch.object(search.ytdlp, "search_all", return_value=[self.hit()]) as engine:
            out = search.find("  atmosphere  ")
        engine.assert_called_once_with("atmosphere")
        self.assertEqual(out[0]["provider"], "soundcloud")
        self.assertEqual(out[0]["title"], "Atmosphere")
        self.assertEqual(out[0]["duration"], 220.5)
        self.assertAlmostEqual(out[0]["match"], 0.91, places=3)
        with self.assertRaises(ValueError):
            search.find("a")

    def test_find_caps_the_list(self):
        many = [self.hit(source_id=f"s{i}", url=f"https://soundcloud.com/x/{i}") for i in range(40)]
        with mock.patch.object(search.ytdlp, "search_all", return_value=many):
            self.assertEqual(len(search.find("atmosphere", limit=5)), 5)

    # -- writing ------------------------------------------------------------
    def test_queue_hit_pins_the_chosen_url(self):
        result = search.queue_hit(self.store, self.queue, self.hit())
        self.assertTrue(result["queued"])
        self.assertEqual(result["provider"], "soundcloud")
        self.queue.wake.assert_called_once()
        with self.store.lock:
            track = self.store.conn.execute(
                "SELECT * FROM tracks WHERE provider_track_id='sc-1'").fetchone()
            job = self.store.conn.execute(
                "SELECT * FROM download_jobs WHERE track_id=?", (track["id"],)).fetchone()
            playlist = self.store.conn.execute(
                "SELECT * FROM playlists WHERE source_url=?", (search.SEARCH_URL,)).fetchone()
            member = self.store.conn.execute(
                "SELECT * FROM playlist_tracks WHERE track_id=?", (track["id"],)).fetchone()
        self.assertEqual(track["provider"], "soundcloud")
        self.assertEqual(track["album"], search.FROM_SEARCH)
        self.assertEqual(track["source_url"], self.hit()["url"])
        self.assertEqual(job["source_url"], self.hit()["url"])   # pinned, never re-searched
        self.assertEqual(job["status"], "queued")
        self.assertEqual(playlist["title"], search.FROM_SEARCH)
        self.assertEqual(member["playlist_id"], playlist["id"])

    def test_second_press_does_not_double_queue(self):
        search.queue_hit(self.store, self.queue, self.hit())
        again = search.queue_hit(self.store, self.queue, self.hit())
        self.assertFalse(again["queued"])
        with self.store.lock:
            jobs = self.store.conn.execute("SELECT COUNT(*) n FROM download_jobs").fetchone()["n"]
        self.assertEqual(jobs, 1)

    def test_track_already_on_disk_is_linked_not_queued(self):
        first = search.queue_hit(self.store, self.queue, self.hit())
        saved = self.root / "atmosphere.mp3"
        saved.write_bytes(b"x" * 32)
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?", (str(saved), first["track_id"]))
            self.store.conn.execute("DELETE FROM download_jobs")
            self.store.conn.commit()
        again = search.queue_hit(self.store, self.queue, self.hit())
        self.assertTrue(again["already"])
        self.assertFalse(again["queued"])
        with self.store.lock:
            jobs = self.store.conn.execute("SELECT COUNT(*) n FROM download_jobs").fetchone()["n"]
        self.assertEqual(jobs, 0)

    def test_a_result_without_a_link_is_refused(self):
        with self.assertRaises(ValueError):
            search.queue_hit(self.store, self.queue, self.hit(url="soundcloud.com/x"))


if __name__ == "__main__":
    unittest.main()


class SearchApi(unittest.TestCase):
    """The route itself: a member session, no network, a real queue row."""

    def setUp(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        import server
        from security import Security
        self.server = server
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR", "/tmp"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "library"
        self.root.mkdir()
        self.store = db.Store(Path(self.tmp.name) / "api.sqlite")
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({"root": str(self.root)})
        self.sec = Security()
        self.sec.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                ("member1", "", "member", "member1"))
            self.store.conn.execute("INSERT INTO browser_sessions VALUES(?,?,?)",
                                    (self.sec.digest("sid-1"), "member1", 4102444800))
            self.store.conn.commit()
        self.queue = mock.Mock()
        saved = (server.boot, server.SECURITY.config, server.SECURITY.csrf)
        self.addCleanup(lambda: setattr(server, "boot", saved[0]))
        self.addCleanup(lambda: setattr(server.SECURITY, "config", saved[1]))
        self.addCleanup(lambda: setattr(server.SECURITY, "csrf", saved[2]))
        self.old_env = {k: os.environ.get(k) for k in
                        ("HARDLYLISTENING_AUTH_REQUIRED", "HARDLYLISTENING_PUBLIC_ORIGIN")}
        os.environ["HARDLYLISTENING_AUTH_REQUIRED"] = "1"
        os.environ["HARDLYLISTENING_PUBLIC_ORIGIN"] = "https://music.example.com"
        server.SECURITY.config = lambda: ("", "", "https://music.example.com")
        server.SECURITY.csrf = lambda handler: None
        server.boot = lambda: (self.store, self.queue)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def get(self, path, session=True):
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        headers = {"Cookie": "__Host-hl_session=sid-1"} if session else {}
        try:
            with urlopen(Request(self.base + path, headers=headers), timeout=5) as response:
                return response.status, response.read().decode()
        except HTTPError as error:
            return error.code, error.read().decode()

    def post(self, payload):
        import json
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        data = json.dumps(payload).encode()
        request = Request(self.base + "/api/search", data=data, method="POST", headers={
            "Cookie": "__Host-hl_session=sid-1", "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:%d" % self.httpd.server_port,
            "Content-Length": str(len(data)),
        })
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read().decode()
        except HTTPError as error:
            return error.code, error.read().decode()

    def test_search_needs_a_session(self):
        status, _ = self.get("/api/search?q=atmosphere", session=False)
        self.assertEqual(status, 401)

    def test_search_returns_hits(self):
        hit = {"url": "https://soundcloud.com/x/y", "title": "Atmosphere", "artist": "FISHER",
               "duration": 200, "artwork": "", "engine": "scsearch", "provider": "soundcloud",
               "source_id": "sc-1", "match": 0.9}
        with mock.patch.object(search, "find", return_value=[hit]):
            status, body = self.get("/api/search?q=atmosphere")
        self.assertEqual(status, 200)
        self.assertEqual(search.__name__ in body or "Atmosphere" in body, True)
        import json
        self.assertEqual(json.loads(body)["results"][0]["title"], "Atmosphere")

    def test_short_query_is_a_400(self):
        status, body = self.get("/api/search?q=a")
        self.assertEqual(status, 400)

    def test_download_queues_the_pinned_hit(self):
        status, body = self.post({"url": "https://soundcloud.com/x/y", "title": "Atmosphere",
                                  "artist": "FISHER", "duration": 200, "engine": "scsearch",
                                  "source_id": "sc-1"})
        self.assertEqual(status, 200)
        import json
        result = json.loads(body)
        self.assertTrue(result["queued"])
        with self.store.lock:
            job = self.store.conn.execute("SELECT * FROM download_jobs").fetchone()
        self.assertEqual(job["source_url"], "https://soundcloud.com/x/y")
