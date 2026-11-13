"""HOST-003: the local server's lifecycle, decided here and executed thinly elsewhere.

HOST-001 answered *whose* server is answering the health check (`none | ours | external`) and
left the rest to this card: what a start is, when it has failed, what a stop may touch, and what
closing the window does to a server this window started.

The rules, in the order they constrain the code:

  * **A start has a deadline and a reason.** Spawn, then watch `/api/health` until it answers or
    the deadline passes. Every ending is named — `already_running`, `port_in_use`, `no_launcher`,
    `spawn_failed`, `exited`, `timeout` — because "it did not start" without a reason is the same
    to a person as a broken button.
  * **Stop touches only this window's child.** A server answering the address that this window
    did not spawn belongs to systemd, Docker or a terminal. The refusal carries the explanation,
    never silence.
  * **Closing hands off by default.** A server this window started keeps running when the window
    closes, and the fact is written to a small file so the next window can say what it found.
    Stopping on close is opt-in (`stop_on_close`), stored beside the handoff record.
  * **The command is the launcher.** `bin/hardlylistening-server` with explicit `--host/--port`
    from `resolve.py`, which is the tested spec for paths and addresses. Nothing here builds a
    second way to start a server.

Why the state file is `lifecycle.json` next to `host.json`, not inside it: `host.json` holds the
one setting a person chooses (the address) and HOST-005 will write it. Two writers to one file is
how a preference is silently dropped. One file for this card's two facts — how close behaves, and
(while a handed-off server runs) which pid and address were left behind — keeps the writers
apart and gives the window one thing to read. `HARDLYLISTENING_DESKTOP_CONFIG` still points both
files at a test directory, because the path is derived from `resolve.config_path()`.

Nothing here imports a server module, opens the live database or names the service on the
machine this was written on. The tests spawn a stub `http.server` on port 0 and a sleeping
python child; the real server and the real address are never touched.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


def _resolve_module():
    """`resolve.py` is the tested spec for paths and addresses; borrow it, never copy it.

    Loaded as a sibling module so this file works when imported as `host.lifecycle` *and* when
    it is loaded by path, as the tests do. A bare `import resolve` would fail in the second case
    and would also look like a non-stdlib dependency to `tests/test_desktop_host.py`.
    """
    try:
        from . import resolve as module
    except ImportError:
        path = Path(__file__).resolve().parent / 'resolve.py'
        spec = importlib.util.spec_from_file_location('hardlylistening_desktop_resolve', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


resolve = _resolve_module()

#: How long a spawn gets to answer before the start is declared failed. The window's poll uses
#: the same number (`ui/state.js`), so the status line and this deadline cannot disagree.
START_DEADLINE = 20.0
#: SIGTERM first — the server shuts its queue worker down on it — then SIGKILL after this.
STOP_GRACE = 3.0
#: How often the start watch asks again. A local server answers in milliseconds; this is a
#: poll loop rather than a wait on a log line because the server's logs belong to its owner.
POLL_INTERVAL = 0.1
#: One health request's own timeout. A hung server must not consume the whole deadline.
HEALTH_TIMEOUT = 2.0
#: A TCP connect is only asked "is something bound here". Anything slower is not an answer.
PORT_TIMEOUT = 0.25
#: The state file, beside `host.json`, in the window's own state directory.
HANDOFF_FILE = 'lifecycle.json'

#: Phases. `failed` keeps ownership when the child is alive (a timeout leaves a server this
#: window started still running, and therefore still stoppable); `exited` does not.
IDLE = 'idle'
STARTING = 'starting'
UP = 'up'
FAILED = 'failed'
STOPPING = 'stopping'

NONE = 'none'
OURS = 'ours'
EXTERNAL = 'external'

#: Reasons. Strings, not enums: they cross into JSON for the window and the Rust mirror.
ALREADY_STARTED = 'already_started'
ALREADY_RUNNING = 'already_running'
PORT_IN_USE = 'port_in_use'
BAD_ADDRESS = 'bad_address'
NO_LAUNCHER = 'no_launcher'
SPAWN_FAILED = 'spawn_failed'
EXITED = 'exited'
TIMEOUT = 'timeout'
NOT_STARTED_HERE = 'not_started_here'
STATE_UNAVAILABLE = 'state_unavailable'


class LaunchError(Exception):
    """A reason a launch could not even be attempted, in the window's vocabulary."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Launch:
    """What to spawn: the canonical launcher, explicit flags, and the environment around it."""

    command: list[str]
    env: dict[str, str]
    cwd: str | None
    address: object          # resolve.Address
    launcher: str
    features: object = None      # resolve.Features; None means "everything on"

    def argv(self) -> list[str]:
        return list(self.command[1:])


