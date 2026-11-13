"""GET /api/v1/capabilities: one honest answer to "what does this server do?".

A client should not have to probe endpoints — or read the README of a particular release — to
find out whether transcoding exists, whether Google sign-in is configured, or whether the
library is scoped per member. The endpoint is public because none of those answers is a secret
and a client needs them before it holds a session.

The risk with a hand-written list is that it drifts from the code and starts lying. So the test
compares it to the modules: the Subsonic version it claims is the one the envelope sends, the
transcode targets are the ones the encoder accepts, and the extension list is the one
`getOpenSubsonicExtensions` returns.
"""
import json
import unittest
from unittest.mock import patch

import server
import subsonic
import test_security as harness


class Capabilities(unittest.TestCase):
    setUp = harness.SecurityHTTP.setUp
    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request

    def caps(self):
        code, headers, raw = self.request('/api/v1/capabilities')
        self.assertEqual(code, 200)
        self.assertTrue(headers.get('Content-Type', '').startswith('application/json'))
        return json.loads(raw)

    def test_it_answers_without_a_session(self):
        """A client asks before it signs in, so this must not be behind the session."""
        body = self.caps()
        self.assertNotIn('error', body)

    def test_it_names_the_server_and_the_api(self):
        body = self.caps()
        self.assertEqual(body['server']['name'], 'HardlyListening')
        self.assertEqual(body['server']['api'], 'v1')
        self.assertTrue(body['server']['version'])
        self.assertIn('requires_members', body['server'])

    def test_the_subsonic_facts_are_the_ones_the_protocol_sends(self):
        body = self.caps()
        documented = body['subsonic']
        self.assertEqual(documented['version'], subsonic.PROTOCOL_VERSION)
        self.assertEqual(documented['server_version'], subsonic.SERVER_VERSION)
        self.assertEqual(documented['extensions'], subsonic.EXTENSIONS)
        self.assertEqual(documented['transcode_targets'], sorted(subsonic.TRANSCODE_TARGETS))
        self.assertTrue(documented['open_subsonic'])

    def test_the_two_boot_switches_are_reported_as_they_are(self):
        """`subsonic.enabled` is a fact, not the constant it used to be — and the web UI joins it.

        Both are read once at boot into `server.FEATURES`; the document is built from that, so a
        server started with `--no-subsonic` must not claim the protocol.
        """
        body = self.caps()
        self.assertTrue(body['subsonic']['enabled'])
        self.assertTrue(body['web_ui']['enabled'])
        with patch.object(server, 'FEATURES', {'web_ui': False, 'subsonic': False}):
            body = self.caps()
        self.assertFalse(body['subsonic']['enabled'])
        self.assertFalse(body['web_ui']['enabled'])
        # The protocol facts are still described; only the switch that admits them is off.
        self.assertEqual(body['subsonic']['version'], subsonic.PROTOCOL_VERSION)

    def test_a_claimed_extension_is_really_answered(self):
        """`openSubsonic` is not a badge: the extension list has to be true."""
        self.assertIn({'name': 'transcoding', 'versions': [1]}, self.caps()['subsonic']['extensions'])

    def test_google_is_reported_from_the_module_that_implements_it(self):
        import oauth_google
        self.assertEqual(self.caps()['features']['accounts']['google'],
                         oauth_google.available())

    def test_it_claims_no_feature_this_server_cannot_do(self):
        features = self.caps()['features']
        self.assertFalse(features['playback']['hls'],
                         'hls is not implemented; claiming it makes clients ask for segments')
        self.assertFalse(features['discovery']['sonic_analysis'],
                         'sonic analysis needs numpy, which this server does not ship')
        self.assertFalse(features['party']['jukebox'])
        self.assertTrue(features['playback']['range'])
        self.assertTrue(features['playback']['transcode'])


if __name__ == '__main__':
    unittest.main()