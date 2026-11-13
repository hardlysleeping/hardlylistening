"""The client SDK's social module, driven against a stub server on 127.0.0.1.

The same harness `tests/test_sdk.py` uses: a `ThreadingHTTPServer` answering canned bodies, and
every request kept so a test reads the wire — which method, which path, which JSON body, which
header — instead of a mock. The stub answers the shapes `api/v1.py`, `social/discussion.py`,
`activity.py` and `server.py` really produce, including the capability document `server.py`
publishes, so a refusal is tested against the vocabulary the document actually uses.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sdk import capabilities, social

COOKIE = 'session-value-that-must-never-be-logged'


def document(**social_flags):
    """The capability document `server.py` publishes, with this test's social flags.

    No flags at all means the domain is absent, which is how a server that has no Social answers.
    """
    build = {
        'server': {'name': 'HardlyListening', 'version': '0.16.6', 'api': 'v1',
                   'requires_members': True},
        'subsonic': {'enabled': True, 'version': '1.16.1', 'server_version': 'scoped-1',
                     'open_subsonic': True},
        'features': {
            'accounts': {'password': True},
            'library': {'scope': 'member'},
            'playback': {'range': True},
            'party': {'enabled': True},
            'discovery': {'search': True},
        },
    }
    if social_flags:
        build['features']['social'] = dict(social_flags)
    return build


FULL = {'comments': True, 'votes': True, 'likes': True, 'activity': True}


def envelope(resource, data):
    body = {'api': 'v1', 'resource': resource, 'data': data}
    if isinstance(data, list):
        body['count'] = len(data)
    return body


SUMMARY = {'score': 3, 'up': 4, 'down': 1, 'vote': 1, 'comments': 2,
           'voters': [{'id': 'm_2', 'value': 1, 'name': 'Grace', 'icon': 'music'},
                      {'id': 'm_3', 'value': -1, 'name': 'Linus', 'icon': 'star'}]}

PAGE = dict(SUMMARY, items=[
    {'id': 11, 'member_id': 'm_1', 'body': 'first', 'created_at': '2026-11-12 10:00:00',
     'updated_at': None, 'likes': 2, 'liked': True, 'can_edit': True,
     'name': 'Ada', 'icon': 'spark'},
    {'id': 12, 'member_id': 'm_2', 'body': 'second', 'created_at': '2026-11-12 11:00:00',
     'updated_at': None, 'likes': 0, 'liked': False, 'can_edit': False,
     'name': 'Grace', 'icon': 'music'},
], next_after=12)

FEED = [
    {'kind': 'boost', 'member_id': 'm_1', 'member': 'Ada', 'member_icon': 'spark',
     'ts': 1700000000.5, 'expires_at': 1700086400.5,
     'track': {'id': 7, 'title': 'Track 7', 'artist_name': 'Artist'}},
    {'kind': 'day', 'member_id': 'm_2', 'member': 'Grace', 'member_icon': 'music',
     'day': '2026-11-12', 'ts': 1699999999.0, 'first_ts': 1699900000.0,
     'counts': {'listen': 2, 'add': 1, 'playlist': 1, 'other': 1},
     'groups': {
         'listen': [{'id': 7, 'title': 'Track 7', 'artist_name': 'Artist', 'ts': 1699999999.0},
                    {'id': 8, 'title': 'Track 8', 'artist_name': 'Artist', 'ts': 1699999000.0}],
         'add': [{'id': 8, 'title': 'Track 8', 'artist_name': 'Artist', 'ts': 1699998000.0}],
         'playlist': [{'ts': 1699997000.0, 'kind': 'playlist_new', 'id': 3, 'title': 'Mix'}],
         'other': [{'ts': 1699996000.0, 'kind': 'join', 'detail': 'joined'}],
     }},
]

BOOST_ROW = {'track_id': 7, 'boosted_at': 1700000000.0, 'expires_at': 1700086400.0,
             'track': {'id': 7, 'title': 'Track 7', 'artist_name': 'Artist'}}

BOOST_WRITE = {'track_id': 9, 'boosted_at': 1700000100.0, 'expires_at': 1700086500.0,
               'swapped': True, 'already_active': False}


class Call:
    """One request the stub received, kept so a test can read the wire instead of a mock."""

    def __init__(self, method, path, query, headers, body):
        self.method, self.path, self.query = method, path, query
        self.headers, self.body = headers, body

    def params(self):
        return {key: values[0] for key, values in self.query.items()}

    def payload(self):
        return json.loads(self.body.decode('utf-8') or '{}') if self.body else None


class Stub(BaseHTTPRequestHandler):
    """A canned server. Each test points `answer` at its own dispatcher."""

    requests = []
    answer = None

    def _record(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        headers = {name.lower(): value for name, value in self.headers.items()}
        length = int(headers.get('content-length') or 0)
        body = self.rfile.read(length) if length else b''
        Stub.requests.append(Call(method, parsed.path, query, headers, body))
        status, extra, payload = Stub.answer(method, parsed.path, query, headers, body)
        self.send_response(status)
        for name, value in extra.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def do_GET(self):
        self._record('GET')

    def do_POST(self):
        self._record('POST')

    def log_message(self, *args):
        pass


class StubServer(unittest.TestCase):
    """A stub in a thread, and the base URL a client is pointed at."""

    def setUp(self):
        Stub.requests = []
        Stub.answer = self.answer
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        self.thread = threading.Thread(target=self.http.serve_forever, args=(0.02,), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = 'http://127.0.0.1:%d' % self.http.server_port
        self.refusal = None       # one (status, document) every request answers with
        self.raw = None           # one (status, headers, body) a test wants verbatim

    def stop(self):
        Stub.answer = None
        self.http.shutdown()
        self.thread.join(timeout=10)
        self.http.server_close()

    def answer(self, method, path, query, headers, body):
        return 404, {'Content-Type': 'application/json'}, b'{"error": "no such endpoint"}'

    def calls(self, method=None, path=None):
        return [call for call in Stub.requests
                if (method is None or call.method == method)
                and (path is None or call.path == path)]

    def json(self, document, status=200):
        return status, {'Content-Type': 'application/json'}, json.dumps(document).encode()


class Wire(StubServer):
    """A working server: the three v1 reads, the member's boost slot, and the discussion writes."""

    def setUp(self):
        super().setUp()
        self.client = social.SocialClient(self.base, capabilities.Capabilities.from_document(
            document(**FULL)), social.Session.from_cookie(COOKIE))

    def answer(self, method, path, query, headers, body):
        if self.raw is not None:
            return self.raw
        if self.refusal is not None:
            return self.json(self.refusal[1], self.refusal[0])
        if method == 'GET' and path == social.ACTIVITY_PATH:
            return self.json(envelope('social.activity', FEED))
        if method == 'GET' and path == social.COMMENTS_PATH:
            return self.json(envelope('social.comments', PAGE))
        if method == 'GET' and path == social.SUMMARY_PATH:
            if 'targets' in query:
                rows = {target: dict(SUMMARY)
                        for target in query['targets'][0].split(',') if target}
                return self.json(envelope('social.summary', rows))
            return self.json(envelope('social.summary', SUMMARY))
        if method == 'GET' and path == social.BOOST_PATH:
            return self.json({'boost': BOOST_ROW})
        if method == 'POST' and path == social.BOOST_PATH:
            return self.json({'ok': True, 'boost': BOOST_WRITE})
        if method == 'POST' and path.startswith(social.DISCUSSION_PATH + '/'):
            return self.json(SUMMARY)
        return super().answer(method, path, query, headers, body)