def build_launch(env: Mapping[str, str] | None = None, app: Path | None = None,
                 config: Path | None = None,
                 environ: Mapping[str, str] | None = None) -> Launch:
    """Build the spawn plan, or raise `LaunchError` with the reason the window will show.

    `env` is the *shell's* declared environment (`HARDLYLISTENING_*`), `environ` is the process
    environment inherited by the child. They are separate so a test can describe a fake desktop
    without pretending to be one.
    """
    env = os.environ if env is None else env
    stored = Path(config) if config is not None else None
    try:
        address = resolve.address(env, stored)
        features = resolve.features(env, stored)
    except ValueError as error:
        raise LaunchError(BAD_ADDRESS, str(error)) from None

    server = resolve.launcher(env, app)
    if not server.is_file():
        raise LaunchError(
            NO_LAUNCHER,
            f'{server} was not found; bundle bin/{resolve.LAUNCHER_NAME} as the sidecar, '
            'or set HARDLYLISTENING_SERVER to a checkout',
        )

    inherited = dict(os.environ if environ is None else environ)
    # The address is passed twice on purpose: argv is what the server parses, and the two
    # environment names are what its own config reads. Both say the same thing, so a server
    # started by this window cannot end up serving an address this window does not poll.
    inherited['HARDLYLISTENING_HOST'] = address.host
    inherited['HARDLYLISTENING_PORT'] = str(address.port)
    python = (env.get('HARDLYLISTENING_PYTHON') or '').strip()
    if python:
        inherited['HARDLYLISTENING_PYTHON'] = python

    # The launcher lives in <app>/bin and finds the application relative to itself, so the
    # working directory is the checkout root — never wherever the window was launched from.
    root = server.parent.parent
    cwd = str(root) if root.is_dir() else None
    # The switches are passed as flags too: the server reads them at boot, and the window's
    # own file is the only place they are chosen. On is the default, so an all-on launch adds
    # nothing to the argv an older shell already produced.
    return Launch(command=[str(server), *address.argv(), *features.argv()], env=inherited,
                  cwd=cwd, address=address, launcher=str(server), features=features)


def probe_host(address) -> str:
    """The host a client connects to; a listen-anywhere bind is reached on loopback."""
    return address.probe_host


