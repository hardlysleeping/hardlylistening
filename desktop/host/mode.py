"""Two independent choices, not one mode switch.

A desktop window has to answer two questions, and they are not the same question:

  1. **Does this window run a local server?**  On or off.
  2. **Where does the window look?**  At this computer's local server, or at a remote one.

Four combinations exist, and all four are legitimate. `local` is the ordinary one; `remote-only`
(no local server, pointing at a remote one) is a *supported* way to use the window, not a
degraded one — a person whose server lives on a VPS has no reason to run a second copy. The two
awkward combinations are `local-behind-remote` (a local server running for other devices while
this window uses the remote) and `local-off-pointed-local` (nothing to talk to, which the window
says out loud rather than quietly re-pointing itself).

The rule that makes this a model rather than UI copy: **the two axes never move each other**.
Turning the local server off does not rewrite the target, and switching the target to remote does
not stop the server. ``with_local_server`` and ``with_target`` are the only mutators, and each
leaves the other axis byte-identical; `tests/test_host_mode.py` and
`tests/test_host_mode.cjs` pin that in Python and in JavaScript.

`desktop/host/resolve.py` answers *where* the local server is; this module answers *whether* one
runs here and *which* of the two addresses the window talks to. Nothing here starts a process,
opens a socket or writes a file.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


def _resolve_module():
    """`resolve.py` owns this install's paths; borrow it, never copy it.

    Loaded as a sibling so this file works when imported as `host.mode` *and* when it is loaded
    by path, as the tests do. The same shim is in `lifecycle.py` and `setup.py`.
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

#: The two axes' defaults. A first run is the ordinary install: one local server, used here.
DEFAULT_RUN_LOCAL_SERVER = True
DEFAULT_TARGET = 'local'

#: `local` means this computer's server, `remote` means an address another machine answers on.
TARGETS = ('local', 'remote')

#: `https` is preferred for a remote address; `http` is accepted and flagged, never refused:
#: a tailnet or a LAN address often has no certificate, and refusing it would be a policy the
#: card did not ask for.
SCHEMES = ('https', 'http')
PREFERRED_SCHEME = 'https'

#: Its own file in the resolver's state directory: `host.json` is the address the local server
#: lives at (HOST-005 writes it), `mode.json` is the two choices below. Keeping them apart means a
#: settings write cannot lose the mode choice, and vice versa.
CONFIG_NAME = resolve.CONFIG_NAME
MODE_FILE = 'mode.json'
MODE_VERSION = 1

#: Hosts that are this computer by definition. `0.0.0.0` and `::` are listen-anywhere bind
#: addresses: as a *remote* address they mean nothing, and as a local one they mean everywhere.
_LOOPBACK_NAMES = {'localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback'}
_ANY = {'0.0.0.0', '::', '', '*'}

#: What the four combinations are called. The ids are the contract with `desktop/ui/mode.js`
#: and with the tests in both languages; rename one and three files disagree.
LOCAL = 'local'
LOCAL_BEHIND_REMOTE = 'local-behind-remote'
LOCAL_OFF_POINTED_LOCAL = 'local-off-pointed-local'
REMOTE_ONLY = 'remote-only'


# --------------------------------------------------------------------------------------
# A remote origin
# --------------------------------------------------------------------------------------

def _machine_names() -> set[str]:
    """This computer's own names, without a DNS lookup that could block or leak.

    `gethostname`/`getfqdn` read local configuration; neither opens a socket. A failure is not
    worth reporting: the own-address rule still catches loopback and the local address itself.
    """
    names: set[str] = set()
    for getter in (socket.gethostname, socket.getfqdn):
        try:
            name = getter().strip().lower().rstrip('.')
        except OSError:
            continue
        if name:
            names.add(name)
            names.add(name.split('.')[0])
    return names


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_NAMES or host in _ANY:
        return True
    if host in ('::1', '0:0:0:0:0:0:0:1'):
        return True
    if host.startswith('127.'):
        parts = host.split('.')
        return len(parts) == 4 and all(part.isdigit() for part in parts)
    return False


def _is_own_address(host: str, local_host: str | None, machine_names) -> bool:
    if _is_loopback(host):
        return True
    if local_host:
        own = str(local_host).strip().strip('[]').lower().rstrip('.')
        if own and host == own:
            return True
    names = {str(name).strip().lower().rstrip('.') for name in (machine_names or ())}
    return host in names


