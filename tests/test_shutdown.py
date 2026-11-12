"""Shutdown: a signal stops the process in order and leaves nothing half-open.

systemd and Docker both stop HardlyListening with SIGTERM, and both treat a process that has to be
killed as a failure. So this exercises the real entry point in a real subprocess — bind, answer
`/api/health`, signal, exit — rather than calling `shutdown()` in-process, because the deadlock
this handler exists to avoid only appears when `serve_forever` owns a thread.
"""
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Signals(unittest.TestCase):
    def start(self, signame):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        port = free_port()
        env = {k: v for k, v in os.environ.items()
               if k not in ('HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB')}
        env.update(HARDLYLISTENING_DATA_DIR=self.tmp.name,
                   HARDLYLISTENING_DB=str(Path(self.tmp.name) / 'hl.db'),
                   PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
        proc = subprocess.Popen([sys.executable, 'server.py', '--port', str(port)],
                                cwd=APP_DIR, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        deadline = time.time() + 60
        output = ''
        while time.time() < deadline:
            if 'HardlyListening on' in output:
                break
            line = proc.stdout.readline()
            if not line:
                break
            output += line
        self.assertIn('HardlyListening on', output, output)
        self.port = port
        return proc, output

    def health(self, port):
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=5) as r:
                return r.status
        except (urllib.error.URLError, OSError):
            return None

    def stop_and_read(self, proc, signame):
        self.assertEqual(self.health(self.port), 200)
        os.killpg(proc.pid, signame)
        try:
            out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail('did not exit within 30 s of the signal')
        return proc.returncode, out

    def test_sigterm_exits_cleanly_and_releases_the_port(self):
        proc, _ = self.start('SIGTERM')
        code, out = self.stop_and_read(proc, signal.SIGTERM)
        self.assertEqual(code, 0, out)
        self.assertIn('SIGTERM: shutting down', out)
        self.assertIn('bye', out)
        self.assertIsNone(self.health(self.port))

    def test_sigint_takes_the_same_path(self):
        proc, _ = self.start('SIGINT')
        code, out = self.stop_and_read(proc, signal.SIGINT)
        self.assertEqual(code, 0, out)
        self.assertIn('SIGINT: shutting down', out)

    def test_shutdown_leaves_the_database_closed_and_no_wal_behind(self):
        proc, _ = self.start('SIGTERM')
        code, out = self.stop_and_read(proc, signal.SIGTERM)
        self.assertEqual(code, 0, out)
        wal = Path(self.tmp.name) / 'hl.db-wal'
        self.assertFalse(wal.exists() and wal.stat().st_size > 0,
                         'WAL was not checkpointed on close')


class QueueStop(unittest.TestCase):
    def test_stop_waits_for_an_idle_worker_and_returns(self):
        from db import Store
        from engine.queue import Queue
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'q.db')
        self.addCleanup(store.close)
        queue = Queue(store)
        queue.start()
        self.assertTrue(queue.thread.is_alive())
        started = time.time()
        queue.stop(grace=5.0)
        self.assertLess(time.time() - started, 10)
        self.assertFalse(queue.thread.is_alive())


if __name__ == '__main__':
    unittest.main()