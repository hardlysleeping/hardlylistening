"""Where the process writes: a declared data directory moves every runtime artifact.

The acceptance test for `SERVER-PKG-002` is that the application tree can be read-only. Byte-level
proof of read-onlyness needs a mount, so this asserts the mechanism instead: with a data directory
declared, the database, the artwork cache, the browser-download cache and artist art all resolve
outside the app tree, and a run that opens the store and writes through those paths leaves the app
tree exactly as it was.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


def run(code: str, **env_overrides):
    env = {k: v for k, v in os.environ.items()
           if k not in ('HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB')}
    env.update(env_overrides)
    env['PYTHONPATH'] = str(APP_DIR)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run([sys.executable, '-c', code], cwd=APP_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


class DataDirectory(unittest.TestCase):
    def test_declared_data_dir_moves_the_database_and_the_caches(self):
        with tempfile.TemporaryDirectory() as data:
            result = run('import paths\n'
                         'for name in ("DATA_DIR", "CACHE_DIR", "ARTIST_ART_DIR", "IDENTITY_DIR",\n'
                         '             "STATE_DIR", "TMP_DIR", "METADATA_CACHE_DIR",\n'
                         '             "FEDERATION_CACHE_DIR", "DB_PATH"):\n'
                         '    print(getattr(paths, name))',
                         HARDLYLISTENING_DATA_DIR=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            (data_dir, cache, artist, identity, state, tmp, metadata, federation,
             database) = result.stdout.split('\n')[:9]
            self.assertEqual(data_dir, data)
            self.assertEqual(cache, str(Path(data) / 'cache'))
            self.assertEqual(artist, str(Path(data) / 'cache' / 'artist-art'))
            self.assertEqual(database, str(Path(data) / 'hardlylistening.db'))
            # The documented layout (§2) is real paths, not a diagram: identity is persistent
            # state that must survive an application replacement, state/ and tmp/ are the
            # remaining mutable directories, and the two cache subdirectories are where
            # federation and metadata would go.
            self.assertEqual(identity, str(Path(data) / 'identity'))
            self.assertEqual(state, str(Path(data) / 'state'))
            self.assertEqual(tmp, str(Path(data) / 'tmp'))
            self.assertEqual(metadata, str(Path(data) / 'cache' / 'metadata'))
            self.assertEqual(federation, str(Path(data) / 'cache' / 'federation'))

    def test_ensure_creates_the_whole_declared_tree_and_identity_is_private(self):
        with tempfile.TemporaryDirectory() as data:
            result = run('import paths; paths.ensure();'
                         ' print(paths.IDENTITY_DIR.stat().st_mode)',
                         HARDLYLISTENING_DATA_DIR=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(int(result.stdout.split()[0]) & 0o777, 0o700)
            for relative in ('identity', 'state', 'tmp', 'cache', 'cache/artist-art',
                             'cache/metadata', 'cache/federation'):
                self.assertTrue((Path(data) / relative).is_dir(), relative)
            # `ensure()` is called on every start: a second run changes nothing and must not
            # complain about directories that already exist.
            again = run('import paths; paths.ensure(); print("ok")', HARDLYLISTENING_DATA_DIR=data)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn('ok', again.stdout)

    def test_undeclared_keeps_todays_paths(self):
        result = run('import paths, db; print(paths.DECLARED); print(paths.CACHE_DIR);'
                     ' print(paths.ARTIST_ART_DIR); print(db.DB_PATH)')
        self.assertEqual(result.returncode, 0, result.stderr)
        declared, cache, artist, database = result.stdout.split('\n')[:4]
        self.assertEqual(declared, 'False')
        self.assertEqual(cache, str(APP_DIR / '.cache'))
        self.assertEqual(artist, str(APP_DIR / 'artist-art'))
        self.assertEqual(database, str(APP_DIR / 'hardlylistening.db'))

    def test_undeclared_ensure_creates_nothing_in_the_app_tree(self):
        """A checkout must not gain an `identity/`, `state/` or `tmp/` just by starting."""
        result = run('import os, paths\n'
                     'names = ("IDENTITY_DIR", "STATE_DIR", "TMP_DIR")\n'
                     'before = {n: os.path.exists(getattr(paths, n)) for n in names}\n'
                     'paths.ensure()\n'
                     'print(paths.DECLARED)\n'
                     'print([n for n in names\n'
                     '       if os.path.exists(getattr(paths, n)) != before[n]])')
        self.assertEqual(result.returncode, 0, result.stderr)
        declared, changed = result.stdout.split('\n')[:2]
        self.assertEqual(declared, 'False')
        self.assertEqual(changed, '[]')

    def test_a_run_writes_nothing_into_the_app_tree(self):
        before = self.snapshot()
        with tempfile.TemporaryDirectory() as data:
            result = run(
                'import paths, db\n'
                'from library import browser_downloads\n'
                'paths.ensure()\n'
                'store = db.Store()\n'
                'store.set_settings({"root": "' + data + '/music"})\n'
                'assert paths.CACHE_DIR.exists() and paths.ARTIST_ART_DIR.exists()\n'
                '(paths.CACHE_DIR / "artwork").mkdir(exist_ok=True)\n'
                '(paths.CACHE_DIR / "artwork" / "cover.jpg").write_bytes(b"x")\n'
                '(paths.ARTIST_ART_DIR / "artist.png").write_bytes(b"x")\n'
                'browser_downloads.archive(store, [])\n'
                'try:\n'
                '    from library import art_store\n'
                '    assert str(art_store.CACHE).startswith(paths.DATA_DIR.as_posix()), art_store.CACHE\n'
                'except ImportError:\n'
                '    pass\n'
                'store.close()\n'
                'print("ok")\n',
                HARDLYLISTENING_DATA_DIR=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('ok', result.stdout)
            self.assertTrue((Path(data) / 'hardlylistening.db').exists())
            self.assertTrue((Path(data) / 'cache' / 'browser-downloads').is_dir())
        self.assertEqual(before, self.snapshot())

    def snapshot(self):
        """Every file under the app tree, excluding the paste and the bytecode cache."""
        skip = {'__pycache__', '.cache', 'backups', 'node_modules', '.git', '.wrangler'}
        found = {}
        for path in APP_DIR.rglob('*'):
            if skip & set(path.relative_to(APP_DIR).parts):
                continue
            if path.is_file():
                found[str(path.relative_to(APP_DIR))] = path.stat().st_mtime_ns
        return found


if __name__ == '__main__':
    unittest.main()