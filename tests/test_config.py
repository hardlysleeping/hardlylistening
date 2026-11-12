"""Runtime config and the packaged entry point: HOST, PORT, MUSIC_DIR, `check`, `version`.

`SERVER-PKG-001` is only satisfied if an existing install is untouched by it: with nothing
declared the server listens on 127.0.0.1:8470 and seeds no root, exactly as before. So the tests
run the real module and the real launcher in subprocesses, with a temporary data directory, rather
than asserting against a parsed copy of the environment.

`check` is held to the same rule it promises: it reports and repairs nothing, so a missing music
directory comes back as a failure and is still missing afterwards.
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
SERVER = APP_DIR / 'bin' / 'hardlylistening-server'

CLEAN = ('HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB', 'HARDLYLISTENING_HOST',
         'HARDLYLISTENING_PORT', 'HARDLYLISTENING_MUSIC_DIR')


def run(code: str, **env_overrides):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env.update(env_overrides)
    env['PYTHONPATH'] = str(APP_DIR)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run([sys.executable, '-c', code], cwd=APP_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


def launcher(*args, **env_overrides):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env.update(env_overrides)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run([str(SERVER), *args], cwd=APP_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class RuntimeConfig(unittest.TestCase):
    def test_nothing_declared_is_todays_behaviour(self):
        result = run('import config; print(config.host()); print(config.port());'
                     ' print(repr(config.music_dir()))')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split('\n')[:3], ['127.0.0.1', '8470', "''"])

    def test_environment_supplies_host_port_and_music_dir(self):
        result = run('import config; print(config.host()); print(config.port());'
                     ' print(config.music_dir())',
                     HARDLYLISTENING_HOST='0.0.0.0', HARDLYLISTENING_PORT='9000',
                     HARDLYLISTENING_MUSIC_DIR='/srv/music')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split('\n')[:3], ['0.0.0.0', '9000', '/srv/music'])

    def test_a_bad_port_refuses_rather_than_falling_back(self):
        for value in ('eighty', '0', '70000'):
            result = run('import config; print(config.port())',
                         HARDLYLISTENING_PORT=value)
            self.assertNotEqual(result.returncode, 0, value)
            self.assertIn('HARDLYLISTENING_PORT', result.stderr, value)


class MusicRootSeed(unittest.TestCase):
    """`MUSIC_DIR` seeds a new database and never overrides a chosen root."""

    def seed(self, code_before: str = '', code_after: str = '', **env):
        with tempfile.TemporaryDirectory() as data:
            result = run(
                'import paths, db, config\n'
                'paths.ensure()\n'
                'store = db.Store()\n'
                + code_before +
                'print("seeded", config.seed_music_root(store))\n'
                'print("root", store.setting("root"))\n'
                + code_after +
                'store.close()\n',
                HARDLYLISTENING_DATA_DIR=data, **env)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_a_new_database_takes_the_declared_root(self):
        out = self.seed(HARDLYLISTENING_MUSIC_DIR='/srv/hardlylistening/music')
        self.assertIn('seeded True', out)
        self.assertIn('root /srv/hardlylistening/music', out)

    def test_a_chosen_root_wins_over_the_variable(self):
        out = self.seed(code_before='store.set_settings({"root": "/somewhere/else"})\n',
                        HARDLYLISTENING_MUSIC_DIR='/srv/hardlylistening/music')
        self.assertIn('seeded False', out)
        self.assertIn('root /somewhere/else', out)

    def test_nothing_declared_seeds_nothing(self):
        out = self.seed()
        self.assertIn('seeded False', out)


class Check(unittest.TestCase):
    def test_version_is_the_installed_marker(self):
        marker = (APP_DIR / 'VERSION').read_text().strip()
        result = launcher('version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), marker)

    def test_unknown_command_is_an_error(self):
        result = launcher('frobnicate')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown command', result.stderr)

    def test_a_sane_install_passes_and_writes_nothing(self):
        if not (shutil.which('yt-dlp') and shutil.which('ffmpeg')):
            self.skipTest('yt-dlp/ffmpeg not installed')
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as music:
            before = sorted(p.name for p in Path(music).iterdir())
            result = launcher('check', HARDLYLISTENING_DATA_DIR=data,
                              HARDLYLISTENING_MUSIC_DIR=music,
                              HARDLYLISTENING_PORT=str(free_port()))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('result: ok', result.stdout)
            self.assertIn('will be created', result.stdout)      # reported, not created
            self.assertFalse((Path(data) / 'hardlylistening.db').exists())
            self.assertEqual(before, sorted(p.name for p in Path(music).iterdir()))

    def test_a_missing_music_directory_fails_and_is_not_created(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as outer:
            music = Path(outer) / 'music'
            result = launcher('check', HARDLYLISTENING_DATA_DIR=data,
                              HARDLYLISTENING_MUSIC_DIR=str(music),
                              HARDLYLISTENING_PORT=str(free_port()))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('music', result.stdout)
            self.assertIn('fail', result.stdout)
            self.assertFalse(music.exists())

    def test_a_busy_port_is_a_warning_not_a_failure(self):
        if not (shutil.which('yt-dlp') and shutil.which('ffmpeg')):
            self.skipTest('yt-dlp/ffmpeg not installed')
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as music, \
                socket.socket() as held:
            held.bind(('127.0.0.1', 0))
            held.listen(1)
            port = held.getsockname()[1]
            result = launcher('check', HARDLYLISTENING_DATA_DIR=data,
                              HARDLYLISTENING_MUSIC_DIR=music,
                              HARDLYLISTENING_PORT=str(port))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('warning', result.stdout)


if __name__ == '__main__':
    unittest.main()