@dataclass(frozen=True)
class Origin:
    """A validated remote address: scheme, host, optional port, and exactly one trailing slash."""

    scheme: str
    host: str
    port: int | None = None

    @property
    def secure(self) -> bool:
        return self.scheme == PREFERRED_SCHEME

    @property
    def host_for_url(self) -> str:
        return f'[{self.host}]' if ':' in self.host else self.host

    @property
    def url(self) -> str:
        """Canonical, and always ends in `/`: `https://host[:port]/`."""
        authority = self.host_for_url + (f':{self.port}' if self.port else '')
        return f'{self.scheme}://{authority}/'

    @property
    def warning(self) -> str:
        """Empty when secure. `http` is allowed, and the window says what it costs."""
        if self.secure:
            return ''
        return (f'This remote address is {self.scheme}, which is not encrypted. '
                f'{PREFERRED_SCHEME} is preferred.')

    def payload(self) -> dict:
        return {'scheme': self.scheme, 'host': self.host, 'port': self.port}


def _authority(authority: str) -> tuple[str, int | None]:
    if not authority:
        raise ValueError('a remote address needs a host')
    if authority.startswith('['):
        end = authority.find(']')
        if end == -1:
            raise ValueError('a bracketed IPv6 address is missing its closing bracket: '
                             f'{authority}')
        host, tail = authority[1:end], authority[end + 1:]
        if tail and not tail.startswith(':'):
            raise ValueError(f'{authority} is not an address this window can read')
        port = tail[1:]
    else:
        head, separator, tail = authority.rpartition(':')
        if separator:
            host, port = head, tail
        else:
            host, port = authority, ''
        if ':' in host:
            raise ValueError(f'a bare IPv6 address needs brackets: [{host}]')
    host = host.strip().lower().rstrip('.')
    if not host:
        raise ValueError('a remote address needs a host')
    if not port:
        return host, None
    try:
        number = int(port, 10)
    except ValueError:
        raise ValueError(f'a remote port is not a number: {port!r}') from None
    if not 0 < number < 65536:
        raise ValueError(f'a remote port is out of range: {number}')
    return host, number


def parse_origin(raw, local_host: str | None = None, machine_names=None) -> Origin:
    """Validate and normalise a remote origin, or raise `ValueError` saying what is wrong.

    The four rules, in the order a person hits them:

      * a **scheme is required** — `example.com` is refused with the fix written out, because a
        bare host does not say whether the window should speak TLS;
      * **https is preferred, http is allowed** — the returned `Origin.warning` says so;
      * **there is exactly one trailing slash** — accepted with none, with one or with several,
        and normalised to one (`Origin.url`);
      * **an address that is this computer is refused** — loopback, a listen-anywhere bind, the
        local server's own host or this machine's own name. A "remote" address that resolves to
        the machine the window runs on is a local server with extra steps.

    The origin is an *origin*: no path, no query string, no `#fragment`, no credentials.
    """
    text = str(raw if raw is not None else '').strip()
    if not text:
        raise ValueError('a remote address is required')
    marker = text.find('://')
    if marker == -1:
        raise ValueError('a remote address needs a scheme: '
                         f'write {PREFERRED_SCHEME}://{text}, not the bare host')
    scheme = text[:marker].strip().lower()
    if scheme not in SCHEMES:
        raise ValueError(f'{scheme or "(none)"} is not a scheme this window speaks: '
                         f'{PREFERRED_SCHEME} (preferred) or http')
    rest = text[marker + 3:]
    if '#' in rest or '?' in rest:
        raise ValueError('a remote address is an origin: no query string, no #fragment')
    cut = rest.find('/')
    authority = rest if cut == -1 else rest[:cut]
    path = '' if cut == -1 else rest[cut:]
    if '@' in authority:
        raise ValueError('a remote address carries no credentials')
    if path.rstrip('/'):
        raise ValueError(f'a remote address is an origin, not a path: {path}')

    host, port = _authority(authority)
    if _is_own_address(host, local_host, machine_names):
        raise ValueError(f'{host} is this computer: a remote address has to be one another '
                         'machine reaches. A server here is the local choice.')
    return Origin(scheme=scheme, host=host, port=port)


