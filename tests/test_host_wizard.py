"""HOST-002: the first-run wizard asks four questions, and the server still owns the account.

The crate cannot be compiled on this machine — no `webkit2gtk-4.1`, and adding it is an OS
layering change — so this file stands in for `cargo build` the way `test_desktop_host.py` does for
HOST-001. `desktop/host/setup.py` is the tested copy of the wizard's rules; `wizard.rs` renders
them and this file refuses to let the two drift.

Four things are actually proven here, rather than asserted:

  * **The answers are validated against the same rules the server applies.** The email and password
    checks are compared *to* `security.Security.validate_email` / `validate_password` value by
    value, and the data-folder rule to `cli._creatable`, which is the check a packaged install
    already runs. A second copy of a rule is only safe while it is measured against the first.
  * **The administrator really is created by the server's own account code.** The account step's
    command is executed against a temporary database and the result is asked to authenticate
    through `Security.authenticate_password`. Refusals are proven too: a short password, a bad
    address and a duplicate email are all refused by the server, and a refusal leaves no
    half-created administrator behind.
  * **The state file is the resume point.** Every completed step is written atomically (0600, no
    temporary files left), a complete file reads back as complete, and a malformed one is reported
    rather than fatal.
  * **The password is never in a place that keeps it.** Not in the argv, not in the state, and the
    module refuses to write a state that contains one.

Nothing here starts a server, opens a socket or touches an existing installation: every path is
under the test's own temporary directory, and the only process spawned is the account one-shot.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DESKTOP = APP_DIR / 'desktop'
SRC_TAURI = DESKTOP / 'src-tauri'
DOC = APP_DIR / 'docs' / 'desktop' / 'wizard.md'

TMP_ROOT = os.environ.get('TMPDIR') or None


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load_module('desktop_host_setup', DESKTOP / 'host' / 'setup.py')
resolve = load_module('desktop_host_resolve', DESKTOP / 'host' / 'resolve.py')

import cli            # noqa: E402  (the server's own path checks, compared to ours)
import db             # noqa: E402  (its default media root is the wizard's suggestion)
import security       # noqa: E402  (its account rules are the wizard's account rules)


class Temporary(unittest.TestCase):
    """A scratch directory per test, inside TMPDIR so nothing lands on the real filesystem."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hl-wizard-', dir=TMP_ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def refused(self, validator, *arguments):
        with self.assertRaises(setup.SetupError) as caught:
            validator(*arguments)
        return str(caught.exception)


def is_root():
    return hasattr(os, 'geteuid') and os.geteuid() == 0


def message(validator, *arguments):
    """The sentence a refusal shows, without the assertion ceremony."""
    try:
        validator(*arguments)
    except setup.SetupError as caught:
        return str(caught)
    raise AssertionError(f'{validator.__name__}{arguments!r} did not refuse')


def open_store(path):
    """A Store for assertions, closed the way the account script closes one."""
    store = db.Store(Path(path))
    def close():
        store._projection_closed = True
        if store._projection_timer:
            store._projection_timer.cancel()
        store.close()
    return store, close


# ---- the four answers ----------------------------------------------------
class MusicFolder(Temporary):
    def test_an_existing_folder_is_taken_as_written(self):
        music = self.tmp / 'music'
        music.mkdir()
        self.assertEqual(setup.validate_music_dir(str(music)), str(music))
        self.assertEqual(setup.validate_music_dir('  %s  ' % music), str(music))

    def test_a_missing_folder_is_named(self):
        missing = self.tmp / 'nowhere'
        message = self.refused(setup.validate_music_dir, str(missing))
        self.assertIn('does not exist', message)
        self.assertIn(str(missing), message)

    def test_a_file_is_not_a_folder(self):
        file_path = self.tmp / 'track.flac'
        file_path.write_text('')
        self.assertIn('is not a folder', self.refused(setup.validate_music_dir, str(file_path)))

    def test_an_empty_answer_asks_for_one(self):
        self.assertEqual(self.refused(setup.validate_music_dir, '   '),
                         'Choose a folder for your music')

    @unittest.skipIf(is_root(), 'root reads everything; the refusal is unreachable')
    def test_a_folder_that_cannot_be_read_is_refused(self):
        music = self.tmp / 'closed'
        music.mkdir()
        os.chmod(music, 0o000)
        self.addCleanup(os.chmod, music, 0o700)
        self.assertIn('cannot be read', self.refused(setup.validate_music_dir, str(music)))


