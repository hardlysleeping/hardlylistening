"""PLAYER-002: many servers, one active, and a secret the keychain keeps.

PLAYER-001 held one session in the page's memory and named this card as the gap. The decision
has two halves and both are asserted here:

  * **What the model is** (`player/shell/accounts.js`, run under node in this file): a list of
    public records, one active server, a switch that leaves the others alone, a form recorded
    from the handshake rather than chosen, and the rule that a secret is not in the list, the
    rendered text, a log line or an address. Every secret below is a fixture.
  * **Where a secret goes** (`player/src-tauri/src/accounts.rs`): the OS keychain, with an
    in-memory fallback that says so. This host has no WebView development packages, so the
    crate has never been compiled — `rustfmt --check` is the only proof here, and the file's
    shape is what is asserted, not its behaviour. That limitation is stated in the document and
    at the end of this module.

The cross-language check is deliberate: Subsonic's token form is md5, the WebView has no md5,
so `accounts.js` carries its own implementation — and it is compared against Python's `hashlib`
rather than against itself.
"""
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PLAYER = APP_DIR / 'player'
SHELL = PLAYER / 'shell'
TAURI = PLAYER / 'src-tauri'
ACCOUNTS_JS = SHELL / 'accounts.js'
ACCOUNTS_RS = TAURI / 'src' / 'accounts.rs'
TAURI_CONF = TAURI / 'tauri.conf.json'
ASSEMBLE = PLAYER / 'assemble.mjs'
DOC = APP_DIR / 'docs' / 'player' / 'accounts.md'
NODE = shutil.which('node')
RUSTFMT = shutil.which('rustfmt')

#: The commands the page calls and the Rust module declares. One list, so a rename on either side
#: fails here instead of at a build nobody can run on this machine.
COMMANDS = ('accounts_mode', 'accounts_put', 'accounts_secret', 'accounts_forget')


