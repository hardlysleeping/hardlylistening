"""What the window is allowed to change, and what each change actually does.

This is the *specification* of HOST-005, written where it can be tested. `src-tauri/src/
settings.rs` is its thin Rust rendering — a file read, a file write and one desktop entry — and
`ui/settings.js` is the panel that paints what this module describes. The crate cannot be
compiled on a machine without `webkit2gtk-4.1`, so the rules live here and the tests drive them.

Five questions, and the truth about each one:

  * **Local only, or the LAN?** The server has exactly one bind control: the address it is given.
    `config.host()` reads `HARDLYLISTENING_HOST`, `server.py --host` beats it, and `main()` builds
    `ThreadingHTTPServer((args.host, args.port))` from it. So "local only" is `127.0.0.1` and
    "the LAN" is `0.0.0.0`, and nothing else is invented here.
  * **The web UI.** `server.py` serves `web/` unless it is started with `--no-web-ui`;
    `config.web_ui()` reads `HARDLYLISTENING_WEB_UI` as the default. The setting is real: off
    means every page and asset from `web/` answers 404 with a sentence, while the API keeps
    working. It is read at boot, so a change needs a restart.
  * **OpenSubsonic.** `--no-subsonic` makes `/rest/*` answer the Subsonic error envelope instead
    of dispatching to `listening.rest()`, and `/api/v1/capabilities` reports the real state.
    Also read at boot.
  * **Autostart.** The one thing here that is not the server's: an XDG autostart entry that runs
    the *launcher* (`hardlylistening-server run --host … --port …`), never the desktop window.
    It is written only when the choice is made explicitly — `save(..., autostart=True|False)` —
    and a patch can never carry it, because a setting that appears in a diff by itself is how an
    entry gets installed without anybody deciding to.

Two rules make the model honest rather than convenient:

  * **The file holds only what the server understands.** `save()` writes `host`, `port`, `web_ui`
    and `subsonic` into the shell's `host.json` (the file `resolve.py` already reads) and
    preserves keys it does not own. Every key it writes is one the server reads — a stored value
    the next start ignored would be a promise the server breaks.
  * **The environment wins, and the window says so.** `resolve.address()` and
    `resolve.features()` give `HARDLYLISTENING_HOST`/`_PORT`/`_WEB_UI`/`_SUBSONIC` precedence
    over the file, so writing one of those keys while its variable is declared would change
    nothing. That write is refused with the variable named, not performed and silently ignored.

`changed()` is the diff the panel shows before and after saving; `restart_required` is its
answer to "do I need to restart the server for this to be true". All four stored settings are
read once, at boot, so all four are restarts.
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

#: What each switch means, in the panel's words. Both are read once at boot, so both are a
#: restart — and both keep the API working, which is the part a person needs to know.
SWITCHES: dict[str, str] = {
    'web_ui': (
        'On: every page and asset in web/ is served, and the window opens it. Off: those '
        'requests answer 404 with a sentence; /api, /rest/ and the health probe keep working, '
        'so a headless server stays usable. Read at boot.'
    ),
    'subsonic': (
        'On: /rest/* answers the Subsonic protocol. Off: it answers a Subsonic error envelope '
        'and nothing else changes — the web UI and /api are unaffected. Read at boot.'
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
_PATCHABLE = ('host', 'port', 'scope', 'web_ui', 'subsonic')

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
    """Everything the shell stores about the server it starts.

    The four values are the four things `server.py` reads at boot: the address it binds and the
    two surfaces it serves. Nothing here is a copy of a server rule — `resolve` is still the
    resolver, and this is the shape a diff and a file write need.
    """

    host: str = resolve.DEFAULT_HOST
    port: int = resolve.DEFAULT_PORT
    web_ui: bool = True
    subsonic: bool = True

    @property
    def scope(self) -> str:
        """`local` or `lan`, derived from the address — never stored separately."""
        return scope_for_host(self.host)

    def address(self) -> resolve.Address:
        return resolve.Address(host=self.host, port=self.port)

    def features(self) -> resolve.Features:
        return resolve.Features(web_ui=self.web_ui, subsonic=self.subsonic)

    def flags(self) -> list[str]:
        """The `--no-…` flags for what is off: the same argv the window spawns with."""
        return self.features().argv()


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
                         'scope': self.settings.scope,
                         'web_ui': self.settings.web_ui,
                         'subsonic': self.settings.subsonic},
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
    pairs = (('host', 'HARDLYLISTENING_HOST'), ('port', 'HARDLYLISTENING_PORT'),
             ('web_ui', 'HARDLYLISTENING_WEB_UI'), ('subsonic', 'HARDLYLISTENING_SUBSONIC'))
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
    try:
        switches = resolve.features(env, config_path)
    except ValueError as error:
        switches = resolve.Features()
        problems.append(f'{error} — showing the defaults instead.')
    _, config_problem = resolve.read_config(config_path)
    if config_problem:
        problems.append(config_problem)
    locked = _locked(env)
    if locked:
        variables = ', '.join('HARDLYLISTENING_' + field.upper() for field in locked)
        problems.append(
            f'{variables} is set for this window and wins over the config file, so '
            'those settings are fixed for this session.'
        )
    return Loaded(
        settings=Settings(host=address.host, port=address.port,
                          web_ui=switches.web_ui, subsonic=switches.subsonic),
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

    All four stored settings are read once, in `main()`, and used for the life of the process:
    the address is handed to `ThreadingHTTPServer` and the two switches become the `FEATURES`
    it serves from. A window that saved a change and said nothing would leave a person polling
    an address nothing listens on, or looking for pages that now 404 — so every change here is
    a restart.
    """
    changes = []
    for field in ('host', 'port', 'web_ui', 'subsonic'):
        old, new = getattr(before, field), getattr(after, field)
        if old != new:
            changes.append({'field': field, 'label': LABELS[field], 'from': old, 'to': new,
                            'restart': True})
    restart = tuple(change['field'] for change in changes if change['restart'])
    if not changes:
        summary = 'Nothing to apply.'
    elif 'host' in restart or 'port' in restart:
        summary = (f'Saved. Restart the server to listen on '
                   f'{after.address().listen()} — it keeps answering on '
                   f'{before.address().listen()} until then.')
    else:
        summary = 'Saved. Restart the server for the new switches to take effect.'
    return {
        'changes': changes,
        'restart': list(restart),
        'restart_required': bool(restart),
        'before': before.address().listen(),
        'after': after.address().listen(),
        'summary': summary,
    }


