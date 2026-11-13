"""HOST-001: the desktop shell is a host, and the server does not need it.

The Tauri crate cannot be compiled on this machine — there is no `webkit2gtk-4.1` and adding it
needs an OS layering change on an immutable host — so this file is what stands in for `cargo
build`. Two halves, the same shape as `tests/test_headless.py`:

  * **The shell's own files** are parsed rather than trusted: the Tauri config, every capability
    file, the sidecar name against the real launcher, and the icon list against the files on
    disk. A bundle config that names a sidecar nobody staged is a release that ships without a
    server, and it fails silently at launch.
  * **The separation** in the other direction: the desktop imports no server module, and no
    server module mentions the desktop. That is the mirror of `test_headless.py`, which forbids
    the core from importing a GUI toolkit. Together they say the GUI is optional and the server
    is not.

`desktop/host/resolve.py` holds the path and address rules so they can be *run* here; the Rust
counterpart is a rendering of those rules, and the two constants it cannot avoid repeating are
compared below.

Nothing here starts a server, opens a socket or writes outside the test's own temporary
directory. The only process spawned is the staging script, on the real launcher, into a copy.
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

#: Text files that are ours to read. Icons are images and are checked by existence instead.
TEXT_SUFFIXES = {'.py', '.rs', '.js', '.cjs', '.json', '.html', '.css', '.md', '.toml', '.sh'}

#: Nothing the shell ships may name a person's machine or home directory: the window is
#: distributed, and a path from the machine it was written on is both useless and private.
FORBIDDEN_LITERALS = (
    '/home/', '/Users/', '/var/home/', 'bazzite', 'cenvalweb', '.ts.net', '10.0.0.',
    'hardysleep', 'tail900410', 'C:\\\\Users\\\\',
)


def load_resolve():
    path = DESKTOP / 'host' / 'resolve.py'
    spec = importlib.util.spec_from_file_location('desktop_host_resolve', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve = load_resolve()


def text_files(root):
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            yield path


def imported_roots(tree):
    """Top-level names a module imports, however deep the import sits (as test_headless does)."""
    import ast

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                yield node.module.split('.')[0]


def core_files():
    """The server's Python: everything except the tests and the desktop shell."""
    for path in sorted(APP_DIR.rglob('*.py')):
        parts = path.relative_to(APP_DIR).parts
        if parts[0] in ('tests', '__pycache__', 'desktop'):
            continue
        yield path


class TauriConfig(unittest.TestCase):
    def setUp(self):
        self.conf = json.loads((SRC_TAURI / 'tauri.conf.json').read_text())

    def test_it_is_a_v2_config(self):
        self.assertTrue(self.conf['$schema'].startswith('https://schema.tauri.app/config/2'))
        self.assertEqual(self.conf['identifier'], 'app.hardlylistening.desktop')
        # Tauri 2 nests the window and the security policy under `app`; a v1 file would have
        # `tauri` and `build.distDir` instead, and would not build with `tauri = "2"`.
        self.assertIn('app', self.conf)
        self.assertNotIn('tauri', self.conf)
        self.assertIn('windows', self.conf['app'])
        self.assertIn('security', self.conf['app'])

    def test_the_window_loads_the_ui_directory_as_written(self):
        # No bundler, no dev server: the assets Tauri embeds are the files in desktop/ui.
        dist = (SRC_TAURI / self.conf['build']['frontendDist']).resolve()
        self.assertEqual(dist, (DESKTOP / 'ui').resolve())
        self.assertTrue((dist / 'index.html').is_file())
        self.assertTrue((dist / 'state.js').is_file())
        self.assertTrue((dist / 'app.js').is_file())
        self.assertFalse((dist / 'node_modules').exists())
        self.assertFalse((DESKTOP / 'package.json').exists(), 'no JS toolchain belongs here')
        page = (dist / 'index.html').read_text()
        # Scripts are files, not inline: the CSP below forbids inline script, so an inline
        # handler would work in a browser and break in the bundled app.
        self.assertNotRegex(page, r'<script(?![^>]*\bsrc=)[^>]*>')
        self.assertRegex(page, r'<script src="state\.js">')

    def test_the_security_policy_allows_only_local_assets_and_ipc(self):
        csp = self.conf['app']['security']['csp']
        self.assertIn("default-src 'self'", csp)
        self.assertIn('ipc:', csp)
        # No remote origin, no eval: the window's data arrives over the shell's own commands.
        self.assertNotIn('unsafe-eval', csp)
        self.assertNotRegex(csp, r'https?://(?!ipc\.localhost)')

    def test_the_sidecar_is_declared_and_named_like_the_real_launcher(self):
        external = self.conf['bundle']['externalBin']
        self.assertEqual(external, ['bin/hardlylistening-server'])
        declared = Path(external[0]).name
        self.assertEqual(declared, resolve.LAUNCHER_NAME)
        launcher = APP_DIR / 'bin' / declared
        self.assertTrue(launcher.is_file(), 'the sidecar is bundled from the canonical launcher')
        self.assertEqual(launcher.name, (APP_DIR / 'bin' / resolve.LAUNCHER_NAME).name)
        # The bundle builds the staged copy itself, from that same file.
        self.assertIn('stage_sidecar.py', self.conf['build']['beforeBuildCommand'])

    def test_the_icon_list_names_files_that_exist(self):
        icons = self.conf['bundle']['icon']
        self.assertTrue(icons)
        for entry in icons:
            self.assertTrue((SRC_TAURI / entry).is_file(),
                            f'{entry} is in the config but not on disk')


