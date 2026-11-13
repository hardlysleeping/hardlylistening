"""How the desktop shell finds the server, and which address it talks to.

This module is the *specification* of the shell's two path decisions, written where it can be
tested: the Tauri crate cannot be compiled on a machine without `webkit2gtk-4.1`, so
`src-tauri/src/host.rs` is the Rust rendering of these rules and this file is what proves them.

Two rules, and nothing else:

  * **The server is the launcher, not a copy of it.** `bin/hardlylistening-server` is the
    canonical entry point (`run`, `check`, `version`) and the same artefact systemd and Docker
    start. The desktop bundles *that file*, renamed only to add Tauri's target-triple suffix,
    which is a packaging name and never a fork. `HARDLYLISTENING_SERVER` points the shell at
    another one for a checkout that is not the parent of `desktop/`.
  * **The shell needs an address to poll; it is not a second source of truth.** The address
    comes from the environment the packaged installs already use (`HARDLYLISTENING_HOST`,
    `HARDLYLISTENING_PORT`), then from the shell's own config file, then from the server's
    documented defaults. Whatever is resolved is passed to the launcher *explicitly* as
    `--host`/`--port`, so one address is polled and one address is served.

Nothing here starts a process, opens a socket or writes a file: those are the crate's job and
they are the parts this machine cannot run. Everything decision-shaped lives here.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

#: The packaged entry point, relative to the application root. Same name systemd's
#: `ExecStart` and the Dockerfile's `CMD` use.
LAUNCHER_NAME = 'hardlylistening-server'
LAUNCHER_RELATIVE = Path('bin') / LAUNCHER_NAME

#: The server's documented defaults (`config.py`). Repeated rather than imported: the desktop
#: imports no server module, and `tests/test_desktop_host.py` enforces that. The server still
#: wins, because these values are handed to it as flags.
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8470

#: Where the shell keeps the one setting a person can choose (host/port). XDG state, not
#: config: it is a window's memory, and losing it costs one default.
CONFIG_NAME = 'hardlylistening-desktop'
CONFIG_FILE = 'host.json'

#: Hosts that mean "this machine". `0.0.0.0` and `::` are listen-anywhere addresses: they are
#: not reachable as written, so the probe (and the browser) uses loopback.
_LOOPBACK = {'127.0.0.1', 'localhost', '::1', '[::1]'}
_ANY = {'0.0.0.0', '', '*', '::', '[::]'}


def desktop_dir() -> Path:
    """`desktop/` — the shell's own root, wherever the checkout happens to be."""
    return Path(__file__).resolve().parents[1]


def app_dir(desktop: Path | None = None) -> Path:
    """The application root that holds `bin/`, `server.py` and `web/`."""
    return (Path(desktop) if desktop is not None else desktop_dir()).resolve().parent


def launcher(env: Mapping[str, str] | None = None, app: Path | None = None) -> Path:
    """The entry point the shell will spawn, and the file the sidecar is bundled from."""
    env = os.environ if env is None else env
    declared = (env.get('HARDLYLISTENING_SERVER') or '').strip()
    if declared:
        return Path(declared).expanduser()
    return app_dir(app) / LAUNCHER_RELATIVE


def sidecar_filename(triple: str) -> str:
    """Tauri's `externalBin` convention: `name` in the config, `name-<triple>` on disk.

    The suffix is not decoration. Tauri resolves a sidecar by asking the build host for its
    target triple, so a bundle built for another architecture keeps its own binary; a staged
    copy without the suffix is a file the bundle will not ship.
    """
    if not triple:
        raise ValueError('a target triple is required for a sidecar filename')
    return f'{LAUNCHER_NAME}-{triple}'


def target_triple(platform: str | None = None, machine: str | None = None) -> str | None:
    """A best-effort Rust target triple for the common desktops, or `None`.

    Used only as a fallback by `tools/stage_sidecar.py`; `rustc -vV` is asked first, because a
    guessed triple that disagrees with the compiler produces a bundle whose sidecar is silently
    absent.
    """
    import sys

    platform = sys.platform if platform is None else platform
    machine = (machine or os.uname().machine).lower() if hasattr(os, 'uname') else (machine or '')
    arch = {'x86_64': 'x86_64', 'amd64': 'x86_64',
            'aarch64': 'aarch64', 'arm64': 'aarch64'}.get(machine)
    if arch is None:
        return None
    if platform.startswith('linux'):
        return f'{arch}-unknown-linux-gnu'
    if platform == 'darwin':
        return f'{arch}-apple-darwin'
    if platform.startswith('win'):
        return f'{arch}-pc-windows-msvc'
    return None