class Refusals(StubServer):
    """What a server that does not advertise Social must hear: a name and a reason, not a shrug."""

    def client(self, **flags):
        return social.SocialClient(self.base, capabilities.Capabilities.from_document(
            document(**flags)), social.Session.from_cookie(COOKIE))

    def entry_points(self, client):
        return {
            'activity': lambda: client.activity(),
            'comments': lambda: client.comments('track', '7'),
            'summary': lambda: client.summary('track', '7'),
            'summaries': lambda: client.summaries('track', ['7']),
            'current_boost': lambda: client.current_boost(),
            'boost': lambda: client.boost(9),
            'comment': lambda: client.comment('track', '7', 'hello'),
            'edit_comment': lambda: client.edit_comment('track', '7', 11, 'hello'),
            'delete_comment': lambda: client.delete_comment('track', '7', 11),
            'vote': lambda: client.vote('track', '7', 1),
            'like': lambda: client.like('track', '7', 11),
        }

    def test_every_entry_point_refuses_by_name_when_social_is_absent(self):
        client = self.client()
        for name, call in self.entry_points(client).items():
            with self.subTest(entry=name):
                with self.assertRaises(social.Unavailable) as caught:
                    call()
                self.assertIn('social', caught.exception.needed)
                self.assertIn('unavailable', str(caught.exception))
                self.assertIn(caught.exception.needed, str(caught.exception))
        self.assertEqual(self.calls(), [])       # the refusal never reached the wire

    def test_a_refusal_is_not_an_empty_answer(self):
        client = self.client()
        with self.assertRaises(social.Unavailable):
            client.activity()
        with self.assertRaises(social.Unavailable):
            client.current_boost()

    def test_a_domain_without_a_flag_refuses_only_what_needs_it(self):
        client = self.client(comments=True)
        with self.assertRaises(social.Unavailable) as caught:
            client.activity()
        self.assertEqual(caught.exception.needed, 'social.activity')
        with self.assertRaises(social.Unavailable) as caught:
            client.like('track', '7', 11)
        self.assertEqual(caught.exception.needed, 'social.likes')
        with self.assertRaises(social.Unavailable) as caught:
            client.vote('track', '7', 1)
        self.assertEqual(caught.exception.needed, 'social.votes')
        self.assertEqual(self.calls(), [])

    def test_a_comment_page_is_allowed_when_only_comments_are_advertised(self):
        client = self.client(comments=True)
        with self.assertRaises(social.Denied):    # the stub has no route; the call still went out
            client.comments('track', '7')
        self.assertEqual(len(self.calls('GET', social.COMMENTS_PATH)), 1)

    def test_a_degraded_document_refuses_and_keeps_the_probe_reason(self):
        client = social.SocialClient(self.base,
                                     capabilities.Capabilities.assume_core('HTTP 500'),
                                     social.Session.from_cookie(COOKIE))
        with self.assertRaises(social.Unavailable) as caught:
            client.activity()
        self.assertIn('HTTP 500', str(caught.exception))

    def test_a_raw_capability_document_is_accepted(self):
        client = social.SocialClient(self.base, {'features': {'social': {'activity': True}}})
        self.assertTrue(client.capabilities.feature('social').get('activity'))