class EveryModuleIsWired(unittest.TestCase):
    """A feature module that nobody loads is a card that is not actually done.

    The shell has one page and no bundler, so "is it in the build" is a question the entry
    document answers: every `.js` under `desktop/ui` must be referenced there, every reference
    must resolve to a file, and every element `app.js` reaches for must exist in the markup.
    """

    def setUp(self):
        self.ui = DESKTOP / 'ui'
        self.page = (self.ui / 'index.html').read_text()

    def test_every_ui_module_is_loaded_by_the_page(self):
        loaded = set(re.findall(r'<script src="([^"]+)"', self.page))
        on_disk = {path.name for path in self.ui.glob('*.js')}
        self.assertEqual(sorted(on_disk - loaded), [], 'an unwired module is a missing feature')

    def test_every_script_reference_resolves(self):
        for name in re.findall(r'<script src="([^"]+)"', self.page):
            self.assertTrue((self.ui / name).is_file(), f'{name} is requested and absent')

    def test_the_modules_app_js_uses_load_before_it(self):
        loaded = re.findall(r'<script src="([^"]+)"', self.page)
        self.assertIn('app.js', loaded)
        app_at = loaded.index('app.js')
        for name in ('state.js', 'mode.js', 'address.js', 'lifecycle.js', 'wizard.js'):
            self.assertIn(name, loaded, f'app.js needs {name}')
            self.assertLess(loaded.index(name), app_at, f'{name} must load before app.js')

    def test_every_element_app_js_reads_exists_in_the_page(self):
        asked = set(re.findall(r"\$\('([^']+)'\)", (self.ui / 'app.js').read_text()))
        self.assertTrue(asked, 'the scan found nothing to check')
        missing = [name for name in sorted(asked) if f'id="{name}"' not in self.page]
        self.assertEqual(missing, [], 'app.js would read a node the page does not paint')


class Capabilities(unittest.TestCase):
    def test_every_capability_file_parses_and_grants_something(self):
        files = sorted((SRC_TAURI / 'capabilities').glob('*.json'))
        self.assertTrue(files, 'a Tauri 2 app needs at least one capability file')
        for path in files:
            data = json.loads(path.read_text())
            self.assertIsInstance(data, dict, path)
            self.assertIn('identifier', data, path)
            self.assertIn('windows', data, path)
            self.assertTrue(data['permissions'], f'{path} grants nothing')
            for permission in data['permissions']:
                self.assertIsInstance(permission, str)
            # The window's own commands are Rust functions, which capabilities do not gate.
            # A plugin permission here would mean JavaScript calls a plugin directly.
            for permission in data['permissions']:
                self.assertNotRegex(permission, r'^(shell|opener|http):')

    def test_the_capability_window_matches_the_configured_window(self):
        conf = json.loads((SRC_TAURI / 'tauri.conf.json').read_text())
        labels = {window.get('label', 'main') for window in conf['app']['windows']}
        for path in (SRC_TAURI / 'capabilities').glob('*.json'):
            data = json.loads(path.read_text())
            self.assertTrue(set(data['windows']) <= labels,
                            f'{path} names a window the config does not create')


