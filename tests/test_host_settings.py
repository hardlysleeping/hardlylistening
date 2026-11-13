"""HOST-005: the settings model, and the settings the server does not have.

`desktop/host/settings.py` is the tested copy of the panel's rules, for the same reason
`resolve.py` is: the crate cannot be compiled on this machine (no `webkit2gtk-4.1`), so the
decisions are proven in Python and mirrored in Rust and JavaScript.

What this file is actually asserting:

  * **"Local only" and "the LAN" are the server's own bind control.** They are `127.0.0.1` and
    `0.0.0.0` handed to `--host`, nothing invented — `server.py` builds its HTTP server from
    that one value.
  * **The two switches are real.** `web_ui` and `subsonic` are stored, refused when the
    environment pins them, and passed to the server as `--no-web-ui` / `--no-subsonic` — the
    flags `server.py` actually gates on. Their vocabulary is compared to `config.py`'s, because
    a window that reads `HARDLYLISTENING_WEB_UI=no` as on would contradict the server.
  * **The environment wins, and the refusal says so.** Writing `host` while
    `HARDLYLISTENING_HOST` is set would change nothing, so it is refused with the variable named.
  * **Autostart is only ever an explicit choice.** It runs the server launcher, not the window,
    and a patch cannot carry it — an entry installed as a side effect of an address change is
    exactly what must not happen.

Every write here lands in a temporary directory: `HARDLYLISTENING_DESKTOP_CONFIG` and
`HARDLYLISTENING_DESKTOP_AUTOSTART` exist so no test can reach a person's real config or
`~/.config/autostart`, even by accident. Nothing starts a process or opens a socket.
"""
import json
import os
import re
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import desktop.host.resolve as resolve
import desktop.host.settings as settings

APP_DIR = Path(__file__).resolve().parents[1]
SRC_TAURI = APP_DIR / 'desktop' / 'src-tauri'
LAUNCHER = str(APP_DIR / 'bin' / 'hardlylistening-server')


