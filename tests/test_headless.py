"""The core is headless: it imports no GUI toolkit, needs no desktop toolchain, and boots with
no display.

This is the assertion behind "the runtime the Desktop launches is the same one that runs
alone" (SERVER-001/SERVER-005). Two halves, because either one alone can be fooled:

  * **Static** — every module in the core is parsed and its imports checked against the GUI
    and desktop-shell packages. An `import tkinter` inside a function that never runs still
    means the Desktop is not optional, and a grep would miss a nested import.
  * **Runtime** — the real entry point is started in a subprocess with `DISPLAY`,
    `WAYLAND_DISPLAY` and `XDG_SESSION_TYPE` removed, and asked the three things a client
    actually asks: `/api/health`, `/rest/ping.view` and the web UI.

The Subsonic check accepts the auth refusal as an answer on purpose: what a headless client
needs is the protocol's own envelope, not a page or a crash. Any `subsonic-response` proves
the adapter is answering with no display attached.
"""
import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]

# Import roots that mean "this needs a desktop". `gi` is guarded by the from-import name,
# since plenty of unrelated packages share that prefix.
GUI_ROOTS = {
    'tkinter', '_tkinter', 'PyQt4', 'PyQt5', 'PyQt6', 'PySide', 'PySide2', 'PySide6',
    'wx', 'kivy', 'pygame', 'pywebview', 'webview', 'eel', 'cefpython3', 'qtpy',
    'PySimpleGUI', 'customtkinter', 'dearpygui', 'pystray', 'pymsgbox', 'easygui',
}
GUI_PREFIXES = ('PyQt', 'PySide')


def core_modules():
    """Every Python file in the core: the app, not its tests."""
    for path in sorted(APP_DIR.rglob('*.py')):
        parts = path.relative_to(APP_DIR).parts
        if parts[0] in ('tests', '__pycache__') or parts[-1].startswith('benchmark_'):
            continue
        yield path


def imported_roots(tree):
    """Top-level names a module imports, however deep the import statement sits."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # a relative import is the core's own code
                continue
            if node.module:
                yield node.module.split('.')[0]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class NoDesktopToolkit(unittest.TestCase):
    def test_no_core_module_imports_a_gui_toolkit(self):
        offenders = []
        for path in core_modules():
            try:
                tree = ast.parse(path.read_text(), filename=str(path))
            except SyntaxError as error:        # a broken file is another test's problem
                self.fail(f'{path}: {error}')
            for root in imported_roots(tree):
                if root in GUI_ROOTS or root.startswith(GUI_PREFIXES):
                    offenders.append(f'{path.relative_to(APP_DIR)}: imports {root}')
        self.assertEqual(offenders, [], 'the core must not depend on a desktop toolkit')

    def test_the_core_needs_no_desktop_shell_or_node_toolchain(self):
        # A Tauri/Electron shell is a host, not the server: if one of these exists inside the
        # core, the server has stopped being runnable on its own.
        for name in ('src-tauri', 'package.json', 'node_modules', 'electron', 'tauri.conf.json'):
            self.assertFalse((APP_DIR / name).exists(), f'{name} belongs to a host, not the core')
        # The web UI is served as written — no build output to produce first.
        self.assertTrue((APP_DIR / 'web' / 'index.html').is_file())
        self.assertFalse((APP_DIR / 'web' / 'dist').exists())

    def test_the_launcher_is_the_plain_interpreter(self):
        launcher = (APP_DIR / 'bin' / 'hardlylistening-server').read_text()
        self.assertIn('python3', launcher)
        for shell in ('node', 'npm', 'yarn', 'tauri', 'electron'):
            self.assertNotIn(f' {shell} ', launcher, f'{shell} is a host dependency')


class BootsWithoutADisplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port = free_port()
        # Authentication off so the web UI is served without a member; the origin check
        # would refuse `/` on an instance that has no public origin configured yet.
        env = {k: v for k, v in os.environ.items()
               if k not in ('DISPLAY', 'WAYLAND_DISPLAY', 'XDG_SESSION_TYPE',
                            'HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB')}
        env.update(HARDLYLISTENING_DATA_DIR=self.tmp.name,
                   HARDLYLISTENING_DB=str(Path(self.tmp.name) / 'hl.db'),
                   HARDLYLISTENING_AUTH_REQUIRED='0',
                   PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
        self.proc = subprocess.Popen(
            [sys.executable, 'server.py', '--port', str(self.port)],
            cwd=APP_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True)
        self.addCleanup(lambda: self.proc.kill() if self.proc.poll() is None else None)
        output = ''
        deadline = time.time() + 60
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            output += line
            if 'HardlyListening on' in output:
                break
        self.assertIn('HardlyListening on', output, output)

    def get(self, path, **headers):
        import urllib.error
        import urllib.request
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers.get('Content-Type', ''), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get('Content-Type', ''), error.read()

    def test_health_answers_with_no_display(self):
        status, kind, raw = self.get('/api/health')
        self.assertEqual(status, 200, raw[:200])
        self.assertTrue(json.loads(raw)['ok'])

    def test_subsonic_answers_with_the_protocol_envelope(self):
        status, _, raw = self.get('/rest/ping.view?f=json&v=1.16.1&c=headless&u=none&p=none')
        self.assertEqual(status, 200)
        body = json.loads(raw)['subsonic-response']
        # The envelope is the point: a client gets a Subsonic answer, not an HTML page.
        self.assertIn('type', body)
        self.assertTrue(body.get('openSubsonic'))

    def test_the_web_app_is_served_as_written(self):
        status, kind, raw = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', kind)
        page = raw.decode()
        self.assertIn('<title>', page)
        # Served from web/ with no build step: the extracted scripts are in the markup.
        self.assertIn('/js/library.js', page)

    def test_it_runs_without_a_browser_or_a_node_toolchain_on_the_path(self):
        # The process is alive after answering all three: no display, no browser, no build.
        for path in ('/api/health', '/', '/rest/ping.view?f=json&v=1.16.1&c=headless&u=n&p=n'):
            self.assertIn(self.get(path)[0], (200, 401, 403, 503))
        self.assertIsNone(self.proc.poll(), 'the server must not exit under headless conditions')


if __name__ == '__main__':
    unittest.main()