class DataFolder(Temporary):
    def test_a_missing_folder_is_acceptable_when_it_can_be_made(self):
        target = self.tmp / 'data'
        self.assertEqual(setup.validate_data_dir(str(target)), str(target))
        self.assertFalse(target.exists(), 'validating must not create anything')

    def test_a_deep_missing_path_judges_its_nearest_ancestor(self):
        target = self.tmp / 'a' / 'b' / 'c'
        self.assertEqual(setup.validate_data_dir(str(target)), str(target))

    def test_a_file_is_refused(self):
        file_path = self.tmp / 'notes.txt'
        file_path.write_text('')
        self.assertIn('is a file, not a folder',
                      self.refused(setup.validate_data_dir, str(file_path)))

    @unittest.skipIf(is_root(), 'root writes everywhere; the refusal is unreachable')
    def test_an_unwritable_parent_is_refused(self):
        parent = self.tmp / 'locked'
        parent.mkdir()
        os.chmod(parent, 0o500)
        self.addCleanup(os.chmod, parent, 0o700)
        message = self.refused(setup.validate_data_dir, str(parent / 'data'))
        self.assertIn('is missing and', message)
        self.assertIn('is not writable', message)

    def test_it_agrees_with_the_servers_own_creatable_check(self):
        wanted = [self.tmp / 'fresh', self.tmp / 'two' / 'levels', self.tmp]
        for path in wanted:
            theirs, _ = cli._creatable(path)
            try:
                setup.validate_data_dir(str(path))
                ours = True
            except setup.SetupError:
                ours = False
            self.assertEqual(ours, theirs, f'{path}: the wizard and `check` disagree')

    def test_preparing_the_folder_is_the_only_write(self):
        target = self.tmp / 'created'
        state = {'data_dir': str(target)}
        self.assertEqual(setup.prepare_data_dir(state), target)
        self.assertTrue(target.is_dir())
        if os.name == 'posix':
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)


class ServerName(Temporary):
    def test_a_name_is_trimmed_and_kept(self):
        self.assertEqual(setup.validate_server_name('  Listening at home  '), 'Listening at home')
        self.assertEqual(setup.validate_server_name('x' * setup.NAME_MAX), 'x' * setup.NAME_MAX)

    def test_an_empty_or_overlong_name_is_refused(self):
        for value in ('', '   ', 'x' * (setup.NAME_MAX + 1)):
            self.assertIn('A server name is 1-', self.refused(setup.validate_server_name, value))

    def test_control_characters_are_refused(self):
        for value in ('two\nlines', 'tab\there', 'null\x00'):
            self.assertIn('cannot contain control characters',
                          self.refused(setup.validate_server_name, value))


class AdminEmail(Temporary):
    VALUES = ('', 'a@b', 'a@b.co', 'A@B.CO', ' a@b.co ', 'a@@b.co', 'a b@c.de', 'a@.co',
              'a@b.', '@b.co', 'a@b.c', 'a@b.co.', 'x' * 250 + '@b.co', 'no-at-sign')

    def test_it_is_the_servers_own_rule(self):
        server = security.Security()
        for value in self.VALUES:
            try:
                theirs = (True, server.validate_email(value))
            except security.Denied as denied:
                theirs = (False, denied.message)
            try:
                ours = (True, setup.validate_email(value))
            except setup.SetupError as refused:
                ours = (False, str(refused))
            self.assertEqual(ours, theirs, f'{value!r}: the wizard and the server disagree')

    def test_an_address_is_lowercased_and_trimmed(self):
        self.assertEqual(setup.validate_email('  Owner@Example.COM '), 'owner@example.com')


class AdminPassword(Temporary):
    VALUES = ('', 'short', 'x' * 9, 'x' * 10, 'x' * 1024, 'x' * 1025, None, 12345)

    def test_it_is_the_servers_own_rule(self):
        server = security.Security()
        for value in self.VALUES:
            try:
                theirs = (True, len(server.validate_password(value)))
            except security.Denied as denied:
                theirs = (False, denied.message)
            try:
                ours = (True, len(setup.validate_password(value)))
            except setup.SetupError as refused:
                ours = (False, str(refused))
            self.assertEqual(ours, theirs, f'{value!r}: the wizard and the server disagree')

    def test_a_password_is_not_trimmed(self):
        padded = ' ' + 'x' * 10 + ' '
        self.assertEqual(setup.validate_password(padded), padded)


