"""The acquisition descriptor, and who is allowed to speak it.

`FED-MIRROR-001` is the first half: a descriptor says only *which provider result to ask for
again* — provider, the provider's own track id, and the metadata a re-acquisition needs. Nothing
that names a credential, a file or a member is allowed into it, and an input that carries any of
that refuses to build rather than being quietly trimmed. The round trip is asserted, not the
function's good intentions: build, encode, decode, and compare bytes.

`FED-001` is the second: Repo federation attaches to `repo/service.py` only, and nothing a player
talks to may reach it. That is a dependency-direction fact, so it is measured from the modules on
disk (AST, like `tests/test_headless.py`) rather than from an import-time trick that a lazy import
would sidestep.
"""
import ast
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from providers.base import TrackRef
from repo import federation, service

APP_DIR = Path(__file__).resolve().parents[1]

CLEAN = ('HARDLYLISTENING_DATA_DIR', 'HARDLYLISTENING_DB')


def run(code: str, **env_overrides):
    """A one-off interpreter, so identity tests never write into the checkout."""
    env = {k: v for k, v in os.environ.items() if k not in CLEAN}
    env.update(env_overrides)
    env['PYTHONPATH'] = str(APP_DIR)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run([sys.executable, '-c', code], cwd=APP_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


class Descriptor(unittest.TestCase):
    def ref(self, **overrides):
        values = dict(provider='soundcloud', provider_track_id='123456', title='Xtal',
                      artist='Aphex Twin', album='Selected Ambient Works', duration=293.0,
                      source_url='https://soundcloud.com/aphex-twin/xtal')
        values.update(overrides)
        return TrackRef(**values)

    def test_a_provider_result_round_trips_through_the_canonical_encoding(self):
        descriptor = service.acquisition_descriptor(self.ref())
        self.assertEqual(descriptor['version'], federation.VERSION)
        text = service.descriptor_json(descriptor)
        self.assertEqual(service.parse_descriptor(text), descriptor)
        # Canonical means stable: parsing and re-encoding cannot change a byte, so two nodes
        # that agree on a descriptor agree on its hash without agreeing on Python.
        self.assertEqual(service.descriptor_json(service.parse_descriptor(text)), text)
        self.assertEqual(text, json.dumps(json.loads(text), ensure_ascii=False,
                                          separators=(',', ':'), sort_keys=True))
        self.assertIn('"version":1', text)

    def test_a_provider_result_with_no_source_url_is_allowed_and_no_other_scheme_is(self):
        self.assertEqual(service.acquisition_descriptor(self.ref(source_url=''))['source_url'], '')
        for bad in ('file:///music/song.mp3', '/music/song.mp3', 'C:\\music\\song.mp3',
                    '~/.cache/song.mp3'):
            with self.subTest(source_url=bad):
                with self.assertRaises(ValueError):
                    service.acquisition_descriptor(self.ref(source_url=bad))

    def test_credential_material_refuses_to_build_and_is_never_echoed(self):
        secrets = {'cookies': 'sessionid=abc', 'http_headers': {'Authorization': 'Bearer abc'},
                   'access_token': 'tok_live_123', 'password': 'hunter2',
                   'api_key': 'sk-live-123', 'cookiefile': '/home/someone/cookies.txt',
                   'session_id': 'sess-1', 'client_secret': 'shh'}
        for name, value in secrets.items():
            with self.subTest(field=name):
                result = {'provider': 'soundcloud', 'provider_track_id': '1', 'title': 'Song',
                          name: value}
                with self.assertRaises(federation.UnsafeDescriptor) as caught:
                    service.acquisition_descriptor(result)
                self.assertIn(name, caught.exception.fields)
                # The refusal is loggable: it names the field, never what was in it.
                self.assertNotIn(str(value), str(caught.exception))

    def test_a_path_or_a_field_off_the_allow_list_refuses_rather_than_dropping(self):
        for name, value in {'file': '/music/song.mp3', 'local_path': 'song.mp3',
                            'outtmpl': '%(title)s.%(ext)s', 'match': 0.93}.items():
            with self.subTest(field=name):
                result = {'provider': 'soundcloud', 'provider_track_id': '1', 'title': 'Song',
                          name: value}
                with self.assertRaises(federation.UnsafeDescriptor) as caught:
                    service.acquisition_descriptor(result)
                self.assertIn(name, caught.exception.fields)

    def test_a_descriptor_from_the_wire_is_checked_again(self):
        good = service.descriptor_json(service.acquisition_descriptor(self.ref()))
        for text in ('[]', 'not a descriptor', '{"version": 2}', '{"version": true}',
                     good.replace('"title"', '"token"')):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    service.parse_descriptor(text)

    def test_duration_must_be_a_finite_number_of_seconds(self):
        payload = service.acquisition_descriptor(self.ref())
        for bad in (float('nan'), float('inf'), -1):
            with self.subTest(duration=bad):
                with self.assertRaises(ValueError):
                    federation.dumps(dict(payload, duration=bad))
                with self.assertRaises(ValueError):
                    service.parse_descriptor(json.dumps(dict(payload, duration=bad)))

    def test_required_fields_cannot_be_blank(self):
        for overrides in ({'title': ''}, {'provider_track_id': '  '}, {'provider': ''},
                          {'title': 'x' * (federation.MAX_TEXT + 1)}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    service.acquisition_descriptor(self.ref(**overrides))

    def test_a_title_is_not_scanned_as_if_it_were_a_secret(self):
        # Fields are judged by name, not by guessing at values: a song may be called anything.
        self.assertEqual(service.acquisition_descriptor(self.ref(title='Password'))['title'],
                         'Password')

    def test_provider_metadata_the_mirror_does_not_use_is_not_carried(self):
        descriptor = service.acquisition_descriptor(
            self.ref(isrc='GBAYE9200001', artwork='https://i1.sndcdn.com/art.jpg',
                     track_number=3, release_id='rel-1'))
        for name in ('isrc', 'artwork', 'track_number', 'release_id'):
            self.assertNotIn(name, descriptor)

    def test_the_alias_names_provider_results_already_use_are_accepted(self):
        descriptor = service.acquisition_descriptor({
            'engine': 'youtube', 'source_id': 'dQw4w9', 'uploader': 'Uploader',
            'url': 'https://www.youtube.com/watch?v=dQw4w9', 'title': 'Song'})
        self.assertEqual(descriptor['provider'], 'youtube')
        self.assertEqual(descriptor['provider_track_id'], 'dQw4w9')
        self.assertEqual(descriptor['artist'], 'Uploader')
        self.assertEqual(descriptor['source_url'], 'https://www.youtube.com/watch?v=dQw4w9')


class Boundary(unittest.TestCase):
    """`FED-001`: federation attaches to `repo/service.py`, and a player never speaks it.

    Measured from the files on disk rather than from `sys.modules`: a lazy import inside a
    function only runs when the function does, and this has to hold before the first request.
    Every Python file in the tree is parsed, so a future `sdk/` or `player/` package joins the
    assertion the day it is created, without anyone remembering to add it here.
    """

    FEDERATION = 'repo.federation'
    #: The modules a player's request actually reaches. Listed so a failure names the surface
    #: rather than only the edge, though the scan below already covers them.
    PLAYER_FACING = ('subsonic.py', 'library/playback.py', 'lyrics.py', 'mixer_room.py',
                     'shares.py', 'listening.py')

    def modules(self):
        for path in sorted(APP_DIR.rglob('*.py')):
            parts = path.relative_to(APP_DIR).parts
            if parts[0] in ('tests', '__pycache__', 'backups'):
                continue
            if parts[-1].startswith('benchmark_'):
                continue
            yield path

    def imports(self, path):
        """Fully-qualified module names, with relative imports resolved to their package."""
        package = list(path.relative_to(APP_DIR).with_suffix('').parts[:-1])
        names = set()
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    keep = package[:len(package) - (node.level - 1)] if node.level > 1 else package
                    base = '.'.join([*keep, base] if base else keep)
                for alias in node.names:
                    names.add(f'{base}.{alias.name}' if base else alias.name)
        return names

    def reachable(self, name):
        return name == self.FEDERATION or name.startswith(self.FEDERATION + '.')

    def test_only_the_repo_package_imports_federation_and_only_its_two_owners(self):
        importers = {path.relative_to(APP_DIR).as_posix()
                     for path in self.modules()
                     if any(self.reachable(name) for name in self.imports(path))}
        # `repo/mirror.py` is the sibling implementation, not a second door: it answers what a
        # descriptor is re-acquired from, which is the same acquisition subject as the descriptor
        # itself. The rule that matters to a player is the second assertion — outside the package,
        # federation is reached through `repo/service.py` or not at all.
        self.assertEqual(importers, {'repo/service.py', 'repo/mirror.py'},
                         'federation belongs to the Repo package and to nobody outside it')
        self.assertFalse([name for name in importers if not name.startswith('repo/')],
                         'a module outside repo/ imported federation directly')

    def test_nothing_a_player_talks_to_imports_it(self):
        for relative in self.PLAYER_FACING:
            path = APP_DIR / relative
            self.assertTrue(path.is_file(), relative)
            self.assertFalse([name for name in self.imports(path) if self.reachable(name)],
                             f'{relative} is player-facing and must not speak federation')

    def test_the_shipped_client_never_names_it(self):
        # The web UI is the other player in this repo: it talks to HTTP, not to Repo's modules.
        words = ('federation', 'acquisition_descriptor')
        for path in sorted((APP_DIR / 'web').rglob('*')):
            if not path.is_file() or path.suffix not in ('.js', '.html', '.css'):
                continue
            text = path.read_text(errors='replace').lower()
            for word in words:
                self.assertNotIn(word, text, f'{path.relative_to(APP_DIR)} names {word}')

    def test_the_facade_reaches_it_and_does_not_expose_the_secret(self):
        descriptor = service.acquisition_descriptor(
            TrackRef(provider='soundcloud', provider_track_id='1', title='Song'))
        self.assertEqual(descriptor['provider_track_id'], '1')
        self.assertTrue(callable(service.node_id))
        self.assertFalse(hasattr(service, 'node_secret'),
                         'the secret has no reason to be on the facade')


class NodeIdentity(unittest.TestCase):
    """`FED-001`'s key half: generated once, `0600`, never regenerated, never printed.

    Run in a subprocess with a declared data directory, because `paths.IDENTITY_DIR` is resolved
    at import time and the checkout must not gain key material.
    """

    def setUp(self):
        self.data = tempfile.mkdtemp(prefix='hl-identity-')
        self.addCleanup(shutil.rmtree, self.data, True)
        self.identity = Path(self.data) / 'identity'

    def test_a_fresh_install_has_no_key_material_until_it_is_asked_for(self):
        result = run('import paths, repo.federation\n'
                     'paths.ensure()\n'
                     'print(sorted(p.name for p in paths.IDENTITY_DIR.iterdir()))\n'
                     'print(oct(paths.IDENTITY_DIR.stat().st_mode & 0o777))',
                     HARDLYLISTENING_DATA_DIR=self.data)
        self.assertEqual(result.returncode, 0, result.stderr)
        files, mode = result.stdout.strip().splitlines()[-2:]
        self.assertEqual(files, '[]', 'a fresh install is not given keys it may never use')
        self.assertEqual(mode, '0o700')

    def test_the_key_is_generated_once_and_stays_private(self):
        result = run('import json\n'
                     'import paths, repo.federation as fed\n'
                     'paths.ensure()\n'
                     'first = (fed.node_id(), fed.node_secret())\n'
                     'second = (fed.node_id(), fed.node_secret())\n'
                     'print(json.dumps({"stable": first == second, "id": first[0],\n'
                     '                  "secret_len": len(first[1])}))',
                     HARDLYLISTENING_DATA_DIR=self.data)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(payload['stable'], 'a second call must not regenerate anything')
        self.assertEqual(payload['secret_len'], federation.SECRET_BYTES)

        stored_id = (self.identity / federation.NODE_ID_FILE).read_text().strip()
        stored_secret = (self.identity / federation.NODE_SECRET_FILE).read_text().strip()
        self.assertEqual(stored_id, payload['id'])
        self.assertRegex(stored_id, r'^[0-9a-f]{32}$')
        self.assertEqual(len(base64.urlsafe_b64decode(stored_secret + '=' * (-len(stored_secret) % 4))),
                         federation.SECRET_BYTES)
        self.assertEqual(int((self.identity / federation.NODE_ID_FILE).stat().st_mode) & 0o777, 0o644)
        self.assertEqual(int((self.identity / federation.NODE_SECRET_FILE).stat().st_mode) & 0o777,
                         0o600)
        # The rule that matters most: nothing printed the secret, and nothing can, because the
        # module has no logging and the only way out is a return value.
        self.assertNotIn(stored_secret, result.stdout)
        self.assertNotIn(stored_secret, result.stderr)

    def test_an_existing_identity_is_never_regenerated(self):
        self.identity.mkdir(mode=0o700)
        (self.identity / federation.NODE_ID_FILE).write_text('node-abc')
        stamp = (self.identity / federation.NODE_ID_FILE).stat().st_mtime_ns
        result = run('import repo.federation as fed; print(fed.node_id()); print(len(fed.node_secret()))',
                     HARDLYLISTENING_DATA_DIR=self.data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split()[0], 'node-abc')
        self.assertEqual((self.identity / federation.NODE_ID_FILE).stat().st_mtime_ns, stamp)
        self.assertTrue((self.identity / federation.NODE_SECRET_FILE).is_file())

    def test_an_empty_secret_is_an_error_rather_than_a_silent_rekey(self):
        self.identity.mkdir(mode=0o700)
        (self.identity / federation.NODE_ID_FILE).write_text('node-abc')
        (self.identity / federation.NODE_SECRET_FILE).write_text('')
        result = run('import repo.federation as fed\n'
                     'try:\n'
                     '    fed.node_secret()\n'
                     'except RuntimeError as error:\n'
                     '    print("refused")\n',
                     HARDLYLISTENING_DATA_DIR=self.data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'refused')
        self.assertEqual((self.identity / federation.NODE_SECRET_FILE).read_text(), '')


if __name__ == '__main__':
    unittest.main()