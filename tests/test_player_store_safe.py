"""P4: the player is a Subsonic client, and the build is where that is proved.

The old promise (PLAYER-007) was asserted over `player/assemble.mjs`'s output, because that
assembler was how the player was built from `web/`. Neither exists any more: the window loads the
Svelte build in `player/ui/dist`, and the old shell is gone. A promise about a build that no
longer ships proves nothing, so every assertion here is made **over the tree the window loads**
and over the sources that produce it.

Four claims, and the way each is derived rather than remembered:

  * **The window loads that build.** `tauri.conf.json` names `../ui/dist` as its front end, and
    every file the entry document asks for resolves inside the tree. A dangling reference is how
    a missing module ships as a blank window.
  * **The build names no private API path, except the two social reads.** The player talks
    Subsonic (`/rest/…`) to whatever server it was given; this server's own `/api/…` routes are
    not part of that contract, and a player that knew them would be a second client of one
    implementation. Social is the one exception, on purpose and narrow: the capability document
    and the activity feed (`sdk/capabilities.py`, `sdk/social.py`), both public reads, both
    derived at test time. Everything else — every Repository route `api/v1.py` registers, every
    social write, every acquisition endpoint in `web/js/repo-discovery.js` — stays forbidden.
  * **No offline or download machinery survives.** The ten `offline_*` commands, `downloadUrl`,
    the `/download` route, and the old shell modules are named in a list and searched for in the
    build, in `player/ui/src` and in `player/src-tauri/src`; the module files themselves must not
    exist. The bare word "downloaded" is not on the list: it is prose on the connect screen and
    nothing to do with a feature.
  * **No acquisition vocabulary.** A phrase list, not the word "download": the phrases that
    describe turning a source page into a library file.

A negative control copies the build, plants one of each problem, and requires the audit to report
them — an audit that cannot fail is not evidence.
"""
import json
import posixpath
import re
import shutil
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PLAYER = APP_DIR / 'player'
UI = PLAYER / 'ui'
DIST = UI / 'dist'
SHELL = PLAYER / 'shell'
TAURI = PLAYER / 'src-tauri'
RUST = TAURI / 'src'
CONFIG = TAURI / 'tauri.conf.json'
MODULE = APP_DIR / 'web' / 'js' / 'repo-discovery.js'
API_SOURCE = APP_DIR / 'api' / 'v1.py'
#: The HTTP surface: the composition root plus the adapter package (P7). A route is answered by
#: one or the other, so the liveness check reads both rather than guessing which file moved.
HTTP_SOURCES = [APP_DIR / 'server.py'] + sorted((APP_DIR / 'httpd').rglob('*.py'))
CAPABILITIES_SOURCE = APP_DIR / 'sdk' / 'capabilities.py'
SOCIAL_SOURCE = APP_DIR / 'sdk' / 'social.py'

TEXT_SUFFIXES = {'.html', '.htm', '.js', '.mjs', '.css', '.json', '.svg', '.txt'}
#: One Repository route as `api/v1.py` writes it. Only the name is captured; the path is rebuilt.
REPO_ROUTE = re.compile(r'/api/v1/repo/([a-z]+)')
#: How a built file could name an endpoint: the path itself, or a prefix it is built from.
API_PATH = re.compile(r'/api/[A-Za-z0-9_./%${}+=&?-]*')
#: The social reads' own constants, as the server declares them.
SOCIAL_PATH = re.compile(r"^(CAPABILITIES_PATH|ACTIVITY_PATH)\s*=\s*'([^']+)'", re.MULTILINE)
#: Social routes that are a member's read or write. The player holds no member session, so a
#: build that named one of these would be reaching for a credential it does not have.
MEMBER_ROUTES = ('/api/boost', '/api/social', '/api/member',
                 '/api/v1/social/comments', '/api/v1/social/summary')
TRAILING = '.,;:)]}>"\'`…'
#: A player must not carry any of these. Deliberately not the bare word "download": the connect
#: screen says "nothing is downloaded here", which is true and is not machinery.
OFFLINE_MACHINERY = (
    'offline_manifest',
    'offline_begin',
    'offline_write',
    'offline_commit',
    'offline_pin',
    'offline_touch',
    'offline_stat',
    'offline_read',
    'offline_delete',
    'offline_free',
    'downloadUrl',
)
#: Files that were the feature. Their absence is the claim; a name in a list is not enough.
GONE = (
    'src-tauri/src/offline.rs',
    'shell/offline.js',
    'shell/api-bridge.js',
    'assemble.mjs',
)
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


def read(path):
    return Path(path).read_text(errors='replace')


def tree_files(root):
    return sorted(path.relative_to(root).as_posix()
                  for path in Path(root).rglob('*') if path.is_file())