class Defaults(Temporary):
    def test_the_music_suggestion_is_the_servers_default_root(self):
        self.assertEqual(setup.default_music_dir(), db.DEFAULTS['root'])
        self.assertEqual(setup.default_music_dir('/home/example'),
                         '/home/example/Music/HardlyListening')

    def test_the_data_suggestion_follows_xdg(self):
        self.assertEqual(setup.default_data_dir({}, '/home/example'),
                         '/home/example/.local/share/hardlylistening')
        self.assertEqual(setup.default_data_dir({'XDG_DATA_HOME': '/srv/data'}, '/home/example'),
                         '/srv/data/hardlylistening')

    def test_defaults_are_suggestions_not_answers(self):
        payload = setup.defaults({}, '/home/example')
        self.assertEqual(sorted(payload), ['data_dir', 'music_dir'])
        self.assertFalse(setup.is_complete(payload))


# ---- the state file, which is the resume point ---------------------------
class StateFile(Temporary):
    def setUp(self):
        super().setUp()
        self.music = self.tmp / 'music'
        self.music.mkdir()
        self.data = self.tmp / 'data'
        self.state = {'version': setup.VERSION}

    def step(self, name, **values):
        self.state = setup.complete(self.state, name, values)
        return self.state

    def fill(self):
        self.step('music', music_dir=str(self.music))
        self.step('data', data_dir=str(self.data))
        self.step('name', server_name='Home')
        self.step('admin', admin_email='Owner@Example.com', admin_created=True)
        return self.state

    def test_a_step_records_one_validated_answer(self):
        state = self.step('music', music_dir=str(self.music))
        self.assertEqual(state['music_dir'], str(self.music))
        self.assertEqual(state['completed'], ['music'])
        self.assertFalse(setup.is_complete(state))
        self.assertEqual(state['version'], setup.VERSION)

    def test_steps_complete_in_order_whatever_order_they_arrive(self):
        self.step('data', data_dir=str(self.data))
        self.step('music', music_dir=str(self.music))
        self.assertEqual(self.state['completed'], ['music', 'data'])

    def test_the_fourth_step_completes_the_config(self):
        self.fill()
        self.assertTrue(setup.is_complete(self.state))
        self.assertEqual(self.state['admin_email'], 'owner@example.com')
        self.assertTrue(self.state['admin_created'])

    def test_an_unanswered_step_leaves_it_incomplete(self):
        self.fill()
        del self.state['server_name']
        self.assertFalse(setup.is_complete(self.state))

    def test_a_bad_answer_is_refused_and_records_nothing(self):
        before = json.dumps(self.state, sort_keys=True)
        with self.assertRaises(setup.SetupError):
            setup.complete(self.state, 'name', {'server_name': ''})
        self.assertEqual(json.dumps(self.state, sort_keys=True), before)
        self.assertNotIn('completed', self.state)

    def test_an_unknown_step_is_refused(self):
        with self.assertRaises(setup.SetupError):
            setup.complete(self.state, 'password', {'password': 'x' * 20})

    def test_a_password_is_never_carried_into_the_config(self):
        brave = dict(self.state, password='a long enough password')
        result = setup.complete(brave, 'admin', {'admin_email': 'a@b.co'})
        self.assertNotIn('password', result)
        with self.assertRaises(setup.SetupError):
            setup.write_state(self.tmp / 'setup.json', {'version': 1, 'password': 'secret'})

    def test_the_file_round_trips_and_leaves_no_temporary(self):
        state = self.fill()
        path = setup.write_state(self.tmp / 'nested' / 'setup.json', state)
        self.assertTrue(path.is_file())
        if os.name == 'posix':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([entry.name for entry in path.parent.iterdir()], ['setup.json'])
        read, problem = setup.read_state(path)
        self.assertEqual(problem, '')
        self.assertTrue(setup.is_complete(read))
        self.assertEqual(read, state)

    def test_a_malformed_file_is_reported_and_ignored(self):
        path = self.tmp / 'setup.json'
        path.write_text('{"version": 1,')
        state, problem = setup.read_state(path)
        self.assertEqual(state, {})
        self.assertIn('not JSON', problem)

    def test_a_file_from_another_version_is_not_guessed_at(self):
        path = self.tmp / 'setup.json'
        path.write_text(json.dumps({'version': 99, 'music_dir': '/music'}))
        state, problem = setup.read_state(path)
        self.assertEqual(state, {})
        self.assertIn('another version', problem)

    def test_a_missing_file_is_not_a_problem(self):
        state, problem = setup.read_state(self.tmp / 'never-written.json')
        self.assertEqual((state, problem), ({}, ''))

    def test_resuming_continues_at_the_first_unfinished_step(self):
        self.step('music', music_dir=str(self.music))
        self.step('data', data_dir=str(self.data))
        self.assertEqual(setup.first_unfinished(self.state), 'name')
        self.assertEqual(setup.first_unfinished(self.fill()), setup.STEPS[0])
        self.assertFalse(setup.is_complete({}))

    def test_the_state_lives_beside_the_shells_config(self):
        env = {'XDG_STATE_HOME': str(self.tmp)}
        self.assertEqual(setup.setup_path(env), resolve.config_path(env).parent / 'setup.json')
        self.assertEqual(setup.CONFIG_NAME, resolve.CONFIG_NAME)
        self.assertEqual(setup.SETUP_FILE, 'setup.json')
        declared = str(self.tmp / 'custom' / 'host.json')
        self.assertEqual(setup.setup_path({'HARDLYLISTENING_DESKTOP_CONFIG': declared}),
                         self.tmp / 'custom' / 'setup.json')


