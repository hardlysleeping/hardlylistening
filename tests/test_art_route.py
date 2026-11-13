"""The cover the car fetches: a capability URL, and the digest it announces.

iOS fetches media-session artwork itself, outside the page's own session, so a member-only
route answers that request with a redirect or a login page and the head unit draws nothing.
The fix is a first-party URL whose secret is the image's own digest — the same bargain the
share links make. These tests pin the route's shape, its cache headers and its anonymity.
"""
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server
from db import Store
from library import art_store
from security import Security

# A digest is whatever the image's bytes hash to; the route only promises 32 lowercase hex.
KEY = "0123456789abcdef0123456789abcdef"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"


class ArtRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.store = Store(Path(self.tmp.name) / "db.sqlite")
        self.sec = Security()
        self.env = patch.dict(os.environ, {
            "HARDLYLISTENING_AUTH_REQUIRED": "1",
            "HARDLYLISTENING_SUPABASE_URL": "https://example.supabase.co",
            "HARDLYLISTENING_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_fixture",
            "HARDLYLISTENING_PUBLIC_ORIGIN": "https://music.example",
            "HARDLYLISTENING_BOOTSTRAP_ADMIN_EMAIL": "owner@example.com"})
        self.env.start()
        self.cache = Path(self.tmp.name) / "artwork"
        self.patches = [patch.object(server, "SECURITY", self.sec),
                        patch.object(server, "boot", return_value=(self.store, None)),
                        patch.object(art_store, "CACHE", self.cache)]
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

    def token(self, uid="u1"):
        import secrets, time
        self.sec.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT OR REPLACE INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                (uid, "friend@example.com", "member", "Friend"))
            sid = secrets.token_urlsafe(32)
            self.store.conn.execute(
                "INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)",
                (self.sec.digest(sid), uid, time.time() + 3600))
            self.store.conn.commit()
        return "__Host-hl_session=" + sid

    def seed_cover(self, key=KEY):
        """The 640 px copy `display()` hands back, without spending an ffmpeg call on it."""
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / f"{key}@640.jpg").write_bytes(JPEG)
        return key

    # ---- the capability URL -------------------------------------------------------------
    def test_a_cover_is_public_and_cached_forever(self):
        key = self.seed_cover()
        code, headers, raw = self.request(f"/api/art/{key}")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(headers["Content-Length"], str(len(JPEG)))
        self.assertIn("immutable", headers["Cache-Control"])
        self.assertEqual(raw, JPEG)

    def test_the_cover_needs_no_session_and_an_unknown_digest_is_404(self):
        code, _, raw = self.request("/api/art/" + "f" * 32)
        self.assertEqual(code, 404)
        self.assertNotIn(b"login", raw.lower())

    def test_the_path_is_exactly_a_32_hex_digest(self):
        for path in ("/api/art/", "/api/art/xyz", "/api/art/" + "a" * 31,
                     "/api/art/" + "a" * 33, "/api/art/" + "A" * 32,
                     "/api/art/" + "a" * 32 + "/extra"):
            code, _, _ = self.request(path)
            self.assertNotEqual(code, 200, path)

    def test_a_cover_is_read_only(self):
        key = self.seed_cover()
        code, _, _ = self.request(f"/api/art/{key}", method="POST", body="{}")
        self.assertNotEqual(code, 200)

    def test_the_route_is_not_the_web_ui(self):
        """A headless server with the browser app switched off still feeds a car its art."""
        key = self.seed_cover()
        with patch.dict(server.FEATURES, {"web_ui": False}):
            code, _, raw = self.request(f"/api/art/{key}")
        self.assertEqual(code, 200)
        self.assertEqual(raw, JPEG)

    # ---- the digest, announced ----------------------------------------------------------
    def test_the_track_cover_route_names_the_digest_it_registered(self):
        track = self.store.conn.execute(
            "INSERT INTO tracks(file,provider,provider_track_id,title,added,art_hash,"
            "art_checked) VALUES(?,?,?,?,?,?,1)",
            ("/music/a.mp3", "fixture", "t1", "One", 0.0, KEY)).lastrowid
        self.store.conn.commit()
        image = self.cache / "track.jpg"
        self.cache.mkdir(parents=True, exist_ok=True)
        image.write_bytes(JPEG)
        with patch("library.artwork.for_track", return_value=image):
            code, headers, raw = self.request(
                f"/api/track/{track}/artwork",
                headers={"Cookie": self.token()})
        self.assertEqual(code, 200)
        self.assertEqual(raw, JPEG)
        self.assertEqual(headers.get("X-Art-Hash"), KEY)


if __name__ == "__main__":
    unittest.main()