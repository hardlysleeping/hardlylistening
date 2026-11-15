"""Playback telemetry: one bounded line per interesting event, so a car that stops is evidence.

Nothing here is a feature. Without it the next report is "it still stops" and neither the
head unit's behaviour nor the page's own state can be checked after the fact. The route is
deliberately dull: member-scoped, 4 KiB cap, fixed field list, one append per request, and a
file that rotates rather than grows.
"""
import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import paths
import server
from db import Store
from security import Security


class PlaybackEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.state = Path(self.tmp.name) / "state"
        self.store = Store(Path(self.tmp.name) / "db.sqlite")
        self.sec = Security()
        self.env = patch.dict(os.environ, {
            "HARDLYLISTENING_AUTH_REQUIRED": "1",
            "HARDLYLISTENING_SUPABASE_URL": "https://example.supabase.co",
            "HARDLYLISTENING_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_fixture",
            "HARDLYLISTENING_PUBLIC_ORIGIN": "https://music.example",
            "HARDLYLISTENING_BOOTSTRAP_ADMIN_EMAIL": "owner@example.com"})
        self.env.start()
        self.patches = [patch.object(server, "SECURITY", self.sec),
                        patch.object(server, "boot", return_value=(self.store, None)),
                        patch.object(paths, "STATE_DIR", self.state)]
        for p in self.patches:
            p.start()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.thread.join()
        self.http.server_close()
        for p in self.patches:
            p.stop()
        self.env.stop()
        with self.store.lock:
            if self.store._projection_timer:
                self.store._projection_timer.cancel()
            self.store.conn.close()
        self.tmp.cleanup()

    def request(self, path, method="GET", headers=None, body=None):
        c = HTTPConnection("127.0.0.1", self.http.server_port, timeout=4)
        c.request(method, path, body=body, headers=dict(headers or {}))
        r = c.getresponse()
        result = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return result

    def member(self):
        self.sec.schema(self.store)
        self.store.conn.execute(
            "INSERT OR REPLACE INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
            ("u1", "friend@example.com", "member", "Friend"))
        self.store.conn.commit()
        import secrets, time
        sid = secrets.token_urlsafe(32)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)",
                (self.sec.digest(sid), "u1", time.time() + 3600))
            self.store.conn.commit()
        return {"Cookie": "__Host-hl_session=" + sid,
                "Origin": "https://music.example"}

    def log(self):
        target = self.state / "playback-events.log"
        if not target.is_file():
            return []
        return [json.loads(line) for line in target.read_text().splitlines() if line.strip()]

    def test_telemetry_requires_a_member(self):
        code, _, _ = self.request("/api/playback/events", "POST", body="{}")
        self.assertEqual(code, 401)

    def test_one_event_is_one_line_and_only_known_fields_survive(self):
        headers = self.member()
        headers["Content-Type"] = "application/json"
        payload = json.dumps({"event": "interrupted", "trackId": 7, "wanted": "playing",
                              "interrupted": True, "readyState": 2, "networkState": 1,
                              "errorCode": 0, "srcKind": "blob",
                              "src": "/api/track/7/audio", "cookie": "secret",
                              "note": "x" * 5000})
        code, _, _ = self.request("/api/playback/events", "POST", headers, payload)
        self.assertEqual(code, 413, "an oversized event is refused, not truncated")
        payload = json.dumps({"event": "interrupted", "trackId": 7, "wanted": "playing",
                              "interrupted": True, "readyState": 2, "networkState": 1,
                              "errorCode": 0, "srcKind": "blob", "at": 123,
                              "src": "/api/track/7/audio", "cookie": "secret"})
        code, _, _ = self.request("/api/playback/events", "POST", headers, payload)
        self.assertEqual(code, 200)
        lines = self.log()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["event"], "interrupted")
        self.assertEqual(lines[0]["trackId"], 7)
        self.assertEqual(lines[0]["srcKind"], "blob")
        self.assertNotIn("src", lines[0])
        self.assertNotIn("cookie", lines[0])

    def test_malformed_events_are_refused(self):
        headers = self.member()
        for body in ("not json", "[]", '""'):
            code, _, _ = self.request("/api/playback/events", "POST", headers, body)
            self.assertEqual(code, 400, body)
        self.assertEqual(self.log(), [])

    def test_reading_the_log_is_not_a_route(self):
        headers = self.member()
        code, _, _ = self.request("/api/playback/events", "GET", headers)
        self.assertEqual(code, 405)

    def test_the_log_rotates_instead_of_growing(self):
        headers = self.member()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "playback-events.log").write_text("x" * (server.PLAYBACK_LOG_MAX + 1))
        code, _, _ = self.request("/api/playback/events", "POST", headers,
                                  json.dumps({"event": "playing"}))
        self.assertEqual(code, 200)
        self.assertTrue((self.state / "playback-events.log.1").is_file())
        self.assertEqual(len(self.log()), 1)

    def test_a_long_string_cannot_smuggle_a_page_of_text_into_one_field(self):
        headers = self.member()
        code, _, _ = self.request("/api/playback/events", "POST", headers,
                                  json.dumps({"event": "e" * 4000}))
        self.assertEqual(code, 200)
        self.assertLessEqual(len(self.log()[0]["event"]), 64)


if __name__ == "__main__":
    unittest.main()