# ---- what the server is handed -------------------------------------------
class WhatTheServerGets(Temporary):
    def state(self):
        music = self.tmp / 'music'
        music.mkdir(exist_ok=True)
        return {
            'version': setup.VERSION,
            'music_dir': str(music),
            'data_dir': str(self.tmp / 'data'),
            'server_name': 'Home',
            'admin_email': 'owner@example.com',
            'completed': list(setup.STEPS),
        }

    def test_the_environment_is_the_packaged_installs_environment(self):
        state = self.state()
        env = setup.server_env(state)
        self.assertEqual(env, {
            'HARDLYLISTENING_DATA_DIR': state['data_dir'],
            'HARDLYLISTENING_DB': str(Path(state['data_dir']) / 'hardlylistening.db'),
            'HARDLYLISTENING_MUSIC_DIR': state['music_dir'],
        })

    def test_the_database_sits_inside_the_chosen_data_folder(self):
        state = self.state()
        self.assertEqual(setup.db_path(state).parent, Path(state['data_dir']))
        self.assertEqual(setup.db_path(state).name, 'hardlylistening.db')

    def test_the_readiness_command_is_the_one_the_installer_runs(self):
        self.assertEqual(setup.check_argv(), ['check'])

    def test_the_wizard_does_not_touch_the_address(self):
        # host/port belong to `resolve` (and HOST-005); the wizard must not grow a second opinion.
        env = setup.server_env(self.state())
        self.assertNotIn('HARDLYLISTENING_HOST', env)
        self.assertNotIn('HARDLYLISTENING_PORT', env)