def api_needles(text):
    """Every `/api/…` path a body of code names, cut at an interpolation or a query string."""
    found = set()
    for match in API_PATH.finditer(text):
        raw = match.group(0).rstrip(TRAILING)
        for cut in (raw.find('${'), raw.find('?')):
            if cut > 0:
                raw = raw[:cut]
        if raw.startswith('/api/') and len(raw) > len('/api/'):
            found.add(raw)
    return found


def repo_routes():
    """Every Repository route `api/v1.py` registers, read from its own table."""
    return tuple(sorted('/api/v1/repo/' + name for name in REPO_ROUTE.findall(read(API_SOURCE))))


def acquisition_needles():
    return api_needles(read(MODULE))


def social_routes():
    """The only HardlyListening paths the player may name, read from the server's sources.

    Two, both reads: the public capability document and the activity feed. They are derived by
    reading `sdk/capabilities.py` and `sdk/social.py` rather than by trusting the player, and
    each is checked to be live — the document answered by the HTTP layer (`server.py` or a module
    in `httpd/`), the feed in `api/v1.py`'s route table — so the allowance cannot grow a route the
    server does not have.
    """
    declared = dict(SOCIAL_PATH.findall(read(CAPABILITIES_SOURCE) + '\n' + read(SOCIAL_SOURCE)))
    if set(declared) != {'CAPABILITIES_PATH', 'ACTIVITY_PATH'}:
        raise AssertionError(f'the social path constants moved or were renamed: {declared}')
    allowed = {declared['CAPABILITIES_PATH'], declared['ACTIVITY_PATH']}
    if not any(declared['CAPABILITIES_PATH'] in read(path) for path in HTTP_SOURCES):
        raise AssertionError('sdk/capabilities.py names a document the HTTP layer does not answer')
    if declared['ACTIVITY_PATH'] not in read(API_SOURCE):
        raise AssertionError('sdk/social.py names a feed api/v1.py does not register')
    return allowed


def references(document):
    """The files one document asks the WebView to load, as tree-relative paths."""
    wanted = []
    for match in re.finditer(r'<(?:script|link)\b[^>]*>', document, re.IGNORECASE):
        for attribute in re.finditer(
                r'\b(?:src|href)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
                match.group(0), re.IGNORECASE):
            value = next(group for group in attribute.groups() if group is not None)
            if (not value or value.startswith(('#', '//'))
                    or re.match(r'^[a-z][a-z0-9+.-]*:', value, re.IGNORECASE)):
                continue
            path = value.split('#')[0].split('?')[0]
            if path:
                wanted.append(posixpath.normpath(path.lstrip('/')))
    return wanted


def audit(root, allowed=()):
    """Everything wrong with one built tree, as sentences. Empty means the build is clean.

    `allowed` is the social whitelist; everything else under `/api/` is a problem.
    """
    root = Path(root)
    allowed = set(allowed)
    problems = []
    files = tree_files(root)
    present = set(files)

    for rel in files:
        if Path(rel).suffix not in TEXT_SUFFIXES:
            continue
        text = read(root / rel)
        for needle in sorted(api_needles(text) | set(repo_routes())):
            if needle in text and needle not in allowed:
                problems.append(f'{rel} names the private API path {needle}')
        for token in OFFLINE_MACHINERY:
            if token in text:
                problems.append(f'{rel} carries offline/download machinery: {token}')
        if '/download' in text:
            problems.append(f'{rel} carries a download route')
        lowered = text.lower()
        for phrase in ACQUISITION_VOCABULARY:
            if phrase in lowered:
                problems.append(f'{rel} carries acquisition vocabulary: "{phrase}"')

    for page in [rel for rel in files if Path(rel).suffix in ('.html', '.htm')]:
        for target in references(read(root / page)):
            if target.startswith('..') or target not in present:
                problems.append(f'{page} asks for {target}, which the tree does not contain')
    return problems


