"""HOST-003: start, stop and hand off the local server, proven without a real one.

`desktop/host/lifecycle.py` holds every decision this card makes: the start's deadline and its
reasons, the port probe, which stop is refused, and what closing the window does. The Rust half
cannot be compiled on this machine, so this file is what stands in for `cargo test` — the same
split `tests/test_desktop_host.py` uses for HOST-001.

Two hard rules keep this file safe to run anywhere:

  * the only server it talks to is a stub `http.server` bound to port 0, and
  * the only child it spawns is a python one-liner that sleeps, exits or serves that stub.

The real server, the real address and the live database are never touched. Nothing here runs
`systemctl`, `docker` or the launcher from `bin/`: argv and environment are proven by spawning a
copy that writes them down and exits, not by starting a server.
"""
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DESKTOP = APP_DIR / 'desktop'


def load_lifecycle():
    path = DESKTOP / 'host' / 'lifecycle.py'
    spec = importlib.util.spec_from_file_location('desktop_host_lifecycle', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lifecycle = load_lifecycle()
resolve = lifecycle.resolve

#: A child that answers `/api/health` and then keeps serving. Two argv: the port it is given.
SERVER_CHILD = '''
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true, "version": "stub"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''

#: A child that answers nothing and waits to be told to stop.
SLEEP_CHILD = 'import time; time.sleep(60)'
#: A child that refuses SIGTERM, so only the grace path ends it.
STUBBORN_CHILD = (
    'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'
)
#: A child that dies on its own before it could answer.
EXIT_CHILD = 'import sys; sys.exit(3)'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class StubHealth(BaseHTTPRequestHandler):
    """Answers exactly what the window asks: `/api/health`, and nothing else."""

    def do_GET(self):
        if self.path != '/api/health':
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({'ok': True, 'version': '0.0.0-stub'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class StubServer:
    """An external server on port 0: never the real address, never the live port."""

    def __init__(self):
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), StubHealth)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def address(self):
        return resolve.Address(host='127.0.0.1', port=self.port)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


class Harness(unittest.TestCase):
    """Shared plumbing: a temporary config directory, a stub child, and cleanup that cannot
    leave a process behind."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hl-lifecycle-'))
        self.config = self.tmp / resolve.CONFIG_NAME / resolve.CONFIG_FILE
        self.children = []
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.close()
        for child in self.children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        for root, dirs, files in os.walk(self.tmp, topdown=False):
            for name in files:
                Path(root, name).unlink()
            for name in dirs:
                Path(root, name).rmdir()
        self.tmp.rmdir()

    # -- plumbing --------------------------------------------------------------------------

    def stub_server(self):
        server = StubServer()
        self.servers.append(server)
        return server

    def launch_for(self, port, launcher=None):
        """A launch plan pointed at a test port, with a launcher that only has to exist."""
        app = self.tmp / 'app'
        if launcher is None:
            launcher = app / 'bin' / resolve.LAUNCHER_NAME
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_text('#!/bin/sh\nexit 0\n')
            launcher.chmod(0o755)
        address = resolve.Address(host='127.0.0.1', port=port)
        return lifecycle.Launch(command=[str(launcher), *address.argv()],
                                env=dict(os.environ),
                                cwd=str(app), address=address, launcher=str(launcher))

    def spawner(self, argv):
        """A `spawn` replacement: whatever the plan says, run this stub child instead."""
        def spawn(command, **kwargs):
            child = subprocess.Popen([sys.executable, *argv], start_new_session=True,
                                     stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            self.children.append(child)
            return child
        return spawn

    def server_child(self, port):
        return self.spawner(['-c', SERVER_CHILD, str(port)])

    def watch(self, launch, spawn, deadline=5.0, grace=1.0):
        return lifecycle.Lifecycle(launch, self.config, spawn=spawn,
                                   deadline=deadline, grace=grace, interval=0.02,
                                   kill_wait=2.0)


class AStartWatchesUntilItIsUpOrNamed(Harness):
    def test_a_start_that_answers_is_up_and_owned(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.server_child(port))
        result = watch.start()
        self.assertTrue(result['started'], result)
        self.assertEqual(result['phase'], lifecycle.UP)
        self.assertEqual(result['ownership'], lifecycle.OURS)
        self.assertIsNotNone(result['pid'])
        self.assertTrue(watch.alive())
        self.assertEqual(watch.stop()['stopped'], True)

    def test_a_child_that_exits_is_named_not_guessed(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.spawner(['-c', EXIT_CHILD]))
        result = watch.start()
        self.assertFalse(result['started'])
        self.assertEqual(result['reason'], lifecycle.EXITED)
        self.assertEqual(result['ownership'], lifecycle.NONE)
        self.assertIsNone(result['pid'])
        self.assertIn('status 3', result['detail'])
        self.assertFalse(watch.alive(), 'the slot must not keep a dead child')

    def test_a_start_that_never_answers_hits_the_deadline_with_a_reason(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.spawner(['-c', SLEEP_CHILD]),
                           deadline=1.0)
        began = time.monotonic()
        result = watch.start()
        elapsed = time.monotonic() - began
        self.assertFalse(result['started'])
        self.assertEqual(result['reason'], lifecycle.TIMEOUT)
        self.assertEqual(result['ownership'], lifecycle.OURS,
                         'a timed-out start is still this window\'s to stop')
        self.assertIn('within 1 s', result['detail'])
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 4.0, 'the deadline, not a hang')

    def test_a_start_already_ours_is_answered_not_repeated(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.server_child(port))
        first = watch.start()
        second = watch.start()
        self.assertTrue(second['started'])
        self.assertEqual(second['reason'], lifecycle.ALREADY_STARTED)
        self.assertEqual(second['pid'], first['pid'])
        watch.stop()


class ThePortIsCheckedBeforeAnythingIsSpawned(Harness):
    def test_an_answering_server_is_external_and_start_is_refused(self):
        server = self.stub_server()
        watch = self.watch(self.launch_for(server.port), self.spawner(['-c', SLEEP_CHILD]))
        result = watch.start()
        self.assertFalse(result['started'])
        self.assertEqual(result['reason'], lifecycle.ALREADY_RUNNING)
        self.assertEqual(result['ownership'], lifecycle.EXTERNAL)
        self.assertIsNone(result['pid'], 'nothing may be spawned over a live server')
        self.assertIn(str(server.port), result['detail'])

    def test_a_bound_port_that_does_not_answer_is_port_in_use(self):
        holder = socket.socket()
        self.addCleanup(holder.close)
        holder.bind(('127.0.0.1', 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        watch = self.watch(self.launch_for(port), self.spawner(['-c', SLEEP_CHILD]))
        result = watch.start()
        self.assertFalse(result['started'])
        self.assertEqual(result['reason'], lifecycle.PORT_IN_USE)
        self.assertEqual(result['ownership'], lifecycle.EXTERNAL)
        self.assertIsNone(result['pid'])

    def test_a_free_port_is_not_mistaken_for_a_busy_one(self):
        self.assertFalse(lifecycle.port_open('127.0.0.1', free_port()))
        server = self.stub_server()
        self.assertTrue(lifecycle.port_open('127.0.0.1', server.port))


class StopTouchesOnlyThisWindowsChild(Harness):
    def test_stop_refuses_an_external_server_and_says_why(self):
        server = self.stub_server()
        watch = self.watch(self.launch_for(server.port), self.spawner(['-c', SLEEP_CHILD]))
        result = watch.stop()
        self.assertFalse(result['stopped'])
        self.assertEqual(result['reason'], lifecycle.NOT_STARTED_HERE)
        self.assertEqual(result['ownership'], lifecycle.EXTERNAL)
        self.assertIn('left alone', result['detail'])
        # And the server is still answering: the refusal was not a silent stop.
        payload, problem = lifecycle.probe_health(server.address, timeout=2.0)
        self.assertIsNotNone(payload, problem)

    def test_stop_refuses_when_nothing_is_running(self):
        watch = self.watch(self.launch_for(free_port()), self.spawner(['-c', SLEEP_CHILD]))
        result = watch.stop()
        self.assertFalse(result['stopped'])
        self.assertEqual(result['reason'], lifecycle.NOT_STARTED_HERE)
        self.assertIn('not running', result['detail'])

    def test_a_sigterm_is_enough_for_a_well_behaved_child(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.server_child(port))
        watch.start()
        result = watch.stop()
        self.assertTrue(result['stopped'])
        self.assertFalse(result['forced'])
        self.assertIn('SIGTERM', result['detail'])
        self.assertEqual(result['phase'], lifecycle.IDLE)
        self.assertEqual(result['ownership'], lifecycle.NONE)

    def test_a_child_that_ignores_sigterm_is_killed_after_the_grace(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.spawner(['-c', STUBBORN_CHILD]),
                           deadline=0.4, grace=0.3)
        started = watch.start()
        # It never answers, so the start times out — but the process is still this window's.
        self.assertEqual(started['reason'], lifecycle.TIMEOUT)
        self.assertTrue(watch.alive())
        result = watch.stop()
        self.assertTrue(result['stopped'])
        self.assertTrue(result['forced'])
        self.assertIn('killed', result['detail'])
        self.assertFalse(watch.alive())


class ADeadChildIsReleasedNotDefended(Harness):
    def test_a_poll_notices_a_child_that_exited_on_its_own(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.server_child(port))
        watch.start()
        watch.process.kill()
        watch.process.wait(timeout=5)
        snapshot = watch.poll()
        self.assertEqual(snapshot['phase'], lifecycle.FAILED)
        self.assertEqual(snapshot['ownership'], lifecycle.NONE)
        self.assertEqual(snapshot['reason'], lifecycle.EXITED)
        self.assertIsNone(snapshot['pid'])
        self.assertIn('no longer running', snapshot['detail'])

    def test_a_poll_never_claims_an_external_server(self):
        server = self.stub_server()
        watch = self.watch(self.launch_for(server.port), self.spawner(['-c', SLEEP_CHILD]))
        snapshot = watch.poll()
        self.assertEqual(snapshot['phase'], lifecycle.UP)
        self.assertEqual(snapshot['ownership'], lifecycle.EXTERNAL)

    def test_a_poll_on_a_quiet_address_is_idle(self):
        watch = self.watch(self.launch_for(free_port()), self.spawner(['-c', SLEEP_CHILD]))
        snapshot = watch.poll()
        self.assertEqual(snapshot['phase'], lifecycle.IDLE)
        self.assertEqual(snapshot['ownership'], lifecycle.NONE)


class ClosingHandsOffUnlessToldOtherwise(Harness):
    def test_close_hands_off_and_the_child_keeps_running(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.server_child(port))
        watch.start()
        pid = watch.pid
        decision = watch.close()
        self.assertEqual(decision['action'], 'handoff')
        self.assertFalse(decision['stopped'])
        self.assertIn(str(pid), decision['detail'])
        # The record is on disk, and it says which pid and where.
        state = json.loads(lifecycle.state_path(self.config).read_text())
        self.assertEqual(state['handoff']['pid'], pid)
        self.assertIn(str(port), state['handoff']['listen'])
        # And the process is genuinely still there, not just remembered.
        self.assertTrue(lifecycle.pid_alive(pid))
        self.assertIsNone(watch.process, 'the window forgets the child it handed off')
        self.assertFalse(watch.alive())

    def test_the_next_window_reads_the_record_and_calls_it_external(self):
        port = free_port()
        watch = self.watch(self.launch_for(port), self.server_child(port))
        watch.start()
        pid = watch.pid
        watch.close()
        # A second window on the same address: the handed-off server is answering.
        second = self.watch(self.launch_for(port), self.spawner(['-c', SLEEP_CHILD]))
        snapshot = second.poll()
        self.assertEqual(snapshot['ownership'], lifecycle.EXTERNAL)
        self.assertEqual(snapshot['handoff']['pid'], pid)
        refusal = second.stop()
        self.assertFalse(refusal['stopped'])
        self.assertEqual(refusal['reason'], lifecycle.NOT_STARTED_HERE)
        self.assertIn('left alone', refusal['detail'])

    def test_stop_on_close_is_opt_in(self):
        port = free_port()
        self.assertEqual(lifecycle.set_stop_on_close(self.config, True), '')
        self.assertTrue(lifecycle.stop_on_close(self.config))
        watch = self.watch(self.launch_for(port), self.server_child(port))
        watch.start()
        pid = watch.pid
        decision = watch.close()
        self.assertEqual(decision['action'], 'stop')
        self.assertTrue(decision['stopped'])
        self.assertFalse(lifecycle.pid_alive(pid))

    def test_close_with_nothing_running_does_nothing(self):
        watch = self.watch(self.launch_for(free_port()), self.spawner(['-c', SLEEP_CHILD]))
        decision = watch.close()
        self.assertEqual(decision['action'], 'nothing')
        self.assertFalse(lifecycle.state_path(self.config).exists())

    def test_a_stale_record_is_removed_not_reported(self):
        gone = subprocess.Popen([sys.executable, '-c', 'pass'])
        gone.wait(timeout=5)
        lifecycle.record_handoff(self.config, gone.pid, None, '127.0.0.1:1234')
        self.assertFalse(lifecycle.pid_alive(gone.pid))
        self.assertEqual(lifecycle.handoff(self.config), {})
        state = json.loads(lifecycle.state_path(self.config).read_text())
        self.assertNotIn('handoff', state)

    def test_the_preference_survives_the_handoff_being_cleared(self):
        lifecycle.set_stop_on_close(self.config, True)
        live = subprocess.Popen([sys.executable, '-c', SLEEP_CHILD], start_new_session=True)
        self.children.append(live)
        self.assertEqual(lifecycle.record_handoff(self.config, live.pid, None, '127.0.0.1:1'), '')
        self.assertTrue(lifecycle.handoff(self.config))
        lifecycle.clear_handoff(self.config)
        self.assertEqual(lifecycle.handoff(self.config), {})
        self.assertTrue(lifecycle.stop_on_close(self.config))

    def test_the_state_file_does_not_overwrite_the_address_settings(self):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({'host': '127.0.0.1', 'port': 9100}))
        lifecycle.set_stop_on_close(self.config, True)
        self.assertEqual(lifecycle.state_path(self.config),
                         self.config.parent / 'lifecycle.json')
        self.assertEqual(json.loads(self.config.read_text()), {'port': 9100,
                                                              'host': '127.0.0.1'})


class TheLaunchPlanIsTheLaunchers(Harness):
    def test_the_argv_is_the_resolvers_explicit_flags(self):
        port = free_port()
        launch = self.launch_for(port)
        self.assertEqual(launch.command[1:], ['--host', '127.0.0.1', '--port', str(port)])
        self.assertEqual(launch.command[1:], resolve.Address(host='127.0.0.1', port=port).argv())

    def test_the_switches_are_flags_on_the_same_command(self):
        """HOST-005's two settings reach the server, not just the file."""
        launch = lifecycle.build_launch(
            {'HARDLYLISTENING_PORT': '', 'HARDLYLISTENING_WEB_UI': '0',
             'HARDLYLISTENING_SUBSONIC': 'off'}, environ={})
        self.assertEqual(launch.command[1:],
                         ['--host', '127.0.0.1', '--port', str(resolve.DEFAULT_PORT),
                          '--no-web-ui', '--no-subsonic'])
        self.assertFalse(launch.features.web_ui)
        self.assertFalse(launch.features.subsonic)
        # On is the default: nothing is added to the argv an older shell produced.
        plain = lifecycle.build_launch({'HARDLYLISTENING_PORT': ''}, environ={})
        self.assertEqual(len(plain.command[1:]), 4)
        self.assertNotIn('--no-web-ui', plain.command)

    def test_a_stored_switch_is_honoured_too(self):
        config = self.tmp / 'host.json'
        config.write_text(json.dumps({'web_ui': False}))
        launch = lifecycle.build_launch({'HARDLYLISTENING_PORT': ''}, environ={},
                                        config=config)
        self.assertIn('--no-web-ui', launch.command)
        self.assertNotIn('--no-subsonic', launch.command)

    def test_a_bad_switch_value_refuses_the_launch(self):
        with self.assertRaises(lifecycle.LaunchError) as raised:
            lifecycle.build_launch({'HARDLYLISTENING_WEB_UI': 'maybe'}, environ={})
        self.assertIn('HARDLYLISTENING_WEB_UI', raised.exception.detail)

    def test_the_environment_says_the_same_address_as_the_argv(self):
        launch = lifecycle.build_launch({'HARDLYLISTENING_PORT': ''},
                                        environ={'PATH': '/usr/bin'})
        self.assertEqual(launch.env['HARDLYLISTENING_HOST'], '127.0.0.1')
        self.assertEqual(launch.env['HARDLYLISTENING_PORT'], str(resolve.DEFAULT_PORT))
        self.assertIn('PATH', launch.env, 'the child needs an environment, not a blank one')

    def test_the_python_override_reaches_the_launcher(self):
        launch = lifecycle.build_launch({'HARDLYLISTENING_PYTHON': '/opt/python3'},
                                        environ={})
        self.assertEqual(launch.env['HARDLYLISTENING_PYTHON'], '/opt/python3')

    def test_a_missing_launcher_is_a_named_reason(self):
        with self.assertRaises(lifecycle.LaunchError) as raised:
            lifecycle.build_launch({'HARDLYLISTENING_SERVER': str(self.tmp / 'nope')},
                                   environ={})
        self.assertEqual(raised.exception.reason, lifecycle.NO_LAUNCHER)
        self.assertIn('HARDLYLISTENING_SERVER', raised.exception.detail)

    def test_a_typo_in_the_port_is_a_named_reason_not_a_fallback(self):
        with self.assertRaises(lifecycle.LaunchError) as raised:
            lifecycle.build_launch({'HARDLYLISTENING_PORT': 'eighty-four-seventy'},
                                   environ={})
        self.assertEqual(raised.exception.reason, lifecycle.BAD_ADDRESS)

    def test_the_working_directory_is_the_application_root(self):
        launch = lifecycle.build_launch({}, environ={})
        self.assertEqual(launch.cwd, str(APP_DIR))
        self.assertEqual(launch.command[0], str(APP_DIR / 'bin' / resolve.LAUNCHER_NAME))

    def test_what_is_spawned_really_carries_that_argv_and_environment(self):
        # A launcher that writes down what it was given and exits: the plan is proven by a real
        # spawn, without starting a server.
        root = self.tmp / 'app'
        launcher = root / 'bin' / resolve.LAUNCHER_NAME
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(
            '#!/bin/sh\n'
            f'printf "%s\\n" "$@" > "{self.tmp}/args.txt"\n'
            f'printf "%s\\n" "$HARDLYLISTENING_HOST:$HARDLYLISTENING_PORT" > "{self.tmp}/env.txt"\n'
            f'pwd > "{self.tmp}/cwd.txt"\n'
            'exit 0\n')
        launcher.chmod(0o755)
        port = free_port()
        launch = lifecycle.build_launch(
            {'HARDLYLISTENING_SERVER': str(launcher), 'HARDLYLISTENING_PORT': str(port)},
            environ={})
        watch = lifecycle.Lifecycle(launch, self.config, deadline=2.0, interval=0.02)
        result = watch.start()
        # The stub exits immediately, so the watch names that rather than claiming success.
        self.assertEqual(result['reason'], lifecycle.EXITED)
        self.assertEqual((self.tmp / 'args.txt').read_text().split(),
                         ['--host', '127.0.0.1', '--port', str(port)])
        self.assertEqual((self.tmp / 'env.txt').read_text().strip(),
                         f'127.0.0.1:{port}')
        self.assertEqual((self.tmp / 'cwd.txt').read_text().strip(), str(root))


class TheSnapshotIsWhatTheWindowReads(Harness):
    def test_it_carries_the_keys_the_window_needs(self):
        watch = self.watch(self.launch_for(free_port()), self.spawner(['-c', SLEEP_CHILD]))
        snapshot = watch.snapshot()
        for key in ('phase', 'ownership', 'pid', 'reason', 'detail', 'health_error',
                    'listen', 'url', 'address', 'stop_on_close', 'handoff'):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot['address']['host'], '127.0.0.1')
        self.assertFalse(snapshot['stop_on_close'])
        self.assertEqual(snapshot['handoff'], {})

    def test_the_rust_constants_are_the_python_ones(self):
        rust = (DESKTOP / 'src-tauri' / 'src' / 'lifecycle.rs').read_text()
        deadlines = re.search(r'pub const START_DEADLINE: Duration = Duration::from_secs\((\d+)\)',
                              rust)
        graces = re.search(r'pub const STOP_GRACE: Duration = Duration::from_secs\((\d+)\)', rust)
        handoff = re.search(r'pub const HANDOFF_FILE: &str = "([^"]+)"', rust)
        self.assertIsNotNone(deadlines)
        self.assertIsNotNone(graces)
        self.assertIsNotNone(handoff)
        self.assertEqual(int(deadlines.group(1)), int(lifecycle.START_DEADLINE))
        self.assertEqual(int(graces.group(1)), int(lifecycle.STOP_GRACE))
        self.assertEqual(handoff.group(1), lifecycle.HANDOFF_FILE)

    def test_the_handoff_file_name_matches_the_documented_one(self):
        self.assertEqual(lifecycle.state_path(self.config).name, 'lifecycle.json')
        doc = (APP_DIR / 'docs' / 'desktop' / 'lifecycle.md').read_text()
        self.assertIn('lifecycle.json', doc)


class NothingHereReachesTheRealInstall(Harness):
    def test_the_module_names_no_service_manager_or_database(self):
        source = (DESKTOP / 'host' / 'lifecycle.py').read_text()
        for marker in ('sqlite3', 'systemctl', 'docker', 'hardlylistening.db'):
            self.assertNotIn(marker, source, marker)

    def test_every_port_in_this_file_is_allocated_not_assumed(self):
        source = Path(__file__).read_text()
        self.assertNotIn(str(resolve.DEFAULT_PORT), source,
                         'the tests must never name the live address')


if __name__ == '__main__':
    unittest.main()