class RustMirrorsTheTestedRules(unittest.TestCase):
    def setUp(self):
        self.rust = (SRC_TAURI / 'src' / 'host.rs').read_text()

    def test_the_rust_constants_are_the_python_ones(self):
        for name in ('DEFAULT_HOST', 'DEFAULT_PORT'):
            match = re.search(rf'pub const {name}: [^=]+= (.+?);', self.rust)
            self.assertIsNotNone(match, f'{name} is not declared in host.rs')
            value = match.group(1).strip().strip('"')
            expected = getattr(resolve, name)
            self.assertEqual(str(value), str(expected), f'{name} drifted between Rust and Python')

    def test_the_rust_state_directory_is_the_python_one(self):
        """HOST-008: `resolve.py` is the resolver, so its two file names are the spec.

        `host.rs` cannot import it, so the directory and file names are its own constants —
        pinned here rather than trusted, and pinned to the module the wizard's aliases also
        point at, which is what makes one resolver mean one name everywhere.
        """
        for name, expected in (('CONFIG_DIR', resolve.CONFIG_NAME),
                               ('CONFIG_FILE', resolve.CONFIG_FILE)):
            match = re.search(rf'const {name}: &str = "([^"]+)";', self.rust)
            self.assertIsNotNone(match, f'{name} is not declared in host.rs')
            self.assertEqual(match.group(1), expected, f'{name} drifted from resolve.py')

    def test_the_rust_launcher_name_is_the_python_one(self):
        match = re.search(r'pub const LAUNCHER_NAME: &str = "([^"]+)"', self.rust)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), resolve.LAUNCHER_NAME)

    def test_the_rust_sidecar_uses_the_triple_suffix(self):
        self.assertIn('format!("{LAUNCHER_NAME}-{triple}")', self.rust)
        self.assertEqual(resolve.sidecar_filename('x86_64-unknown-linux-gnu'),
                         f'{resolve.LAUNCHER_NAME}-x86_64-unknown-linux-gnu')


class ShellCarriesNoPrivatePaths(unittest.TestCase):
    def test_no_source_names_a_home_directory_or_a_host(self):
        offenders = []
        for path in text_files(DESKTOP):
            text = path.read_text(encoding='utf-8', errors='replace')
            for literal in FORBIDDEN_LITERALS:
                if literal in text:
                    offenders.append(f'{path.relative_to(APP_DIR)}: {literal!r}')
        self.assertEqual(offenders, [], 'the shell is distributed; no machine-specific paths')

    def test_no_source_carries_an_absolute_user_path(self):
        offenders = []
        patterns = (re.compile(r'/home/[A-Za-z0-9._-]+/'), re.compile(r'/Users/[A-Za-z0-9._-]+/'))
        for path in text_files(DESKTOP):
            text = path.read_text(encoding='utf-8', errors='replace')
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    offenders.append(f'{path.relative_to(APP_DIR)}: {match.group(0)}')
        self.assertEqual(offenders, [])


class TheReadmeIsHonest(unittest.TestCase):
    """The documentation is part of the deliverable: a bundle that quietly needs Python 3 is a
    lie told at install time, so the README has to say it, along with what a build needs."""

    def setUp(self):
        self.text = (DESKTOP / 'README.md').read_text()

    def test_it_documents_the_sidecar_triple_convention(self):
        self.assertIn('externalBin', self.text)
        self.assertRegex(self.text, r'hardlylistening-server-<')
        self.assertIn('rustc -vV', self.text)

    def test_it_admits_the_python_dependency(self):
        self.assertIn('python3', self.text)
        self.assertRegex(self.text, r'(?i)still needs a python')
        # And does not pretend a future freeze has happened.
        self.assertRegex(self.text, r'(?i)not (a|the) self-contained')

    def test_it_lists_what_a_build_needs(self):
        for package in ('webkit2gtk-4.1', 'gtk+-3.0', 'libsoup-3.0', 'cargo tauri'):
            self.assertIn(package, self.text)

    def test_it_accounts_for_every_card_it_implements(self):
        # A card that ships without a line in the README is a card a reader cannot find. The
        # wizard, the lifecycle, the two choices, the settings, the address and the dependency
        # rule each get their paragraph and their doc link.
        for card in ('HOST-001', 'HOST-002', 'HOST-003', 'HOST-005', 'HOST-006'):
            self.assertIn(card, self.text)
        for page in ('wizard.md', 'lifecycle.md', 'modes.md', 'settings.md', 'addresses.md',
                     'dependency.md'):
            self.assertIn(page, self.text)

    def test_it_states_what_could_not_be_verified(self):
        self.assertRegex(self.text, r'(?i)never seen a compiler|cannot be compiled')
        self.assertRegex(self.text, r'(?i)webview')