def port_open(host: str, port: int, timeout: float = PORT_TIMEOUT) -> bool:
    """Whether something is bound at `host:port`. A refusal means the address is free."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_health(address, timeout: float = HEALTH_TIMEOUT) -> tuple[dict | None, str]:
    """One `GET /api/health`: the payload, or a sentence explaining why there is none."""
    url = address.api('/api/health')
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except (urllib.error.URLError, OSError, ValueError) as error:
        return None, f'{url}: {error}'
    if not 200 <= status < 300:
        return None, f'/api/health answered {status}'
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as error:
        return None, f'/api/health was not JSON: {error}'
    if not isinstance(payload, dict):
        return None, '/api/health did not answer with an object'
    return payload, ''


# ---- the window's memory: how close behaves, and what was handed off ------------------------

def state_path(config: Path) -> Path:
    """`lifecycle.json` beside the address file, wherever the config was resolved to."""
    return Path(config).parent / HANDOFF_FILE


def read_state(config: Path) -> dict:
    """The file's object, or `{}`. Malformed is reported to nobody and ignored: a window that
    will not open because its own memory lost a brace is worse than one that forgets a
    preference."""
    path = state_path(config)
    try:
        text = path.read_text()
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(config: Path, data: Mapping) -> str:
    """Replace the file atomically; returns a problem sentence, or `''`.

    Written beside the target and renamed, so a crash mid-write leaves the previous record
    rather than a truncated one. The window's memory is not user data, so this is the only
    write this module does.
    """
    path = state_path(config)
    tmp = path.with_suffix(path.suffix + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(dict(data), indent=2, sort_keys=True) + '\n')
        os.replace(tmp, path)
    except OSError as error:
        return f'{path} could not be written: {error.strerror or error}'
    return ''


def pid_alive(pid: int | None) -> bool:
    """Whether the pid is still there. `PermissionError` means it exists and is not ours."""
    if not pid or pid <= 0 or not hasattr(os, 'kill'):
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def stop_on_close(config: Path) -> bool:
    """Whether closing the window also stops the server it started. Off unless chosen."""
    return read_state(config).get('stop_on_close') is True


def set_stop_on_close(config: Path, flag: bool) -> str:
    data = read_state(config)
    data['stop_on_close'] = bool(flag)
    return write_state(config, data)


def record_handoff(config: Path, pid: int, launch: Launch | None, listen: str,
                   at: float | None = None) -> str:
    """Record that this window's server is being left running, and where it is."""
    data = read_state(config)
    data['handoff'] = {
        'pid': int(pid),
        'listen': listen,
        'launcher': launch.launcher if launch is not None else None,
        'recorded_at': float(time.time() if at is None else at),
    }
    return write_state(config, data)


def clear_handoff(config: Path) -> str:
    data = read_state(config)
    if 'handoff' not in data:
        return ''
    data.pop('handoff', None)
    return write_state(config, data)


def handoff(config: Path) -> dict:
    """The handed-off server, if a window left one that is still running.

    A record naming a pid that is gone (a reboot, a crash, a server someone stopped by hand) is
    removed rather than reported: the window asks "is a server of mine running", and a stale
    pid is a wrong answer. Pid reuse is the residual risk; the record is advice, not authority,
    and the address in it is what the window actually polls.
    """
    record = read_state(config).get('handoff')
    if not isinstance(record, dict):
        return {}
    if not pid_alive(record.get('pid')):
        clear_handoff(config)
        return {}
    return record


# ---- the state machine ----------------------------------------------------------------------