def check_origin(raw, local_host: str | None = None,
                 machine_names=None) -> tuple[Origin | None, str]:
    """`parse_origin` without the exception, for a window that has to show a message.

    Returns `(origin, '')` or `(None, problem)`, the same shape `resolve.read_config` uses.
    """
    try:
        return parse_origin(raw, local_host, machine_names), ''
    except ValueError as error:
        return None, str(error)


# --------------------------------------------------------------------------------------
# The four combinations
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Combination:
    """What one pair of answers permits, in one place.

    `start_enabled`/`stop_enabled` are the *choice-level* permissions: with the local server
    switched off there is nothing for Start or Stop to act on, and the switch is the action.
    The runtime gate on top of them lives in `desktop/ui/state.js`: a server that is already
    running, or one systemd/Docker started, still narrows Start and refuses Stop — this model
    never overrides ownership.

    `unreachable` is the sentence shown when the address the window talks to does not answer.
    `{address}` is replaced with the URL, or with "the remote address" when none is usable.
    """

    id: str
    run_local_server: bool
    target: str
    talk_to: str
    start_enabled: bool
    stop_enabled: bool
    summary: str
    unreachable: str
    needs_attention: bool = False
    #: Always False, including `remote-only`. Kept as a field so the claim is testable rather
    #: than implied: running no local server is a choice, not a fault to fix.
    degraded: bool = False


_COMBINATIONS = {
    (True, 'local'): Combination(
        id=LOCAL,
        run_local_server=True,
        target='local',
        talk_to='local',
        start_enabled=True,
        stop_enabled=True,
        summary='A local server on this computer, and this window uses it.',
        unreachable='The local server at {address} is not answering. Start it from this window.',
    ),
    (True, 'remote'): Combination(
        id=LOCAL_BEHIND_REMOTE,
        run_local_server=True,
        target='remote',
        talk_to='remote',
        start_enabled=True,
        stop_enabled=True,
        summary='A local server runs for other devices; this window uses the remote address.',
        unreachable='The remote server at {address} is not answering. The local server on this '
                    'computer keeps running; point the window at local to use it instead.',
    ),
    (False, 'local'): Combination(
        id=LOCAL_OFF_POINTED_LOCAL,
        run_local_server=False,
        target='local',
        talk_to='local',
        start_enabled=False,
        stop_enabled=False,
        summary='This window runs no local server, and it is still pointed at the local address.',
        unreachable='Nothing answers {address}: this window is not running a local server. '
                    'Switch the local server on, or point the window at a remote server.',
        needs_attention=True,
    ),
    (False, 'remote'): Combination(
        id=REMOTE_ONLY,
        run_local_server=False,
        target='remote',
        talk_to='remote',
        start_enabled=False,
        stop_enabled=False,
        summary='No local server. This window uses the remote address.',
        unreachable='The remote server at {address} is not answering. This window runs no local '
                    'server, and it does not need one: retry when the remote is back, or switch '
                    'the local server on.',
    ),
}


def combination(run_local_server, target) -> Combination:
    """The combination for one pair of answers. Both axes are arguments, never inferred."""
    if target not in TARGETS:
        raise ValueError(f'{target!r} is not a target this window understands')
    pair = (bool(run_local_server), target)
    try:
        return _COMBINATIONS[pair]
    except KeyError:                                                # pragma: no cover
        raise ValueError(f'no combination for {pair!r}') from None


# --------------------------------------------------------------------------------------
# The mode, and the two mutators that do not touch the other axis
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Mode:
    """The persisted answers: run a local server or not, and where the window looks.

    `problem` is a plain-language failure from the stored file — a bad target, an unusable
    remote address — and never a reason to rewrite the choices. A window that quietly turned
    "remote" into "local" because a stored URL was malformed would hide the one thing the
    person needs to fix.
    """

    run_local_server: bool = DEFAULT_RUN_LOCAL_SERVER
    target: str = DEFAULT_TARGET
    remote_origin: Origin | None = None
    problem: str = ''

    @property
    def combination(self) -> Combination:
        return combination(self.run_local_server, self.target)

    @property
    def talk_to(self) -> str:
        return self.combination.talk_to

    @property
    def needs_attention(self) -> bool:
        return self.combination.needs_attention

    def endpoint(self, local_url: str | None = None) -> str | None:
        """The URL the window talks to. `None` means "pointed at a remote with no address"."""
        if self.target == 'remote':
            return self.remote_origin.url if self.remote_origin is not None else None
        return local_url

    def unreachable_text(self, local_url: str | None = None) -> str:
        address = self.endpoint(local_url) or 'the remote address'
        return self.combination.unreachable.replace('{address}', address)

    def payload(self) -> dict:
        return {
            'version': MODE_VERSION,
            'run_local_server': self.run_local_server,
            'target': self.target,
            'remote_origin': self.remote_origin.url if self.remote_origin else None,
        }


