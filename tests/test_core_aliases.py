"""ARCH-009: the old module names and the `core/` modules are one object.

The card moves auth, settings and the database into `core/` and keeps `db.py`, `config.py` and
`security.py` as the imports every module already uses. That is only safe if the old name *is*
the implementation: `import db` must hand back `core.db` itself, not a copy of its attributes.

This was learned the hard way. A star-import shim looked right, passed the targeted tests, and
then a full run emptied the live `download_jobs` table, because `mock.patch.object(db, "DB_PATH")`
patched the copy while `server` and `deletion` kept reading the original. These tests fail loudly
if that happens again.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

import config
import core.config
import core.db
import core.security
import db
import security


class Aliases(unittest.TestCase):
    def test_old_names_are_the_core_modules(self):
        self.assertIs(db, core.db)
        self.assertIs(config, core.config)
        self.assertIs(security, core.security)

    def test_a_patch_through_the_old_name_reaches_the_implementation(self):
        with mock.patch.object(db, "DB_PATH", "/tmp/x.db"):
            self.assertEqual(core.db.DB_PATH, "/tmp/x.db")
        with mock.patch.object(security, "SECURITY", "sentinel"):
            self.assertEqual(core.security.SECURITY, "sentinel")

    def test_string_patches_reach_the_same_object(self):
        with mock.patch("db.DB_PATH", "/tmp/y.db"):
            self.assertEqual(core.db.DB_PATH, "/tmp/y.db")

    def test_the_app_dir_is_the_checkout_not_core(self):
        self.assertEqual(db.APP_DIR, Path(db.__file__).resolve().parents[1])
        self.assertTrue((db.APP_DIR / "VERSION").is_file())

    def test_the_methods_are_the_implementation(self):
        self.assertIs(db.Store, core.db.Store)
        self.assertIs(db.connect, core.db.connect)
        self.assertIs(config.port, core.config.port)
        self.assertIs(security.SECURITY, core.security.SECURITY)

    def test_sys_modules_agrees(self):
        self.assertIs(sys.modules["db"], sys.modules["core.db"])
        self.assertIs(sys.modules["config"], sys.modules["core.config"])
        self.assertIs(sys.modules["security"], sys.modules["core.security"])


if __name__ == "__main__":
    unittest.main()