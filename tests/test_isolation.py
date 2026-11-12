"""Domains are separable: one broken domain is not a broken server.

The board asks two questions. Does playback still serve when Social is down? Does
the Library still answer when discovery (the provider-facing Repo reads) is down?
Since everything runs in one process, "down" here means the import itself fails —
the worst realistic version of a broken module, and the one a lazy import is
supposed to survive.

The mechanism is a `sys.meta_path` finder that refuses the named packages and a
matching purge from `sys.modules`, so a route that truly needs them fails while a
route that does not keeps working. Nothing here touches real media, network or
another domain's data.
"""
import io
import sys
import tempfile
import threading
import unittest
import wave
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import test_security as harness


@contextmanager
def blocked(*prefixes):
    """Make `import <prefix>...` fail, and restore the real modules afterwards."""
    saved = {name: module for name, module in sys.modules.items()
             if any(name == p or name.startswith(p + '.') for p in prefixes)}

    class Refuse:
        def find_module(self, name, path=None):        # pragma: no cover - legacy hook
            return None

        def find_spec(self, name, path=None, target=None):
            if any(name == p or name.startswith(p + '.') for p in prefixes):
                raise ModuleNotFoundError(f"blocked by the isolation test: {name}")
            return None

    finder = Refuse()
    for name in saved:
        sys.modules.pop(name, None)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for name in [n for n in sys.modules
                     if any(n == p or n.startswith(p + '.') for p in prefixes)]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def media(seconds=1, rate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x00' * (rate * seconds))
    return buffer.getvalue()


class Isolation(unittest.TestCase):
    """One HTTP fixture for both questions, sharing the security harness."""

    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request
    token = harness.SecurityHTTP.token
    user = harness.SecurityHTTP.user

    def setUp(self):
        harness.SecurityHTTP.setUp(self)
        root = Path(self.tmp.name) / 'music'
        root.mkdir()
        self.store.set_settings({'root': str(root)})
        self.blob = media()
        path = root / 'song.wav'
        path.write_bytes(self.blob)
        self.track_id = self.store.track('local', 'iso-1', 'Isolated', artist_name='Artist',
                                         album='Album', source_url='https://example.test/x')
        with self.store.lock:
            self.store.conn.execute(
                'UPDATE tracks SET file=?, duration=1 WHERE id=?',
                (str(path), self.track_id))
            self.store.conn.commit()
        self.identity = self.user('member')
        self.cookie = {'Cookie': self.token('u1'), 'Origin': 'https://music.example'}

    def audio(self):
        code, headers, body = self.request(
            f'/api/track/{self.track_id}/audio', headers={**self.cookie, 'Range': 'bytes=0-99'})
        return code, headers, body

    def library_saves(self):
        code, _headers, body = self.request('/api/listening/saves', headers=self.cookie)
        return code, body

    def test_playback_serves_while_social_is_down(self):
        with blocked('social'):
            code, headers, body = self.audio()
        self.assertEqual(code, 206, body[:200])
        self.assertEqual(len(body), 100)
        self.assertEqual(headers.get('Content-Range'), f'bytes 0-99/{len(self.blob)}')

    def test_the_library_answers_while_discovery_is_down(self):
        with blocked('discovery'):
            code, body = self.library_saves()
        self.assertEqual(code, 200, body[:200])

    def failing(self, path):
        """The refused request: an error status, or the dropped connection it is today.

        `do_GET` has no catch-all, so an ImportError inside a route closes that one
        connection instead of answering. That is a rough edge, not a crash — what this
        suite pins is that the process and every other domain keep serving. If a status
        is returned it must be an error, never a success pretending.
        """
        from http.client import RemoteDisconnected
        try:
            code, _headers, body = self.request(path, headers=self.cookie)
        except (RemoteDisconnected, ConnectionResetError):
            return None
        self.assertGreaterEqual(code, 400, body[:200])
        return code

    def test_a_discovery_route_fails_alone_and_the_server_stays_up(self):
        """Discovery is bound at import time, so "down" means it raises when asked.

        `repo/service.py` binds `discovery` on import, so an import block added later would
        prove nothing here — the call is where a provider outage actually shows up.
        """
        from repo import service as repo_service
        with patch.object(repo_service, 'artist', side_effect=RuntimeError('discovery is down')):
            self.failing('/api/artist?name=Artist')
        self.assertEqual(self.request('/api/health')[0], 200)
        self.assertEqual(self.audio()[0], 206)

    def test_a_social_route_fails_alone_and_playback_is_untouched(self):
        with blocked('social'):
            self.failing(f'/api/social/track/{self.track_id}')
            self.assertEqual(self.request('/api/health')[0], 200)
            code, body = self.library_saves()
            self.assertEqual(code, 200, body[:200])
            self.assertEqual(self.audio()[0], 206)


if __name__ == '__main__':
    unittest.main()