class TheAccountGoesThroughTheServer(Temporary):
    """The step's command, executed. The administrator is the server's, not a row of ours."""

    PASSWORD = 'a long enough password'

    def setUp(self):
        super().setUp()
        self.state = {'version': setup.VERSION, 'data_dir': str(self.tmp / 'data'),
                      'admin_email': 'Owner@Example.com'}
        setup.prepare_data_dir(self.state)
        self.db_path = setup.db_path(self.state)
        self.env = dict(os.environ, **setup.server_env(self.state))

    def run_script(self, payload):
        return subprocess.run(
            setup.admin_argv(sys.executable),
            input=json.dumps(payload), cwd=str(APP_DIR),
            capture_output=True, text=True, env=self.env, timeout=120)

    def payload(self, password=None, email=None):
        payload = setup.admin_payload(self.state, password or self.PASSWORD)
        payload['email'] = email or payload['email']
        return payload

    def members(self):
        import sqlite3

        store, close = open_store(self.db_path)
        try:
            return [dict(row) for row in store.conn.execute(
                'SELECT role,email FROM members ORDER BY rowid')]
        except sqlite3.OperationalError:      # a refused step never created the auth schema
            return []
        finally:
            close()

    def test_the_password_is_never_an_argument(self):
        argv = setup.admin_argv(sys.executable)
        self.assertEqual(len(argv), 3)
        self.assertEqual(argv[1], '-c')
        self.assertNotIn(self.PASSWORD, ' '.join(argv))
        self.assertIn('security', argv[2])

    def test_the_result_authenticates_through_the_servers_own_login(self):
        result = self.run_script(self.payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(answer['email'], 'owner@example.com')
        self.assertEqual(self.members(), [{'role': 'admin', 'email': 'owner@example.com'}])

        store, close = open_store(self.db_path)
        try:
            member = security.Security().authenticate_password(
                store, 'owner@example.com', self.PASSWORD)
            self.assertEqual(member['role'], 'admin')
            self.assertEqual(member['id'], answer['member_id'])
        finally:
            close()

    def test_a_short_password_is_refused_by_the_server_and_leaves_no_account(self):
        result = self.run_script({'db': str(self.db_path), 'email': 'owner@example.com',
                                  'password': 'short'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Password must be at least', result.stderr)
        self.assertEqual(self.members(), [], 'a refusal must not leave a nameless administrator')

    def test_a_bad_address_is_refused_by_the_server(self):
        result = self.run_script({'db': str(self.db_path), 'email': 'not-an-address',
                                  'password': self.PASSWORD})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Enter an email address', result.stderr)
        self.assertEqual(self.members(), [])

    def test_a_second_account_with_the_same_email_is_refused(self):
        self.assertEqual(self.run_script(self.payload()).returncode, 0)
        again = self.run_script(self.payload(password='another long password'))
        self.assertNotEqual(again.returncode, 0)
        self.assertIn('already in use', again.stderr)
        self.assertEqual(len(self.members()), 1)


# ---- the Rust rendering, held to the Python copy -------------------------
class RustMirrorsTheTestedRules(unittest.TestCase):
    def setUp(self):
        self.rust = (SRC_TAURI / 'src' / 'wizard.rs').read_text()
        self.python = (DESKTOP / 'host' / 'setup.py').read_text()

    def test_the_account_script_is_byte_for_byte_the_python_one(self):
        match = re.search(r'const ADMIN_SCRIPT: &str = r#"(.*?)"#;', self.rust, re.S)
        self.assertIsNotNone(match, 'wizard.rs does not declare ADMIN_SCRIPT')
        self.assertEqual(match.group(1), setup.ADMIN_SCRIPT)

    def test_the_constants_are_the_python_ones(self):
        expected = {
            'VERSION': f'const VERSION: u64 = {setup.VERSION};',
            'NAME_MAX': f'const NAME_MAX: usize = {setup.NAME_MAX};',
            'EMAIL_MAX': f'const EMAIL_MAX: usize = {setup.EMAIL_MAX};',
            'PASSWORD_MIN': f'const PASSWORD_MIN: usize = {setup.PASSWORD_MIN};',
            'PASSWORD_MAX': f'const PASSWORD_MAX: usize = {setup.PASSWORD_MAX};',
            'SETUP_FILE': 'const SETUP_FILE: &str = "setup.json";',
            'DB_NAME': f'const DB_NAME: &str = "{setup.DB_NAME}";',
            'CONFIG_DIR': f'const CONFIG_DIR: &str = "{setup.CONFIG_NAME}";',
            'MUSIC_SUFFIX': f'const MUSIC_SUFFIX: &str = "{setup.MUSIC_SUFFIX}";',
            'XDG_DATA_SUFFIX': f'const XDG_DATA_SUFFIX: &str = "{setup.XDG_DATA_SUFFIX}";',
            'DATA_LEAF': f'const DATA_LEAF: &str = "{setup.DATA_LEAF}";',
            'STEPS': ('const STEPS: [&str; %d] = [%s];'
                      % (len(setup.STEPS), ', '.join(f'"{s}"' for s in setup.STEPS))),
            'FIELDS': ('const FIELDS: [&str; %d] = [%s];'
                       % (len(setup.FIELDS), ', '.join(f'"{f}"' for f in setup.FIELDS))),
        }
        for name, literal in expected.items():
            self.assertIn(literal, self.rust, f'{name} drifted between Rust and Python')

    def test_the_start_environment_is_the_python_one(self):
        self.assertIn('pub fn server_env()', self.rust)
        names = setup.server_env({'data_dir': '/tmp/data', 'music_dir': '/tmp/music'})
        self.assertEqual(len(names), 3)
        for name in names:
            self.assertIn(f'("{name}",', self.rust, f'{name} is not handed to a start')

    def test_every_message_is_rendered_in_the_rust(self):
        for fragment in setup.MESSAGES:
            self.assertIn(fragment, self.rust, f'{fragment!r} is not in wizard.rs')

    def test_every_message_fragment_is_actually_produced_here(self):
        music = Path(tempfile.mkdtemp(prefix='hl-wizard-msg-', dir=TMP_ROOT))
        self.addCleanup(shutil.rmtree, music, True)
        a_file = music / 'track.flac'
        a_file.write_text('')
        produced = [
            message(setup.validate_music_dir, ''),
            message(setup.validate_data_dir, ''),
            message(setup.validate_music_dir, str(music / 'nope')),
            message(setup.validate_music_dir, str(a_file)),
            message(setup.validate_data_dir, str(a_file)),
            message(setup.validate_server_name, ''),
            message(setup.validate_server_name, 'a\nb'),
            message(setup.validate_email, 'nope'),
            message(setup.validate_password, 'short'),
            message(setup.validate_password, 'x' * 2000),
            message(setup.write_state, music / 'setup.json',
                    {'version': 1, 'password': 'a long enough password'}),
        ]
        joined = ' | '.join(produced)
        for fragment in setup.MESSAGES:
            if fragment in ('cannot be read', 'is not writable', 'is missing and'):
                continue                    # permission refusals need a non-root, non-writable path
            self.assertIn(fragment, joined, f'{fragment!r} is not produced by this module')
        # And the fragments that were skipped are at least reachable from the module's own text.
        for fragment in ('cannot be read', 'is not writable', 'is missing and'):
            self.assertIn(fragment, self.python)


class TheWizardIsAPureClient(unittest.TestCase):
    def test_setup_py_imports_only_the_standard_library(self):
        import ast

        tree = ast.parse((DESKTOP / 'host' / 'setup.py').read_text())
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                roots.add(node.module.split('.')[0])
        offenders = sorted(root for root in roots if root not in sys.stdlib_module_names)
        self.assertEqual(offenders, [], 'the wizard is a client; it may not import the server')

    def test_the_rules_are_printable_so_the_window_can_be_compared(self):
        rules = setup.rules()
        self.assertEqual(rules['steps'], list(setup.STEPS))
        self.assertEqual(rules['password_min'], setup.PASSWORD_MIN)
        self.assertEqual(rules['name_max'], setup.NAME_MAX)
        self.assertEqual(rules['default_server_name'], setup.DEFAULT_SERVER_NAME)
        result = subprocess.run([sys.executable, str(DESKTOP / 'host' / 'setup.py'), '--rules'],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['steps'], list(setup.STEPS))

    def test_the_check_table_is_the_same_rule_a_terminal_gets(self):
        table = [{'field': 'admin_email', 'value': 'a@b.co'},
                 {'field': 'admin_email', 'value': 'nope'},
                 {'field': 'server_name', 'value': '  '}]
        result = subprocess.run(
            [sys.executable, str(DESKTOP / 'host' / 'setup.py'), '--check-table'],
            input=json.dumps(table), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        verdicts = json.loads(result.stdout)
        self.assertEqual([row['ok'] for row in verdicts], [True, False, False])
        self.assertEqual(verdicts[1]['detail'], 'Enter an email address')


class TheDocumentationIsHonest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DOC.is_file(), f'{DOC} is part of the deliverable')
        self.text = DOC.read_text()

    def test_it_names_the_four_questions(self):
        for word in ('music folder', 'data folder', 'server name', 'administrator'):
            self.assertIn(word, self.text.lower())

    def test_it_says_where_the_state_lives_and_how_a_run_resumes(self):
        self.assertIn('setup.json', self.text)
        self.assertIn('hardlylistening-desktop', self.text)
        self.assertRegex(self.text, r'(?i)resume|resumes|second launch')

    def test_it_says_the_server_creates_the_account(self):
        self.assertIn('Security', self.text)
        self.assertIn('create_user', self.text)
        self.assertIn('set_password', self.text)

    def test_it_says_the_password_is_not_stored(self):
        self.assertRegex(self.text, r'(?is)password.*(never|not).*(state|argv)')

    def test_it_names_what_is_unverified(self):
        self.assertRegex(self.text, r'(?i)never (seen a compiler|been compiled)|cannot be compiled')
        self.assertRegex(self.text, r'(?i)unverified|not verified')

    def test_it_names_the_environment_the_server_is_given(self):
        for name in ('HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB', 'HARDLYLISTENING_MUSIC_DIR'):
            self.assertIn(name, self.text)


if __name__ == '__main__':
    unittest.main()