class Lifecycle:
    """One window's view of *the* local server: spawn, watch, stop, hand off.

    Process operations are injected (`spawn`, `sleep`, `clock`, `health`, `port_open`) so the
    rules are exercised with a stub server and a stub child; the Rust command registers the
    same sequence with the OS calls it owns.
    """

    def __init__(self, launch: Launch, config: Path,
                 spawn: Callable = subprocess.Popen,
                 health: Callable | None = None,
                 port_probe: Callable | None = None,
                 clock: Callable = time.monotonic,
                 sleep: Callable = time.sleep,
                 deadline: float = START_DEADLINE,
                 grace: float = STOP_GRACE,
                 interval: float = POLL_INTERVAL,
                 kill_wait: float = 2.0):
        self.launch = launch
        self.address = launch.address
        self.config = Path(config)
        self._spawn = spawn
        self._health = health or (lambda address: probe_health(address, HEALTH_TIMEOUT))
        self._port_open = port_probe or port_open
        self._clock = clock
        self._sleep = sleep
        self.deadline = deadline
        self.grace = grace
        self.interval = interval
        self.kill_wait = kill_wait

        self.process = None
        self.phase = IDLE
        self.ownership = NONE
        self.reason = ''
        self.detail = ''
        self.pid = None
        self.started_at = 0.0
        self.health_error = ''
        self.forced = False

    # -- reading ---------------------------------------------------------------------------

    def alive(self) -> bool:
        """Whether the child this window spawned is still running. A dead one is reaped and
        forgotten here, so ownership cannot outlive the process it describes."""
        if self.process is None:
            return False
        try:
            code = self.process.poll()
        except OSError:
            code = None
        if code is not None:
            self.process = None
            self.pid = None
            return False
        return True

    def snapshot(self) -> dict:
        """Everything the window shows, in one object. `phase` and `ownership` are separate
        because "failed" and "not mine" are different facts."""
        record = handoff(self.config) if self.ownership != OURS else {}
        return {
            'phase': self.phase,
            'ownership': self.ownership,
            'pid': self.pid,
            'reason': self.reason,
            'detail': self.detail,
            'health_error': self.health_error,
            'forced': self.forced,
            'listen': self.address.listen(),
            'url': self.address.url(),
            'address': {'host': self.address.host, 'port': self.address.port,
                        'source': self.address.source},
            'started_at': self.started_at,
            'stop_on_close': stop_on_close(self.config),
            'handoff': record,
        }

    # -- transitions -----------------------------------------------------------------------

    def _result(self, **fields) -> dict:
        out = {
            'phase': self.phase,
            'ownership': self.ownership,
            'pid': self.pid,
            'reason': self.reason or None,
            'detail': self.detail,
            'started': False,
            'stopped': False,
        }
        out.update(fields)
        return out

    def _detach(self) -> None:
        """Forget the child without signalling it: used when it exited on its own."""
        self.process = None
        self.pid = None

    def start(self) -> dict:
        """Spawn and watch. Returns `{started, phase, ownership, reason, detail, pid}`."""
        if self.alive():
            self.reason = ALREADY_STARTED
            self.detail = f'pid {self.pid} is already this window\'s server'
            return self._result(started=True)

        payload, _problem = self._health(self.address)
        if payload is not None:
            # Something answers. It is not ours: the child slot is empty.
            self._detach()
            self.ownership = EXTERNAL
            self.phase = UP
            self.reason = ALREADY_RUNNING
            self.detail = (f'{self.address.listen()} answered; this window did not start that '
                           'server, so it will not stop it')
            return self._result(started=False)

        if self._port_open(probe_host(self.address), self.address.port):
            # Bound but not answering /api/health: another program owns the port. Starting a
            # second server would either fail or silently serve somewhere else.
            self.ownership = EXTERNAL
            self.phase = FAILED
            self.reason = PORT_IN_USE
            self.detail = (f'{self.address.listen()} is already in use and does not answer '
                           '/api/health; this window will not start a second server there')
            return self._result(started=False)

        try:
            child = self._spawn(
                self.launch.command,
                cwd=self.launch.cwd,
                env=self.launch.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # A server that is meant to survive the window closing must not share the
                # desktop session's signal handling: a terminal Ctrl+C or the session ending
                # would otherwise take it down and make the handoff a lie.
                start_new_session=True,
            )
        except OSError as error:
            self.ownership = NONE
            self.phase = FAILED
            self.reason = SPAWN_FAILED
            self.detail = f'{self.launch.command[0]}: {error}'
            return self._result()

        # A record left by an earlier window describes a server that is no longer the one
        # serving this address, so it stops being true the moment this window spawns.
        clear_handoff(self.config)

        self.process = child
        self.pid = child.pid
        self.phase = STARTING
        self.ownership = OURS
        self.reason = ''
        self.detail = ''
        self.health_error = ''
        self.started_at = self._clock()

        until = self.started_at + self.deadline
        while self._clock() < until:
            try:
                code = child.poll()
            except OSError:
                code = None
            if code is not None:
                # A child that exited on its own is nobody's: say so and release the slot.
                self._detach()
                self.phase = FAILED
                self.ownership = NONE
                self.reason = EXITED
                self.detail = (f'the server exited with status {code} before it answered '
                               f'{self.address.url()}')
                return self._result()

            payload, problem = self._health(self.address)
            if payload is not None:
                self.phase = UP
                self.ownership = OURS
                self.health_error = ''
                return self._result(started=True)
            self.health_error = problem
            self._sleep(self.interval)

        # The deadline passed with a live child: the start failed, but the process is still
        # this window's, so Stop stays available. Killing it here would hide the reason the
        # window is looking at.
        self.phase = FAILED
        self.reason = TIMEOUT
        self.detail = (f'no answer from {self.address.url()} within {self.deadline:.0f} s; '
                       'the process this window started is still running')
        return self._result()

    def poll(self) -> dict:
        """One status tick: reap a child that exited, ask health once, settle the phase.

        Called often; it never starts or stops anything.
        """
        running = self.alive()
        payload, problem = self._health(self.address)
        self.health_error = problem if payload is None else ''

        if self.ownership == OURS:
            if not running:
                self.phase = FAILED
                self.ownership = NONE
                self.reason = EXITED
                self.detail = 'the server this window started is no longer running'
                self.started_at = 0.0
            elif payload is not None:
                self.phase = UP
            elif self.phase == STARTING:
                pass                      # still within the start's own watch
            return self.snapshot()

        if payload is not None:
            self.phase = UP
            self.ownership = EXTERNAL
        else:
            self.phase = IDLE
            self.ownership = NONE
        return self.snapshot()

    def stop(self) -> dict:
        """SIGTERM, grace, SIGKILL — but only for a child this window spawned."""
        if not self.alive():
            self._detach()
            payload, _ = self._health(self.address)
            if payload is not None:
                self.phase = UP
                self.ownership = EXTERNAL
                self.reason = NOT_STARTED_HERE
                self.detail = (f'{self.address.listen()} is answered by a server this window '
                               'did not start (systemd, Docker or a terminal); it was left alone')
            else:
                self.phase = IDLE
                self.ownership = NONE
                self.reason = NOT_STARTED_HERE
                self.detail = 'this window is not running a server'
            return self._result(stopped=False)

        child = self.process
        pid = self.pid
        self.phase = STOPPING
        self.forced = False

        try:
            child.terminate()
        except (OSError, ProcessLookupError):
            pass                          # already gone: the wait below settles it

        if not self._wait_exit(child, self.grace):
            self.forced = True
            try:
                child.kill()
            except (OSError, ProcessLookupError):
                pass
            self._wait_exit(child, self.kill_wait)

        self._detach()
        self.phase = IDLE
        self.ownership = NONE
        self.reason = ''
        self.detail = ('the server did not exit within the grace period and was killed'
                       if self.forced else 'the server exited on SIGTERM')
        self.started_at = 0.0
        clear_handoff(self.config)
        return self._result(stopped=True, forced=self.forced, pid=pid)

    def _wait_exit(self, child, seconds: float) -> bool:
        until = self._clock() + seconds
        while self._clock() < until:
            try:
                if child.poll() is not None:
                    return True
            except OSError:
                return True
            self._sleep(self.interval)
        try:
            return child.poll() is not None
        except OSError:
            return True

    def close(self, keep_running: bool | None = None) -> dict:
        """The window is closing.

        Default: hand off — the child keeps running and the record says where it is. If the
        person chose `stop_on_close`, this is the one path that stops it on the way out. An
        external server is neither stopped nor claimed, whatever the preference says.
        """
        keep = (not stop_on_close(self.config)) if keep_running is None else bool(keep_running)
        if not self.alive():
            return {'action': 'nothing', 'stopped': False, 'handoff': {},
                    'detail': 'nothing this window started is running'}
        if keep:
            record = {'pid': self.pid, 'listen': self.address.listen(),
                      'launcher': self.launch.launcher, 'recorded_at': float(time.time())}
            problem = record_handoff(self.config, self.pid, self.launch, self.address.listen())
            # The record is on disk and the child is not signalled. This window stops
            # supervising it: the next window reads the record and treats the server as
            # external to itself, which is exactly what it is now.
            self.process = None
            self.pid = None
            self.phase = IDLE
            self.ownership = NONE
            if problem:
                return {'action': 'handoff', 'stopped': False, 'handoff': record,
                        'detail': problem, 'problem': True}
            return {'action': 'handoff', 'stopped': False, 'handoff': record,
                    'detail': f'pid {record["pid"]} keeps serving {self.address.listen()}'}
        result = self.stop()
        return {'action': 'stop', 'stopped': result['stopped'], 'handoff': {},
                'detail': result['detail'], 'forced': result['forced']}