"""The first-run wizard's decisions: four answers, one small file, two commands.

HOST-002. A person who has just installed the window is asked for the four things a
HardlyListening install cannot guess: where the music is, where the database and caches should
live, what this server is called, and who its administrator is. Everything that is a *rule* is
here, where it can be run: the Tauri crate cannot be compiled on the machine this was written on,
so `src-tauri/src/wizard.rs` is the Rust rendering of this file and this file is what proves it.

Three constraints shaped it:

  * **The server owns the account.** The wizard never writes SQL. The administrator is created by
    running the server's own `Security.create_user` and `Security.set_password` in a server
    process (`ADMIN_SCRIPT` below), so the schema, the email rule, the scrypt parameters and the
    "that email is already in use" refusal are the server's, not a second copy of them. The
    password travels on that child's stdin: it is never an argument, never in the state file and
    never in a log.
  * **The state file is the resume point.** Every completed step is written to
    `<XDG_STATE_HOME>/hardlylistening-desktop/setup.json`, beside the shell's `host.json`. A
    second launch with all four steps done shows the shell; anything less reopens the wizard on
    the first unfinished step. Losing the file costs four answers, not a database.
  * **The server still runs headless.** Nothing here is imported by the server, and the only
    things handed to it are the flags and environment variables the packaged installs already
    use (`HARDLYLISTENING_DATA_DIR`, `HARDLYLISTENING_DB`, `HARDLYLISTENING_MUSIC_DIR`), so a
    wizard-configured install is reproducible by hand in systemd or Docker.

`desktop/host/__init__.py` says the package must be deletable without the headless install
noticing; this module keeps that true by importing nothing but the standard library.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

#: The state file's own schema version. A file this module did not write is reported, not guessed
#: at, and a newer version is left alone rather than silently downgraded.
VERSION = 1

#: The four steps, in the order the wizard asks them. The order is not decoration: the data
#: folder is where the account is written, so it comes before the account.
STEPS = ('music', 'data', 'name', 'admin')

#: The field each step completes. A step is done when its field holds a validated value.
STEP_FIELDS = {
    'music': ('music_dir',),
    'data': ('data_dir',),
    'name': ('server_name',),
    'admin': ('admin_email',),
}

#: Every field the wizard keeps. The password is deliberately absent: it is passed through and
#: forgotten, so a lost laptop loses a login, not a credential.
FIELDS = ('music_dir', 'data_dir', 'server_name', 'admin_email')

#: The server's own limits, repeated here because the desktop imports no server module and
#: because the window has to answer before a server exists. `tests/test_host_wizard.py` compares
#: all four against `security.Security`, which is the copy that actually enforces them.
PASSWORD_MIN = 10
PASSWORD_MAX = 1024
EMAIL_MAX = 254
NAME_MAX = 64

DEFAULT_SERVER_NAME = 'HardlyListening'

#: Relative to the person's home, and both mirror a server default: `db.DEFAULTS['root']` for the
#: media root, and the XDG data layout for an install that is not a checkout.
MUSIC_SUFFIX = 'Music/HardlyListening'
XDG_DATA_SUFFIX = '.local/share'
DATA_LEAF = 'hardlylistening'

#: The shell's state directory. The same name `resolve.CONFIG_NAME` uses, so `setup.json` and
#: `host.json` sit beside each other; the test compares the two constants rather than trusting it.
CONFIG_NAME = 'hardlylistening-desktop'
SETUP_FILE = 'setup.json'
DB_NAME = 'hardlylistening.db'

#: Sentence fragments the Rust mirrors. A message that says something different in the window and
#: in `check` is a support call, so the exact wording is part of the contract and the test reads
#: `wizard.rs` for every one of these, as well as proving each one is produced here.
MESSAGES = (
    'Choose a folder for your music',
    'Choose a folder for the server data',
    'does not exist',
    'is not a folder',
    'is a file, not a folder',
    'cannot be read',
    'is not writable',
    'is missing and',
    'A server name is 1-',
    'A server name cannot contain control characters',
    'Enter an email address',
    'Password must be at least',
    'Password is too long',
    'a password is never written to the config',
)

#: The server-side half of the account step. Run as `<python> -c ADMIN_SCRIPT` with the payload on
#: stdin, in the application root (so `db` and `security` resolve), by `wizard.rs`. It calls the
#: server's own account code and nothing else: `create_user` applies the schema and mints the
#: member, `set_password` validates the address and hashes with the server's scrypt profile.
#: `wizard.rs` carries the same text; the test refuses to let the two drift.
ADMIN_SCRIPT = '''\
import json, sys
from pathlib import Path
from db import Store
from security import Security

payload = json.load(sys.stdin)
store = Store(Path(payload["db"]))
member_id = None
try:
    sec = Security()
    # Both answers are checked by the server's own validators *before* the member row exists:
    # `create_user` commits, so a refusal after it would leave a nameless administrator behind.
    email = sec.validate_email(payload["email"])
    password = sec.validate_password(payload["password"])
    sec.schema(store)
    with store.lock:
        taken = store.conn.execute(
            "SELECT 1 FROM member_credentials WHERE login_email=?", (email,)).fetchone()
    if taken:
        raise SystemExit("That email is already in use")
    member_id = sec.create_user(store, {"role": "admin"}, None, "admin")
    sec.set_password(store, member_id, password, email)
finally:
    store._projection_closed = True
    timer = getattr(store, "_projection_timer", None)
    if timer:
        timer.cancel()
    store.close()
print(json.dumps({"member_id": member_id, "email": email}))
'''


class SetupError(ValueError):
    """A refused answer, with the sentence the window shows."""


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def _path(value, empty_message: str) -> Path:
    text = _text(value)
    if not text:
        raise SetupError(empty_message)
    return Path(text).expanduser()


# ---- the four answers ----------------------------------------------------
def validate_music_dir(value) -> str:
    """An existing folder the server can read. Never created: a library is the person's."""
    path = _path(value, 'Choose a folder for your music')
    if not path.exists():
        raise SetupError(f'{path} does not exist')
    if not path.is_dir():
        raise SetupError(f'{path} is not a folder')
    if not os.access(path, os.R_OK):
        raise SetupError(f'{path} cannot be read')
    try:
        with os.scandir(path) as entries:      # `access` can lie across a mount; a read cannot
            next(entries, None)
    except OSError as error:
        raise SetupError(f'{path} cannot be read: {error.strerror or error}') from None
    return str(path)


