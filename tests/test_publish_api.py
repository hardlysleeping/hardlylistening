"""The caller for `library/funkwhale.py`: one administrator's explicit POST, and the reason.

The adapter's own tests prove the honest definition of success. This file proves the *route*:
that it exists at all, that a member cannot reach it, that an unconfigured server refuses before
it touches a file, and that the adapter's reason comes back to the person who asked.

Everything here talks to the same loopback stub the adapter's tests use. No instance, token or
real library is touched.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from db import Store
from security import Security

try:
    from test_funkwhale import Stub, TOKEN
except ImportError:                                  # `python -m unittest tests.test_publish_api`
    from tests.test_funkwhale import Stub, TOKEN

AUDIO = b'fLaC' + bytes(range(64)) * 8


class PublishRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "test.sqlite")
        self.sec = Security()
        self.sec.schema(self.store)
        with self.store.lock:
            for uid, role in (("admin1", "admin"), ("member1", "member")):
                self.store.conn.execute(
                    "INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                    (uid, "", role, uid))
            self.store.conn.commit()
        self.sessions = {}
        for uid in ("admin1", "member1"):
            sid = "session-" + uid
            self.sessions[uid] = sid
            with self.store.lock:
                self.store.conn.execute(
                    "INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)",
                    (self.sec.digest(sid), uid, 4102444800))
                self.store.conn.commit()

        self.audio = self.root / "a.flac"
        self.audio.write_bytes(AUDIO)
        with self.store.lock:
            cursor = self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,"
                "duration,file,added) VALUES('local','1','A Song','An Artist','An Album',"
                "180,?,1700000000)", (str(self.audio),))
            self.track_id = cursor.lastrowid
            self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,file,added)"
                " VALUES('local','2','Never Downloaded','',1700000000)")
            self.store.conn.commit()

        self.old = (server.boot, server.SECURITY.config, server.SECURITY.csrf,
                    server.SECURITY.limit,
                    os.environ.get("HARDLYLISTENING_AUTH_REQUIRED"),
                    os.environ.get("HARDLYLISTENING_PUBLIC_ORIGIN"),
                    os.environ.get("HARDLYLISTENING_FUNKWHALE_INSTANCE"),
                    os.environ.get("HARDLYLISTENING_FUNKWHALE_TOKEN"))
        os.environ["HARDLYLISTENING_AUTH_REQUIRED"] = "1"
        os.environ["HARDLYLISTENING_PUBLIC_ORIGIN"] = "https://music.example"
        server.SECURITY.config = lambda: ("", "", "https://music.example")
        server.SECURITY.csrf = lambda handler: None
        # The route's own bucket (5 a minute) would make a suite of one-shot uploads flaky;
        # the limiter itself is exercised by its own tests.
        server.SECURITY.limit = lambda *args, **kwargs: None

        class FakeQueue:
            paused = False
            def wake(self): pass
            def pause(self, value): self.paused = bool(value)

        server.boot = lambda: (self.store, FakeQueue())
        # Each test owns a fresh job: a worker left over from the previous one (a stub that
        # never finishes) is abandoned rather than allowed to report onto this test's song.
        import library.publishing as publishing
        with publishing._lock:
            publishing._state.update({"token": publishing._state["token"] + 1,
                                      "running": False, "track_id": None,
                                      "result": None, "error": ""})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        boot, config, csrf, limit, auth, origin, instance, token = self.old
        server.boot = boot
        server.SECURITY.config = config
        server.SECURITY.csrf = csrf
        server.SECURITY.limit = limit
        for name, value in (("HARDLYLISTENING_AUTH_REQUIRED", auth),
                            ("HARDLYLISTENING_PUBLIC_ORIGIN", origin),
                            ("HARDLYLISTENING_FUNKWHALE_INSTANCE", instance),
                            ("HARDLYLISTENING_FUNKWHALE_TOKEN", token)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.store.conn.close()
        self.tmp.cleanup()

    def set_instance(self, url, token=TOKEN):
        os.environ["HARDLYLISTENING_FUNKWHALE_INSTANCE"] = url
        if token is None:
            os.environ.pop("HARDLYLISTENING_FUNKWHALE_TOKEN", None)
        else:
            os.environ["HARDLYLISTENING_FUNKWHALE_TOKEN"] = token

    def post(self, path, member="admin1"):
        data = b"{}"
        request = Request(self.base + path, data=data, method="POST", headers={
            "Cookie": "__Host-hl_session=" + self.sessions[member],
            "Content-Type": "application/json",
            "Origin": self.base,
            "Content-Length": str(len(data)),
        })
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as error:
            return error.code, json.loads(error.read().decode())

    def get(self, path, member="admin1"):
        request = Request(self.base + path, method="GET", headers={
            "Cookie": "__Host-hl_session=" + self.sessions[member],
        })
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as error:
            return error.code, json.loads(error.read().decode())

    def wait(self, path, member="admin1", timeout=15.0):
        """The publish is off the request thread; read the status read until it stops."""
        deadline = time.time() + timeout
        code, body = self.get(path, member)
        while time.time() < deadline and code == 200 and body.get("running"):
            time.sleep(0.05)
            code, body = self.get(path, member)
        return code, body

    # -- the action --------------------------------------------------------
    def test_a_finished_upload_with_a_track_is_a_success(self):
        with Stub(statuses=["pending", "finished"]) as stub:
            self.set_instance(stub.url)
            code, body = self.post("/api/track/%d/publish" % self.track_id)
            self.assertEqual(code, 200)
            self.assertTrue(body["started"])
            self.assertEqual(body["instance"], stub.url)
            self.assertEqual(body["title"], "A Song")
            code, body = self.wait("/api/track/%d/publish" % self.track_id)
        self.assertEqual(code, 200)
        self.assertFalse(body["running"])
        self.assertEqual(body["result"]["track_id"], 42)
        self.assertEqual(body["error"], "")
        # The token travels in the header, never the query string.
        self.assertEqual(stub.token, "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, stub.calls[0][1])
        # And the file really went, whole.
        self.assertIn(AUDIO, stub.bodies[0])

    def test_an_errored_import_keeps_its_reason(self):
        with Stub(statuses=["errored"]) as stub:
            self.set_instance(stub.url)
            code, body = self.post("/api/track/%d/publish" % self.track_id)
            self.assertEqual(code, 200)
            code, body = self.wait("/api/track/%d/publish" % self.track_id)
        self.assertEqual(code, 200)
        self.assertIn("errored", body["error"])
        self.assertIsNone(body["result"])

    def test_finished_without_a_track_is_refused(self):
        with Stub(statuses=["pending", "finished"], track=False) as stub:
            self.set_instance(stub.url)
            self.post("/api/track/%d/publish" % self.track_id)
            code, body = self.wait("/api/track/%d/publish" % self.track_id)
        self.assertEqual(code, 200)
        self.assertIn("no track", body["error"])

    def test_a_plain_http_instance_is_refused_by_the_adapter(self):
        self.set_instance("http://music.example")
        code, body = self.post("/api/track/%d/publish" % self.track_id)
        self.assertEqual(code, 200)
        code, body = self.wait("/api/track/%d/publish" % self.track_id)
        self.assertIn("plain http", body["error"])

    # -- who may ask -------------------------------------------------------
    def test_a_member_cannot_publish(self):
        with Stub() as stub:
            self.set_instance(stub.url)
            code, body = self.post("/api/track/%d/publish" % self.track_id, member="member1")
        self.assertEqual(code, 403)
        self.assertIn("Administrator", body["error"])
        self.assertEqual(stub.calls, [])          # nothing left the server

    def test_a_member_cannot_read_the_publish_status(self):
        with Stub() as stub:
            self.set_instance(stub.url)
            self.post("/api/track/%d/publish" % self.track_id)
            self.wait("/api/track/%d/publish" % self.track_id)
            code, body = self.get("/api/track/%d/publish" % self.track_id, member="member1")
        self.assertEqual(code, 403)
        self.assertIn("Administrator", body["error"])

    def test_a_second_publish_while_one_runs_is_refused(self):
        import library.publishing as publishing
        with Stub(statuses=["pending"]) as stub:
            self.set_instance(stub.url)
            code, first = self.post("/api/track/%d/publish" % self.track_id)
            self.assertTrue(first["started"])
            code, second = self.post("/api/track/%d/publish" % self.track_id)
        self.assertEqual(code, 200)
        self.assertFalse(second["started"])
        self.assertIn("still running", second["reason"])
        with publishing._lock:                    # abandon the worker; the next test gets a new job
            publishing._state.update({"token": publishing._state["token"] + 1,
                                      "running": False, "track_id": None})

    # -- refusals before any bytes move ------------------------------------
    def test_no_instance_configured_refuses(self):
        self.set_instance("")
        code, body = self.post("/api/track/%d/publish" % self.track_id)
        self.assertEqual(code, 400)
        self.assertIn("HARDLYLISTENING_FUNKWHALE_INSTANCE", body["error"])

    def test_no_token_configured_refuses(self):
        self.set_instance("https://music.example", token=None)
        code, body = self.post("/api/track/%d/publish" % self.track_id)
        self.assertEqual(code, 400)
        self.assertIn("HARDLYLISTENING_FUNKWHALE_TOKEN", body["error"])

    def test_a_track_with_no_file_is_refused(self):
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT id FROM tracks WHERE provider_track_id='2'").fetchone()
        with Stub() as stub:
            self.set_instance(stub.url)
            code, body = self.post("/api/track/%d/publish" % row["id"])
        self.assertEqual(code, 400)
        self.assertIn("no stored file", body["error"])
        self.assertEqual(stub.calls, [])

    def test_an_unknown_track_is_a_404(self):
        with Stub() as stub:
            self.set_instance(stub.url)
            code, body = self.post("/api/track/999999/publish")
        self.assertEqual(code, 404)
        self.assertEqual(stub.calls, [])

    # -- it is a caller, not a habit ---------------------------------------
    def test_nothing_publishes_automatically(self):
        """The adapter has exactly one caller, and it is this route's action."""
        root = Path(__file__).resolve().parent.parent
        users = []
        for path in root.rglob("*.py"):
            if any(part in ("tests", "backups", ".venv", "node_modules")
                   for part in path.parts):
                continue
            if path.name == "funkwhale.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "funkwhale.publish" in text or "import funkwhale" in text:
                users.append(path.relative_to(root).as_posix())
        # The adapter's one caller is the job wrapper; the wrapper's one caller is the route.
        self.assertEqual(sorted(users),
                         ["httpd/actions/library.py", "library/publishing.py"])


if __name__ == "__main__":
    unittest.main()