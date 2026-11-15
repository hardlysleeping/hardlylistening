"""Publishing to Funkwhale: the API surface the plan called unknown, exercised against a stub.

The honest part of this card is the definition of success. Funkwhale answers `201` with an *upload*
and no track; the track appears later, when the upload reports `finished`. So the tests are written
around the ways "accepted" lies — pending forever, errored, finished without a track — and each one
has to be a failure with a reason rather than a zero that looks like a song.

Everything here talks to a local stub over loopback. No instance, token or real library is touched.
"""
import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from library import funkwhale

TOKEN = 'secret-token-value'
AUDIO = b'fLaC' + bytes(range(64)) * 8


class Stub:
    """The two endpoints, with a script for what the upload does over time."""

    def __init__(self, statuses=('pending', 'finished'), track=True):
        self.statuses = list(statuses)
        self.track = track
        self.calls = []
        self.bodies = []
        self.polls = 0
        self.token = None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def _answer(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                outer.calls.append((self.command, self.path, dict(self.headers)))
                outer.token = self.headers.get('Authorization', '')
                outer.bodies.append(body)
                if self.command == 'POST' and self.path == '/api/v2/uploads/':
                    payload = {'uuid': 'upload-1', 'import_status': outer.statuses[0],
                               'filename': 'a.flac', 'size': len(AUDIO)}
                    # The create answer is a full upload object, so an instance that finished
                    # the work instantly already carries the track here.
                    if outer.statuses[0] == 'finished' and outer.track:
                        payload['track'] = {'id': 42, 'fid': 'abc-123', 'title': 'A Song'}
                    return self._json(201, payload)
                if self.command == 'GET' and self.path.startswith('/api/v2/uploads/'):
                    # Each poll answers the next scripted status, and the last one repeats, so
                    # "pending forever" is expressible as a one-element list.
                    status = outer.statuses[min(outer.polls, len(outer.statuses) - 1)]
                    outer.polls += 1
                    payload = {'uuid': 'upload-1', 'import_status': status}
                    if status == 'finished' and outer.track:
                        payload['track'] = {'id': 42, 'fid': 'abc-123', 'title': 'A Song'}
                    return self._json(200, payload)
                return self._json(404, {'detail': 'no'})

            def _json(self, code, payload):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _answer

        self.http = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = 'http://127.0.0.1:%d' % self.http.server_port
        return self

    def __exit__(self, *exc):
        self.http.shutdown()
        self.http.server_close()


class Publish(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.file = Path(self.dir.name) / 'a.flac'
        self.file.write_bytes(AUDIO)

    def test_a_finished_upload_with_a_track_is_the_only_success(self):
        with Stub(statuses=['pending', 'finished']) as stub:
            result = funkwhale.publish(self.file, stub.url, TOKEN, sleep=lambda _: None)
            self.assertEqual(result['track_id'], 42)
            self.assertEqual(result['fid'], 'abc-123')
            self.assertEqual(result['status'], 'finished')
            # The token travels in a header, never in a URL.
            self.assertEqual(stub.token, 'Bearer ' + TOKEN)
            self.assertTrue(all('token' not in path for _, path, _ in stub.calls))
            # The bytes were sent as a file part, not as an escaped string.
            self.assertIn(b'name="audio_file"; filename="a.flac"', stub.bodies[0])
            self.assertIn(AUDIO, stub.bodies[0])

    def test_an_upload_that_never_finishes_is_a_failure_not_a_zero(self):
        with Stub(statuses=['pending']) as stub:
            with self.assertRaises(funkwhale.PublishError) as caught:
                funkwhale.publish(self.file, stub.url, TOKEN, attempts=3,
                                  sleep=lambda _: None)
            self.assertIn('did not finish', str(caught.exception))

    def test_an_errored_upload_names_itself(self):
        with Stub(statuses=['pending', 'errored']) as stub:
            with self.assertRaises(funkwhale.PublishError) as caught:
                funkwhale.publish(self.file, stub.url, TOKEN, sleep=lambda _: None)
            self.assertIn('errored', str(caught.exception))

    def test_finished_without_a_track_is_still_a_failure(self):
        # The state "accepted" hides: the server says it is done, and nothing is there.
        with Stub(statuses=['pending', 'finished'], track=False) as stub:
            with self.assertRaises(funkwhale.PublishError) as caught:
                funkwhale.publish(self.file, stub.url, TOKEN, sleep=lambda _: None)
            self.assertIn('no track', str(caught.exception))

    def test_a_done_on_the_first_answer_needs_no_polling(self):
        with Stub(statuses=['finished']) as stub:
            result = funkwhale.publish(self.file, stub.url, TOKEN, sleep=lambda _: None)
            self.assertEqual(result['track_id'], 42)
            self.assertEqual([c[0] for c in stub.calls], ['POST'])


class Refusals(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.file = Path(self.dir.name) / 'a.mp3'
        self.file.write_bytes(b'ID3' + b'\0' * 32)

    def test_a_token_is_required_and_named_in_the_error(self):
        with mock.patch.dict(os.environ, {funkwhale.TOKEN_ENV: ''}):
            with self.assertRaises(funkwhale.PublishError) as caught:
                funkwhale.Client('https://music.example', funkwhale.token())
        self.assertIn(funkwhale.TOKEN_ENV, str(caught.exception))

    def test_a_plain_http_instance_is_refused_except_for_loopback(self):
        with self.assertRaises(funkwhale.PublishError) as caught:
            funkwhale.base('http://music.example')
        self.assertIn('in the clear', str(caught.exception))
        self.assertEqual(funkwhale.base('http://127.0.0.1:5000'), 'http://127.0.0.1:5000')

    def test_a_url_with_a_query_is_refused(self):
        with self.assertRaises(funkwhale.PublishError):
            funkwhale.base('https://music.example/?token=oops')

    def test_a_missing_file_is_refused_before_anything_is_sent(self):
        with Stub() as stub:
            client = funkwhale.Client(stub.url, TOKEN)
            with self.assertRaises(funkwhale.PublishError) as caught:
                client.upload(Path(self.dir.name) / 'nope.flac')
            self.assertIn('is not a file', str(caught.exception))
            self.assertEqual(stub.calls, [])

    def test_an_http_error_body_is_kept_and_the_token_is_not(self):
        class Refuse:
            def __init__(self, code):
                self.code = code

            def read(self):
                return b'{"detail": "Invalid token."}'

            def close(self):
                pass

        def opener(request, timeout=None):
            raise __import__('urllib.error', fromlist=['HTTPError']).HTTPError(
                request.full_url, 401, 'Unauthorized', {}, Refuse(401))

        client = funkwhale.Client('https://music.example', TOKEN, opener=opener)
        with self.assertRaises(funkwhale.PublishError) as caught:
            client.upload(self.file)
        self.assertEqual(caught.exception.status, 401)
        self.assertIn('Invalid token', caught.exception.body.decode())
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_the_instance_and_token_are_read_from_the_environment(self):
        with mock.patch.dict(os.environ, {funkwhale.INSTANCE_ENV: 'https://music.example',
                                          funkwhale.TOKEN_ENV: 'abc'}):
            self.assertEqual(funkwhale.instance(), 'https://music.example')
            self.assertEqual(funkwhale.token(), 'abc')


if __name__ == '__main__':
    unittest.main()