def validate_data_dir(value) -> str:
    """A folder that exists and is writable, or one whose nearest existing ancestor is.

    Same rule as `cli._creatable`, and the same reason: a packaged install's data directory does
    not exist before the first start, so "could this be made" is the honest question. This
    function never creates anything; `prepare_data_dir` is the explicit, separate write.
    """
    path = _path(value, 'Choose a folder for the server data')
    if path.exists():
        if not path.is_dir():
            raise SetupError(f'{path} is a file, not a folder')
        if not os.access(path, os.W_OK | os.X_OK):
            raise SetupError(f'{path} is not writable')
        return str(path)
    ancestor = path
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise SetupError(f'{path} is missing and {ancestor} is not a folder')
    if not os.access(ancestor, os.W_OK | os.X_OK):
        raise SetupError(f'{path} is missing and {ancestor} is not writable')
    return str(path)


def validate_server_name(value) -> str:
    """What this install is called in the window. A label, not an address.

    The server has no name setting today, so this is the shell's own: it titles the window and
    tells two installs apart. Nothing is sent to the server, and nothing replaces a hostname.
    """
    text = _text(value)
    if not 1 <= len(text) <= NAME_MAX:
        raise SetupError('A server name is 1-64 characters')
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise SetupError('A server name cannot contain control characters')
    return text


def validate_email(value) -> str:
    """The server's address rule, character for character (`Security.validate_email`)."""
    email = _text(value).lower()
    if not 5 <= len(email) <= EMAIL_MAX or email.count('@') != 1 or any(
            character.isspace() for character in email):
        raise SetupError('Enter an email address')
    local, _, domain = email.partition('@')
    if not local or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
        raise SetupError('Enter an email address')
    return email


def validate_password(value) -> str:
    """The server's password rule (`Security.validate_password`): a length, and a ceiling."""
    if not isinstance(value, str) or len(value) < PASSWORD_MIN:
        raise SetupError(f'Password must be at least {PASSWORD_MIN} characters')
    if len(value) > PASSWORD_MAX:
        raise SetupError('Password is too long')
    return value


VALIDATORS = {
    'music_dir': validate_music_dir,
    'data_dir': validate_data_dir,
    'server_name': validate_server_name,
    'admin_email': validate_email,
    'password': validate_password,
}