class Reads(Wire):

    def test_the_feed_parses_a_boost_and_a_day_card(self):
        items = self.client.activity(limit=20)
        self.assertEqual([item.kind for item in items], ['boost', 'day'])
        boost = items[0]
        self.assertEqual((boost.member.name, boost.track.title, boost.track.id),
                         ('Ada', 'Track 7', 7))
        self.assertEqual(boost.expires_at, 1700086400.5)
        self.assertFalse(boost.swapped)
        card = items[1]
        self.assertEqual((card.member.id, card.day), ('m_2', '2026-11-12'))
        self.assertEqual(card.counts['listen'], 2)
        self.assertEqual([entry.kind for entry in card.groups['listen']], ['listen', 'listen'])
        self.assertEqual(card.groups['add'][0].track.id, 8)
        self.assertEqual(card.groups['playlist'][0].playlist_id, 3)
        self.assertEqual(card.groups['playlist'][0].title, 'Mix')
        self.assertEqual(card.groups['other'][0].detail, 'joined')
        self.assertEqual(self.calls('GET', social.ACTIVITY_PATH)[0].params()['limit'], '20')

    def test_a_comment_page_carries_its_summary_its_items_and_the_cursor(self):
        page = self.client.comments('track', '7', after=5)
        self.assertEqual((page.summary.score, page.summary.up, page.summary.down,
                          page.summary.vote, page.summary.comments), (3, 4, 1, 1, 2))
        self.assertEqual([voter.member.name for voter in page.summary.voters],
                         ['Grace', 'Linus'])
        self.assertEqual(page.summary.voters[1].value, -1)
        self.assertEqual(page.comments[0].body, 'first')
        self.assertEqual(page.comments[0].member.id, 'm_1')
        self.assertEqual(page.comments[0].member.name, 'Ada')
        self.assertTrue(page.comments[0].liked)
        self.assertTrue(page.comments[0].can_edit)
        self.assertEqual(page.comments[1].likes, 0)
        self.assertFalse(page.comments[1].can_edit)
        self.assertEqual(page.next_after, 12)
        params = self.calls('GET', social.COMMENTS_PATH)[0].params()
        self.assertEqual((params['kind'], params['target'], params['after']), ('track', '7', '5'))

    def test_a_summary_is_one_target_of_counts(self):
        found = self.client.summary('album', 'a' * 64)
        self.assertEqual((found.score, found.up, found.down, found.vote, found.comments),
                         (3, 4, 1, 1, 2))
        self.assertEqual(len(found.voters), 2)
        params = self.calls('GET', social.SUMMARY_PATH)[0].params()
        self.assertEqual((params['kind'], params['target']), ('album', 'a' * 64))

    def test_a_summary_batch_is_keyed_by_target(self):
        rows = self.client.summaries('track', ['7', '8'])
        self.assertEqual(sorted(rows), ['7', '8'])
        self.assertEqual(rows['8'].score, 3)
        params = self.calls('GET', social.SUMMARY_PATH)[0].params()
        self.assertEqual((params['kind'], params['targets']), ('track', '7,8'))

    def test_a_batch_with_no_targets_asks_nothing(self):
        self.assertEqual(self.client.summaries('track', []), {})
        self.assertEqual(self.calls(), [])

    def test_the_current_boost_is_read_and_null_is_an_absence(self):
        found = self.client.current_boost()
        self.assertEqual((found.track_id, found.track.title), (7, 'Track 7'))
        self.assertEqual(found.boosted_at, 1700000000.0)

    def test_a_null_boost_is_none_rather_than_an_empty_boost(self):
        self.refusal = (200, {'boost': None})
        self.assertIsNone(self.client.current_boost())

    def test_an_unknown_feed_kind_is_named_rather_than_dropped(self):
        self.refusal = (200, envelope('social.activity', [{'kind': 'shout'}]))
        with self.assertRaises(social.ClientError) as caught:
            self.client.activity()
        self.assertIn('shout', str(caught.exception))