def run_node(args, cwd=APP_DIR):
    if not NODE:
        raise unittest.SkipTest('node is not on PATH')
    return subprocess.run([NODE, *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def node_json(script):
    """Run a node program whose last line is JSON on stdout, and read it."""
    result = run_node(['-e', script])
    if result.returncode != 0:
        raise AssertionError(f'node failed: {result.stderr or result.stdout}')
    return json.loads(result.stdout)


def code_only(source):
    """Comments may name what is absent; statements may not reach for it."""
    source = re.sub(r'/\*[\s\S]*?\*/', '', source)
    return re.sub(r'^\s*//.*$', '', source, flags=re.M)


class Shipped(unittest.TestCase):
    """The model is in the window because the shell directory is the allow-list."""

    def test_the_shell_directory_is_what_ships(self):
        result = run_node([str(ASSEMBLE), '--list'])
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertIn('accounts.js', plan['shell'])
        self.assertIn('connect.js', plan['shell'])

    def test_the_assembled_tree_carries_the_account_model_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'www'
            result = run_node([str(ASSEMBLE), '--out', str(out)])
            self.assertEqual(result.returncode, 0, result.stderr)
            copied = out / 'accounts.js'
            self.assertTrue(copied.is_file(), 'the model did not reach the built tree')
            self.assertEqual(copied.read_bytes(), ACCOUNTS_JS.read_bytes())

    def test_the_account_model_touches_no_storage_and_no_network(self):
        code = code_only(ACCOUNTS_JS.read_text())
        for call in ('localStorage', 'sessionStorage', 'indexedDB', 'document.cookie',
                     'setItem', 'writeFile', 'fetch(', 'XMLHttpRequest', 'WebSocket', 'document.'):
            self.assertNotIn(call, code, f'{call} does not belong in the account model')
        # The vault is a dependency passed in, and the two that exist are both here.
        for name in ('memoryVault', 'keychainVault', 'PlayerAccounts'):
            self.assertIn(name, code)

    def test_the_model_says_why_a_secret_is_kept_apart(self):
        source = ACCOUNTS_JS.read_text()
        self.assertIn('PLAYER-001', source, 'the card it replaces has to be named')
        self.assertIn('keychain', source.lower())


class TheModel(unittest.TestCase):
    """Run under node, against a stubbed vault: no keychain, no file, no network."""

    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest('node is not on PATH')
        cls.report = node_json(f'''
require({json.dumps(str(ACCOUNTS_JS))});
const api = globalThis.PlayerAccounts;
(async () => {{
  const vault = api.memoryVault();
  const store = api.open({{ vault }});
  const one = {{ server: "https://one.example", username: "bazzite", accepted: "password",
    secret: "s3cret-one" }};
  const two = {{ server: "https://two.example/sub/", username: "bazzite", accepted: "token",
    secret: "s3cret-two" }};
  const first = await store.add(one);
  const second = await store.add(two);
  const idsBefore = store.list().map(account => account.id).join(",");
  const storedBefore = [await vault.get(first.account.id), await vault.get(second.account.id)];
  const switched = store.use(second.account.id);
  const storedAfter = [await vault.get(first.account.id), await vault.get(second.account.id)];
  const params = api.authParams(store.list()[1], "s3cret-two", "salty").params;
  process.stdout.write(JSON.stringify({{
    accounts: store.list(),
    active: store.active(),
    rendered: store.render(),
    saved: store.serialise(),
    log: store.log("ping p=s3cret-two u=bazzite"),
    idsStable: idsBefore === store.list().map(account => account.id).join(","),
    secretsKept: storedBefore[0] === storedAfter[0] && storedBefore[1] === storedAfter[1],
    switchedTo: switched.ok ? switched.active.server : null,
    forms: store.list().map(account => account.form),
    params,
    refused: [api.origin("https://user:pw@one.example").error || null,
               api.origin("https://one.example/rest").error || null],
    memoryMode: vault.mode(),
    memoryPersists: vault.persists,
    status: store.status()
  }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
''')

    def test_two_servers_are_two_accounts(self):
        accounts = self.report['accounts']
        self.assertEqual(len(accounts), 2)
        self.assertEqual([account['server'] for account in accounts],
                         ['https://one.example', 'https://two.example/sub'])
        self.assertEqual(len({account['id'] for account in accounts}), 2)

    def test_only_one_account_is_active_and_switching_changes_nothing_else(self):
        self.assertEqual(self.report['switchedTo'], 'https://two.example/sub')
        self.assertEqual(self.report['active']['server'], 'https://two.example/sub')
        self.assertEqual(sum(1 for account in self.report['accounts'] if account['active']), 1)
        self.assertTrue(self.report['idsStable'])
        self.assertTrue(self.report['secretsKept'],
                        'switching the active server moved or dropped a stored secret')

    def test_the_list_round_trips_without_any_secret(self):
        self.assertNotIn('s3cret', json.dumps(self.report['saved']))
        self.assertNotIn('s3cret', json.dumps(self.report['accounts']))
        self.assertEqual(sorted(self.report['saved']['accounts'][0]),
                         ['added', 'form', 'id', 'label', 'server', 'username'])

    def test_a_secret_is_never_rendered_nor_logged(self):
        self.assertIn('one.example', self.report['rendered'])
        self.assertNotIn('s3cret', self.report['rendered'])
        self.assertNotIn('s3cret', self.report['log'])
        self.assertIn('***', self.report['log'])

    def test_a_secret_is_never_in_an_address(self):
        for refused in self.report['refused']:
            self.assertTrue(refused, 'an address with credentials, or a /rest suffix, was accepted')
        self.assertNotIn('pw@', ''.join(self.report['refused']))

    def test_the_form_is_the_one_the_server_accepted(self):
        self.assertEqual(self.report['forms'], ['password', 'token'])
        # A token request is md5(password + salt) sent as t= beside its s=.
        self.assertEqual(self.report['params']['s'], 'salty')
        self.assertEqual(self.report['params']['t'], hashlib.md5(b's3cret-twosalty').hexdigest())
        self.assertEqual(self.report['params']['u'], 'bazzite')
        self.assertNotIn('p', self.report['params'])

    def test_the_in_memory_fallback_is_honest_about_not_persisting(self):
        self.assertEqual(self.report['memoryMode'], 'memory')
        self.assertIs(self.report['memoryPersists'], False)
        self.assertEqual(self.report['status']['mode'], 'memory')
        self.assertIs(self.report['status']['persists'], False)


class TheTokenDigest(unittest.TestCase):
    """md5 is in the model because Subsonic's token form is md5, and the WebView has none."""

    def test_md5_agrees_with_hashlib(self):
        vectors = ['', 'abc', 'password', 'p' * 55, 'p' * 56, 'p' * 64, 'x' * 100,
                   'bazzite:salty', 'pässwörd', 'パスワード']
        script = (f'require({json.dumps(str(ACCOUNTS_JS))});'
                  'const api = globalThis.PlayerAccounts;'
                  f'process.stdout.write(JSON.stringify({json.dumps(vectors)}'
                  '.map(value => api.md5Hex(value))));')
        digests = node_json(script)
        for value, digest in zip(vectors, digests):
            self.assertEqual(digest, hashlib.md5(value.encode()).hexdigest(), repr(value))


class TheSecretStore(unittest.TestCase):
    """`accounts.rs`: the keychain first, memory only where the keychain cannot answer."""

    @classmethod
    def setUpClass(cls):
        cls.source = ACCOUNTS_RS.read_text()
        cls.code = code_only(cls.source)

    def test_the_file_parses(self):
        if not RUSTFMT:
            self.skipTest('rustfmt is not on PATH')
        result = subprocess.run([RUSTFMT, '--check', '--edition', '2021', str(ACCOUNTS_RS)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_keychain_is_the_primary_store(self):
        self.assertIn('use keyring::Entry;', self.code)
        self.assertIn('keyring::Error::NoEntry', self.code)
        self.assertIn('Mode::Keychain', self.code)
        self.assertIn('set_password', self.code)

    def test_the_fallback_is_memory_and_says_so(self):
        self.assertIn('Mode::Memory', self.code)
        self.assertIn('fn open() -> Vault', self.code)
        self.assertIn('probe()', self.code)
        # The mode is reported to the page, so it can tell a person the password is forgotten.
        self.assertIn('"memory"', self.code)

    def test_nothing_here_writes_a_file(self):
        for writer in ('std::fs', 'fs::write', 'File::create', 'write_all', 'OpenOptions',
                       'std::io::Write', 'env::temp_dir'):
            self.assertNotIn(writer, self.code, f'{writer} would put a secret on disk')

    def test_a_secret_is_never_printed(self):
        self.assertNotIn('{secret}', self.source)
        for block in re.findall(r'eprintln!\((?:[^()]|\([^()]*\))*\)', self.code):
            self.assertNotIn('secret,', block)
            self.assertNotIn('{secret}', block)

    def test_the_commands_the_page_calls_are_the_ones_declared(self):
        declared = set(re.findall(r'pub fn (accounts_[a-z_]+)\(', self.code))
        self.assertTrue(set(COMMANDS) <= declared, declared)
        page = ACCOUNTS_JS.read_text()
        for command in COMMANDS:
            self.assertIn(f'"{command}"', page, f'{command} is declared in Rust and unused in the page')

    def test_the_service_name_is_the_bundle_identifier(self):
        identifier = json.loads(TAURI_CONF.read_text())['identifier']
        service = re.search(r'pub const SERVICE: &str = "([^"]+)"', self.source).group(1)
        self.assertEqual(service, identifier,
                         'the keychain entries have to be findable by the name the app has')

    def test_the_module_states_why_the_secret_is_not_in_the_list(self):
        for phrase in ('config file', 'keychain', 'memory'):
            self.assertIn(phrase, self.source.lower())


class TheDocument(unittest.TestCase):
    """The wiring a shared file needs is written down, because it is not in the tree yet."""

    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text()

    def test_the_doc_exists_and_names_the_card(self):
        self.assertIn('PLAYER-002', self.text)

    def test_the_doc_gives_the_wiring_a_shared_file_needs(self):
        for anchor in ('mod accounts;', '.manage(', 'invoke_handler',
                       'keyring = { version = "3"', 'Cargo.toml'):
            self.assertIn(anchor, self.text, f'{anchor} is missing from the wiring')
        for command in COMMANDS:
            self.assertIn(command, self.text)

    def test_the_doc_is_plain_about_what_is_unverified(self):
        lowered = self.text.lower()
        for phrase in ('unverified', 'rustfmt', 'never been compiled'):
            self.assertIn(phrase, lowered)

    def test_the_doc_says_where_a_secret_may_not_go(self):
        lowered = self.text.lower()
        for phrase in ('config file', 'in memory', 'plaintext'):
            self.assertIn(phrase, lowered)


if __name__ == '__main__':
    unittest.main()