class TempCase(unittest.TestCase):
    """A config file and an autostart directory that are never a person's."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hl-settings-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, True))
        self.config = self.tmp / 'host.json'
        self.env = {'XDG_CONFIG_HOME': str(self.tmp / 'config')}
        self.autostart = self.tmp / 'config' / 'autostart' / settings.AUTOSTART_FILE

    def load(self, **kwargs):
        return settings.load(self.env, self.config, **kwargs)

    def save(self, loaded, patch=None, **kwargs):
        return settings.save(loaded, patch, path=self.config, env=self.env, **kwargs)


class TheBindAddressIsTheServersOwn(TempCase):
    def test_local_means_loopback_and_lan_means_every_interface(self):
        self.assertEqual(settings.host_for_scope(settings.LOCAL), resolve.DEFAULT_HOST)
        self.assertEqual(settings.host_for_scope(settings.LAN), '0.0.0.0')
        for scope in (settings.LOCAL, settings.LAN):
            address = settings.Settings(host=settings.host_for_scope(scope)).address()
            self.assertEqual(address.scope, 'loopback' if scope == settings.LOCAL else 'any')
            # The flags, not a second concept: this is what `server.py --host` reads.
            self.assertEqual(address.argv()[0], '--host')
            self.assertEqual(address.argv()[1], settings.host_for_scope(scope))

    def test_a_specific_interface_address_is_a_lan_exposure(self):
        self.assertEqual(settings.scope_for_host('192.168.1.50'), settings.LAN)
        self.assertEqual(settings.scope_for_host('127.0.0.1'), settings.LOCAL)
        self.assertEqual(settings.scope_for_host('::1'), settings.LOCAL)

    def test_an_unknown_exposure_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            settings.host_for_scope('everyone')


class Defaults(TempCase):
    def test_a_shell_that_has_never_saved_uses_the_servers_defaults(self):
        loaded = self.load()
        self.assertEqual((loaded.settings.host, loaded.settings.port), ('127.0.0.1', 8470))
        self.assertEqual(loaded.settings.scope, settings.LOCAL)
        self.assertFalse(loaded.autostart)
        self.assertEqual(loaded.problems, ())
        self.assertEqual(loaded.locked, ())
        self.assertEqual(loaded.config_path, self.config)

    def test_the_panel_is_described_without_inventing_copy(self):
        described = settings.describe(self.load())
        keys = [row['key'] for row in described['rows']]
        self.assertEqual(keys, ['host', 'port', 'web_ui', 'subsonic', 'autostart'])
        by_key = {row['key']: row for row in described['rows']}
        self.assertTrue(by_key['host']['editable'])
        self.assertFalse(by_key['port']['editable'])
        for key, label in (('web_ui', 'Web UI'), ('subsonic', 'OpenSubsonic')):
            row = by_key[key]
            self.assertEqual(row['label'], label)
            self.assertEqual(row['value'], 'On')          # the server's default
            self.assertTrue(row['supported'] and row['editable'], f'{key} is not a control')
            self.assertFalse(row['locked'])
            self.assertEqual(row['detail'], settings.SWITCHES[key])
            self.assertEqual([option['value'] for option in row['options']], [False, True])
        self.assertEqual(by_key['autostart']['value'], 'Off')


class TheTwoSwitchesAreReal(TempCase):
    def test_a_switch_is_stored_as_a_boolean_and_read_back(self):
        loaded = self.load()
        self.assertTrue(loaded.settings.web_ui and loaded.settings.subsonic)
        saved = self.save(loaded, {'web_ui': False})
        self.assertEqual(saved.written, ('web_ui',))
        self.assertEqual(json.loads(self.config.read_text()),
                         {'host': '127.0.0.1', 'port': 8470,
                          'web_ui': False, 'subsonic': True})
        # The resolver the window and the launcher use agrees, so there is one answer.
        features = resolve.features(self.env, self.config)
        self.assertFalse(features.web_ui)
        self.assertTrue(features.subsonic)
        self.assertEqual(features.argv(), ['--no-web-ui'])

    def test_both_off_is_two_flags_and_nothing_else(self):
        saved = self.save(self.load(), {'web_ui': False, 'subsonic': False})
        self.assertEqual(saved.written, ('web_ui', 'subsonic'))
        self.assertEqual(settings.load(self.env, self.config).settings.flags(),
                         ['--no-web-ui', '--no-subsonic'])
        # On writes no flag at all: the server's own defaults are the on state.
        self.assertEqual(settings.Settings().flags(), [])

    def test_a_string_truthy_value_is_refused_rather_than_guessed(self):
        loaded = self.load()
        with self.assertRaises(ValueError):
            self.save(loaded, {'web_ui': 'false'})
        with self.assertRaises(ValueError):
            self.save(loaded, {'subsonic': 0})
        self.assertFalse(self.config.exists())

    def test_a_declared_variable_pins_the_switch_and_names_itself(self):
        env = dict(self.env, HARDLYLISTENING_WEB_UI='0')
        loaded = settings.load(env, self.config)
        self.assertFalse(loaded.settings.web_ui)
        self.assertIn('web_ui', loaded.locked)
        saved = settings.save(loaded, {'web_ui': True}, path=self.config, env=env)
        self.assertEqual(saved.written, ())
        self.assertIn('web_ui', [field for field, _ in saved.refused])
        self.assertIn('HARDLYLISTENING_WEB_UI', dict(saved.refused)['web_ui'])
        self.assertFalse(self.config.exists())

    def test_the_vocabulary_is_the_servers_own(self):
        """`config.web_ui()` and `resolve._switch()` must not disagree about a value."""
        import config
        for value in config.ON + config.OFF:
            self.assertEqual(resolve._switch(value, 'the switch'), value in config.ON, value)
            with unittest.mock.patch.dict(os.environ, {'HARDLYLISTENING_WEB_UI': value}):
                self.assertEqual(config.web_ui(), value in config.ON, value)
        self.assertEqual(sorted(config.ON + config.OFF),
                         sorted(resolve.ON + resolve.OFF),
                         'the two spellings drifted')
        with self.assertRaises(ValueError):
            resolve._switch('maybe', 'HARDLYLISTENING_WEB_UI')


class NothingElseIsInvented(TempCase):
    def test_an_unknown_key_is_refused_rather_than_stored(self):
        saved = self.save(self.load(), {'expose_to_the_internet': True})
        self.assertEqual(saved.written, ())
        self.assertEqual([field for field, _ in saved.refused], ['expose_to_the_internet'])
        self.assertIn('not a setting', dict(saved.refused)['expose_to_the_internet'])


class ItWritesOnlyWhatTheServerUnderstands(TempCase):
    def test_saving_lan_writes_the_address_the_server_reads(self):
        saved = self.save(self.load(), {'scope': settings.LAN, 'port': 8480})
        self.assertEqual(saved.written, ('host', 'port'))
        self.assertTrue(saved.saved)
        self.assertEqual(json.loads(self.config.read_text()),
                         {'host': '0.0.0.0', 'port': 8480, 'web_ui': True, 'subsonic': True})
        # And the resolver the window actually uses agrees, so there is one address, not two.
        address = resolve.address(self.env, self.config)
        self.assertEqual((address.host, address.port, address.source),
                         ('0.0.0.0', 8480, 'desktop-config'))

    def test_keys_the_shell_does_not_own_survive_a_save(self):
        self.config.write_text(json.dumps({'host': '127.0.0.1', 'port': 8470,
                                           'their_key': {'kept': True}}))
        self.save(self.load(), {'scope': settings.LAN})
        stored = json.loads(self.config.read_text())
        self.assertEqual(stored['their_key'], {'kept': True})
        self.assertEqual(stored['host'], '0.0.0.0')

    def test_an_unreadable_file_is_kept_beside_the_new_one(self):
        self.config.write_text('{"host": "127.0.0.1",')
        loaded = self.load()
        self.assertTrue(loaded.problems, 'a malformed file is reported, not swallowed')
        saved = self.save(loaded, {'port': 8481})
        backup = self.config.with_name(self.config.name + '.bak')
        self.assertEqual(saved.backup, backup)
        self.assertEqual(backup.read_text(), '{"host": "127.0.0.1",')
        self.assertEqual(json.loads(self.config.read_text()),
                         {'host': '127.0.0.1', 'port': 8481, 'web_ui': True, 'subsonic': True})

    def test_a_bad_port_or_host_is_an_error_not_a_default(self):
        loaded = self.load()
        with self.assertRaises(ValueError):
            self.save(loaded, {'port': 'eighty-four-seventy'})
        with self.assertRaises(ValueError):
            self.save(loaded, {'port': 70000})
        with self.assertRaises(ValueError):
            self.save(loaded, {'host': ''})
        with self.assertRaises(ValueError):
            self.save(loaded, {'host': 'two words'})
        self.assertFalse(self.config.exists())

    def test_nothing_changed_writes_nothing(self):
        saved = self.save(self.load(), {})
        self.assertEqual(saved.written, ())
        self.assertFalse(saved.saved)
        self.assertEqual(saved.autostart, 'unchanged')
        self.assertFalse(self.config.exists())
        self.assertEqual(saved.diff['summary'], 'Nothing to apply.')


class TheEnvironmentWins(TempCase):
    def setUp(self):
        super().setUp()
        self.config.write_text(json.dumps({'host': '127.0.0.1', 'port': 8470}))

    def test_a_declared_address_is_locked_and_named(self):
        env = dict(self.env, HARDLYLISTENING_HOST='0.0.0.0', HARDLYLISTENING_PORT='9000')
        loaded = settings.load(env, self.config)
        self.assertEqual((loaded.settings.host, loaded.settings.port), ('0.0.0.0', 9000))
        self.assertEqual(set(loaded.locked), {'host', 'port'})
        self.assertTrue(any('HARDLYLISTENING_HOST' in text for text in loaded.problems),
                        loaded.problems)

    def test_a_locked_field_is_refused_instead_of_silently_ignored(self):
        env = dict(self.env, HARDLYLISTENING_HOST='0.0.0.0')
        loaded = settings.load(env, self.config)
        saved = settings.save(loaded, {'scope': settings.LOCAL}, path=self.config, env=env)
        self.assertEqual(saved.written, ())
        self.assertIn('host', [field for field, _ in saved.refused])
        self.assertIn('HARDLYLISTENING_HOST', dict(saved.refused)['host'])
        self.assertEqual(json.loads(self.config.read_text())['host'], '127.0.0.1')

    def test_an_unlocked_field_still_saves(self):
        env = dict(self.env, HARDLYLISTENING_HOST='0.0.0.0')
        loaded = settings.load(env, self.config)
        saved = settings.save(loaded, {'port': 9001}, path=self.config, env=env)
        self.assertEqual(saved.written, ('port',))
        self.assertEqual(json.loads(self.config.read_text())['port'], 9001)


class AutostartIsAnExplicitChoice(TempCase):
    def test_the_default_is_no_entry_and_nothing_is_written_for_you(self):
        loaded = self.load()
        self.assertFalse(loaded.autostart)
        saved = self.save(loaded, {'scope': settings.LAN})
        self.assertEqual(saved.autostart, 'unchanged')
        self.assertFalse(self.autostart.exists(), 'a save must never install an entry by itself')

    def test_turning_it_on_writes_the_server_launcher_not_the_window(self):
        saved = self.save(self.load(), {}, autostart=True, launcher=LAUNCHER)
        self.assertEqual(saved.autostart, 'written')
        text = self.autostart.read_text()
        exec_line = next(line for line in text.splitlines() if line.startswith('Exec='))
        self.assertIn('run --host 127.0.0.1 --port 8470', exec_line)
        self.assertIn('hardlylistening-server', exec_line)
        # The window is the thing this must NOT start.
        self.assertNotIn('tauri', exec_line.lower())
        self.assertNotIn('desktop', exec_line.lower())
        self.assertIn('X-GNOME-Autostart-enabled=true', text)
        self.assertIn('Terminal=false', text)

    def test_the_autostart_entry_serves_what_the_window_does(self):
        self.save(self.load(), {'web_ui': False, 'subsonic': False},
                  autostart=True, launcher=LAUNCHER)
        exec_line = next(line for line in self.autostart.read_text().splitlines()
                         if line.startswith('Exec='))
        self.assertIn('--no-web-ui --no-subsonic', exec_line)
        # On is the default, so an all-on entry carries neither flag.
        self.save(self.load(), {'web_ui': True, 'subsonic': True},
                  autostart=True, launcher=LAUNCHER)
        exec_line = next(line for line in self.autostart.read_text().splitlines()
                         if line.startswith('Exec='))
        self.assertNotIn('--no-', exec_line)

    def test_turning_it_off_removes_only_that_entry(self):
        self.save(self.load(), {}, autostart=True, launcher=LAUNCHER)
        self.assertTrue(self.autostart.exists())
        other = self.autostart.with_name('someone-elses.desktop')
        other.write_text('[Desktop Entry]\n')
        saved = self.save(self.load(), {}, autostart=False)
        self.assertEqual(saved.autostart, 'removed')
        self.assertFalse(self.autostart.exists())
        self.assertTrue(other.exists(), 'only the entry this shell names is removed')

    def test_an_entry_installed_by_hand_survives_a_save_that_makes_no_choice(self):
        self.autostart.parent.mkdir(parents=True)
        self.autostart.write_text('[Desktop Entry]\nName=installed by hand\n')
        before = self.autostart.read_text()
        self.save(self.load(), {'port': 8482})
        self.assertEqual(self.autostart.read_text(), before)

    def test_turning_it_on_without_a_launcher_is_an_error(self):
        with self.assertRaises(ValueError):
            self.save(self.load(), {}, autostart=True, launcher=None)

    def test_a_path_with_spaces_and_percent_is_quoted_for_the_desktop_entry(self):
        entry = settings.autostart_entry(settings.Settings(),
                                        '/tmp/a folder 100%/hardlylistening-server')
        exec_line = next(line for line in entry.splitlines() if line.startswith('Exec='))
        self.assertIn('"/tmp/a folder 100%%/hardlylistening-server"', exec_line)
        self.assertIn(' run --host ', exec_line)

    def test_the_entry_lives_where_xdg_says_and_the_override_is_honoured(self):
        self.assertEqual(settings.autostart_path({}),
                         Path.home() / '.config' / settings.AUTOSTART_DIR /
                         settings.AUTOSTART_FILE)
        self.assertEqual(settings.autostart_path({'XDG_CONFIG_HOME': '/tmp/example'}),
                         Path('/tmp/example') / 'autostart' / settings.AUTOSTART_FILE)
        self.assertEqual(settings.autostart_path({settings.AUTOSTART_ENV: '/tmp/entry.desktop'}),
                         Path('/tmp/entry.desktop'))

    def test_a_patch_cannot_carry_the_choice(self):
        saved = self.save(self.load(), {'autostart': True}, launcher=LAUNCHER)
        self.assertEqual([field for field, _ in saved.refused], ['autostart'])
        self.assertFalse(self.autostart.exists())
        self.assertEqual(saved.autostart, 'unchanged')


class TheDiffSaysWhenARestartIsNeeded(TempCase):
    def test_a_new_address_needs_a_restart_and_says_where_it_still_answers(self):
        diff = settings.changed(settings.Settings(), settings.Settings(host='0.0.0.0'))
        self.assertTrue(diff['restart_required'])
        self.assertEqual(diff['restart'], ['host'])
        self.assertEqual(diff['changes'][0]['label'], 'Who can reach it')
        self.assertIn('127.0.0.1:8470', diff['summary'])
        self.assertIn('0.0.0.0:8470', diff['summary'])
        self.assertEqual((diff['before'], diff['after']), ('127.0.0.1:8470', '0.0.0.0:8470'))

    def test_a_new_port_needs_one_too(self):
        diff = settings.changed(settings.Settings(), settings.Settings(port=9000))
        self.assertEqual(diff['restart'], ['port'])

    def test_nothing_changed_is_not_a_restart(self):
        diff = settings.changed(settings.Settings(), settings.Settings())
        self.assertFalse(diff['restart_required'])
        self.assertEqual(diff['changes'], [])
        self.assertEqual(diff['summary'], 'Nothing to apply.')

    def test_a_save_reports_the_diff_it_made(self):
        saved = self.save(self.load(), {'scope': settings.LAN})
        self.assertTrue(saved.restart_required)
        self.assertEqual(saved.diff['restart'], ['host'])
        self.assertEqual(saved.to_dict()['settings'],
                         {'host': '0.0.0.0', 'port': 8470, 'scope': settings.LAN,
                          'web_ui': True, 'subsonic': True})

    def test_a_switch_alone_still_needs_a_restart_and_says_which_one(self):
        diff = settings.changed(settings.Settings(), settings.Settings(subsonic=False))
        self.assertEqual(diff['restart'], ['subsonic'])
        self.assertEqual(diff['changes'][0]['label'], 'OpenSubsonic')
        self.assertIn('restart the server', diff['summary'].lower())
        self.assertIn('switches', diff['summary'])


class TheRustAndThePythonShareTheEntry(TempCase):
    """Two constants cannot be avoided on the Rust side; they are compared rather than trusted."""

    def setUp(self):
        self.rust = (SRC_TAURI / 'src' / 'settings.rs').read_text()

    def test_the_entry_name_and_directory_are_the_same_in_both_languages(self):
        for name in ('AUTOSTART_DIR', 'AUTOSTART_FILE'):
            match = re.search(rf'pub const {name}: &str = "([^"]+)";', self.rust)
            self.assertIsNotNone(match, f'{name} is not declared in settings.rs')
            self.assertEqual(match.group(1), getattr(settings, name),
                             f'{name} drifted between Rust and Python')

    def test_the_rust_entry_runs_the_server_launcher_not_the_window(self):
        self.assertIn('X-GNOME-Autostart-enabled', self.rust)
        self.assertIn('run --host', self.rust)
        # The window binary is never the thing an entry starts: the entry's body says so.
        entry = re.search(r'pub fn desktop_entry.*?\n}\n', self.rust, re.S)
        self.assertIsNotNone(entry, 'desktop_entry() is missing from settings.rs')
        self.assertIn('not the desktop window', entry.group(0))
        self.assertNotIn('tauri', entry.group(0))


if __name__ == '__main__':
    unittest.main()