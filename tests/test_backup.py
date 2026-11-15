"""Backup: a consistent copy of the database, taken while the server is running.

`SERVER-PKG-010`. The interesting failure is not "the file is missing", it is "the copy looks
right and is not": with WAL on, `hardlylistening.db` alone is not a database, and a byte copy
taken mid-write can be torn. So these tests keep a real writer open while the backup runs and
then read the copy back, which is the only way to prove the online-backup API was used rather
than `shutil.copyfile`.
"""
import contextlib
import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
SERVER = APP_DIR / 'bin' / 'hardlylistening-server'

CLEAN = ('HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB', 'HARDLYLISTENING_HOST',
         'HARDLYLISTENING_PORT', 'HARDLYLISTENING_MUSIC_DIR')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Backup(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix='hl-backup-'))
        self.addCleanup(shutil.rmtree, self.data, True)
        self.db = self.data / 'hardlylistening.db'
        self.out = self.data / 'out'

    def env(self):
        env = {k: v for k, v in os.environ.items() if k not in CLEAN}
        env.update(HARDLYLISTENING_DATA_DIR=str(self.data), HARDLYLISTENING_DB=str(self.db),
                   PYTHONPATH=str(APP_DIR), PYTHONDONTWRITEBYTECODE='1')
        return env

    def make_store(self):
        """A real store, written to, and left open — the state a backup has to survive."""
        code = (
            'import db\n'
            f'store = db.Store(__import__("pathlib").Path({str(self.db)!r}))\n'
            'store.set_settings({"root": "/music/library", "pacing": "7"})\n'
            'store.conn.execute("INSERT INTO logs (ts, level, msg) VALUES (1, \'info\', \'hello\')")\n'
            'store.conn.commit()\n'
            'print("ready", flush=True)\n'
            'import sys; sys.stdin.readline()\n'          # stay open until the parent says so
            'store.close()\n'
        )
        proc = subprocess.Popen([os.sys.executable, '-c', code], cwd=str(APP_DIR), env=self.env(),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        self.addCleanup(self._stop, proc)
        line = proc.stdout.readline().strip()
        self.assertEqual(line, 'ready', proc.stderr.read() if line != 'ready' else '')
        return proc

    @staticmethod
    def _stop(proc):
        if proc.poll() is None:
            try:
                proc.stdin.write('\n')
                proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                pass
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            with contextlib.suppress(OSError):
                pipe.close()

    def run_cli(self, *args):
        return subprocess.run([str(SERVER), 'backup', *args], cwd=str(APP_DIR), env=self.env(),
                              capture_output=True, text=True, timeout=120)

    def read_copy(self, path: Path):
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            settings = dict(conn.execute('SELECT key,value FROM settings'))
            logs = [r[0] for r in conn.execute('SELECT msg FROM logs')]
        finally:
            conn.close()
        return settings, logs

    def test_a_live_database_is_copied_consistently(self):
        self.make_store()                     # writer open, WAL not checkpointed
        result = self.run_cli(str(self.out))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        copy = self.out / 'hardlylistening.db'
        self.assertTrue(copy.is_file())
        settings, logs = self.read_copy(copy)
        self.assertEqual(settings['root'], '/music/library')
        self.assertEqual(settings['pacing'], '7')
        self.assertEqual(logs, ['hello'])
        self.assertIn('integrity ok', result.stdout)
        # the copy is a standalone database, not a file that needs the original's -wal
        self.assertFalse((self.out / 'hardlylistening.db-wal').exists())

    def test_the_output_names_identity_as_not_copied(self):
        self.make_store()
        identity = self.data / 'identity'
        identity.mkdir()
        (identity / 'node-id').write_text('node-1')
        result = self.run_cli(str(self.out))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(str(identity), result.stdout)
        self.assertIn('not copied', result.stdout)
        # The directory is the operator's to rsync: a database backup must not read as a
        # complete backup because it happens to have succeeded.
        self.assertFalse((self.out / 'identity').exists())

    def test_it_refuses_when_there_is_no_database(self):
        result = self.run_cli(str(self.out))
        self.assertEqual(result.returncode, 1)
        self.assertIn('no database', result.stderr)
        self.assertFalse(self.out.exists())   # nothing was created to hold a backup of nothing

    def test_it_refuses_to_replace_an_existing_copy_without_force(self):
        self.make_store()
        self.assertEqual(self.run_cli(str(self.out)).returncode, 0)
        copy = self.out / 'hardlylistening.db'
        before = digest(copy)
        result = self.run_cli(str(self.out))
        self.assertEqual(result.returncode, 1)
        self.assertIn('already exists', result.stderr)
        self.assertEqual(digest(copy), before)
        self.assertEqual(self.run_cli(str(self.out), '--force').returncode, 0)
        self.assertTrue(copy.is_file())

    def test_a_missing_destination_is_created(self):
        self.make_store()
        deep = self.out / 'nested' / 'deeper'
        result = self.run_cli(str(deep))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((deep / 'hardlylistening.db').is_file())

    def test_the_live_database_is_not_modified(self):
        self.make_store()
        before = digest(self.db)
        self.assertEqual(self.run_cli(str(self.out)).returncode, 0)
        self.assertEqual(digest(self.db), before)

    def test_a_failed_copy_leaves_no_partial_file(self):
        self.make_store()
        # A destination that cannot be written: a regular file where the directory must go.
        blocker = self.out
        blocker.write_text('not a directory\n')
        result = self.run_cli(str(blocker))
        self.assertEqual(result.returncode, 1)
        self.assertIn('backup:', result.stderr)
        self.assertEqual(list(blocker.parent.glob('*.part')), [])


if __name__ == '__main__':
    unittest.main()