"""PLAYER-001: the player is a Tauri 2 shell around its own Svelte build.

What is asserted here is the *shell*: `tauri.conf.json`, the capability file and `Cargo.toml`
are the files Tauri 2 reads, and the window has to open the Svelte build in `player/ui/dist`,
not some other tree. What the build itself must and must not contain is
`tests/test_player_store_safe.py`'s job — that test walks the tree the window loads.

The old halves are gone with the old shell. `player/shell/` and `player/assemble.mjs` assembled
the player out of `web/`, and P4 retired both: the window loads `ui/dist` and the assembler ran
nothing the shipped binary reads. Their assertions went with them rather than being rewritten
against a different tree, because the tree they described (one entry document, one connect
screen, `player/www`) no longer exists.

The player is a client, so it also has to stay a separate tree: nothing under `player/` may
reach `desktop/` (another shippable) or import the server's Python. That is asserted here as
well as by `tests/test_headless.py`, which is what keeps `player/` from turning into a host
the core needs.
"""
import json
import re
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PLAYER = APP_DIR / 'player'
TAURI = PLAYER / 'src-tauri'

CODE_SUFFIXES = {'.rs', '.js', '.mjs', '.ts', '.svelte', '.html', '.css', '.json', '.toml'}
#: What the player must never reach for. The host shell is a separate artefact, and the
#: server is something a player talks to over HTTP, never imports.
FOREIGN = ('desktop/', 'from desktop', 'import desktop', 'server.py', 'listening.py',
           'subsonic.py', 'sys.path')


def player_sources():
    for path in sorted(PLAYER.rglob('*')):
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue
        parts = path.relative_to(PLAYER).parts
        # `ui/node_modules` and `ui/dist` are installed and generated, not written here.
        if parts[0] in ('target', 'gen'):
            continue
        if 'node_modules' in parts or 'dist' in parts:
            continue
        yield path


class TauriConfiguration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((TAURI / 'tauri.conf.json').read_text())
        cls.capabilities = [json.loads(path.read_text())
                            for path in sorted((TAURI / 'capabilities').glob('*.json'))]

    def test_the_window_opens_at_the_app_root(self):
        windows = self.config['app']['windows']
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]['label'], 'main')
        self.assertEqual(windows[0]['url'], 'index.html')

    def test_the_front_end_is_the_svelte_build_and_its_build_runs_first(self):
        build = self.config['build']
        self.assertEqual(build['frontendDist'], '../ui/dist')
        self.assertIn('ui', build['beforeBuildCommand'])
        self.assertIn('ui', build['beforeDevCommand'])
        self.assertTrue((PLAYER / 'ui' / 'package.json').is_file())
        self.assertTrue((PLAYER / 'ui' / 'vite.config.ts').is_file())

    def test_desktop_and_mobile_targets_are_declared(self):
        bundle = self.config['bundle']
        self.assertTrue(bundle['active'])
        self.assertEqual(bundle['targets'], 'all')
        self.assertIn('minimumSystemVersion', bundle['iOS'])
        self.assertGreaterEqual(int(bundle['android']['minSdkVersion']), 24)

    def test_the_window_may_reach_the_server_somebody_typed(self):
        """`connect-src` is the reason a sign-in can fail before a request ever leaves the app.

        `'self'` plus the native bridge covers the bundled page and `invoke`, and nothing else.
        A client whose entire purpose is to talk to a server the person names has to be allowed
        to make that request: while it was not, the page-side transport was refused by its own
        policy and the screen said "could not reach" about a server that was answering.
        """
        csp = self.config['app']['security']['csp']
        rules = [part.strip() for part in csp.split(';') if part.strip()]
        connect = [rule for rule in rules if rule.startswith('connect-src')]
        self.assertEqual(len(connect), 1)
        sources = connect[0].split()[1:]
        self.assertIn('ipc:', sources, 'the native bridge still has to be allowed')
        for scheme in ('https:', 'http:'):
            self.assertIn(scheme, sources)
        self.assertNotIn('*', sources)
        # Widening the connection policy must not widen the script policy with it.
        script = [rule for rule in rules if rule.startswith('script-src')]
        self.assertEqual(script, ["script-src 'self'"])

    def test_one_version_is_stamped_wherever_it_is_read(self):
        ui = json.loads((PLAYER / 'ui' / 'package.json').read_text())
        source = (PLAYER / 'ui' / 'src' / 'lib' / 'app' / 'version.ts').read_text()
        stamped = re.search(r'APP_VERSION = "([^"]+)"', source).group(1)
        self.assertEqual(self.config['version'], ui['version'])
        self.assertEqual(self.config['version'], stamped)

    def test_capabilities_grant_the_core_and_nothing_else(self):
        self.assertTrue(self.capabilities, 'no capability file means no window can load')
        for capability in self.capabilities:
            self.assertEqual(capability['windows'], ['main'])
            self.assertTrue(capability['permissions'])
            for permission in capability['permissions']:
                self.assertTrue(permission.startswith('core:'),
                                f'{permission} is a plugin permission the shell does not use')

    def test_cargo_is_tauri_2_and_builds_a_library_mobile_can_link(self):
        text = (TAURI / 'Cargo.toml').read_text()
        self.assertIn('tauri-build = { version = "2"', text)
        self.assertIn('tauri = { version = "2"', text)
        crate = re.search(r'crate-type = \[(.*?)\]', text).group(1)
        for kind in ('staticlib', 'cdylib', 'rlib'):
            self.assertIn(kind, crate)

    def test_no_generated_mobile_project_is_checked_in(self):
        for platform in ('apple', 'android'):
            self.assertFalse((TAURI / 'gen' / platform).exists(),
                             f'gen/{platform} is generated by the Tauri CLI, not committed')
        # The docs have to say so, because the generated project is not here to read.
        docs = (APP_DIR / 'docs' / 'player.md').read_text()
        self.assertIn('tauri ios init', docs)
        self.assertIn('tauri android init', docs)