def check_value(field: str, value) -> str:
    """Validate one answer, or raise `SetupError` with the sentence to show."""
    try:
        validator = VALIDATORS[field]
    except KeyError:
        raise SetupError(f'unknown field {field!r}') from None
    return validator(value)


# ---- defaults ------------------------------------------------------------
def default_music_dir(home: Path | str | None = None) -> str:
    """`~/Music/HardlyListening` — the server's own default root (`db.DEFAULTS['root']`)."""
    return str((Path(home) if home is not None else Path.home()) / MUSIC_SUFFIX)


def default_data_dir(env: Mapping[str, str] | None = None,
                     home: Path | str | None = None) -> str:
    """The XDG data location for this app: `~/.local/share/hardlylistening` by default."""
    env = os.environ if env is None else env
    declared = _text(env.get('XDG_DATA_HOME'))
    base = Path(declared).expanduser() if declared else (
        Path(home) if home is not None else Path.home()) / XDG_DATA_SUFFIX
    return str(base / DATA_LEAF)


def defaults(env: Mapping[str, str] | None = None, home: Path | str | None = None) -> dict:
    """What the window pre-fills. A suggestion, never a saved answer."""
    return {'music_dir': default_music_dir(home), 'data_dir': default_data_dir(env, home)}


# ---- the state file ------------------------------------------------------
def setup_path(env: Mapping[str, str] | None = None, config: Path | None = None) -> Path:
    """`setup.json`, beside the shell's `host.json` and behind the same override.

    `HARDLYLISTENING_DESKTOP_CONFIG` names the shell's config *file*; the wizard's state is its
    sibling. The test compares this with `resolve.config_path(...).parent` so the two cannot drift.
    """
    env = os.environ if env is None else env
    declared = _text(env.get('HARDLYLISTENING_DESKTOP_CONFIG'))
    if declared:
        return Path(declared).expanduser().parent / SETUP_FILE
    if config is not None:
        return Path(config).parent / SETUP_FILE
    state = _text(env.get('XDG_STATE_HOME'))
    base = Path(state).expanduser() if state else Path.home() / '.local' / 'state'
    return base / CONFIG_NAME / SETUP_FILE


