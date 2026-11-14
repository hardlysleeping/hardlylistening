"""The installer's file list: what lands in `/opt`, and what must never land there.

`SERVER-PKG-006`. The script itself needs root and a systemd host, so what is testable here is the
part that goes wrong quietly: the copy. An exclusion that is one level too wide (`--exclude=./*`
instead of `--exclude=./.cache`) ships a live database into `/opt`, where the next upgrade
overwrites it; an exclusion that is too narrow leaves `web/` behind and the install serves nothing.

So this reproduces the tar invocation from `deploy/install.sh` — same list, mirrored deliberately,
because the alternative is running a root-only script in a test — and then asks the copied tree to
pass its own `check` with the packaged paths.

If you change the excludes in `deploy/install.sh`, change them here too: this test failing is the
signal that the two drifted.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]

# Mirrored from deploy/install.sh — keep the two lists identical.
EXCLUDES = [
    './.git', './backups', './tests', './docs', './deploy', './node_modules', './__pycache__',
    './*.pyc', './.cache', './artist-art', './hardlylistening.db', './hardlylistening.db-*',
]


class InstallTree(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hl-install-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prefix = self.tmp / 'opt' / 'hardlylistening'
        self.data = self.tmp / 'data'
        self.music = self.tmp / 'music'
        self.prefix.mkdir(parents=True)
        self.music.mkdir()

    def copy_tree(self):
        args = ['tar', '-C', str(APP_DIR), '-cf', '-']
        for pattern in EXCLUDES:
            args.append(f'--exclude={pattern}')
        args.append('.')
        with open(self.tmp / 'tree.tar', 'wb') as tar:
            subprocess.run(args, stdout=tar, check=True)
        subprocess.run(['tar', '-C', str(self.prefix), '-xf', str(self.tmp / 'tree.tar')], check=True)

    def test_the_copied_tree_is_a_runnable_server(self):
        self.copy_tree()
        # What the service needs and would silently miss.
        for needed in ('web/index.html', 'bin/hardlylistening-server', 'cli.py', 'config.py',
                       'core/db.py', 'core/config.py', 'core/security.py',
                       'paths.py', 'server.py', 'VERSION'):
            self.assertTrue((self.prefix / needed).exists(), needed)

    def test_no_runtime_state_or_checkout_tooling_is_copied(self):
        self.copy_tree()
        for unwanted in ('hardlylistening.db', 'hardlylistening.db-wal', 'hardlylistening.db-shm',
                         '.cache', 'artist-art', 'backups', 'tests', 'docs', 'deploy',
                         '__pycache__'):
            self.assertFalse((self.prefix / unwanted).exists(), unwanted)

    def test_check_passes_from_the_copied_tree(self):
        self.copy_tree()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('HARDLYLISTENING_')}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            free = sock.getsockname()[1]
        env.update(HARDLYLISTENING_DATA_DIR=str(self.data),
                   HARDLYLISTENING_MUSIC_DIR=str(self.music),
                   HARDLYLISTENING_PORT=str(free),
                   PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([str(self.prefix / 'bin' / 'hardlylistening-server'), 'check'],
                                cwd=str(self.prefix), env=env, capture_output=True, text=True,
                                timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('result: ok', result.stdout)
        self.assertIn(str(self.prefix), result.stdout)          # it really probed the copy
        self.assertIn(str(self.data), result.stdout)
        # The copy is a server, not a checkout: no database was created beside the code.
        self.assertFalse((self.prefix / 'hardlylistening.db').exists())
        self.assertFalse((self.prefix / '__pycache__').exists())

    def test_the_script_itself_is_well_formed(self):
        script = APP_DIR / 'deploy' / 'install.sh'
        self.assertTrue(os.access(script, os.X_OK), 'install.sh is not executable')
        self.assertEqual(subprocess.run(['sh', '-n', str(script)]).returncode, 0)
        dry = subprocess.run([str(script), '--dry-run'], capture_output=True, text=True, timeout=120)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn('mode      dry run', dry.stdout)
        self.assertIn('  + ', dry.stdout)                     # it lists the steps
        self.assertIn('a starting config', dry.stdout)        # ...in the future tense
        uninstall = subprocess.run([str(script), 'uninstall', '--dry-run'],
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
        self.assertIn('Would remove', uninstall.stdout)
        self.assertIn('never will', uninstall.stdout)         # the data promise is stated


if __name__ == '__main__':
    unittest.main()