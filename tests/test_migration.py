"""Migration: an existing checkout becomes a packaged install without losing anything.

`SERVER-PKG-011`. The migration is deliberately small — one environment variable and a directory
copy — and that is exactly why it needs a test rather than a paragraph: `paths.py` resolves the
database, the artwork cache, the browser-download cache and artist art from the environment at
import time, so a single misspelled variable or a half-moved cache tree shows up as an empty
library, not as an error.

So this builds a checkout-shaped instance with data in it (Repo, Library, accounts, settings),
moves it the way `docs/server-packaging.md` §11 says to move it, then opens it through the
packaged entry point and compares what came out. The comparison is row-level, not "the file
exists", and the second half of the test is that starting twice changes nothing: migrations are
idempotent, which is what lets an update be a file replacement.
"""
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]

CLEAN = ('HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB', 'HARDLYLISTENING_HOST',
         'HARDLYLISTENING_PORT', 'HARDLYLISTENING_MUSIC_DIR')


def env_for(**extra):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env.update(extra)
    env['PYTHONPATH'] = str(APP_DIR)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def open_store(**env_extra):
    """Open the Store exactly as the server would, in a subprocess, and report what it sees."""
    return subprocess.run(
        [sys.executable, '-c',
         'import json, paths, db\n'
         'store = db.Store()\n'
         'paths.ensure()\n'
         'out = {\n'
         '  "db": str(paths.DB_PATH),\n'
         '  "cache": str(paths.CACHE_DIR),\n'
         '  "artist_art": str(paths.ARTIST_ART_DIR),\n'
         '  "identity": str(paths.IDENTITY_DIR),\n'
         '  "settings": store.settings(),\n'
         '  "tracks": [r[0] for r in store.conn.execute("SELECT title FROM tracks ORDER BY id")],\n'
         '  "playlists": [r[0] for r in store.conn.execute("SELECT title FROM playlists ORDER BY id")],\n'
         '  "members": [r[0] for r in store.conn.execute("SELECT id FROM members ORDER BY id")],\n'
         '  "credentials": [r[0] for r in store.conn.execute("SELECT login_email FROM member_credentials ORDER BY login_email")],\n'
         '  "logs": store.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0],\n'
         '}\n'
         'store.close()\n'
         'print("JSON" + json.dumps(out))\n'],
        cwd=str(APP_DIR), env=env_for(**env_extra), capture_output=True, text=True, timeout=120)


def parse(result):
    for line in result.stdout.splitlines():
        if line.startswith('JSON'):
            import json
            return json.loads(line[4:])
    raise AssertionError(f'no result\nstdout={result.stdout}\nstderr={result.stderr}')


