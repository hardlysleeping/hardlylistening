"""What the window is allowed to change, and what each change actually does.

This is the *specification* of HOST-005, written where it can be tested. `src-tauri/src/
settings.rs` is its thin Rust rendering — a file read, a file write and one desktop entry — and
`ui/settings.js` is the panel that paints what this module describes. The crate cannot be
compiled on a machine without `webkit2gtk-4.1`, so the rules live here and the tests drive them.

Four questions, and the truth about each one:

  * **Local only, or the LAN?** The server has exactly one bind control: the address it is given.
    `config.host()` reads `HARDLYLISTENING_HOST`, `server.py --host` beats it, and `main()` builds
    `ThreadingHTTPServer((args.host, args.port))` from it. So "local only" is `127.0.0.1` and
    "the LAN" is `0.0.0.0`, and nothing else is invented here.
  * **The web UI.** The server serves `web/` on every start and reads no variable that turns it
    off. There is no switch, so this module does not pretend to have one: the setting is marked
    unsupported with the reason, and a patch that asks for it is refused rather than stored.
  * **OpenSubsonic.** Same shape: `server.py` dispatches every `/rest/` request to
    `listening.rest()` unconditionally, and `/api/v1/capabilities` reports `subsonic.enabled` as
    a constant `true`. Unsupported, and said so.
  * **Autostart.** The one thing here that is not the server's: an XDG autostart entry that runs
    the *launcher* (`hardlylistening-server run --host … --port …`), never the desktop window.
    It is written only when the choice is made explicitly — `save(..., autostart=True|False)` —
    and a patch can never carry it, because a setting that appears in a diff by itself is how an
    entry gets installed without anybody deciding to.

Two rules make the model honest rather than convenient:

  * **The file holds only what the server understands.** `save()` writes `host` and `port` into
    the shell's `host.json` (the file `resolve.py` already reads) and preserves keys it does not
    own. It never writes a flag the server would ignore — a stored `web_ui: false` would be a
    promise the next start breaks.
  * **The environment wins, and the window says so.** `resolve.address()` gives
    `HARDLYLISTENING_HOST`/`_PORT` precedence over the file, so writing `host` while the variable
    is declared would change nothing. That write is refused with the variable named, not
    performed and silently ignored.

`changed()` is the diff the panel shows before and after saving; `restart_required` is its
answer to "do I need to restart the server for this to be true".
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from . import resolve

#: The two named exposures, and the bind address each one really is. `0.0.0.0` is IPv4
#: any-interface; an IPv6 LAN needs `--host ::`, which the panel does not offer because the
#: server's default and every packaged install are IPv4. Documented, not silently widened.
LOCAL = 'local'
LAN = 'lan'

SCOPES: dict[str, dict[str, str]] = {
    LOCAL: {
        'host': resolve.DEFAULT_HOST,
        'label': 'This computer only',
        'detail': 'The server binds 127.0.0.1, so only this machine can connect.',
    },
    LAN: {
        'host': '0.0.0.0',
        'label': 'Anyone on this network',
        'detail': ('The server binds 0.0.0.0, so every interface answers. There is no '
                   'authentication for the LAN by itself — set HARDLYLISTENING_AUTH_REQUIRED '
                   'and a public origin before exposing it.'),
    },
}

#: Settings the server has no switch for. Each reason names the code that would have to change,
#: because "not supported" without a why is indistinguishable from a missing feature.
UNSUPPORTED: dict[str, str] = {
    'web_ui': (
        'The server always serves web/: server.py answers / from WEB_DIR and reads no variable '
        'that turns it off. Hiding it would be a reverse-proxy rule, not a server setting, so '
        'this window does not offer it.'
    ),
    'subsonic': (
        'OpenSubsonic is always on: server.py dispatches every /rest/ request to '
        'listening.rest() and /api/v1/capabilities reports subsonic.enabled as a constant true. '
        'No variable, flag or setting gates it, so a toggle here would be a lie.'
    ),
}

#: The XDG autostart entry: one file, named so it can be found and removed by name.
AUTOSTART_DIR = 'autostart'
AUTOSTART_FILE = 'hardlylistening-server.desktop'

#: Overrides the autostart directory. Same shape as `HARDLYLISTENING_DESKTOP_CONFIG`: it exists
#: so a test — or a portable install — never has to reach into a real `~/.config`.
AUTOSTART_ENV = 'HARDLYLISTENING_DESKTOP_AUTOSTART'

#: Fields a patch may carry. `scope` is accepted so the panel can post what it shows; it is
#: translated to `host` before anything is written.
_PATCHABLE = ('host', 'port', 'scope')

#: What each field is called in the window.
LABELS = {
    'host': 'Who can reach it',
    'port': 'Port',
    'web_ui': 'Web UI',
    'subsonic': 'OpenSubsonic',
    'autostart': 'Start at sign-in',
}


@dataclass(frozen=True)
class Settings:
    """The address the server will be started on — the only thing this shell stores."""

    host: str = resolve.DEFAULT_HOST
    port: int = resolve.DEFAULT_PORT

    @property
    def scope(self) -> str:
        """`local` or `lan`, derived from the address — never stored separately."""
        return scope_for_host(self.host)

    def address(self) -> resolve.Address:
        return resolve.Address(host=self.host, port=self.port)


@dataclass(frozen=True)
class Loaded:
    """Everything the panel needs to paint itself, and every problem worth showing."""

    settings: Settings
    autostart: bool
    locked: tuple[str, ...]
    problems: tuple[str, ...]
    config_path: Path
    autostart_path: Path


@dataclass(frozen=True)
class Saved:
    """What a save did: written here, refused there, and whether a restart is needed."""

    settings: Settings
    written: tuple[str, ...]
    refused: tuple[tuple[str, str], ...]
    autostart: str                      # 'written', 'removed' or 'unchanged'
    diff: dict
    config_path: Path
    backup: Path | None = None

    @property
    def saved(self) -> bool:
        """Whether anything left the file: the config, or the autostart entry, or both."""
        return bool(self.written) or self.autostart != 'unchanged'

    @property
    def restart_required(self) -> bool:
        return bool(self.diff.get('restart_required'))

    def to_dict(self) -> dict:
        return {
            'saved': self.saved,
            'settings': {'host': self.settings.host, 'port': self.settings.port,
                         'scope': self.settings.scope},
            'written': list(self.written),
            'refused': [{'field': field, 'reason': reason} for field, reason in self.refused],
            'autostart': self.autostart,
            'restart_required': self.restart_required,
            'diff': self.diff,
            'config_path': str(self.config_path),
            'backup': str(self.backup) if self.backup else None,
        }


def scope_for_host(host: str) -> str:
    """Which exposure an address is — `resolve.Address.scope` is the one that decides."""
    return LOCAL if resolve.Address(host=host).scope == 'loopback' else LAN


def host_for_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise ValueError(f'unknown exposure: {scope!r}')
    return SCOPES[scope]['host']


def autostart_path(env: Mapping[str, str] | None = None) -> Path:
    """`<XDG_CONFIG_HOME>/autostart/hardlylistening-server.desktop`, overridable for tests."""
    env = os.environ if env is None else env
    declared = (env.get(AUTOSTART_ENV) or '').strip()
    if declared:
        return Path(declared).expanduser()
    base = (env.get('XDG_CONFIG_HOME') or '').strip()
    root = Path(base).expanduser() if base else Path.home() / '.config'
    return root / AUTOSTART_DIR / AUTOSTART_FILE


def _locked(env: Mapping[str, str]) -> tuple[str, ...]:
    """Fields an environment variable pins. Those writes cannot take effect, so they are refused."""
    pairs = (('host', 'HARDLYLISTENING_HOST'), ('port', 'HARDLYLISTENING_PORT'))
    return tuple(field for field, name in pairs if (env.get(name) or '').strip())


def load(env: Mapping[str, str] | None = None, path: Path | None = None,
         autostart: Path | None = None) -> Loaded:
    """The effective settings, where they came from, and what could not be honoured.

    A malformed file or an out-of-range port is reported and the defaults are shown: a window
    that refuses to open because a JSON file lost a brace is worse than one that says which file
    it ignored. `resolve.address()` is still the only resolver — this does not re-decide.
    """
    env = os.environ if env is None else env
    config_path = Path(path) if path is not None else resolve.config_path(env)
    entry = Path(autostart) if autostart is not None else autostart_path(env)
    problems: list[str] = []
    try:
        address = resolve.address(env, config_path)
    except ValueError as error:
        address = resolve.Address()
        problems.append(f'{error} — showing the defaults instead.')
    _, config_problem = resolve.read_config(config_path)
    if config_problem:
        problems.append(config_problem)
    locked = _locked(env)
    if locked:
        variables = ', '.join('HARDLYLISTENING_' + field.upper() for field in locked)
        problems.append(
            f'{variables} is set for this window and wins over the config file, so the '
            'exposure and port are fixed for this session.'
        )
    return Loaded(
        settings=Settings(host=address.host, port=address.port),
        autostart=entry.is_file(),
        locked=locked,
        problems=tuple(problems),
        config_path=config_path,
        autostart_path=entry,
    )


def _clean_host(value) -> str:
    host = str(value).strip()
    if not host:
        raise ValueError('a bind address is required')
    if any(ch.isspace() for ch in host):
        raise ValueError(f'a bind address cannot contain spaces: {host!r}')
    return host


def _clean_port(value) -> int:
    # The resolver's own rule, called rather than copied: two range checks that disagree is
    # exactly the kind of drift this module exists to avoid.
    return resolve._port(value, 'the port')


def changed(before: Settings, after: Settings) -> dict:
    """What a save would change, and whether the running server has to be restarted for it.

    The address is read by the server once, at `main()`, and handed to
    `ThreadingHTTPServer`. A window that saved a new bind and said nothing would leave a person
    polling an address nothing listens on — so every address change is a restart.
    """
    changes = []
    for field in ('host', 'port'):
        old, new = getattr(before, field), getattr(after, field)
        if old != new:
            changes.append({'field': field, 'label': LABELS[field], 'from': old, 'to': new,
                            'restart': True})
    restart = tuple(change['field'] for change in changes if change['restart'])
    if not changes:
        summary = 'Nothing to apply.'
    elif restart:
        summary = (f'Saved. Restart the server to listen on '
                   f'{after.address().listen()} — it keeps answering on '
                   f'{before.address().listen()} until then.')
    else:
        summary = 'Saved.'
    return {
        'changes': changes,
        'restart': list(restart),
        'restart_required': bool(restart),
        'before': before.address().listen(),
        'after': after.address().listen(),
        'summary': summary,
    }


def describe(loaded: Loaded) -> dict:
    """The panel's content as rows, so the window paints a model instead of inventing copy.

    `supported: False` rows exist to be explained, not toggled. A disabled switch with no reason
    reads as broken; a row that says which code would need to change reads as a fact.
    """
    settings = loaded.settings
    rows = [
        {
            'key': 'host',
            'label': LABELS['host'],
            'value': SCOPES[settings.scope]['label'],
            'detail': SCOPES[settings.scope]['detail'],
            'editable': True,
            'supported': True,
            'locked': 'host' in loaded.locked,
            'options': [{'value': name, 'label': SCOPES[name]['label']} for name in (LOCAL, LAN)],
        },
        {
            'key': 'port',
            'label': LABELS['port'],
            'value': settings.port,
            'detail': 'The address the window polls and the launcher is told to bind.',
            'editable': False,      # the panel shows it; the file holds it
            'supported': True,
            'locked': 'port' in loaded.locked,
        },
        {
            'key': 'web_ui',
            'label': LABELS['web_ui'],
            'value': 'Always served',
            'detail': UNSUPPORTED['web_ui'],
            'editable': False,
            'supported': False,
            'locked': True,
        },
        {
            'key': 'subsonic',
            'label': LABELS['subsonic'],
            'value': 'Always on',
            'detail': UNSUPPORTED['subsonic'],
            'editable': False,
            'supported': False,
            'locked': True,
        },
        {
            'key': 'autostart',
            'label': LABELS['autostart'],
            'value': 'On' if loaded.autostart else 'Off',
            'detail': ('Runs the server launcher at sign-in, not this window. Logs are not '
                       'captured: an autostarted server prints to nothing.'),
            'editable': True,
            'supported': True,
            'locked': False,
            'options': [{'value': False, 'label': 'Off'}, {'value': True, 'label': 'On'}],
        },
    ]
    return {
        'rows': rows,
        'problems': list(loaded.problems),
        'config_path': str(loaded.config_path),
        'autostart_path': str(loaded.autostart_path),
    }


def save(loaded: Loaded, patch: Mapping | None = None, *, autostart: bool | None = None,
         launcher: str | Path | None = None, path: Path | None = None,
         env: Mapping[str, str] | None = None) -> Saved:
    """Apply a patch of `host`/`port`/`scope`, and write the autostart entry only if asked.

    `autostart=None` — the default — is "no choice was made": the entry is neither written nor
    removed, whatever the file currently says. A patch cannot carry `autostart` at all, so no
    code path installs one as a side effect of changing the address.
    """
    env = os.environ if env is None else env
    patch = dict(patch or {})
    config_path = Path(path) if path is not None else loaded.config_path
    entry_path = loaded.autostart_path
    refused: list[tuple[str, str]] = []
    wanted = loaded.settings

    for field in patch:
        if field in ('web_ui', 'subsonic'):
            refused.append((field, UNSUPPORTED[field]))
        elif field == 'autostart':
            refused.append((field, 'autostart is only changed by an explicit choice, '
                                   'never by a patch'))
        elif field not in _PATCHABLE:
            refused.append((field, 'not a setting this shell knows'))

    if 'scope' in patch and 'host' not in patch:
        wanted = replace(wanted, host=host_for_scope(str(patch['scope'])))
    if 'host' in patch:
        wanted = replace(wanted, host=_clean_host(patch['host']))
    if 'port' in patch:
        wanted = replace(wanted, port=_clean_port(patch['port']))

    for field in ('host', 'port'):
        value = getattr(wanted, field)
        if value != getattr(loaded.settings, field) and field in loaded.locked:
            variable = 'HARDLYLISTENING_' + field.upper()
            refused.append((field, f'{variable} is set for this window and wins over the config '
                                   f'file, so {field} cannot be changed here'))
            wanted = replace(wanted, **{field: getattr(loaded.settings, field)})

    written: tuple[str, ...] = ()
    backup: Path | None = None
    if wanted != loaded.settings:
        stored, _ = resolve.read_config(config_path)
        backup = _backup_unreadable(config_path)
        written = tuple(field for field in ('host', 'port')
                        if getattr(wanted, field) != getattr(loaded.settings, field))
        _write_config(config_path, stored, wanted)

    entry = 'unchanged'
    if autostart is not None:
        entry = write_autostart(entry_path, bool(autostart), wanted, launcher)

    return Saved(
        settings=wanted,
        written=written,
        refused=tuple(refused),
        autostart=entry,
        diff=changed(loaded.settings, wanted),
        config_path=config_path,
        backup=backup,
    )


def _backup_unreadable(path: Path) -> Path | None:
    """Keep a copy of a file this module could not parse before replacing it.

    `resolve.read_config` reports and ignores a malformed file, which is right for opening a
    window and wrong for overwriting one: whatever a person hand-edited is theirs, so it is
    moved aside as `<name>.bak` rather than destroyed.
    """
    if not path.is_file():
        return None
    _, problem = resolve.read_config(path)
    if not problem:
        return None
    backup = path.with_name(path.name + '.bak')
    backup.write_bytes(path.read_bytes())
    return backup


def _write_config(path: Path, stored: Mapping, settings: Settings) -> None:
    """Write `host`/`port`, preserving every key this shell does not own."""
    data = dict(stored) if isinstance(stored, Mapping) else {}
    data['host'] = settings.host
    data['port'] = settings.port
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True) + '\n'
    temp = path.with_name(path.name + '.tmp')
    try:
        temp.write_text(text)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def autostart_entry(settings: Settings, launcher: str | Path) -> str:
    """The `.desktop` file: the *server* launcher, with the address as explicit flags.

    The flags are the same `argv()` the window spawns with, so an autostarted server binds the
    address the window polls. `Terminal=false` is honest about the cost: an autostarted server
    has no journal, no terminal and therefore no visible log.
    """
    if launcher is None or not str(launcher).strip():
        raise ValueError('an autostart entry needs the server launcher path')
    command = ' '.join([_exec_quote(str(launcher)), 'run'] + settings.address().argv())
    return (
        '[Desktop Entry]\n'
        'Type=Application\n'
        'Version=1.0\n'
        'Name=HardlyListening server\n'
        'Comment=Starts the HardlyListening server at sign-in, not the desktop window\n'
        f'Exec={command}\n'
        'Terminal=false\n'
        'NoDisplay=false\n'
        'X-GNOME-Autostart-enabled=true\n'
    )


def _exec_quote(value: str) -> str:
    """Quote for a `Desktop Entry` Exec key: `%` is the field code, and quoting needs backslashes."""
    text = value.replace('%', '%%')
    if any(ch in text for ch in ' \t"\'\\$`'):
        for ch in ('\\', '"', '$', '`'):
            text = text.replace(ch, '\\' + ch)
        text = f'"{text}"'
    return text


def write_autostart(path: Path, enabled: bool, settings: Settings,
                    launcher: str | Path | None) -> str:
    """Install or remove the one entry this module names, and nothing else."""
    path = Path(path)
    if not enabled:
        if path.is_file():
            path.unlink()       # the file this module just named, by an explicit choice
            return 'removed'
        return 'unchanged'
    text = autostart_entry(settings, launcher)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    try:
        temp.write_text(text)
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return 'written'