class Writes(Wire):

    def test_a_comment_is_posted_to_the_discussion_route_as_json(self):
        result = self.client.comment('track', '7', 'hello there')
        call = self.calls('POST')[0]
        self.assertEqual(call.path, '/api/social/track/7/comments')
        self.assertEqual(call.payload(), {'body': 'hello there'})
        self.assertEqual(call.headers['content-type'], 'application/json')
        self.assertEqual(call.headers['cookie'], f'{social.SESSION_COOKIE}={COOKIE}')
        self.assertEqual(call.headers['origin'], self.base)
        self.assertEqual(result.score, 3)

    def test_an_edit_puts_the_comment_id_in_the_path(self):
        self.client.edit_comment('playlist', '3', 11, 'fixed')
        call = self.calls('POST')[0]
        self.assertEqual(call.path, '/api/social/playlist/3/comments/11')
        self.assertEqual(call.payload(), {'body': 'fixed'})

    def test_a_delete_is_the_same_route_with_the_flag(self):
        self.client.delete_comment('album', 'b' * 64, 11)
        call = self.calls('POST')[0]
        self.assertEqual(call.path, f'/api/social/album/{"b" * 64}/comments/11')
        self.assertEqual(call.payload(), {'delete': True})

    def test_a_vote_sends_an_integer_value(self):
        self.client.vote('track', '7', -1)
        call = self.calls('POST')[0]
        self.assertEqual(call.path, '/api/social/track/7/vote')
        self.assertEqual(call.payload(), {'value': -1})
        self.assertIsInstance(call.payload()['value'], int)

    def test_a_like_is_the_comment_id_in_the_path_and_an_empty_body(self):
        self.client.like('track', '7', 11)
        call = self.calls('POST')[0]
        self.assertEqual(call.path, '/api/social/track/7/like/11')
        self.assertEqual(call.payload(), {})

    def test_a_boost_posts_the_track_id_to_the_unversioned_route(self):
        result = self.client.boost(9)
        call = self.calls('POST', social.BOOST_PATH)[0]
        self.assertEqual(call.path, '/api/boost')
        self.assertEqual(call.payload(), {'track_id': 9})
        self.assertTrue(result.swapped)
        self.assertFalse(result.already_active)
        self.assertEqual(result.track_id, 9)

    def test_a_write_never_puts_the_session_in_the_url(self):
        self.client.comment('track', '7', 'hello')
        call = self.calls('POST')[0]
        self.assertNotIn(COOKIE, call.path)
        self.assertNotIn(COOKIE, json.dumps(call.query))