class TheShellImportsNothingFromTheServer(unittest.TestCase):
    def test_desktop_python_imports_only_the_standard_library_and_itself(self):
        offenders = []
        for path in sorted(DESKTOP.rglob('*.py')):
            import ast

            tree = ast.parse(path.read_text(), filename=str(path))
            for root in imported_roots(tree):
                if root in sys.stdlib_module_names or root == 'host':
                    continue
                offenders.append(f'{path.relative_to(APP_DIR)}: imports {root}')
        self.assertEqual(offenders, [], 'the shell is a client; it may not borrow the server')

    def test_no_desktop_source_puts_the_server_on_sys_path(self):
        for path in sorted(DESKTOP.rglob('*.py')):
            for line in path.read_text().splitlines():
                if 'sys.path' in line:
                    self.assertRegex(line, r'(?i)desktop',
                                     f'{path.relative_to(APP_DIR)}: {line.strip()}')

    def test_deleting_the_shell_leaves_the_core_green(self):
        # Inspected, not deleted: the core must not import, spawn or mention the shell. An
        # `import` inside a function that never runs is exactly what a grep for the word would
        # miss, so the imports are parsed.
        offenders = []
        for path in core_files():
            import ast

            tree = ast.parse(path.read_text(), filename=str(path))
            for root in imported_roots(tree):
                if root == 'desktop' or re.match(r'(?i)desktop|src_tauri', root):
                    offenders.append(f'{path.relative_to(APP_DIR)}: imports {root}')
            text = path.read_text()
            for marker in ('src-tauri', 'desktop/'):
                if marker in text:
                    offenders.append(f'{path.relative_to(APP_DIR)}: mentions {marker}')
        self.assertEqual(offenders, [], 'the server must run with desktop/ deleted')

    def test_no_server_manifest_names_the_shell(self):
        manifests = [APP_DIR / 'Dockerfile', APP_DIR / '.dockerignore',
                     APP_DIR / 'install-service.sh', APP_DIR / 'start.sh']
        manifests += sorted((APP_DIR / 'deploy').glob('*'))
        for path in manifests:
            if not path.is_file():
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            if path.name == '.dockerignore':
                # The one file where naming the shell is the opposite of needing it: an entry
                # here keeps it out of the image. Asserted as an exclusion, not as a mention.
                self.assertIn('desktop', [line.strip() for line in text.splitlines()],
                              'the image must exclude the shell')
                self.assertNotIn('!desktop', text, 'nothing may pull the shell back in')
                continue
            self.assertNotIn('desktop', text, f'{path.name} must not need the shell')

    def test_the_launcher_stays_the_plain_interpreter(self):
        launcher = (APP_DIR / 'bin' / resolve.LAUNCHER_NAME).read_text()
        self.assertIn('python3', launcher)
        for shell in ('node', 'npm', 'yarn', 'tauri', 'electron'):
            self.assertNotIn(f' {shell} ', launcher, f'{shell} is a host dependency')


