"""HOST-004: two independent choices, and the four states they make.

The card: *run a local server on/off*, and *point the UI at local or remote* are separate
questions; remote-only, with no local server, is a supported state. The model lives in
`desktop/host/mode.py` and is rendered in `desktop/ui/mode.js`; the crate cannot be compiled on
this machine, so these two files plus `tests/test_host_mode.cjs` are the proof.

What is pinned here, and why each one is worth a test:

  * **the matrix** — all four combinations, with the address the window talks to and whether
    Start/Stop act at all. A fifth state, or a missing one, means the model drifted;
  * **the non-couplings** — turning the local server off must not rewrite the target, and
    switching the target to remote must not stop (or start) the server. This is the card's
    actual subject: the two controls are independent, and a helper that re-derives one from the
    other is exactly the bug the card forbids;
  * **the remote-origin rule** — scheme required, https preferred, one trailing slash, and an
    address that is this computer refused;
  * **the persistence format** — a stored file survives a round trip, a broken one is reported
    and ignored, and an unusable remote address never silently becomes "local".

Nothing here starts a server, opens a socket or writes outside a temporary directory.
"""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DESKTOP = APP_DIR / 'desktop'


def load_module(name):
    path = DESKTOP / 'host' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(f'desktop_host_{name}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mode = load_module('mode')
resolve = load_module('resolve')

IDS = {
    (True, 'local'): 'local',
    (True, 'remote'): 'local-behind-remote',
    (False, 'local'): 'local-off-pointed-local',
    (False, 'remote'): 'remote-only',
}


class TheFourCombinations(unittest.TestCase):
    def test_every_pair_of_answers_has_exactly_one_state(self):
        for run_local in (True, False):
            for target in mode.TARGETS:
                found = mode.combination(run_local, target)
                self.assertEqual(found.id, IDS[(run_local, target)],
                                 f'{run_local}/{target} is {found.id}')

    def test_the_matrix_is_what_the_documentation_says(self):
        # id, talk_to, start, stop, needs_attention — one row per combination.
        expected = {
            'local': ('local', True, True, False),
            'local-behind-remote': ('remote', True, True, False),
            'local-off-pointed-local': ('local', False, False, True),
            'remote-only': ('remote', False, False, False),
        }
        for (run_local, target), combination_id in IDS.items():
            found = mode.combination(run_local, target)
            self.assertEqual((found.talk_to, found.start_enabled, found.stop_enabled,
                              found.needs_attention),
                             expected[combination_id], combination_id)

    def test_the_local_server_controls_only_exist_when_there_is_a_local_server(self):
        for run_local in (True, False):
            for target in mode.TARGETS:
                found = mode.combination(run_local, target)
                self.assertEqual(found.start_enabled, run_local, found.id)
                self.assertEqual(found.stop_enabled, run_local, found.id)

    def test_the_window_talks_to_the_address_the_target_names(self):
        for run_local in (True, False):
            self.assertEqual(mode.combination(run_local, 'local').talk_to, 'local')
            self.assertEqual(mode.combination(run_local, 'remote').talk_to, 'remote')

    def test_remote_only_is_supported_and_not_degraded(self):
        # The sentence on the card, as an assertion: no state is a degraded one.
        for run_local in (True, False):
            for target in mode.TARGETS:
                self.assertFalse(mode.combination(run_local, target).degraded,
                                 f'{run_local}/{target}')
        self.assertEqual(mode.combination(False, 'remote').id, 'remote-only')

    def test_only_the_dead_end_asks_for_attention(self):
        for (run_local, target), combination_id in IDS.items():
            self.assertEqual(mode.combination(run_local, target).needs_attention,
                             combination_id == 'local-off-pointed-local')

    def test_an_unknown_target_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            mode.combination(True, 'elsewhere')
        with self.assertRaises(ValueError):
            mode.mode(target='elsewhere')


class TheTwoChoicesNeverMoveEachOther(unittest.TestCase):
    def test_turning_the_local_server_off_does_not_rewrite_the_target(self):
        remote = mode.mode(target='remote', remote_origin=mode.parse_origin(
            'https://music.example.org'))
        off = mode.with_local_server(remote, False)
        self.assertEqual(off.target, 'remote')
        self.assertEqual(off.remote_origin, remote.remote_origin)
        self.assertEqual(off.combination.id, 'remote-only')
        # ...and the way back does not invent a target either.
        self.assertEqual(mode.with_local_server(off, True).target, 'remote')

    def test_turning_the_local_server_off_does_not_touch_a_local_target_either(self):
        # The awkward state is preserved on purpose: the window says what is wrong instead of
        # quietly re-pointing itself at a remote server.
        off = mode.with_local_server(mode.mode(), False)
        self.assertEqual(off.target, 'local')
        self.assertEqual(off.combination.id, 'local-off-pointed-local')

    def test_switching_the_target_to_remote_leaves_the_local_server_choice_alone(self):
        on = mode.mode()
        remote = mode.with_target(on, 'remote', mode.parse_origin('https://music.example.org'))
        self.assertTrue(remote.run_local_server)
        self.assertEqual(remote.combination.id, 'local-behind-remote')
        self.assertEqual(mode.with_target(remote, 'local').run_local_server, True)

    def test_switching_the_target_with_the_local_server_off_stays_off(self):
        off = mode.with_local_server(mode.mode(), False)
        remote = mode.with_target(off, 'remote', mode.parse_origin('https://music.example.org'))
        self.assertFalse(remote.run_local_server)
        self.assertEqual(remote.combination.id, 'remote-only')

    def test_a_remembered_remote_address_survives_a_switch_back_to_local(self):
        origin = mode.parse_origin('https://music.example.org')
        remote = mode.with_target(mode.mode(), 'remote', origin)
        back = mode.with_target(remote, 'local')
        self.assertEqual(back.remote_origin, origin)
        self.assertEqual(back.endpoint('http://127.0.0.1:8470'), 'http://127.0.0.1:8470')


class TheAddressTheWindowUses(unittest.TestCase):
    def test_local_uses_the_resolved_local_address(self):
        local = mode.mode()
        self.assertEqual(local.endpoint('http://127.0.0.1:8470'), 'http://127.0.0.1:8470')

    def test_remote_uses_the_remote_origin_with_one_trailing_slash(self):
        remote = mode.mode(target='remote', remote_origin=mode.parse_origin(
            'https://music.example.org:8443'))
        self.assertEqual(remote.endpoint('http://127.0.0.1:8470'),
                         'https://music.example.org:8443/')

    def test_a_remote_target_with_no_address_talks_to_nothing_and_says_so(self):
        remote = mode.mode(target='remote')
        self.assertIsNone(remote.endpoint('http://127.0.0.1:8470'))
        self.assertIn('the remote address', remote.unreachable_text('http://127.0.0.1:8470'))

    def test_remote_only_with_the_remote_down_does_not_tell_the_person_to_run_a_server(self):
        off = mode.with_local_server(mode.mode(), False)
        remote = mode.with_target(off, 'remote', mode.parse_origin('https://music.example.org'))
        text = remote.unreachable_text('http://127.0.0.1:8470')
        self.assertIn('https://music.example.org/', text)
        self.assertIn('runs no local server', text)
        self.assertIn('does not need one', text)
        # It offers the switch, it does not require it.
        self.assertIn('or switch the local server on', text)
        self.assertNotRegex(text, r'(?i)\bdegraded\b|\bfallback\b')

    def test_the_dead_end_explains_itself_without_rewriting_anything(self):
        off = mode.with_local_server(mode.mode(), False)
        text = off.unreachable_text('http://127.0.0.1:8470')
        self.assertIn('http://127.0.0.1:8470', text)
        self.assertIn('not running a local server', text)
        self.assertEqual(off.target, 'local')


class TheRemoteOriginRule(unittest.TestCase):
    def test_a_scheme_is_required(self):
        with self.assertRaises(ValueError) as raised:
            mode.parse_origin('music.example.org')
        self.assertIn('needs a scheme', str(raised.exception))
        self.assertIn('https://music.example.org', str(raised.exception))
        self.assertIn('needs a scheme', mode.check_origin('music.example.org')[1])

    def test_only_http_and_https_are_schemes_this_window_speaks(self):
        for raw in ('ftp://music.example.org', 'file:///srv/music', '//music.example.org'):
            with self.assertRaises(ValueError, msg=raw):
                mode.parse_origin(raw)

    def test_https_is_the_canonical_form(self):
        origin = mode.parse_origin('HTTPS://Music.Example.ORG')
        self.assertEqual(origin.url, 'https://music.example.org/')
        self.assertTrue(origin.secure)
        self.assertEqual(origin.warning, '')

    def test_http_is_accepted_and_flagged(self):
        origin = mode.parse_origin('http://music.example.org:8470')
        self.assertEqual(origin.url, 'http://music.example.org:8470/')
        self.assertFalse(origin.secure)
        self.assertIn('https is preferred', origin.warning.lower())

    def test_one_trailing_slash_is_the_canonical_form(self):
        for raw in ('https://music.example.org', 'https://music.example.org/',
                    'https://music.example.org///'):
            self.assertEqual(mode.parse_origin(raw).url, 'https://music.example.org/', raw)

    def test_an_origin_is_not_a_path(self):
        with self.assertRaises(ValueError):
            mode.parse_origin('https://music.example.org/library')
        with self.assertRaises(ValueError):
            mode.parse_origin('https://music.example.org/?x=1')
        with self.assertRaises(ValueError):
            mode.parse_origin('https://music.example.org/#top')
        with self.assertRaises(ValueError):
            mode.parse_origin('https://someone:secret@music.example.org/')

    def test_a_port_is_a_number_in_range(self):
        self.assertEqual(mode.parse_origin('https://music.example.org:8443/').port, 8443)
        for raw in ('https://music.example.org:port/', 'https://music.example.org:0/',
                    'https://music.example.org:70000/'):
            with self.assertRaises(ValueError, msg=raw):
                mode.parse_origin(raw)

    def test_ipv6_needs_brackets_and_keeps_them_in_the_url(self):
        self.assertEqual(mode.parse_origin('https://[2001:db8::10]:8443/').url,
                         'https://[2001:db8::10]:8443/')
        with self.assertRaises(ValueError):
            mode.parse_origin('https://2001:db8::10/')

    def test_an_address_that_is_this_computer_is_refused(self):
        for raw in ('https://127.0.0.1:8470/', 'https://localhost:8470/', 'https://[::1]/',
                    'https://0.0.0.0:8470/', 'https://127.9.9.9/'):
            with self.assertRaises(ValueError, msg=raw) as raised:
                mode.parse_origin(raw)
            self.assertIn('is this computer', str(raised.exception), raw)

    def test_the_local_server_address_is_not_a_remote_address(self):
        # The window knows the host it resolved; a "remote" that is that host is local.
        with self.assertRaises(ValueError):
            mode.parse_origin('http://192.168.1.50:8470/', local_host='192.168.1.50')
        self.assertEqual(mode.parse_origin('http://192.168.1.50:8470/').url,
                         'http://192.168.1.50:8470/')

    def test_this_machines_own_name_is_not_a_remote_address(self):
        names = ['media-box', 'media-box.example']
        with self.assertRaises(ValueError):
            mode.parse_origin('https://media-box.example/', machine_names=names)
        self.assertTrue(mode.parse_origin('https://media-box.tail.example/',
                                          machine_names=['media-box']).secure)

    def test_an_empty_address_is_not_an_address(self):
        for raw in ('', '   ', None):
            with self.assertRaises(ValueError):
                mode.parse_origin(raw)


class TheStoredFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hl-mode-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, True))
        self.path = self.tmp / 'mode.json'

    def test_the_default_is_one_local_server_used_here(self):
        found = mode.load({}, self.path)
        self.assertEqual((found.run_local_server, found.target), (True, 'local'))
        self.assertEqual(found.combination.id, 'local')
        self.assertEqual(found.problem, '')

    def test_the_payload_is_the_documented_format(self):
        remote = mode.with_target(mode.mode(), 'remote',
                                  mode.parse_origin('https://music.example.org:8443'))
        payload = remote.payload()
        self.assertEqual(payload, {
            'version': 1,
            'run_local_server': True,
            'target': 'remote',
            'remote_origin': 'https://music.example.org:8443/',
        })
        self.assertEqual(json.loads(mode.dumps(remote)), payload)
        self.assertTrue(mode.dumps(remote).endswith('\n'))

    def test_the_file_lives_beside_the_host_file(self):
        path = mode.mode_path({'XDG_STATE_HOME': str(self.tmp)})
        self.assertEqual(path, self.tmp / 'hardlylistening-desktop' / 'mode.json')
        self.assertEqual(path.parent, resolve.config_path(
            {'XDG_STATE_HOME': str(self.tmp)}).parent)
        self.assertEqual(mode.CONFIG_NAME, resolve.CONFIG_NAME)
        self.assertEqual(mode.mode_path({'HARDLYLISTENING_DESKTOP_MODE': str(self.path)}),
                         self.path)

    def test_the_mode_file_is_not_the_host_file(self):
        # HOST-005 owns host.json. Two files, so a settings write cannot lose the choices.
        self.assertNotEqual(mode.MODE_FILE, resolve.CONFIG_FILE)

    def test_a_round_trip_keeps_every_answer(self):
        stored = {
            'version': 1,
            'run_local_server': False,
            'target': 'remote',
            'remote_origin': 'https://music.example.org/',
        }
        self.path.write_text(json.dumps(stored))
        found = mode.load({}, self.path)
        self.assertEqual(found.problem, '')
        self.assertEqual((found.run_local_server, found.target, found.remote_origin.url),
                         (False, 'remote', 'https://music.example.org/'))
        self.assertEqual(found.combination.id, 'remote-only')

    def test_a_malformed_file_is_reported_and_ignored(self):
        self.path.write_text('{"target": "remote",')
        found = mode.load({}, self.path)
        self.assertEqual((found.run_local_server, found.target), (True, 'local'))
        self.assertIn('not JSON', found.problem)

    def test_an_unknown_target_is_reported_and_the_default_kept(self):
        self.path.write_text(json.dumps({'target': 'elsewhere'}))
        found = mode.load({}, self.path)
        self.assertEqual(found.target, 'local')
        self.assertIn('elsewhere', found.problem)

    def test_a_wrong_version_is_reported_and_the_defaults_kept(self):
        self.path.write_text(json.dumps({'version': 99, 'run_local_server': False,
                                         'target': 'remote'}))
        found = mode.load({}, self.path)
        self.assertEqual((found.run_local_server, found.target), (True, 'local'))
        self.assertIn('version 99', found.problem)

    def test_run_local_server_must_be_a_boolean(self):
        self.path.write_text(json.dumps({'run_local_server': 'false'}))
        found = mode.load({}, self.path)
        self.assertTrue(found.run_local_server)
        self.assertIn('not true or false', found.problem)

    def test_a_remote_target_without_an_address_stays_remote(self):
        # The asymmetry that matters: no silent fall back to the local server.
        self.path.write_text(json.dumps({'run_local_server': False, 'target': 'remote'}))
        found = mode.load({}, self.path)
        self.assertEqual(found.target, 'remote')
        self.assertFalse(found.run_local_server)
        self.assertIsNone(found.remote_origin)
        self.assertIn('no usable address', found.problem)
        self.assertEqual(found.combination.id, 'remote-only')
        self.assertEqual(found.talk_to, 'remote')

    def test_a_remote_target_keeps_the_local_server_choice_it_was_given(self):
        # No stored choice means the default, one local server — and that is a real combination.
        self.path.write_text(json.dumps({'target': 'remote'}))
        found = mode.load({}, self.path)
        self.assertTrue(found.run_local_server)
        self.assertEqual(found.combination.id, 'local-behind-remote')

    def test_an_unusable_stored_address_is_reported_and_the_target_kept(self):
        self.path.write_text(json.dumps({'target': 'remote',
                                         'remote_origin': 'music.example.org'}))
        found = mode.load({}, self.path)
        self.assertEqual(found.target, 'remote')
        self.assertIsNone(found.remote_origin)
        self.assertIn('unusable', found.problem)
        self.assertIn('needs a scheme', found.problem)

    def test_a_stored_address_that_is_this_computer_is_refused(self):
        self.path.write_text(json.dumps({'target': 'remote',
                                         'remote_origin': 'http://127.0.0.1:8470/'}))
        found = mode.load({}, self.path)
        self.assertIsNone(found.remote_origin)
        self.assertIn('unusable', found.problem)

    def test_the_dead_end_state_is_persisted_not_corrected(self):
        self.path.write_text(json.dumps({'run_local_server': False, 'target': 'local'}))
        found = mode.load({}, self.path)
        self.assertEqual(found.problem, '')
        self.assertEqual(found.combination.id, 'local-off-pointed-local')
        self.assertTrue(found.needs_attention)


if __name__ == '__main__':
    unittest.main()