@dataclass(frozen=True)
class Address:
    """Where the shell looks for the server, and what it hands to the launcher."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    #: 'environment', 'desktop-config' or 'default' — surfaced in the window so a surprising
    #: address can be explained without opening a terminal.
    source: str = 'default'

    @property
    def probe_host(self) -> str:
        """The host a *client* connects to: a listen-anywhere bind is reached on loopback."""
        if self.host in _ANY:
            return '::1' if self.host in ('::', '[::]') else '127.0.0.1'
        return self.host

    @property
    def scope(self) -> str:
        host = self.host.strip('[]')
        if host in _LOOPBACK:
            return 'loopback'
        if self.host in _ANY:
            return 'any'
        return 'lan' if not _is_loopback_v6(host) else 'loopback'

    def host_for_url(self) -> str:
        host = self.probe_host
        if ':' in host and not host.startswith('['):
            return f'[{host}]'
        return host

    def url(self) -> str:
        return f'http://{self.host_for_url()}:{self.port}'

    def api(self, path: str = '') -> str:
        return self.url() + (path if path.startswith('/') else '/' + path.lstrip('/'))

    def listen(self) -> str:
        """`host:port` as the launcher will print it — for the window's status line."""
        host = self.host
        if ':' in host and not host.startswith('['):
            host = f'[{host}]'
        return f'{host}:{self.port}'

    def argv(self) -> list[str]:
        """The explicit flags, so the server cannot resolve a different address than we poll."""
        return ['--host', self.host or DEFAULT_HOST, '--port', str(self.port)]


def _is_loopback_v6(host: str) -> bool:
    return host in ('::1', '0:0:0:0:0:0:0:1')


def config_path(env: Mapping[str, str] | None = None) -> Path:
    """Where the shell remembers host/port. `HARDLYLISTENING_DESKTOP_CONFIG` overrides it."""
    env = os.environ if env is None else env
    declared = (env.get('HARDLYLISTENING_DESKTOP_CONFIG') or '').strip()
    if declared:
        return Path(declared).expanduser()
    state = (env.get('XDG_STATE_HOME') or '').strip()
    base = Path(state).expanduser() if state else Path.home() / '.local' / 'state'
    return base / CONFIG_NAME / CONFIG_FILE


def read_config(path: Path) -> tuple[dict, str]:
    """The shell's config file, plus a plain-language problem if it could not be used.

    A malformed file is reported and ignored rather than fatal: a window that will not open
    because a JSON file lost a brace is worse than a window that says which file it ignored.
    """
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return {}, ''
    except OSError as error:
        return {}, f'{path} could not be read: {error.strerror or error}'
    try:
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError) as error:
        return {}, f'{path} is not JSON: {error}'
    if not isinstance(data, dict):
        return {}, f'{path} does not hold an object'
    return data, ''


def _port(value, origin: str) -> int:
    try:
        port = int(str(value).strip(), 10)
    except (TypeError, ValueError):
        raise ValueError(f'{origin} is not a number: {value!r}') from None
    if not 0 < port < 65536:
        raise ValueError(f'{origin} is out of range: {port}')
    return port


def address(env: Mapping[str, str] | None = None, path: Path | None = None) -> Address:
    """Resolve the address: environment, then the shell's config, then the defaults.

    A typo raises instead of falling back — starting the server somewhere the operator did not
    ask for is worse than refusing, which is the same rule `config.port()` follows.
    """
    env = os.environ if env is None else env
    stored, _ = read_config(Path(path) if path is not None else config_path(env))

    env_host = (env.get('HARDLYLISTENING_HOST') or '').strip()
    env_port = (env.get('HARDLYLISTENING_PORT') or '').strip()
    if env_host or env_port:
        return Address(
            host=env_host or str(stored.get('host') or DEFAULT_HOST),
            port=_port(env_port, 'HARDLYLISTENING_PORT') if env_port
            else _stored_port(stored),
            source='environment',
        )
    if isinstance(stored.get('host'), str) or 'port' in stored:
        return Address(host=str(stored.get('host') or DEFAULT_HOST).strip() or DEFAULT_HOST,
                       port=_stored_port(stored), source='desktop-config')
    return Address()


def _stored_port(stored: Mapping) -> int:
    if 'port' not in stored:
        return DEFAULT_PORT
    return _port(stored.get('port'), 'the desktop config port')


def resolve(env: Mapping[str, str] | None = None, app: Path | None = None,
            path: Path | None = None) -> dict:
    """Everything the window needs on startup, in one call.

    Returned rather than cached so the tests can ask about a fake environment and a fake
    checkout without a process, a display or a socket.
    """
    env = os.environ if env is None else env
    config = Path(path) if path is not None else config_path(env)
    server = launcher(env, app)
    stored, problem = read_config(config)
    return {
        'app_dir': str(app_dir(app)),
        'launcher': str(server),
        'launcher_exists': server.is_file(),
        'launcher_executable': server.is_file() and os.access(server, os.X_OK),
        'address': address(env, config),
        'config_path': str(config),
        'config_problem': problem,
        'python_override': (env.get('HARDLYLISTENING_PYTHON') or '').strip(),
    }