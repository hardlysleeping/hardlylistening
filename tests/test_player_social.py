"""PLAYER-006: social only when the server has it, and said plainly when it does not.

The player talks Subsonic, and Subsonic has no social surface. So this card's answer is not a
feed: it is the condition under which a feed may be drawn at all, taken from the server rather
than assumed. Three things are pinned here, and each one is a claim about somebody else's code:

  * **The decision and its evidence.** The player may draw Activity only when the connected
    server's public capability document names `social.activity` *and* the deployment does not
    require members, because the feed is a public read only then (`api/v1.py`'s `_reader` returns
    nobody when `security.required` is false, and `social_thread`/`social_summary` use `_member`,
    which always authenticates). A plain Subsonic server has no `/api/v1/capabilities` at all, so
    it degrades to "no social" rather than to an empty feed. Those two readings are asserted
    against the server's own source below, so a change there fails here instead of silently
    turning the player's feed into a guess.

  * **The fixture is the server's own output.** The capability documents and the activity answer
    the node half feeds the module are built here: the answer goes through `api.v1.envelope`, so
    it is a v1 envelope because the server makes it one, and its rows are asserted to be rows
    `sdk.social.activity_item` parses (a boost and a day card). The SDK's own reading of each
    document is recorded beside it, and `tests/test_player_social.cjs` asserts the module reaches
    the same conclusion -- two independent readers, one fixture.

  * **The statement.** The module's route table is one route, its refusals are named, it never
    sends or holds a credential, and the docs' tables cannot drift from either. Whether the
    refusals actually come out of `window.fetch` in order is the node half's job.

Run both halves (neither alone is the claim):

    python -m unittest tests.test_player_social
    node tests/test_player_social.cjs

`python -m tests.test_player_social --write` regenerates the fixture from this server.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import api.v1 as api_v1
import db
import security
import subsonic
from sdk import capabilities as sdk_capabilities
from sdk import social as sdk_social

APP_DIR = Path(__file__).resolve().parents[1]
PLAYER = APP_DIR / 'player'
SHELL = PLAYER / 'shell'
SOCIAL = SHELL / 'social.js'
DOCS = APP_DIR / 'docs' / 'player' / 'social.md'
FIXTURE = APP_DIR / 'tests' / 'fixtures' / 'player' / 'social.json'
NODE_TEST = APP_DIR / 'tests' / 'test_player_social.cjs'
API_V1 = APP_DIR / 'api' / 'v1.py'
SERVER = APP_DIR / 'server.py'
SECURITY = Path(security.__file__)  # the implementation, wherever ARCH-009 put it
SDK_CAPS = APP_DIR / 'sdk' / 'capabilities.py'

#: The one route this card answers on the app's own front end, and what it is answered from.
ROUTES = [
    ('GET', '/api/activity', 'GET /api/v1/social/activity'),
]

#: The routes it refuses with a reason even when the server does have social, because each one
#: is a member's read or write and this player holds no member session. `REFUSAL_SAMPLES` is one
#: real URL per prefix, so the patterns are exercised on something a page would ask for.
REFUSALS = ['/api/boost', '/api/social/', '/api/member/']
REFUSAL_SAMPLES = {
    '/api/boost': '/api/boost',
    '/api/social/': '/api/social/summary',
    '/api/member/': '/api/member/m1',
}

#: The two rows the activity answer carries: one live boost and one member's day. They are the
#: shapes `activity.active_boosts()` and `activity.feed()` produce, and the fixture test asserts
#: `sdk.social.activity_item` parses both -- so the node half is never exercised on a shape the
#: SDK would reject.
ACTIVITY_ROWS = [
    {'kind': 'boost', 'member_id': 'm1', 'member': 'Ana', 'member_icon': 'music',
     'ts': 1760000000.0, 'expires_at': 1760086400.0,
     'track': {'id': 11, 'title': 'Night Drive', 'artist_name': 'Ana Vale'}},
    {'kind': 'day', 'member_id': 'm1', 'member': 'Ana', 'member_icon': 'music',
     'day': '2026-11-12', 'ts': 1760000500.0, 'first_ts': 1760000100.0,
     'counts': {'listen': 1},
     'groups': {'listen': [{'id': 11, 'title': 'Night Drive', 'artist_name': 'Ana Vale',
                            'ts': 1760000500.0}],
                'add': [], 'playlist': [], 'other': []}},
]


def server_document(requires_members, social):
    """One `/api/v1/capabilities` answer, in the shape `server.py` builds it.

    The document is the server's own: `server.name` is `subsonic.SERVER_TYPE`, the version is
    `db.VERSION`, and the feature keys are the five the handler advertises. Only `social` is
    varied, because only `social` is this card's question.
    """
    return {
        'server': {'name': subsonic.SERVER_TYPE, 'version': db.VERSION, 'api': 'v1',
                   'requires_members': requires_members},
        'subsonic': {'enabled': True, 'version': subsonic.PROTOCOL_VERSION,
                     'server_version': subsonic.SERVER_VERSION, 'open_subsonic': True,
                     'extensions': list(subsonic.EXTENSIONS),
                     'transcode_targets': sorted(subsonic.TRANSCODE_TARGETS),
                     'default_transcode': subsonic.DEFAULT_TRANSCODE},
        'features': {
            'accounts': {'password': True, 'login_link': True, 'invite_only': True,
                         'google': False},
            'library': {'scope': 'member', 'bookmarks': True},
            'playback': {'range': True, 'transcode': True, 'hls': False},
            'party': {'enabled': True, 'chat': True, 'jukebox': False},
            'social': social,
            'discovery': {'search': True, 'sonic_analysis': False},
        },
    }


SOCIAL_FULL = {'comments': True, 'votes': True, 'likes': True, 'activity': True}


class _Handler:
    """A handler whose only job is to be written to, so `api.v1.envelope` makes the answer."""

    def __init__(self):
        self.body = None

    def json(self, body):
        self.body = body


def activity_envelope():
    """`GET /api/v1/social/activity` as the server writes it: the v1 envelope, not a guess."""
    handler = _Handler()
    api_v1.envelope(handler, 'social.activity', ACTIVITY_ROWS, len(ACTIVITY_ROWS))
    return handler.body


#: What the module must conclude from each document, and what the SDK concludes from the same
#: one. `state` is the module's own word; `sdk_available` is `feature('social').available`;
#: `sdk_activity` is the flag the SDK's gate needs for the feed.
EXPECTATIONS = {
    'local': {'state': 'activity', 'needed': '', 'available': True,
              'sdk_available': True, 'sdk_activity': True},
    'hosted': {'state': 'members', 'needed': 'members', 'available': False,
               'sdk_available': True, 'sdk_activity': True},
    'no_social': {'state': 'absent', 'needed': 'social', 'available': False,
                  'sdk_available': False, 'sdk_activity': False},
    'no_activity': {'state': 'no-activity', 'needed': 'social.activity', 'available': False,
                    'sdk_available': True, 'sdk_activity': False},
    'empty': {'state': 'absent', 'needed': 'social', 'available': False,
              'sdk_available': False, 'sdk_activity': False},
}


def build_fixture():
    return {
        'documents': {
            # A deployment with accounts off: the feed is a public read.
            'local': server_document(False, dict(SOCIAL_FULL)),
            # The default deployment: the domain is advertised, the read needs a session.
            'hosted': server_document(True, dict(SOCIAL_FULL)),
            'no_social': server_document(False, {}),
            'no_activity': server_document(False, {'comments': True, 'votes': True,
                                                   'likes': True}),
            'empty': {},
        },
        'activity': activity_envelope(),
        'rows': ACTIVITY_ROWS,
        'expectations': EXPECTATIONS,
        'paths': {'capabilities': sdk_capabilities.CAPABILITIES_PATH,
                  'activity': sdk_social.ACTIVITY_PATH},
    }


def serialise(fixture):
    return json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def code_only(source):
    """The file with comments and string literals removed, so a sentence is not a statement.

    Walks the file once rather than guessing with regexes: an apostrophe in `member's` and a
    `// import` in a comment both survive a naive strip, and both would make a check meaningless.
    """
    out = []
    index, size = 0, len(source)
    while index < size:
        char = source[index]
        pair = source[index + 1] if index + 1 < size else ''
        if char == '/' and pair == '*':
            end = source.find('*/', index + 2)
            if end < 0:
                break
            index = end + 2
            out.append(' ')
        elif char == '/' and pair == '/':
            end = source.find('\n', index)
            if end < 0:
                break
            index = end
        elif char in '"\'':
            quote = char
            index += 1
            while index < size:
                if source[index] == '\\':
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            out.append(quote * 2)
        elif char == '`':
            index += 1
            while index < size:
                if source[index] == '\\':
                    index += 2
                    continue
                if source[index] == '`':
                    index += 1
                    break
                index += 1
            out.append('``')
        else:
            out.append(char)
            index += 1
    return ''.join(out)


def route_rows(source):
    """Every route row of `ROUTES`, as (method, path, from), read from the module itself.

    `from` is written as a string plus a constant (`"GET " + ACTIVITY_PATH`) so the row cannot
    drift from where it is answered; the constants are resolved here for the same reason.
    """
    block = re.search(r'const ROUTES = \[(.+?)\n  \];', source, re.S)
    if not block:
        raise AssertionError('social.js lost its ROUTES table')
    constants = dict(re.findall(r'const ([A-Z][A-Z0-9_]*) = "([^"]*)";', source))
    rows = []
    for row in re.findall(r'\{([^{}]+)\}', block.group(1)):
        method = re.search(r'method:\s*"([^"]+)"', row).group(1)
        path = re.search(r'path:\s*"([^"]+)"', row).group(1)
        expression = re.search(r'from:\s*([^,}]+)', row).group(1)
        pieces = []
        for literal, name in re.findall(r'"([^"]*)"|([A-Z][A-Z0-9_]*)', expression):
            if not name:
                pieces.append(literal)
            elif name in constants:
                pieces.append(constants[name])
            else:
                raise AssertionError(f'ROUTES names {name}, which social.js does not define')
        rows.append((method, path, ''.join(pieces)))
    return rows


def refusal_paths(source):
    """Every refusal pattern of `REFUSALS`, flattened so a path can be matched against it."""
    block = re.search(r'const REFUSALS = \[(.+?)\n  \];', source, re.S)
    if not block:
        raise AssertionError('social.js lost its REFUSALS table')
    return [raw.replace('\\/', '/') for raw in re.findall(r'\[/(\^[^\n]+?\$)/,', block.group(1))]


class Fixture(unittest.TestCase):
    """The documents and the answer are this server's own output, not a remembered shape."""

    def test_the_fixture_is_current(self):
        self.assertTrue(FIXTURE.is_file(),
                        'no fixture -- run: python -m tests.test_player_social --write')
        self.assertEqual(json.loads(FIXTURE.read_text()), build_fixture(),
                         'the server\'s document or the v1 envelope changed; regenerate with '
                         '--write and re-read the module where it reads a field that moved')

    def test_the_activity_answer_is_a_v1_envelope(self):
        answer = build_fixture()['activity']
        self.assertEqual(answer['api'], 'v1')
        self.assertEqual(answer['resource'], 'social.activity')
        self.assertEqual(answer['count'], len(ACTIVITY_ROWS))
        self.assertEqual(answer['data'], ACTIVITY_ROWS)

    def test_the_rows_are_rows_the_sdk_parses(self):
        kinds = [sdk_social.activity_item(row).kind for row in ACTIVITY_ROWS]
        self.assertEqual(kinds, ['boost', 'day'])
        day = sdk_social.activity_item(ACTIVITY_ROWS[1])
        self.assertEqual(day.counts, {'listen': 1})
        self.assertEqual([entry.title for entry in day.groups['listen']], ['Night Drive'])
        boost = sdk_social.activity_item(ACTIVITY_ROWS[0])
        self.assertEqual([boost.track_id, boost.track.title], [11, 'Night Drive'])

    def test_every_document_is_read_the_way_the_sdk_reads_it(self):
        # The module mirrors `sdk.capabilities`; this is where the two are held to the same
        # reading. `available` is `bool(details)` there, and the feed's flag is `activity`.
        for name, expectation in EXPECTATIONS.items():
            document = build_fixture()['documents'][name]
            features = sdk_capabilities.Capabilities.from_document(document).features
            self.assertEqual(features.social.available, expectation['sdk_available'], name)
            self.assertEqual(bool(features.social.get('activity', False)),
                             expectation['sdk_activity'], name)

    def test_a_plain_subsonic_answer_degrades_to_no_domain(self):
        # What `probe()` does with a document it could not read: every domain unavailable, and
        # `degraded` set so a caller can say why. A player in this state offers no social.
        core = sdk_capabilities.Capabilities.assume_core('HTTP 404')
        self.assertTrue(core.degraded)
        self.assertFalse(core.feature('social').available)
        self.assertFalse(core.feature('social').get('activity', False))