class Errors(Wire):

    def test_an_error_envelope_becomes_its_typed_refusal(self):
        self.refusal = (404, {'error': 'Target not found'})
        with self.assertRaises(social.NotFound) as caught:
            self.client.comments('track', '7')
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.message, 'Target not found')
        self.assertIsInstance(caught.exception, social.Denied)
        self.assertIn('404', str(caught.exception))

    def test_a_missing_session_is_named(self):
        self.refusal = (401, {'error': 'Sign in required'})
        with self.assertRaises(social.SignInRequired):
            self.client.activity()

    def test_a_spent_bucket_carries_its_retry_after(self):
        self.refusal = (429, {'error': 'Rate limit exceeded', 'retry_after': 42})
        with self.assertRaises(social.RateLimited) as caught:
            self.client.vote('track', '7', 1)
        self.assertEqual(caught.exception.retry_after, 42)

    def test_a_moderation_refusal_keeps_the_servers_sentence(self):
        self.refusal = (403, {'error': 'Only author or administrator may moderate'})
        with self.assertRaises(social.Denied) as caught:
            self.client.delete_comment('track', '7', 11)
        self.assertNotIsInstance(caught.exception, (social.NotFound, social.SignInRequired))
        self.assertIn('Only author or administrator', str(caught.exception))

    def test_a_status_without_an_envelope_is_a_client_error(self):
        self.raw = (500, {'Content-Type': 'text/html'}, b'<h1>boom</h1>')
        with self.assertRaises(social.ClientError) as caught:
            self.client.activity()
        self.assertIn('500', str(caught.exception))

    def test_an_answer_that_is_not_a_v1_envelope_is_a_client_error(self):
        self.refusal = (200, {'items': FEED})
        with self.assertRaises(social.ClientError) as caught:
            self.client.activity()
        self.assertIn('v1 envelope', str(caught.exception))

    def test_a_non_json_answer_is_a_client_error(self):
        self.raw = (200, {'Content-Type': 'text/html'}, b'<html>proxy</html>')
        with self.assertRaises(social.ClientError):
            self.client.summary('track', '7')


class CredentialsStayOutOfMessages(Wire):

    def test_a_refusal_never_carries_the_session_cookie(self):
        self.refusal = (403, {'error': 'Same-origin request required'})
        with self.assertRaises(social.Denied) as caught:
            self.client.comment('track', '7', 'hello')
        self.assertNotIn(COOKIE, str(caught.exception))
        self.assertNotIn(COOKIE, repr(caught.exception))
        self.assertNotIn(COOKIE, repr(self.client))

    def test_a_wire_failure_never_carries_the_session_cookie(self):
        dead = social.SocialClient('http://127.0.0.1:1', capabilities.Capabilities.from_document(
            document(**FULL)), social.Session.from_cookie(COOKIE))
        with self.assertRaises(social.ClientError) as caught:
            dead.activity()
        self.assertNotIn(COOKIE, str(caught.exception))
        self.assertNotIn(COOKIE, repr(caught.exception))

    def test_the_session_does_not_print_its_cookie(self):
        session = social.Session.from_cookie(COOKIE)
        self.assertNotIn(COOKIE, repr(session))
        self.assertEqual(session.header(), {'Cookie': f'{social.SESSION_COOKIE}={COOKIE}'})

    def test_a_cookie_header_is_read_back_into_a_session(self):
        session = social.Session.from_cookie_header(
            f'other=1; {social.SESSION_COOKIE}={COOKIE}; more=2')
        self.assertEqual(session.cookie, COOKIE)
        self.assertEqual(social.Session.from_cookie_header('other=1').header(), {})


if __name__ == '__main__':
    unittest.main()
