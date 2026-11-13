"""PLAYER-007: the store-safe promise is asserted over the built tree, not over the list.

`player/assemble.mjs` keeps an allow-list and an `EXCLUDED` list, and a list is a promise. A
promise is not proof: a file can be re-added by editing one line, or by inlining its contents
into something already on the list, and the allow-list would still read correctly. So every
assertion here is made **over the output** — `assemble.mjs` is run into a temporary directory
(it never writes into the worktree), and the finished files are walked and read.

What is asserted, and where each list comes from:

  * **Exclusions are absent, by name.** The names come from the build's own plan
    (`assemble.mjs --list`), and the receipt's drops are checked against that same plan, so a
    file dropped from the entry document is also a file the tree does not contain.
  * **No acquisition endpoint survives in the output.** The endpoint list is **derived from
    `web/js/repo-discovery.js`** at test time — every `/api/…` literal the module's code
    contains — so if the module grows an endpoint, the assertion follows it without an edit
    here. The entry document is the one exception: its inline script is the app's own markup
    and already names some of those routes (see `docs/player/store-safe.md`), so it may carry
    only the endpoints it carried in `web/index.html`, in the same number. No other file may
    name any of them at all.
  * **No `web/` file that acquisition needs is present.** "Needs" is derived, not remembered:
    a `web/` file is acquisition's if it mentions an endpoint only the module owns. Today that
    closure is the module itself; the test asserts the closure is exactly that (so the
    derivation is not vacuous) and that none of it reached the tree.
  * **Nothing dangles.** Every `<script src>` and `<link href>` in every assembled document,
    and every `@import` in every assembled stylesheet, resolves to a file inside the tree. A
    dangling reference is how an excluded module gets re-added by accident: the page asks for
    it and the build no longer notices.
  * **The receipt explains the difference.** Every drop names an `EXCLUDED` path with that
    entry's reason; every `EXCLUDED` path the source entry document references is in the
    receipt; only the entry document was rewritten.
  * **The tree is smaller by exactly the drops.** Output bytes equal the sources' bytes minus
    the exact text the receipt says was removed. Any other difference would be a copy that was
    not byte-for-byte, or a removal nobody recorded.
  * **No acquisition vocabulary in the built text.** A phrase list, not the word "download":
    the app's own markup says "Download folder" about a member's library, which is Library
    vocabulary and stays until PLAYER-003 replaces the surface. The phrases here are the ones
    that describe turning a source page into a library file.
  * **The Repository (SDK-004) is unreachable.** The route names are **derived from `api/v1.py`'s
    `ROUTES` table** at test time — every `/api/v1/repo/<name>` it registers — and no assembled
    file may name one, or the module (`sdk.repo`, `sdk/repo.py`). The reachability half follows
    the file graph the WebView loads: `<script src>`/`<link href>` from the entry documents,
    `@import` in stylesheets, and relative ES-module/worker specifiers, transitively. The build
    is also asked outright for the module (`--include-web ../sdk/repo.py`) and must refuse it,
    which is the one assertion that does not depend on the audit being right.
    What this cannot prove: a player script could rebuild a route by concatenation or fetch a
    `.js` at runtime, and an edit to `assemble.mjs` itself is outside every assertion here. What
    it does prove is that the files the player serves, and the files those make it load, carry
    neither the Repository's routes nor its module. `.py` can never load in a WebView, and the
    build refuses it by suffix, so the module has two independent barriers rather than one.

Two negative controls prove the assertions bite rather than pass vacuously:

  * the build refuses `--include-web js/repo-discovery.js` (the guard in `assemble.mjs`);
  * and — independently of that guard — the same audit is run over a tree that has the module
    put back and a page that asks for it, and must report exactly those findings. That second
    one is the point of the card: the assertion, not the list, is what has to hold.

Node is already a requirement of `tests/test_player_shell.py`; nothing else is imported.
"""
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PLAYER = APP_DIR / 'player'
WEB = APP_DIR / 'web'
SHELL = PLAYER / 'shell'
ASSEMBLE = PLAYER / 'assemble.mjs'
ENTRY = 'index.html'
MANIFEST = '.assembly.json'
MODULE = WEB / 'js' / 'repo-discovery.js'
NODE = shutil.which('node')
API_SOURCE = APP_DIR / 'api' / 'v1.py'
#: One Repository route as `api/v1.py` writes it. Only the name is captured; the path is rebuilt.
REPO_ROUTE = re.compile(r"/api/v1/repo/([a-z]+)")
#: How a built file could name the Repository: its routes, or the module itself.
REPOSITORY_MARKERS = ('/api/v1/repo/', 'sdk.repo', 'sdk/repo', 'repo.py')