class Evidence(unittest.TestCase):
    """Why the condition is what it is, asserted over the server's own source.

    A docstring is not a proof, and neither is this file's summary: these read the lines that
    decide whether the feed is public, so the decision cannot drift away from the reason given
    for it in `docs/player/social.md`.
    """

    @classmethod
    def setUpClass(cls):
        cls.api = API_V1.read_text()
        cls.server = SERVER.read_text()
        cls.security = SECURITY.read_text()

    @staticmethod
    def function(source, name):
        match = re.search(r'\ndef ' + name + r'\((.+?)(?=\ndef |\n# -- |\Z)', source, re.S)
        if not match:
            raise AssertionError(f'no {name} in the file')
        return match.group(0)

    def test_the_activity_feed_is_a_shared_read_and_the_rest_is_a_members(self):
        # `_reader` answers without a session only in local mode; `_member` always authenticates.
        self.assertIn('_reader(handler, store, security)', self.function(self.api, 'social_activity'))
        for name in ('social_thread', 'social_summary'):
            self.assertIn('_member(handler, store, security)',
                          self.function(self.api, name), name)
        self.assertIn('if not security.required:', self.function(self.api, '_reader'))
        self.assertIn('security.authenticate(handler, store)', self.function(self.api, '_member'))

    def test_the_feed_route_is_the_versioned_read_the_docs_name(self):
        self.assertIn("'/api/v1/social/activity', social_activity)", self.api)
        self.assertIn("'/api/v1/social/comments', social_thread)", self.api)
        self.assertIn("'/api/v1/social/summary', social_summary)", self.api)

    def test_the_capability_document_is_public_and_names_the_flags_the_fixture_uses(self):
        # Answered before the `/api/v1/` dispatch, so it is readable without a session.
        before = self.server.index("if path == '/api/v1/capabilities':")
        after = self.server.index("if path.startswith('/api/v1/'):")
        self.assertLess(before, after, 'capabilities must be answered before authentication')
        self.assertIn("'requires_members': bool(SECURITY.required)", self.server)
        for flag in ('comments', 'votes', 'likes', 'activity'):
            self.assertIn(f"'{flag}': True", self.server, flag)

    def test_a_member_required_server_wants_a_cookie_the_player_never_holds(self):
        self.assertIn("HARDLYLISTENING_AUTH_REQUIRED", self.security)
        self.assertIn("'1'", self.security)          # required unless it is switched off
        self.assertIn("__Host-hl_session", self.security)
        self.assertIn('Sign in required', self.security)

    def test_the_hosts_own_activity_answer_is_the_shape_this_route_answers(self):
        # `{"items": feed(...)}`: the module hands the server's rows over in the same envelope, so
        # the app's `data.items` finds them without a second rendering.
        self.assertIn('{"items": social_service.feed(', self.server)

    def test_the_sdk_refuses_by_name_before_the_wire(self):
        caps = SDK_CAPS.read_text()
        self.assertIn('available=bool(details)', caps)
        self.assertIn('def assume_core', caps)
        social = (APP_DIR / 'sdk' / 'social.py').read_text()
        self.assertIn("self._require('the activity feed', 'activity')", social)


