"""The acquisition descriptor, and who is allowed to speak it.

`FED-MIRROR-001` is the first half: a descriptor says only *which provider result to ask for
again* — provider, the provider's own track id, and the metadata a re-acquisition needs. Nothing
that names a credential, a file or a member is allowed into it, and an input that carries any of
that refuses to build rather than being quietly trimmed. The round trip is asserted, not the
function's good intentions: build, encode, decode, and compare bytes.

`FED-001` is the second: Repo federation attaches to `repo/service.py` only, and nothing a player
talks to may reach it. That is a dependency-direction fact, so it is measured from the modules on
disk (AST, like `tests/test_headless.py`) rather than from an import-time trick that a lazy import
would sidestep.
"""
import json
import unittest

from providers.base import TrackRef
from repo import federation, service


class Descriptor(unittest.TestCase):
    def ref(self, **overrides):
        values = dict(provider='soundcloud', provider_track_id='123456', title='Xtal',
                      artist='Aphex Twin', album='Selected Ambient Works', duration=293.0,
                      source_url='https://soundcloud.com/aphex-twin/xtal')
        values.update(overrides)
        return TrackRef(**values)

    def test_a_provider_result_round_trips_through_the_canonical_encoding(self):
        descriptor = service.acquisition_descriptor(self.ref())
        self.assertEqual(descriptor['version'], federation.VERSION)
        text = service.descriptor_json(descriptor)
        self.assertEqual(service.parse_descriptor(text), descriptor)
        # Canonical means stable: parsing and re-encoding cannot change a byte, so two nodes
        # that agree on a descriptor agree on its hash without agreeing on Python.
        self.assertEqual(service.descriptor_json(service.parse_descriptor(text)), text)
        self.assertEqual(text, json.dumps(json.loads(text), ensure_ascii=False,
                                          separators=(',', ':'), sort_keys=True))
        self.assertIn('"version":1', text)

    def test_a_provider_result_with_no_source_url_is_allowed_and_no_other_scheme_is(self):
        self.assertEqual(service.acquisition_descriptor(self.ref(source_url=''))['source_url'], '')
        for bad in ('file:///music/song.mp3', '/music/song.mp3', 'C:\\music\\song.mp3',
                    '~/.cache/song.mp3'):
            with self.subTest(source_url=bad):
                with self.assertRaises(ValueError):
                    service.acquisition_descriptor(self.ref(source_url=bad))

    def test_credential_material_refuses_to_build_and_is_never_echoed(self):
        secrets = {'cookies': 'sessionid=abc', 'http_headers': {'Authorization': 'Bearer abc'},
                   'access_token': 'tok_live_123', 'password': 'hunter2',
                   'api_key': 'sk-live-123', 'cookiefile': '/home/someone/cookies.txt',
                   'session_id': 'sess-1', 'client_secret': 'shh'}
        for name, value in secrets.items():
            with self.subTest(field=name):
                result = {'provider': 'soundcloud', 'provider_track_id': '1', 'title': 'Song',
                          name: value}
                with self.assertRaises(federation.UnsafeDescriptor) as caught:
                    service.acquisition_descriptor(result)
                self.assertIn(name, caught.exception.fields)
                # The refusal is loggable: it names the field, never what was in it.
                self.assertNotIn(str(value), str(caught.exception))

    def test_a_path_or_a_field_off_the_allow_list_refuses_rather_than_dropping(self):
        for name, value in {'file': '/music/song.mp3', 'local_path': 'song.mp3',
                            'outtmpl': '%(title)s.%(ext)s', 'match': 0.93}.items():
            with self.subTest(field=name):
                result = {'provider': 'soundcloud', 'provider_track_id': '1', 'title': 'Song',
                          name: value}
                with self.assertRaises(federation.UnsafeDescriptor) as caught:
                    service.acquisition_descriptor(result)
                self.assertIn(name, caught.exception.fields)

    def test_a_descriptor_from_the_wire_is_checked_again(self):
        good = service.descriptor_json(service.acquisition_descriptor(self.ref()))
        for text in ('[]', 'not a descriptor', '{"version": 2}', '{"version": true}',
                     good.replace('"title"', '"token"')):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    service.parse_descriptor(text)

    def test_duration_must_be_a_finite_number_of_seconds(self):
        payload = service.acquisition_descriptor(self.ref())
        for bad in (float('nan'), float('inf'), -1):
            with self.subTest(duration=bad):
                with self.assertRaises(ValueError):
                    federation.dumps(dict(payload, duration=bad))
                with self.assertRaises(ValueError):
                    service.parse_descriptor(json.dumps(dict(payload, duration=bad)))

    def test_required_fields_cannot_be_blank(self):
        for overrides in ({'title': ''}, {'provider_track_id': '  '}, {'provider': ''},
                          {'title': 'x' * (federation.MAX_TEXT + 1)}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    service.acquisition_descriptor(self.ref(**overrides))

    def test_a_title_is_not_scanned_as_if_it_were_a_secret(self):
        # Fields are judged by name, not by guessing at values: a song may be called anything.
        self.assertEqual(service.acquisition_descriptor(self.ref(title='Password'))['title'],
                         'Password')

    def test_provider_metadata_the_mirror_does_not_use_is_not_carried(self):
        descriptor = service.acquisition_descriptor(
            self.ref(isrc='GBAYE9200001', artwork='https://i1.sndcdn.com/art.jpg',
                     track_number=3, release_id='rel-1'))
        for name in ('isrc', 'artwork', 'track_number', 'release_id'):
            self.assertNotIn(name, descriptor)

    def test_the_alias_names_provider_results_already_use_are_accepted(self):
        descriptor = service.acquisition_descriptor({
            'engine': 'youtube', 'source_id': 'dQw4w9', 'uploader': 'Uploader',
            'url': 'https://www.youtube.com/watch?v=dQw4w9', 'title': 'Song'})
        self.assertEqual(descriptor['provider'], 'youtube')
        self.assertEqual(descriptor['provider_track_id'], 'dQw4w9')
        self.assertEqual(descriptor['artist'], 'Uploader')
        self.assertEqual(descriptor['source_url'], 'https://www.youtube.com/watch?v=dQw4w9')


if __name__ == '__main__':
    unittest.main()