TEXT_SUFFIXES = {'.html', '.htm', '.js', '.mjs', '.css', '.json', '.svg', '.txt'}
#: Acquisition's own vocabulary: the phrases that describe turning a source page into a file in
#: a library. Deliberately not the bare word "download" — see the module docstring.
ACQUISITION_VOCABULARY = (
    'download to library',
    'download to your library',
    'download album',
    'download this album',
    'download track',
    'download song',
    'queue for download',
    'queue it for download',
    'joins your library',
    'add to your downloads',
)
SCRIPT_BODY = re.compile(r'(<script\b[^>]*>)(.*?)(</script\s*>)', re.IGNORECASE | re.DOTALL)
TAG = re.compile(r'<(script|link)\b[^>]*>', re.IGNORECASE)
ATTRIBUTE = re.compile(
    r'\b(?:src|href)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', re.IGNORECASE)
CSS_IMPORT = re.compile(r'@import\s+(?:url\(\s*)?["\']?([^"\')\s;]+)', re.IGNORECASE)
API_PATH = re.compile(r'/api/[A-Za-z0-9_./%${}+=&?-]*')
#: A path match runs to the next quote, so it can pick up sentence punctuation after it.
TRAILING = '.,;:)]}>"\'`…'


def run_node(*args):
    if not NODE:
        raise unittest.SkipTest('node is not on PATH')
    return subprocess.run([NODE, *args], cwd=APP_DIR, capture_output=True, text=True,
                          timeout=120)


@lru_cache(maxsize=1)
def plan():
    """The build's own statement of what it does: sources, exclusions, refusals."""
    result = run_node(str(ASSEMBLE), '--list')
    if result.returncode != 0:
        raise AssertionError(f'assemble.mjs --list failed: {result.stderr}')
    return json.loads(result.stdout)


def assembler_root(tmp):
    out = Path(tmp) / 'www'
    result = run_node(str(ASSEMBLE), '--out', str(out))
    if result.returncode != 0:
        raise AssertionError(f'assemble.mjs failed: {result.stderr}')
    return out


def tree_files(root):
    return sorted(path.relative_to(root).as_posix()
                  for path in Path(root).rglob('*') if path.is_file())


def read(path):
    return Path(path).read_text(errors='replace')


def api_needles(text):
    """Every `/api/…` path a body of code or markup names, as a substring to search for.

    A template literal is cut where its interpolation starts (`/api/feed/${id}` is the
    substring `/api/feed/`), and a query string is cut at the `?`, because a leak may write the
    same route without the parameters this module happens to pass.
    """
    found = set()
    for match in API_PATH.finditer(text):
        raw = match.group(0).rstrip(TRAILING)
        for cut in (raw.find('${'), raw.find('?')):
            if cut > 0:
                raw = raw[:cut]
        if raw.startswith('/api/') and len(raw) > len('/api/'):
            found.add(raw)
    return found


def acquisition_needles():
    return api_needles(read(MODULE))


def inherited_needles():
    """The module's endpoints that the app's own entry markup already names itself.

    `web/index.html` is carried as-is apart from the recorded drops, and its inline script is
    the host's: it calls `/api/subscriptions` and `/api/feed/…` because it is the whole app, not
    the player. Those names are inherited, not added, and the test pins that they do not grow.
    """
    return acquisition_needles() & api_needles(read(WEB / ENTRY))