class Independence(unittest.TestCase):
    """A player is a client: it does not need the host shell or the server's code."""

    def test_the_ios_recipe_gives_the_product_the_keys_a_phone_needs(self):
        """Two keys cannot live in the repo, so the recipe writes them and reads them back.

        `tauri ios init`/`ios build` regenerate `Info.plist`, so `UIBackgroundModes: [audio]`
        (without it iOS suspends the app at lock) and `NSAllowsLocalNetworking` (without it a
        plain `http://` LAN server is refused in the WebView's own `<img>`/`<audio>` loads, and
        from iOS 17 a direct IP address is refused by default) are written onto the built
        product. A recipe that stops writing one would still produce an IPA, so the check is
        that each key is written *and* guarded by a read-back that fails the build.
        """
        yaml = __import__('yaml')
        recipe = yaml.safe_load((APP_DIR / 'codemagic.yaml').read_text())
        workflows = recipe['workflows']
        self.assertIn('player-ios-unsigned', workflows)
        steps = {step['name']: step['script'] for step in workflows['player-ios-unsigned']['scripts']}
        packaging = steps['Package the unsigned IPA']

        for key, guard in (
            ('UIBackgroundModes:0', 'the audio background mode'),
            ('NSAppTransportSecurity:NSAllowsLocalNetworking', 'the local networking exception'),
        ):
            self.assertIn(f'Add :{key}', packaging, f'{key} is never written')
            self.assertIn(f'Print :{key}', packaging, f'{key} is never read back')
            self.assertIn(guard, packaging, f'{key} has no failing guard')
        # The narrow key, not the wide one: arbitrary loads would open every public host.
        self.assertNotIn('Add :NSAppTransportSecurity:NSAllowsArbitraryLoads', packaging,
                         'arbitrary loads would open every public host, not just the LAN')
        # The product, not the intermediate .app, is what reaches a phone.
        self.assertIn('ipa-check', packaging)
        self.assertIn('HardlyListeningPlayer-unsigned.ipa',
                      workflows['player-ios-unsigned'].get('artifacts', [''])[0])

    def test_the_player_is_a_tree_of_its_own(self):
        self.assertFalse((APP_DIR / 'src-tauri').exists(), 'the core must not gain a host shell')
        self.assertTrue((TAURI / 'tauri.conf.json').is_file())
        self.assertTrue((PLAYER / 'ui' / 'index.html').is_file())

    def test_the_player_reaches_neither_the_host_nor_the_server(self):
        offenders = []
        for path in player_sources():
            text = path.read_text(errors='replace')
            for needle in FOREIGN:
                if needle in text:
                    offenders.append(f'{path.relative_to(APP_DIR)}: {needle}')
        self.assertEqual(offenders, [], 'the player must not import the host or the server')


if __name__ == '__main__':
    unittest.main()