class SidecarStaging(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hl-sidecar-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script = DESKTOP / 'tools' / 'stage_sidecar.py'

    def run_stage(self, *args):
        return subprocess.run(
            [sys.executable, str(self.script), '--out', str(self.tmp), *args],
            capture_output=True, text=True, cwd=str(APP_DIR))

    def test_the_staged_sidecar_is_the_launcher_byte_for_byte(self):
        triple = 'x86_64-unknown-linux-gnu'
        result = self.run_stage('--triple', triple)
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = self.tmp / resolve.sidecar_filename(triple)
        self.assertTrue(staged.is_file())
        self.assertEqual(staged.read_bytes(),
                         (APP_DIR / 'bin' / resolve.LAUNCHER_NAME).read_bytes())
        check = self.run_stage('--triple', triple, '--check')
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn('OK', check.stdout)

    def test_a_drifted_copy_is_reported(self):
        triple = 'x86_64-unknown-linux-gnu'
        self.assertEqual(self.run_stage('--triple', triple).returncode, 0)
        (self.tmp / resolve.sidecar_filename(triple)).write_text('#!/bin/sh\necho local fork\n')
        check = self.run_stage('--triple', triple, '--check')
        self.assertEqual(check.returncode, 1)
        self.assertIn('DIFFERS', check.stdout)

    def test_it_refuses_to_stage_something_that_is_not_the_launcher(self):
        other = self.tmp / 'server.py'
        other.write_text('# not the entry point\n')
        result = self.run_stage('--triple', 'x86_64-unknown-linux-gnu', '--server', str(other))
        self.assertEqual(result.returncode, 2)
        self.assertIn('refusing', result.stderr)


class Addresses(unittest.TestCase):
    def test_the_default_is_loopback_on_the_servers_port(self):
        address = resolve.Address()
        self.assertEqual(address.url(), 'http://127.0.0.1:8470')
        self.assertEqual(address.listen(), '127.0.0.1:8470')
        self.assertEqual(address.scope, 'loopback')
        self.assertEqual(address.argv(), ['--host', '127.0.0.1', '--port', '8470'])

    def test_a_lan_address_is_itself(self):
        address = resolve.Address(host='192.168.1.50', port=8470)
        self.assertEqual(address.url(), 'http://192.168.1.50:8470')
        self.assertEqual(address.scope, 'lan')

    def test_a_listen_anywhere_bind_is_reached_on_loopback(self):
        address = resolve.Address(host='0.0.0.0', port=8470)
        self.assertEqual(address.url(), 'http://127.0.0.1:8470')
        self.assertEqual(address.listen(), '0.0.0.0:8470')
        self.assertEqual(address.scope, 'any')

    def test_ipv6_is_bracketed_in_the_url(self):
        address = resolve.Address(host='::1', port=8471)
        self.assertEqual(address.url(), 'http://[::1]:8471')
        self.assertEqual(address.api('/api/health'), 'http://[::1]:8471/api/health')


class AddressResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hl-desktop-config-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = self.tmp / 'host.json'

    def test_the_environment_wins_and_says_so(self):
        self.config.write_text(json.dumps({'host': '127.0.0.1', 'port': 9000}))
        address = resolve.address({'HARDLYLISTENING_HOST': '127.0.0.2',
                                   'HARDLYLISTENING_PORT': '9100'}, self.config)
        self.assertEqual((address.host, address.port, address.source),
                         ('127.0.0.2', 9100, 'environment'))

    def test_the_config_file_is_used_when_the_environment_is_silent(self):
        self.config.write_text(json.dumps({'host': '192.168.1.50', 'port': 9200}))
        address = resolve.address({}, self.config)
        self.assertEqual((address.host, address.port, address.source),
                         ('192.168.1.50', 9200, 'desktop-config'))

    def test_a_malformed_config_is_reported_and_ignored(self):
        self.config.write_text('{"host": "127.0.0.1",')
        address = resolve.address({}, self.config)
        self.assertEqual((address.host, address.port, address.source),
                         ('127.0.0.1', 8470, 'default'))
        resolved = resolve.resolve({}, path=self.config)
        self.assertIn('not JSON', resolved['config_problem'])

    def test_a_typo_in_the_port_is_an_error_not_a_fallback(self):
        with self.assertRaises(ValueError):
            resolve.address({'HARDLYLISTENING_PORT': 'eighty-four-seventy'}, self.config)
        with self.assertRaises(ValueError):
            resolve.address({'HARDLYLISTENING_PORT': '70000'}, self.config)

    def test_the_config_lives_under_xdg_state(self):
        path = resolve.config_path({'XDG_STATE_HOME': str(self.tmp)})
        self.assertEqual(path, self.tmp / 'hardlylistening-desktop' / 'host.json')
        self.assertEqual(resolve.config_path({'HARDLYLISTENING_DESKTOP_CONFIG': str(self.config)}),
                         self.config)


class LauncherResolution(unittest.TestCase):
    def test_the_launcher_is_the_repository_entry_point(self):
        launcher = resolve.launcher({})
        self.assertEqual(launcher, APP_DIR / 'bin' / 'hardlylistening-server')
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))

    def test_an_explicit_launcher_wins(self):
        launcher = resolve.launcher({'HARDLYLISTENING_SERVER': '/tmp/example-server'})
        self.assertEqual(str(launcher), '/tmp/example-server')

    def test_resolve_reports_what_the_window_fills_its_facts_panel_with(self):
        payload = resolve.resolve({}, path=Path('/tmp/does-not-exist/host.json'))
        for key in ('app_dir', 'launcher', 'launcher_exists', 'address', 'config_path',
                    'config_problem', 'python_override'):
            self.assertIn(key, payload)
        self.assertTrue(payload['launcher_exists'])
        self.assertEqual(payload['app_dir'], str(APP_DIR))

    def test_the_triple_suffix_is_required(self):
        with self.assertRaises(ValueError):
            resolve.sidecar_filename('')
        self.assertEqual(resolve.target_triple('linux', 'x86_64'), 'x86_64-unknown-linux-gnu')
        self.assertEqual(resolve.target_triple('darwin', 'arm64'), 'aarch64-apple-darwin')
        self.assertIsNone(resolve.target_triple('sunos', 'sparc'))


if __name__ == '__main__':
    unittest.main()