def switch_row(settings: Settings, loaded: Loaded, key: str) -> dict:
    """One real on/off switch, painted from the model rather than from a fixed sentence."""
    on = getattr(settings, key)
    return {
        'key': key,
        'label': LABELS[key],
        'value': 'On' if on else 'Off',
        'detail': SWITCHES[key],
        'editable': True,
        'supported': True,
        'locked': key in loaded.locked,
        'options': [{'value': False, 'label': 'Off'}, {'value': True, 'label': 'On'}],
    }


def describe(loaded: Loaded) -> dict:
    """The panel's content as rows, so the window paints a model instead of inventing copy.

    Every row here is a real control. `supported` stays in the shape because the panel and its
    tests read it, but nothing is `False` any more: the card that added this model's two
    unsupported rows was closed by giving the server the switches, not by softening the copy.
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
        switch_row(settings, loaded, 'web_ui'),
        switch_row(settings, loaded, 'subsonic'),
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


def _clean_flag(value, field: str) -> bool:
    """A switch is a JSON boolean. `"false"` is refused rather than guessed: string truthiness
    is how a "false" ends up stored as on."""
    if not isinstance(value, bool):
        raise ValueError(f'{field} is true or false, not {value!r}')
    return value


def save(loaded: Loaded, patch: Mapping | None = None, *, autostart: bool | None = None,
         launcher: str | Path | None = None, path: Path | None = None,
         env: Mapping[str, str] | None = None) -> Saved:
    """Apply a patch of `host`/`port`/`scope`/`web_ui`/`subsonic`, and the autostart entry only
    when it is asked for explicitly.

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
        if field == 'autostart':
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
    for field in ('web_ui', 'subsonic'):
        if field in patch:
            wanted = replace(wanted, **{field: _clean_flag(patch[field], field)})

    for field in ('host', 'port', 'web_ui', 'subsonic'):
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
        written = tuple(field for field in ('host', 'port', 'web_ui', 'subsonic')
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
    """Write the four settings, preserving every key this shell does not own."""
    data = dict(stored) if isinstance(stored, Mapping) else {}
    data['host'] = settings.host
    data['port'] = settings.port
    data['web_ui'] = settings.web_ui
    data['subsonic'] = settings.subsonic
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

    The flags are the same `argv()` the window spawns with — the address it binds and the
    `--no-…` switches for what is off — so an autostarted server serves what the window shows.
    `Terminal=false` is honest about the cost: an autostarted server has no journal, no terminal
    and therefore no visible log.
    """
    if launcher is None or not str(launcher).strip():
        raise ValueError('an autostart entry needs the server launcher path')
    command = ' '.join([_exec_quote(str(launcher)), 'run'] + settings.address().argv()
                       + settings.flags())
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