def acquisition_files():
    """`web/` files acquisition needs: the ones naming an endpoint only the module uses."""
    exclusive = acquisition_needles() - inherited_needles()
    found = set()
    for path in sorted(WEB.rglob('*')):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            if exclusive & api_needles(read(path)):
                found.add(path.relative_to(WEB).as_posix())
    return found, exclusive


def markup_only(html):
    """Drop script bodies: an inline template that writes an `<a href>` is code, not a request."""
    return SCRIPT_BODY.sub(lambda match: match.group(1) + match.group(3), html)


def is_external(value):
    return (not value or value.startswith(('#', '//'))
            or re.match(r'^[a-z][a-z0-9+.-]*:', value, re.IGNORECASE) is not None)


def references(document, rel):
    """The files one assembled document asks the WebView to load, as tree-relative paths."""
    wanted = []
    for tag in TAG.finditer(markup_only(document)):
        for match in ATTRIBUTE.finditer(tag.group(0)):
            value = next(group for group in match.groups() if group is not None)
            if is_external(value):
                continue
            path = value.split('#')[0].split('?')[0]
            if not path:
                continue
            if path.startswith('/'):
                wanted.append((value, path.lstrip('/')))
            else:
                joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel), path))
                wanted.append((value, joined))
    return wanted


def css_references(document, rel):
    wanted = []
    for match in CSS_IMPORT.finditer(document):
        value = match.group(1)
        if is_external(value):
            continue
        path = value.split('#')[0].split('?')[0]
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel), path))
        wanted.append((value, joined))
    return wanted


