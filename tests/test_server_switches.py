"""SERVER-006: the two switches the window offers really gate what they say they gate.

The card existed because HOST-005 refused to store "web UI on/off" and "OpenSubsonic on/off" —
the server had no switch for either, so a stored flag would have been a promise the next start
broke. This file is the other half of that bargain: `--no-web-ui` and `--no-subsonic` are parsed
into `server.FEATURES` at boot, the request path reads that, and `/api/v1/capabilities` reports
it honestly rather than as a constant.

Three things are proven, rather than asserted:

  * **The flag vocabulary is the environment's.** The parser's defaults come from
    `config.web_ui()`/`config.subsonic()`, and `dest` is the positive name, so `--no-web-ui`
    cannot end up meaning "on" through a second negation.
  * **Off means a 404 with a sentence, not a blank page.** Every page and asset from `web/`
    is gated in `static()`, which is the one function that serves them; the API, `/rest/` and
    the health probe are not gated, because they are not the web UI.
  * **Off for OpenSubsonic is the protocol's own error.** A Subsonic client gets a
    `subsonic-response` with `status: failed` and a message, in the format it asked for — not a
    bare HTTP 404 it would report as a network fault.

`server.FEATURES` is patched rather than a socket being started with flags: it is the exact dict
`main()` writes before the server binds, so a test flips the same state a real start would.
"""
import json
import unittest
from unittest.mock import patch

import server
import test_security as harness

ONLY_UI_OFF = {'web_ui': False, 'subsonic': True}
ONLY_SUBSONIC_OFF = {'web_ui': True, 'subsonic': False}


class TheFlagsAreTheEnvironmentsDefaults(unittest.TestCase):
    def test_on_is_the_default_when_nothing_declares_otherwise(self):
        args = server.argument_parser('127.0.0.1', 8470).parse_args([])
        self.assertTrue(args.web_ui)
        self.assertTrue(args.subsonic)
        self.assertEqual((args.host, args.port), ('127.0.0.1', 8470))

    def test_each_flag_turns_off_only_its_own_surface(self):
        parser = server.argument_parser('127.0.0.1', 8470)
        args = parser.parse_args(['--no-web-ui'])
        self.assertFalse(args.web_ui)
        self.assertTrue(args.subsonic)
        args = parser.parse_args(['--no-subsonic'])
        self.assertTrue(args.web_ui)
        self.assertFalse(args.subsonic)
        args = parser.parse_args(['--no-web-ui', '--no-subsonic'])
        self.assertFalse(args.web_ui or args.subsonic)

    def test_an_environment_default_of_off_is_kept_and_a_flag_does_not_undo_it(self):
        # `HARDLYLISTENING_WEB_UI=0` is the default; there is no flag that turns it back on,
        # so a unit file's decision cannot be overridden by a window that only sends --no-*.
        args = server.argument_parser('127.0.0.1', 8470, web_ui=False).parse_args([])
        self.assertFalse(args.web_ui)
        args = server.argument_parser('127.0.0.1', 8470, web_ui=False).parse_args(
            ['--no-subsonic'])
        self.assertFalse(args.web_ui)
        self.assertFalse(args.subsonic)


class TheWebUiSwitch(unittest.TestCase):
    setUp = harness.SecurityHTTP.setUp
    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request

    def test_pages_and_assets_are_404_with_a_sentence(self):
        with patch.object(server, 'FEATURES', ONLY_UI_OFF):
            for path in ('/', '/index.html', '/login', '/party', '/swipe',
                         '/js/playback.js', '/offline.html'):
                code, headers, raw = self.request(path)
                self.assertEqual(code, 404, path)
                self.assertIn(b'the web ui is disabled', raw.lower(), path)
                self.assertTrue(headers.get('Content-Type', '').startswith('text/plain'), path)

    def test_the_api_and_the_health_probe_are_not_the_web_ui(self):
        with patch.object(server, 'FEATURES', ONLY_UI_OFF):
            code, _, raw = self.request('/api/health')
            self.assertEqual(code, 200)
            self.assertFalse(json.loads(raw).get('error'))
            code, _, raw = self.request('/api/v1/capabilities')
            self.assertEqual(code, 200)
            self.assertFalse(json.loads(raw)['web_ui']['enabled'])

    def test_with_the_switch_on_the_pages_are_served(self):
        """The control: the gate is the switch, not a missing file or the auth redirect."""
        with patch.object(server, 'FEATURES', {'web_ui': True, 'subsonic': True}):
            code, headers, raw = self.request('/login')
            self.assertEqual(code, 200)
            self.assertIn(b'html', headers.get('Content-Type', '').encode())
            self.assertNotIn(b'disabled on this server', raw)


class TheOpenSubsonicSwitch(unittest.TestCase):
    setUp = harness.SecurityHTTP.setUp
    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request

    def test_rest_answers_the_protocols_own_error(self):
        with patch.object(server, 'FEATURES', ONLY_SUBSONIC_OFF):
            code, headers, raw = self.request(
                '/rest/ping?u=someone&p=secret&v=1.16.1&c=test&f=json')
            self.assertEqual(code, 200)
            self.assertTrue(headers.get('Content-Type', '').startswith('application/json'))
            body = json.loads(raw)['subsonic-response']
            self.assertEqual(body['status'], 'failed')
            self.assertEqual(body['error']['code'], 0)
            self.assertIn('disabled', body['error']['message'].lower())

    def test_the_xml_client_gets_xml(self):
        with patch.object(server, 'FEATURES', ONLY_SUBSONIC_OFF):
            code, headers, raw = self.request('/rest/ping?u=someone&p=secret&v=1.16.1&c=test')
            self.assertEqual(code, 200)
            self.assertTrue(headers.get('Content-Type', '').startswith('application/xml'))
            self.assertIn(b'<subsonic-response', raw)
            self.assertIn(b'disabled', raw.lower())

    def test_the_rest_of_the_server_is_unaffected(self):
        with patch.object(server, 'FEATURES', ONLY_SUBSONIC_OFF):
            code, _, raw = self.request('/api/health')
            self.assertEqual(code, 200)
            code, _, raw = self.request('/api/v1/capabilities')
            self.assertEqual(code, 200)
            document = json.loads(raw)
            self.assertFalse(document['subsonic']['enabled'])
            self.assertTrue(document['web_ui']['enabled'])

    def test_with_the_switch_on_rest_is_dispatched(self):
        """The control: nothing about `/rest/` is answered by the gate when it is on."""
        with patch.object(server, 'FEATURES', {'web_ui': True, 'subsonic': True}):
            code, _, raw = self.request(
                '/rest/ping?u=someone&p=secret&v=1.16.1&c=test&f=json')
            self.assertEqual(code, 200)
            self.assertNotIn(b'disabled on this server', raw)


if __name__ == '__main__':
    unittest.main()