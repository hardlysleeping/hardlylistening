"""The suite may not open the install's own database.

This pins the guard that stops a test run from writing real data. A test isolates by patching
`db.DB_PATH` or by passing a temporary path; when a patch misses (a module copy, a shim, a new
call site), the run used to fall through to the live library and empty the download queue. The
guard compares against the path captured at import, so a copy of `db` still refuses.

`HARDLYLISTENING_ALLOW_LIVE_DB_TESTS=1` is the deliberate-read hatch, tested through the helper
rather than through `connect()` — a test must never actually open the live file.
"""
import os
import tempfile
import unittest
from unittest import mock

import db


class Guard(unittest.TestCase):
    def test_connect_without_isolation_is_refused(self):
        with mock.patch.object(db, "DB_PATH", db.INSTALL_DB_PATH):
            with self.assertRaises(RuntimeError) as caught:
                db.connect()
        self.assertIn("live database", str(caught.exception))

    def test_store_without_isolation_is_refused(self):
        with mock.patch.object(db, "DB_PATH", db.INSTALL_DB_PATH):
            with self.assertRaises(RuntimeError):
                db.Store()

    def test_a_temporary_path_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(os.path.join(tmp, "t.db"))
            conn.close()

    def test_patching_db_path_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(db, "DB_PATH", os.path.join(tmp, "t.db")):
                store = db.Store()
                store.conn.close()

    def test_the_escape_hatch_is_the_only_way_past(self):
        with self.assertRaises(RuntimeError):
            db._refuse_live_db_in_tests(db.INSTALL_DB_PATH)
        with mock.patch.dict(os.environ, {"HARDLYLISTENING_ALLOW_LIVE_DB_TESTS": "1"}):
            db._refuse_live_db_in_tests(db.INSTALL_DB_PATH)


if __name__ == "__main__":
    unittest.main()