class Module(unittest.TestCase):
    """The module's own statement: one route, named refusals, no credential."""

    @classmethod
    def setUpClass(cls):
        cls.source = SOCIAL.read_text()
        cls.code = code_only(cls.source)

    def test_the_route_table_is_exactly_the_documented_one(self):
        self.assertEqual(route_rows(self.source), ROUTES)

    def test_the_refusals_name_the_routes_they_cover(self):
        patterns = refusal_paths(self.source)
        self.assertEqual(len(patterns), 3, patterns)
        for path in REFUSALS:
            sample = REFUSAL_SAMPLES[path]
            self.assertTrue(any(re.search(pattern, sample) for pattern in patterns),
                            f'no refusal explains {path} (asked as {sample})')

    def test_it_holds_no_credential_and_sends_none(self):
        for word in ('credentials', 'password', 'username', 'localStorage', 'sessionStorage',
                     'document.cookie', 'indexedDB'):
            self.assertNotIn(word, self.code, f'{word} has no business in the social module')
        # No query the module builds carries the protocol's auth parameters.
        self.assertNotIn('"u"', self.code)
        self.assertNotIn('"p"', self.code)
        self.assertNotIn('"t"', self.code)
        self.assertNotIn('md5', self.code)

    def test_the_member_session_is_named_as_the_reason(self):
        # The refusal a person reads has to say what is missing, not that something failed.
        self.assertIn('__Host-hl_session', self.source)
        self.assertIn('member session', self.source)

    def test_a_server_with_no_social_delegates_and_never_answers_empty(self):
        # The absent branch returns null so the bridge's own refusal stands, word for word.
        self.assertIn('if (decision.state === "absent") return null;', self.source)
        self.assertNotIn('items: []', self.source)
        self.assertNotIn('{ items: [] }', self.source)

    def test_it_records_endpoints_and_statuses_only(self):
        trace = re.search(r'state\.trace\.push\((.+?)\);', self.source, re.S).group(1)
        self.assertIn('endpoint', trace)
        self.assertIn('status', trace)
        for leak in ('url', 'password', 'salt', 'query'):
            self.assertNotIn(leak, trace, f'the trace records {leak}')

    def test_it_is_a_classic_script_with_no_build_step(self):
        for needle in ('import ', 'require(', 'export '):
            self.assertNotIn(needle, self.code, needle)
        self.assertIn('root.PlayerSocial = PlayerSocial;', self.source)

    def test_it_speaks_the_apps_own_answer_shape(self):
        # The app reads `data.items`; the module wraps the v1 envelope's `data` as `items`.
        self.assertIn('{ items: document.data }', self.source)
        self.assertIn('document.api) !== "v1"', self.source)

    def test_the_source_is_ascii(self):
        SOCIAL.read_text().encode('ascii')