class PlayerBuild(unittest.TestCase):
    """Assert over the tree the window loads, and over the sources that produce it."""

    @classmethod
    def setUpClass(cls):
        if not DIST.is_dir():
            raise unittest.SkipTest(
                'player/ui/dist is not built; run `npm --prefix player/ui run build`')

    def test_the_window_loads_this_build(self):
        config = json.loads(read(CONFIG))
        self.assertEqual(config['build']['frontendDist'], '../ui/dist')
        self.assertIn('ui', config['build']['beforeBuildCommand'])
        self.assertTrue((DIST / 'index.html').is_file())
        self.assertTrue((UI / 'package.json').is_file(), 'the build is a package, not a copy')

    def test_every_reference_resolves_inside_the_build(self):
        files = set(tree_files(DIST))
        wanted = references(read(DIST / 'index.html'))
        self.assertTrue(wanted, 'the entry document asks for its scripts and stylesheets')
        for target in wanted:
            self.assertIn(target, files, f'index.html asks for {target}')

    def test_the_player_names_only_the_social_reads(self):
        found = set()
        for rel in tree_files(DIST):
            if Path(rel).suffix in TEXT_SUFFIXES:
                found |= api_needles(read(DIST / rel))
        self.assertEqual(sorted(found - social_routes()), [],
                         'the player names this server\'s private routes')
        # The allowance is not vacuous: the probe ships, so "nothing outside the list" is a
        # statement about a build that actually asks a server something.
        self.assertIn('/api/v1/capabilities', found, 'the social probe is in the build')

    def test_the_social_allowance_is_read_only(self):
        # The exception is two public reads. A member's route needs a session this player does
        # not hold, so naming one would be reaching for a credential that is not there.
        body = ''.join(read(DIST / rel) for rel in tree_files(DIST)
                       if Path(rel).suffix in TEXT_SUFFIXES)
        for route in MEMBER_ROUTES + repo_routes():
            self.assertNotIn(route, body, f'the build must not name {route}')

    def test_the_absence_is_not_vacuous(self):
        # The build has to contain the client for "it names no private route" to mean anything.
        body = ''.join(read(DIST / rel) for rel in tree_files(DIST)
                       if Path(rel).suffix in TEXT_SUFFIXES)
        self.assertIn('/rest/', body, 'the Subsonic client is in the build')
        for route in ('getAlbumList2', 'search3'):
            self.assertIn(route, body, f'{route} is part of the client this test covers')

    def test_no_offline_or_download_machinery_is_left(self):
        self.assertEqual(audit(DIST, social_routes()), [],
                         'the built player carries machinery it must not')
        for rel in GONE:
            self.assertFalse((PLAYER / rel).exists(), f'{rel} is part of the removed feature')
        self.assertFalse(SHELL.exists(), 'the old shell is retired, not merely unused')
        lib = read(RUST / 'lib.rs')
        self.assertNotIn('offline::', lib)
        self.assertNotIn('mod offline', lib)

    def test_the_sources_carry_no_machinery_either(self):
        # A stale build could hide a regression in the source; this is the half that cannot.
        for base in (UI / 'src', RUST):
            for path in sorted(base.rglob('*')):
                if not path.is_file() or path.suffix not in ('.ts', '.svelte', '.rs', '.json'):
                    continue
                if 'node_modules' in path.parts or 'test' in path.name:
                    continue
                text = read(path)
                for token in OFFLINE_MACHINERY:
                    self.assertNotIn(token, text, f'{path.relative_to(PLAYER)} carries {token}')
        for rel in ('src-tauri/src/offline.rs', 'shell/offline.js'):
            self.assertFalse((PLAYER / rel).exists(), rel)

    def test_the_audit_reports_planted_problems(self):
        # The negative control. If this ever passes silently, the assertions above prove nothing.
        with tempfile.TemporaryDirectory() as tmp:
            doctored = Path(tmp) / 'dist'
            shutil.copytree(DIST, doctored)
            page = doctored / 'index.html'
            page.write_text(page.read_text().replace(
                '</head>',
                '<script>const a = "/api/track/7/download"; const b = "offline_manifest";'
                ' const c = "download album"; const d = "/api/v1/social/comments";</script>'
                '\n</head>', 1))
            # Audited with the real whitelist, so this also proves the whitelist is exact:
            # a social route *outside* it is still a problem, an allowed one is not.
            problems = '\n'.join(audit(doctored, social_routes()))
        self.assertIn('/api/track/7/download', problems)
        self.assertIn('offline_manifest', problems)
        self.assertIn('download album', problems)
        self.assertIn('/api/v1/social/comments', problems)
        self.assertNotIn('/api/v1/capabilities', problems)

    def test_the_acquisition_lists_are_not_empty(self):
        # Both lists are derived from live sources; an empty one would make the audit vacuous.
        self.assertIn('/api/tracks', acquisition_needles(),
                      'the endpoint list is read from web/js/repo-discovery.js')
        self.assertEqual(repo_routes(), ('/api/v1/repo/artist', '/api/v1/repo/releases',
                                         '/api/v1/repo/snapshot', '/api/v1/repo/subscriptions',
                                         '/api/v1/repo/tracks'))
        # The social whitelist is two reads and no writes, from the server's own constants.
        self.assertEqual(social_routes(), {'/api/v1/capabilities',
                                           '/api/v1/social/activity'})


if __name__ == '__main__':
    unittest.main()