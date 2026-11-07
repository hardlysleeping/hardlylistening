"""Server/API regression for the one-file import and admin-only move rules."""
import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

import server
from db import Store
from security import Security


class ImportAndMove(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "test.sqlite"
        self.store = Store(self.db_path)
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
                self.store.conn.execute("INSERT INTO browser_sessions VALUES(?,?,?)",
                    (self.sec.digest(sid), uid, 4102444800))
                self.store.conn.commit()
        self.old_boot = server.boot
        self.old_security_config = server.SECURITY.config
        self.old_security_csrf = server.SECURITY.csrf
        self.old_auth = os.environ.get("HARDLYLISTENING_AUTH_REQUIRED")
        self.old_origin = os.environ.get("HARDLYLISTENING_PUBLIC_ORIGIN")
        os.environ["HARDLYLISTENING_AUTH_REQUIRED"] = "1"
        os.environ["HARDLYLISTENING_PUBLIC_ORIGIN"] = "https://music.example.com"
        server.SECURITY.config = lambda: ("", "", "https://music.example.com")
        server.SECURITY.csrf = lambda handler: None
        class FakeQueue:
            paused = False
            def wake(self): pass
            def pause(self, value): self.paused = bool(value)
        self.queue = FakeQueue()
        server.boot = lambda: (self.store, self.queue)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        server.boot = self.old_boot
        server.SECURITY.config = self.old_security_config
        server.SECURITY.csrf = self.old_security_csrf
        if self.old_auth is None: os.environ.pop("HARDLYLISTENING_AUTH_REQUIRED", None)
        else: os.environ["HARDLYLISTENING_AUTH_REQUIRED"] = self.old_auth
        if self.old_origin is None: os.environ.pop("HARDLYLISTENING_PUBLIC_ORIGIN", None)
        else: os.environ["HARDLYLISTENING_PUBLIC_ORIGIN"] = self.old_origin
        self.store.conn.close()
        self.tmp.cleanup()

    def post(self, path, member, payload):
        data = json.dumps(payload).encode()
        req = Request(self.base + path, data=data, method="POST", headers={
            "Cookie": "__Host-hl_session=" + self.sessions[member],
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:%d" % self.httpd.server_port,
            "Content-Length": str(len(data)),
        })
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.read().decode()
        except HTTPError as error:
            return error.code, error.read().decode()

    def test_zip_rejected_before_any_import(self):
        code, body = self.post("/api/import/upload", "admin1", {
            "name": "songs.zip", "data": "data:application/zip;base64,AAAA"})
        self.assertEqual(code, 400)
        self.assertIn("not folders, zips or multiple files", body)

    def test_non_audio_rejected(self):
        code, body = self.post("/api/import/upload", "admin1", {
            "name": "cover.jpg", "data": "data:image/jpeg;base64,AAAA"})
        self.assertEqual(code, 400)
        self.assertIn("one audio file", body)

    def test_oversized_upload_rejected_at_twenty_mib(self):
        raw = b"x" * (20 * 1024 * 1024 + 1)
        payload = {"name": "long.mp3", "data": "data:audio/mpeg;base64," + base64.b64encode(raw).decode()}
        # Send through a compact streaming-like JSON body: urllib's request body is
        # still a single value, matching the real endpoint's parser limit.
        code, body = self.post("/api/import/upload", "admin1", payload)
        self.assertEqual(code, 400)
        self.assertIn("limited to 20 MB", body)

    def test_valid_audio_upload_at_twenty_mib_imports_without_expanding_repo(self):
        import wave
        source = self.root / "max-size.wav"
        sample_bytes = 20 * 1024 * 1024 - 44
        with wave.open(str(source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(1)
            audio.setframerate(8000)
            audio.writeframes(b"\x80" * sample_bytes)
        self.assertEqual(source.stat().st_size, 20 * 1024 * 1024)
        self.store.set_settings({"root": str(self.root / "library")})
        payload = {"name": source.name,
                   "data": "data:audio/wav;base64," + base64.b64encode(source.read_bytes()).decode()}
        code, body = self.post("/api/import/upload", "admin1", payload)
        self.assertEqual(code, 200, body)
        result = json.loads(body)
        self.assertEqual(result["imported"], 1, result)
        self.assertEqual(result["failed"], 0, result)
        row = self.store.conn.execute("SELECT file,size FROM tracks WHERE provider='local'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["size"], 20 * 1024 * 1024)
        self.assertTrue(Path(row["file"]).is_file())
        self.assertNotEqual(Path(row["file"]).resolve(), source.resolve())

    def test_multiple_paths_rejected(self):
        code, body = self.post("/api/import", "admin1", {"paths": "/tmp/one.mp3" + chr(10) + "/tmp/two.mp3"})
        self.assertEqual(code, 400)
        self.assertIn("multiple files", body)

    def test_folder_path_rejected(self):
        folder = self.root / "folder"
        folder.mkdir()
        code, body = self.post("/api/import", "admin1", {"paths": str(folder)})
        self.assertEqual(code, 400)
        self.assertIn("not folders", body)

    def test_member_cannot_move_library_even_with_valid_csrf(self):
        called = False
        def forbidden_move(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("move_library must never run for a member")
        with patch("library.import_files.move_library", forbidden_move):
            code, body = self.post("/api/library/move", "member1", {"to": str(self.root / "new")})
        self.assertEqual(code, 403)
        self.assertIn("Administrator required", body)
        self.assertFalse(called)

    def test_admin_move_passes_gate_and_calls_move(self):
        with patch("library.import_files.move_library", return_value={"ok": True}) as move:
            code, body = self.post("/api/library/move", "admin1", {"to": str(self.root / "new")})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])
        move.assert_called_once_with(self.store, str(self.root / "new"))


if __name__ == "__main__":
    unittest.main()