class Docs(unittest.TestCase):
    """The document is the deliverable a person reads before believing the tables."""

    @classmethod
    def setUpClass(cls):
        cls.text = DOCS.read_text()

    def test_it_exists_and_carries_the_decision(self):
        self.assertTrue(DOCS.is_file(), 'docs/player/social.md is a deliverable')
        for token in ('social.activity', 'requires_members', 'absent', 'members'):
            self.assertIn(token, self.text, token)

    def test_its_route_table_is_the_modules_table(self):
        rows = re.findall(r'^\|\s*(GET|POST)\s*\|\s*`(/api/[^`]+)`\s*\|\s*`([^`]+)`\s*\|',
                          self.text, re.M)
        self.assertEqual([(m, p, f) for m, p, f in rows],
                         [(m, p, f) for m, p, f in ROUTES])

    def test_its_refusal_table_is_the_modules_refusal_table(self):
        for path in ('/api/boost', '/api/social/', '/api/member/'):
            self.assertIn('`' + path, self.text, f'the doc does not refuse {path}')
        self.assertIn('501', self.text)

    def test_the_module_is_wired_and_the_doc_says_so(self):
        for tag in ('<script src="./connect.js"></script>',
                    '<script src="./api-bridge.js"></script>',
                    '<script src="./library-boot.js"></script>',
                    '<script src="./social.js"></script>'):
            self.assertIn(tag, self.text, tag)
        self.assertIn('PlayerSocial.attach(', self.text)
        # Applied in the integration pass, not by the card. The order matters: the module attaches
        # from `library-boot.js` after the bridge is installed, so its wrapper sits outside it.
        connect = (SHELL / 'connect.html').read_text()
        loaded = re.findall(r'<script src="\./([^"]+)"', connect)
        self.assertIn('social.js', loaded, 'the card ships a module nobody loads')
        self.assertLess(loaded.index('library-boot.js'), loaded.index('social.js'),
                        'social.js must load after the boot that attaches it')
        boot = (SHELL / 'library-boot.js').read_text()
        self.assertIn('PlayerSocial.attach({ session: session, say: message => say(message, document) })',
                      boot, 'the boot has to hand the session to the module')

    def test_it_says_what_is_unverified(self):
        section = self.text.lower()
        self.assertIn('unverified', section)
        for token in ('cors', 'webkit', 'no window'):
            self.assertIn(token, section, token)

    def test_the_evidence_is_cited_by_file_and_line(self):
        for cited in ('api/v1.py', 'server.py', 'security.py', 'sdk/capabilities.py'):
            self.assertIn(cited, self.text, cited)

    def test_the_node_half_exists_and_names_the_fixture(self):
        self.assertTrue(NODE_TEST.is_file())
        self.assertIn('social.json', NODE_TEST.read_text())


if __name__ == '__main__':
    import sys

    if '--write' in sys.argv:
        sys.argv.remove('--write')
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(serialise(build_fixture()))
        print(f'wrote {FIXTURE.relative_to(APP_DIR)}')
    unittest.main()