def read_state(path: Path) -> tuple[dict, str]:
    """The saved answers, plus a plain-language problem if the file could not be used.

    A malformed file is reported and ignored, exactly as `resolve.read_config` treats `host.json`:
    a window that will not open because a JSON file lost a brace is worse than one that says so
    and asks the four questions again.
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
    if data.get('version') != VERSION:
        return {}, f'{path} was written by another version of the wizard'
    return data, ''


def write_state(path: Path, state: dict) -> Path:
    """Write the answers atomically: a half-written config is a launch that asks again.

    The temporary file is created by `mkstemp`, so it is 0600 from the first byte — the file holds
    a person's paths and login address, and nothing here needs to be readable by anyone else.
    """
    if not isinstance(state, dict):
        raise SetupError('the wizard state must be an object')
    if 'password' in state:
        raise SetupError('a password is never written to the config')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = json.dumps(state, indent=2, sort_keys=True) + '\n'
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.setup-', suffix='.json')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def complete(state: dict, step: str, values: Mapping | None = None) -> dict:
    """The state after one step: the answer, validated, and the step recorded.

    Pure — it returns a new dict and writes nothing — so the resume rule can be exercised without
    a filesystem. The saved `state` is authoritative for the steps already done, which is what
    makes a resumed run continue rather than restart.
    """
    if step not in STEP_FIELDS:
        raise SetupError(f'unknown step {step!r}')
    values = dict(values or {})
    missing = [field for field in STEP_FIELDS[step] if field not in values]
    if missing:
        raise SetupError(f'{step} is missing {", ".join(missing)}')
    result = dict(state or {})
    result.pop('password', None)
    result['version'] = VERSION
    for field in STEP_FIELDS[step]:
        result[field] = check_value(field, values[field])
    for carried in ('admin_created',):
        if carried in values:
            result[carried] = values[carried] is True
    done = [name for name in (result.get('completed') or []) if name in STEPS]
    if step not in done:
        done.append(step)
    result['completed'] = [name for name in STEPS if name in done]
    return result


def is_complete(state: dict) -> bool:
    """All four steps answered, with a value each. The resume rule, in one place."""
    if not isinstance(state, dict) or state.get('version') != VERSION:
        return False
    done = state.get('completed')
    if not isinstance(done, list) or any(step not in done for step in STEPS):
        return False
    return all(bool(_text(state.get(field))) for field in FIELDS)


def first_unfinished(state: dict) -> str:
    """The step to reopen on: the first one without a recorded answer, else the first."""
    done = state.get('completed') if isinstance(state.get('completed'), list) else []
    for step in STEPS:
        if step not in done or not _text(state.get(STEP_FIELDS[step][0])):
            return step
    return STEPS[0]


# ---- what the server is handed -------------------------------------------
def db_path(state: Mapping) -> Path:
    """The database the wizard creates the administrator in: inside the chosen data folder."""
    return Path(str(state.get('data_dir') or '')).expanduser() / DB_NAME


def prepare_data_dir(state: Mapping) -> Path:
    """Create the data folder. The one write the wizard makes outside its state file.

    Called when the data step is completed, so the account step has somewhere to put a database
    and a start cannot fail on a missing parent directory.
    """
    path = Path(validate_data_dir(state.get('data_dir')))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def server_env(state: Mapping) -> dict:
    """The environment the server needs to serve *this* install.

    `HARDLYLISTENING_DATA_DIR` moves the database and every cache; `HARDLYLISTENING_DB` is named
    explicitly so the account step and a later start cannot disagree about which file they mean
    (`paths.py`: an explicit DB wins over the data dir). `HARDLYLISTENING_MUSIC_DIR` only seeds a
    library that has no chosen root yet — after that the database wins, which is what keeps this
    from re-pointing a library on every start.
    """
    return {
        'HARDLYLISTENING_DATA_DIR': str(Path(str(state.get('data_dir') or '')).expanduser()),
        'HARDLYLISTENING_DB': str(db_path(state)),
        'HARDLYLISTENING_MUSIC_DIR': str(Path(str(state.get('music_dir') or '')).expanduser()),
    }


def check_argv() -> list:
    """`check`, the command `deploy/install.sh` runs before it enables a unit.

    Informational: it also looks for yt-dlp and ffmpeg, which the wizard does not install. It is
    the honest way to show what a first start will complain about, with the wizard's own
    environment, without starting anything.
    """
    return ['check']


def admin_payload(state: Mapping, password) -> dict:
    """What the account step hands the server process. The password goes on stdin, not argv."""
    return {
        'db': str(db_path(state)),
        'email': validate_email(state.get('admin_email')),
        'password': validate_password(password),
    }


def admin_argv(python: str | None = None) -> list:
    """How to run `ADMIN_SCRIPT`: an interpreter, `-c`, and the script. No secret in argv."""
    return [_text(python) or 'python3', '-c', ADMIN_SCRIPT]


def rules() -> dict:
    """The numbers and words the window repeats, so a test can compare the two copies."""
    return {
        'version': VERSION,
        'steps': list(STEPS),
        'fields': list(FIELDS),
        'password_min': PASSWORD_MIN,
        'password_max': PASSWORD_MAX,
        'email_max': EMAIL_MAX,
        'name_max': NAME_MAX,
        'default_server_name': DEFAULT_SERVER_NAME,
        'music_suffix': MUSIC_SUFFIX,
        'xdg_data_suffix': XDG_DATA_SUFFIX,
        'data_leaf': DATA_LEAF,
    }


# ---- for a terminal and for the tests ------------------------------------
def main(argv: list | None = None) -> int:
    """A small command line, because the rules should be runnable without a window.

        setup.py --rules              the numbers `ui/wizard.js` mirrors
        setup.py --defaults HOME      what the window pre-fills for a home directory
        setup.py --check-table        stdin: [{"field": ..., "value": ...}] -> verdicts
    """
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['--rules']:
        print(json.dumps(rules(), indent=2, sort_keys=True))
        return 0
    if argv[:1] == ['--defaults']:
        home = argv[1] if len(argv) > 1 else str(Path.home())
        print(json.dumps(defaults({}, home), sort_keys=True))
        return 0
    if argv[:1] == ['--check-table']:
        table = json.load(sys.stdin)
        verdicts = []
        for row in table:
            try:
                value = check_value(row.get('field'), row.get('value'))
            except SetupError as error:
                verdicts.append({'ok': False, 'detail': str(error)})
            else:
                verdicts.append({'ok': True, 'value': value, 'detail': ''})
        print(json.dumps(verdicts))
        return 0
    print(__doc__ or '', file=sys.stderr)
    print('usage: setup.py --rules | --defaults HOME | --check-table', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())