def mode(run_local_server=DEFAULT_RUN_LOCAL_SERVER, target=DEFAULT_TARGET,
         remote_origin: Origin | None = None, problem: str = '') -> Mode:
    """Build a `Mode`, refusing an axis this window does not understand."""
    if target not in TARGETS:
        raise ValueError(f'{target!r} is not a target this window understands')
    return Mode(run_local_server=bool(run_local_server), target=target,
                remote_origin=remote_origin, problem=problem)


def with_local_server(current: Mode, on: bool) -> Mode:
    """Switch the local server off or on. **The target is copied, not re-derived.**"""
    return Mode(run_local_server=bool(on), target=current.target,
                remote_origin=current.remote_origin)


def with_target(current: Mode, target: str, remote_origin: Origin | None = None) -> Mode:
    """Point the window elsewhere. **The local server choice is copied, not re-derived.**

    Turning the target to remote does not stop a local server — it cannot, from here: this
    function returns a value and starts nothing. A remembered remote origin survives a switch
    back to local, so returning does not mean retyping the address.
    """
    if target not in TARGETS:
        raise ValueError(f'{target!r} is not a target this window understands')
    return Mode(run_local_server=current.run_local_server, target=target,
                remote_origin=remote_origin if remote_origin is not None else current.remote_origin)


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------

def mode_path(env: Mapping[str, str] | None = None) -> Path:
    """`<XDG_STATE_HOME>/hardlylistening-desktop/mode.json`, from `resolve.state_dir()`.
    `HARDLYLISTENING_DESKTOP_MODE` overrides it, as its sibling variable does for `host.json`.
    """
    env = os.environ if env is None else env
    declared = (env.get('HARDLYLISTENING_DESKTOP_MODE') or '').strip()
    if declared:
        return Path(declared).expanduser()
    return resolve.state_dir(env) / MODE_FILE


def read_mode(path: Path) -> tuple[dict, str]:
    """The stored object, plus a plain-language problem. A bad file is reported, not fatal."""
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


def dumps(current: Mode) -> str:
    """The file's text. Two spaces of indent: it is a file a person may open and fix."""
    return json.dumps(current.payload(), indent=2) + '\n'


def load(env: Mapping[str, str] | None = None, path: Path | None = None,
         local_host: str | None = None, machine_names=None) -> Mode:
    """Read the choices back, keeping every readable answer and explaining every unreadable one.

    The one asymmetry worth knowing: an unusable remote address does **not** flip the target to
    local. The target stays remote with no address, `problem` says why, and the window shows
    that. Falling back would look like it worked and talk to a server the person did not choose.
    """
    env = os.environ if env is None else env
    config = Path(path) if path is not None else mode_path(env)
    stored, problem = read_mode(config)
    problems = [problem] if problem else []

    version = stored.get('version')
    if version is not None and version != MODE_VERSION:
        problems.append(f'{config} was written for version {version}; this window reads '
                        f'{MODE_VERSION} and is using the defaults')
        stored = {}

    run_local = stored.get('run_local_server', DEFAULT_RUN_LOCAL_SERVER)
    if not isinstance(run_local, bool):
        problems.append(f'run_local_server in {config} is not true or false')
        run_local = DEFAULT_RUN_LOCAL_SERVER

    target = stored.get('target', DEFAULT_TARGET)
    if target not in TARGETS:
        problems.append(f'{target!r} is not a target this window understands')
        target = DEFAULT_TARGET

    origin = None
    raw_origin = stored.get('remote_origin')
    if raw_origin not in (None, ''):
        origin, origin_problem = check_origin(raw_origin, local_host, machine_names)
        if origin_problem:
            problems.append(f'the remote address in {config} is unusable: {origin_problem}')
    if target == 'remote' and origin is None:
        problems.append('this window is pointed at a remote server but has no usable address')

    return Mode(run_local_server=run_local, target=target, remote_origin=origin,
                problem='; '.join(problems))