#: A relative module, dynamic import or worker a script could load at runtime. Matched so the
#: closure stays honest if the player ever grows an ES module; today it matches nothing.
JS_SPECIFIER = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\bimportScripts\s*\(\s*|\bnew\s+Worker\s*\(\s*)"""
    r"""['"]([^'"]+)['"]""")


def repo_routes():
    """Every Repository route `api/v1.py` registers, read from its `ROUTES` table.

    Derived rather than remembered: a sixth read in `api/v1.py` becomes a needle here without an
    edit, so the assertion keeps following the server instead of a snapshot of it.
    """
    names = set(REPO_ROUTE.findall(read(API_SOURCE)))
    return tuple(sorted('/api/v1/repo/' + name for name in names))


def repository_markers():
    return tuple(repo_routes()) + REPOSITORY_MARKERS


def reachable(root, entries=(ENTRY, 'connect.html')):
    """Every file the player's documents make the WebView load, transitively.

    Documents contribute `<script src>`/`<link href>`, stylesheets contribute `@import`, and a
    script contributes the relative specifiers of an import, a dynamic import, an
    `importScripts` or a `new Worker` — the four ways a built tree can ask for another file. A
    specifier that is external, absolute, or names nothing in the tree is simply not followed;
    `audit` is what reports a dangling reference.
    """
    root = Path(root)
    present = set(tree_files(root))
    seen, pending = set(), list(entries)
    while pending:
        rel = pending.pop()
        if rel in seen or rel not in present:
            continue
        seen.add(rel)
        suffix = Path(rel).suffix
        wanted = []
        if suffix in ('.html', '.htm'):
            wanted = references(read(root / rel), rel)
        elif suffix == '.css':
            wanted = css_references(read(root / rel), rel)
        elif suffix in ('.js', '.mjs'):
            for match in JS_SPECIFIER.finditer(markup_only(read(root / rel))):
                value = match.group(1)
                if is_external(value) or value.startswith('/'):
                    continue
                path = value.split('#')[0].split('?')[0]
                if not path:
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), path))
                wanted.append((value, target))
        pending.extend(target for _, target in wanted)
    return seen


def audit(root):
    """Everything wrong with one built tree, as a list of sentences. Empty means store-safe."""
    root = Path(root)
    problems = []
    excluded = dict(plan()['excluded'])
    files = tree_files(root)
    present = set(files)

    for rel in sorted(excluded):
        if rel in present:
            problems.append(f'excluded file reached the tree: {rel}')

    module_endpoints = acquisition_needles()
    inherited = inherited_needles()
    exclusive = module_endpoints - inherited
    for rel in files:
        if rel == MANIFEST or Path(rel).suffix not in TEXT_SUFFIXES:
            continue
        text = read(root / rel)
        found = {needle for needle in exclusive if needle in text}
        if rel != ENTRY:
            found |= {needle for needle in inherited if needle in text}
        for needle in sorted(found):
            problems.append(f'{rel} names the acquisition endpoint {needle}')

    needed, _ = acquisition_files()
    for rel in sorted(needed & present):
        problems.append(f'acquisition needs web/{rel} and it is in the tree')

    for rel in files:
        suffix = Path(rel).suffix
        if suffix in ('.html', '.htm'):
            wanted = references(read(root / rel), rel)
        elif suffix == '.css':
            wanted = css_references(read(root / rel), rel)
        else:
            continue
        for value, target in wanted:
            if target.startswith('..') or target not in present:
                problems.append(f'{rel} asks for {value}, which the tree does not contain')

    for rel in files:
        if rel == MANIFEST or Path(rel).suffix not in TEXT_SUFFIXES:
            continue
        text = read(root / rel).lower()
        for phrase in ACQUISITION_VOCABULARY:
            if phrase in text:
                problems.append(f'{rel} carries acquisition vocabulary: "{phrase}"')

    return problems


class StoreSafeBuild(unittest.TestCase):
    """Assemble once, then assert over the output. Nothing is written into the worktree."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = assembler_root(cls._tmp.name)
        cls.files = tree_files(cls.out)
        cls.receipt = json.loads((cls.out / MANIFEST).read_text())

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_no_excluded_file_reaches_the_tree(self):
        excluded = [rel for rel, why in plan()['excluded']]
        self.assertTrue(excluded, 'an empty EXCLUDED list would make this test vacuous')
        for rel in excluded:
            self.assertNotIn(rel, self.files, f'{rel} is excluded by policy')
        # The module is also refused by name, and by the walk over the finished tree.
        self.assertIn('repo-discovery.js', plan()['forbidden'])
        self.assertFalse([rel for rel in self.files if 'repo-discovery' in rel])

    def test_no_assembled_file_names_an_acquisition_endpoint(self):
        needles = acquisition_needles()
        self.assertIn('/api/tracks', needles,
                      'the endpoint list is read from web/js/repo-discovery.js')
        exclusive = needles - inherited_needles()
        self.assertTrue(exclusive, 'the module has endpoints the entry document does not name')
        leaks = []
        for rel in self.files:
            if rel == MANIFEST or Path(rel).suffix not in TEXT_SUFFIXES:
                continue
            text = read(self.out / rel)
            shared = [] if rel == ENTRY else sorted(needles)
            for needle in sorted(exclusive) + shared:
                if needle in text:
                    leaks.append(f'{rel}: {needle}')
        self.assertEqual(leaks, [], 'acquisition endpoints survived into the assembled tree')

    def test_no_assembled_file_names_a_repository_route(self):
        # SDK-004: `/api/v1/repo/*` is acquisition, and the player is a streaming client. The
        # needles are the routes `api/v1.py` registers right now, plus the module's own name.
        routes = repo_routes()
        self.assertEqual(routes, ('/api/v1/repo/artist', '/api/v1/repo/releases',
                                  '/api/v1/repo/snapshot', '/api/v1/repo/subscriptions',
                                  '/api/v1/repo/tracks'),
                         'the route list is read from api/v1.py, not remembered')
        leaks = []
        for rel in self.files:
            if rel == MANIFEST or Path(rel).suffix not in TEXT_SUFFIXES:
                continue
            text = read(self.out / rel)
            for needle in repository_markers():
                if needle in text:
                    leaks.append(f'{rel}: {needle}')
        self.assertEqual(leaks, [], 'the Repository survived into the player build')

    def test_the_player_cannot_reach_the_sdk(self):
        # The stronger half: not just the copied files, but everything those files make the
        # WebView load. The closure is asserted non-empty first, so a broken follow cannot make
        # this pass by finding nothing.
        closure = reachable(self.out)
        self.assertIn(ENTRY, closure)
        self.assertIn('connect.html', closure)
        for rel in ('js/library.js', 'js/playback-queue.js', 'js/social-feed.js',
                    'styles/app.css', 'connect.js', 'api-bridge.js'):
            self.assertIn(rel, closure, f'{rel} is what the player loads it from')
        self.assertEqual(closure - set(self.files), set(),
                         'the closure stays inside the tree')
        for rel in sorted(closure):
            if Path(rel).suffix not in TEXT_SUFFIXES:
                continue
            text = read(self.out / rel)
            for needle in repository_markers():
                self.assertNotIn(needle, text,
                                 f'{rel} is reachable from the player and names {needle}')

    def test_the_build_refuses_the_repository_module(self):
        # Independent of the audit: the build is asked for the module outright. It must fail on
        # the suffix alone, because no `.py` may ship, and leave no half-built tree behind.
        self.assertTrue((APP_DIR / 'sdk' / 'repo.py').is_file())
        self.assertIn('*.py', plan()['forbidden'], 'the player ships no server code')
        self.assertFalse([rel for rel in self.files if Path(rel).suffix == '.py'])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'www'
            result = run_node(str(ASSEMBLE), '--out', str(out),
                              '--include-web', '../sdk/repo.py')
            self.assertEqual(result.returncode, 1,
                             'a build that ships the Repository module must not succeed')
            self.assertIn('repo.py', result.stderr)
            self.assertIn('server code', result.stderr)
            self.assertFalse(out.exists(), 'a refusal leaves no half-built tree')

    def test_the_entry_document_adds_no_acquisition_endpoint(self):
        source = read(WEB / ENTRY)
        built = read(self.out / ENTRY)
        inherited = inherited_needles()
        self.assertTrue(inherited, 'the entry document names some of these itself')
        for needle in sorted(inherited):
            self.assertEqual(built.count(needle), source.count(needle),
                             f'the assembly changed how often {needle} is named')

    def test_no_web_file_that_acquisition_needs_is_present(self):
        needed, exclusive = acquisition_files()
        self.assertEqual(needed, {'js/repo-discovery.js'},
                         'the closure is derived: a web/ file naming these endpoints needs them')
        for rel in sorted(needed):
            self.assertNotIn(rel, self.files, f'web/{rel} is acquisition and must not ship')

    def test_every_reference_resolves_inside_the_tree(self):
        self.assertIn(ENTRY, self.files)
        self.assertIn('connect.html', self.files)
        wanted = references(read(self.out / ENTRY), ENTRY)
        self.assertTrue(wanted, 'the entry document asks for its scripts and stylesheets')
        for value, target in references(read(self.out / 'connect.html'), 'connect.html'):
            self.assertIn(target, self.files, f'connect.html asks for {value}')
        for value, target in wanted:
            self.assertIn(target, self.files, f'{ENTRY} asks for {value}')
        # The stylesheets are what the entry document's one stylesheet pulls in.
        for value, target in css_references(read(self.out / 'styles' / 'app.css'),
                                            'styles/app.css'):
            self.assertIn(target, self.files, f'styles/app.css imports {value}')

    def test_the_receipt_accounts_for_the_drops(self):
        excluded = dict(plan()['excluded'])
        drops = self.receipt['dropped']
        self.assertTrue(drops, 'the entry document is rewritten without a receipt')
        self.assertEqual({drop['file'] for drop in drops}, {ENTRY},
                         'only the entry document is rewritten')
        for drop in drops:
            self.assertIn(drop['path'], excluded, f'{drop["path"]} is not an exclusion')
            self.assertEqual(drop['why'], excluded[drop['path']],
                             'the receipt carries the decision, not a paraphrase of it')
            self.assertIn(drop['removed'], read(WEB / ENTRY),
                          'the receipt names text the source does not contain')
        source = read(WEB / ENTRY)
        referenced = {rel for rel in excluded if '/' + rel in source or rel in source}
        referenced |= {rel for rel in excluded if '/' + rel.rsplit('.', 1)[0] in source}
        self.assertEqual({drop['path'] for drop in drops}, referenced,
                         'every exclusion the entry document references is a recorded drop')
        self.assertEqual(sorted(self.receipt['assembled']),
                         [rel for rel in self.files if rel != MANIFEST],
                         'the receipt lists the tree it is the receipt for')

    def test_the_tree_is_smaller_by_exactly_the_drops(self):
        source_bytes = 0
        for rel in self.receipt['assembled']:
            source = WEB / rel
            if not source.is_file():
                source = SHELL / rel
            self.assertTrue(source.is_file(), f'{rel} came from nowhere')
            source_bytes += source.stat().st_size
        removed = sum(len(drop['removed'].encode()) for drop in self.receipt['dropped'])
        built = sum((self.out / rel).stat().st_size for rel in self.files if rel != MANIFEST)
        self.assertLess(built, source_bytes, 'the exclusion has to make the tree smaller')
        self.assertEqual(built, source_bytes - removed,
                         'the tree differs from its sources by the recorded drops and nothing '
                         'else')
        # And the receipt's own numbers describe the tree on disk.
        self.assertEqual(sum(entry['bytes'] for entry in self.receipt['files']), built)
        for entry in self.receipt['files']:
            digest = hashlib.sha256((self.out / entry['path']).read_bytes()).hexdigest()
            self.assertEqual(digest, entry['sha256'], f'{entry["path"]} is not what was copied')

    def test_the_audit_finds_nothing(self):
        self.assertEqual(audit(self.out), [], 'the built tree must be store-safe')

    def test_the_audit_reports_the_module_put_back(self):
        # The negative control for the assertions themselves. The build's guard is not in the
        # way here: this tree is copied from a good build, then doctored, so only the audit can
        # notice. If this ever passes silently, the checks above prove nothing.
        with tempfile.TemporaryDirectory() as tmp:
            doctored = Path(tmp) / 'www'
            shutil.copytree(self.out, doctored)
            shutil.copyfile(MODULE, doctored / 'js' / 'repo-discovery.js')
            page = (doctored / ENTRY).read_text()
            # A page that asks for what the build deliberately left out: the manifest is
            # excluded, and putting the link back is how a missing file is requested again.
            (doctored / ENTRY).write_text(
                page.replace('</head>', '<script src="/js/repo-discovery.js"></script>\n'
                                        '<link rel="manifest" href="/app.webmanifest">\n</head>',
                             1))
            problems = audit(doctored)
            text = '\n'.join(problems)
            self.assertIn('excluded file reached the tree: js/repo-discovery.js', text)
            self.assertIn('acquisition needs web/js/repo-discovery.js', text)
            self.assertIn('asks for /app.webmanifest, which the tree does not contain', text)
            self.assertIn('/api/tracks', text)
            self.assertIn('acquisition vocabulary', text)

    def test_the_build_refuses_the_acquisition_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'www'
            result = run_node(str(ASSEMBLE), '--out', str(out),
                              '--include-web', 'js/repo-discovery.js')
            self.assertEqual(result.returncode, 1,
                             'a build that ships the module must not succeed')
            self.assertIn('repo-discovery.js', result.stderr)
            self.assertIn('policy', result.stderr)
            self.assertFalse(out.exists(), 'a refusal leaves no half-built tree')


if __name__ == '__main__':
    unittest.main()