class Migration(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='hl-migrate-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.app = self.root / 'checkout'          # application tree, as a checkout has it
        self.app.mkdir()
        self.music = self.root / 'music'
        self.music.mkdir()
        self.data = self.root / 'data'             # the packaged data directory

    def seed_checkout(self):
        """Data written the pre-packaging way: database and caches inside the app tree."""
        code = (
            'import pathlib, db, paths\n'
            'from security import Security\n'
            f'store = db.Store(pathlib.Path({str(self.app / "hardlylistening.db")!r}))\n'
            'Security().schema(store)\n'
            f'store.set_settings({{"root": {str(self.music)!r}, "pacing": "9"}})\n'
            'store.conn.execute("INSERT INTO artists (provider, artist_id, name) '
            'VALUES (\'soundcloud\', \'1\', \'A\')")\n'
            'store.conn.execute("INSERT INTO releases (artist_id, provider, release_id, title, first_seen) '
            'VALUES (1, \'soundcloud\', \'r1\', \'Album\', 1)")\n'
            'for i, title in enumerate(["One", "Two"]):\n'
            '    store.conn.execute("INSERT INTO tracks (artist_id, release_id, provider, provider_track_id, '
            'title, artist_name, album, added) VALUES (1, 1, \'soundcloud\', ?, ?, \'A\', \'Album\', 1)", (str(i), title))\n'
            'store.conn.execute("INSERT INTO playlists (title, added) VALUES (\'Mix\', 1)")\n'
            'store.conn.execute("INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (1, 1, 0)")\n'
            'store.conn.execute("INSERT INTO members (id, email, role) VALUES (\'admin1\', \'a@b.c\', \'admin\')")\n'
            'store.conn.execute("INSERT INTO member_credentials (member_id, login_email) '
            'VALUES (\'admin1\', \'a@b.c\')")\n'
            'store.conn.execute("INSERT INTO logs (ts, level, msg) VALUES (1, \'info\', \'before\' )")\n'
            'store.conn.commit()\n'
            'store.close()\n'
        )
        r = subprocess.run([sys.executable, '-c', code], cwd=str(APP_DIR),
                           env=env_for(HARDLYLISTENING_DATA_DIR=str(self.app)), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

        # Caches the checkout wrote inside the app tree, exactly where paths.py puts them.
        for rel in ('.cache/artwork', 'artist-art', '.cache/browser-downloads'):
            (self.app / rel).mkdir(parents=True, exist_ok=True)
        (self.app / '.cache' / 'artwork' / 'abc.webp').write_bytes(b'art')

        # Node identity: storage only (FED-001 owns the format), but this is the directory the
        # backup boundary names and the one thing a rebuilt container must not lose.
        (self.app / 'identity').mkdir(exist_ok=True)
        (self.app / 'identity' / 'node-id').write_text('node-abc')

    def migrate(self):
        """§11 of the packaging doc: copy the database and the caches, then set the variable."""
        self.data.mkdir()
        for name in ('hardlylistening.db', 'hardlylistening.db-wal', 'hardlylistening.db-shm'):
            if (self.app / name).exists():
                shutil.copy2(self.app / name, self.data / name)
        shutil.copytree(self.app / '.cache' / 'artwork', self.data / 'cache' / 'artwork')
        shutil.copytree(self.app / '.cache' / 'browser-downloads',
                        self.data / 'cache' / 'browser-downloads')
        shutil.move(str(self.app / 'artist-art'), str(self.data / 'cache' / 'artist-art'))
        shutil.copytree(self.app / 'identity', self.data / 'identity')

    def digest(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_nothing_is_lost_and_nothing_stays_in_the_app_tree(self):
        self.seed_checkout()

        before = parse(open_store(HARDLYLISTENING_DATA_DIR=str(self.app)))   # a checkout's own layout

        # The pre-packaging read used the app tree as its data directory, so it needs it there.
        self.assertEqual(before['db'], str(self.app / 'hardlylistening.db'))
        self.assertEqual(before['tracks'], ['One', 'Two'])
        self.assertEqual(before['playlists'], ['Mix'])
        self.assertEqual(before['members'], ['admin1'])

        self.migrate()
        after = parse(open_store(HARDLYLISTENING_DATA_DIR=str(self.data)))

        self.assertEqual(after['db'], str(self.data / 'hardlylistening.db'))
        self.assertEqual(after['cache'], str(self.data / 'cache'))
        self.assertEqual(after['artist_art'], str(self.data / 'cache' / 'artist-art'))
        self.assertEqual(before['identity'], str(self.app / 'identity'))
        self.assertEqual(after['identity'], str(self.data / 'identity'))
        self.assertEqual((self.data / 'identity' / 'node-id').read_text(), 'node-abc')
        for key in ('settings', 'tracks', 'playlists', 'members', 'credentials', 'logs'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(before['settings']['root'], str(self.music))   # the Repo root moved with it
        self.assertTrue((self.data / 'cache' / 'artwork' / 'abc.webp').is_file())

        # The cache directories `ensure()` creates now live under the data directory, not the app.
        self.assertTrue((self.data / 'cache' / 'artist-art').is_dir())

    def test_a_second_start_changes_nothing(self):
        self.seed_checkout()
        self.migrate()
        first = parse(open_store(HARDLYLISTENING_DATA_DIR=str(self.data)))
        db_file = self.data / 'hardlylistening.db'
        stamp = self.digest(db_file)
        # Opening twice is what every restart does; a migration that is not idempotent turns
        # that into drift, and drift is discovered months later as a missing row.
        second = parse(open_store(HARDLYLISTENING_DATA_DIR=str(self.data)))
        self.assertEqual(second, first)
        self.assertEqual(self.digest(db_file), stamp)
        self.assertEqual((self.data / 'identity' / 'node-id').read_text(), 'node-abc')
        with sqlite3.connect(f'file:{db_file}?mode=ro', uri=True) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_the_old_location_still_works_untouched(self):
        """The live install's case: no DATA_DIR, so the paths must be byte-identical to before.

        Only `paths` is imported here. Opening a store would run migrations against whatever
        database sits in the application tree, and this suite must never touch a real one.
        """
        result = subprocess.run(
            [sys.executable, '-c', 'import paths; print(paths.DB_PATH); print(paths.CACHE_DIR);'
                                   ' print(paths.ARTIST_ART_DIR)'],
            cwd=str(APP_DIR), env=env_for(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), [str(APP_DIR / 'hardlylistening.db'),
                                                 str(APP_DIR / '.cache'),
                                                 str(APP_DIR / 'artist-art')])

    def test_the_declared_database_variable_beats_the_data_directory(self):
        self.seed_checkout()
        self.migrate()
        elsewhere = self.root / 'elsewhere.db'
        # Paths only: a brand-new database there has no accounts tables, and this test is about
        # which file wins, not about what is inside it.
        result = subprocess.run(
            [sys.executable, '-c', 'import paths; print(paths.DB_PATH)'],
            cwd=str(APP_DIR),
            env=env_for(HARDLYLISTENING_DATA_DIR=str(self.data),
                        HARDLYLISTENING_DB=str(elsewhere)),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(elsewhere))


if __name__ == '__main__':
    unittest.main()