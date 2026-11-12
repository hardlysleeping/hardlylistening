"""The OpenSubsonic transcoding extension, answered with real encodes.

The fixture's only media is a real WAV. A client that accepts the stored file gets those exact
bytes with Range support; a client that would need a re-encode gets `canTranscode` plus
parameters, and `getTranscodeStream` returns an encode made by ffmpeg — never audio in a
format the client asked not to receive, and never a claim of a transcode this server cannot
produce.
"""
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from test_listening import ListeningHTTP

WAV_CLIENT = {
    'name': 'fixture', 'platform': 'test',
    'directPlayProfiles': [{'containers': ['wav'], 'audioCodecs': ['pcm'], 'protocols': ['http']}],
}
MP3_CLIENT = {
    'name': 'fixture', 'platform': 'test',
    'directPlayProfiles': [{'containers': ['mp3'], 'audioCodecs': ['mp3'], 'protocols': ['http']}],
}


class SubsonicTranscode(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    api = ListeningHTTP.api
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save
    token = ListeningHTTP.token
    user = ListeningHTTP.user

    def setUp(self):
        ListeningHTTP.setUp(self)
        import subsonic
        patcher = patch.object(subsonic, 'CACHE', Path(self.tmp.name) / 'transcode')
        patcher.start()
        self.addCleanup(patcher.stop)
        for track_id in self.ids:
            self.save('track', track_id)

    def decide(self, media=None, client=None, device=None, media_type='song'):
        device = device or self.device
        query = urlencode(dict(u=device['username'], p=device['password'], f='json',
                               mediaId=str(self.ids[0] if media is None else media),
                               mediaType=media_type))
        code, _, raw = self.request('/rest/getTranscodeDecision.view?' + query, 'POST',
                                    {'Content-Type': 'application/json'},
                                    json.dumps(client if client is not None else WAV_CLIENT))
        return code, json.loads(raw)['subsonic-response']

    def stream_url(self, decision, media=None, **extra):
        args = dict(u=self.device['username'], p=self.device['password'], f='json',
                    mediaId=str(self.ids[0] if media is None else media), mediaType='song',
                    transcodeParams=decision.get('transcodeParams', ''))
        args.update(extra)
        return '/rest/getTranscodeStream.view?' + urlencode(args)

    def test_direct_play_decision_describes_the_stored_file(self):
        code, body = self.decide()
        self.assertEqual(code, 200)
        decision = body['transcodeDecision']
        self.assertTrue(decision['canDirectPlay'])
        self.assertFalse(decision['canTranscode'])
        self.assertTrue(decision['transcodeParams'])
        self.assertNotIn('errorReason', decision)
        self.assertEqual(decision['sourceStream']['container'], 'wav')
        self.assertEqual(decision['sourceStream']['codec'], 'pcm')
        self.assertEqual(decision['sourceStream']['protocol'], 'http')

    def test_a_client_that_needs_a_re_encode_is_offered_one(self):
        _, body = self.decide(client=MP3_CLIENT)
        decision = body['transcodeDecision']
        self.assertFalse(decision['canDirectPlay'])
        self.assertTrue(decision['canTranscode'])
        self.assertTrue(decision['transcodeParams'])
        self.assertNotIn('errorReason', decision)
        self.assertEqual(decision['transcodeReason'], ['Profile1ContainerNotSupported'])

    def test_a_plain_stream_gets_the_re_encode_it_asked_for(self):
        query = urlencode(dict(u=self.device['username'], p=self.device['password'], f='json',
                               id=self.ids[0], format='mp3'))
        code, headers, raw = self.request('/rest/stream.view?' + query)
        self.assertEqual(code, 200)
        self.assertTrue(headers.get('Content-Type', '').startswith('audio/'))
        self.assertNotEqual(raw, self.media)
        self.assertTrue(raw.startswith(b'ID3') or raw[:1] == b'\xff', raw[:8])

    def test_a_plain_stream_with_format_raw_is_the_stored_file(self):
        query = urlencode(dict(u=self.device['username'], p=self.device['password'], f='json',
                               id=self.ids[0], format='raw'))
        code, _, raw = self.request('/rest/stream.view?' + query)
        self.assertEqual(code, 200)
        self.assertEqual(raw, self.media)

    def test_a_plain_stream_refuses_a_format_this_server_cannot_encode(self):
        self.assertEqual(self.response('stream', id=self.ids[0], format='wma')['error']['code'], 30)

    def test_the_transcode_stream_is_encoded_audio_with_range_support(self):
        _, body = self.decide(client=MP3_CLIENT)
        url = self.stream_url(body['transcodeDecision'])
        code, headers, raw = self.request(url)
        self.assertEqual(code, 200)
        self.assertTrue(headers.get('Content-Type', '').startswith('audio/'))
        self.assertEqual(headers.get('Accept-Ranges'), 'bytes')
        self.assertNotEqual(raw, self.media)
        self.assertTrue(raw.startswith(b'ID3') or raw[:1] == b'\xff', raw[:8])
        full = len(raw)

        code, headers, raw = self.request(url, 'GET', {'Range': 'bytes=0-99'})
        self.assertEqual(code, 206)
        self.assertEqual(len(raw), 100)
        self.assertEqual(headers.get('Content-Range'), 'bytes 0-99/%d' % full)

    def test_stream_is_the_stored_bytes_with_range_support(self):
        _, body = self.decide()
        url = self.stream_url(body['transcodeDecision'])
        code, headers, raw = self.request(url)
        self.assertEqual(code, 200)
        self.assertEqual(raw, self.media)
        self.assertTrue(headers.get('Content-Type', '').startswith('audio/'))
        self.assertEqual(headers.get('Accept-Ranges'), 'bytes')

        code, headers, raw = self.request(url, 'GET', {'Range': 'bytes=100-199'})
        self.assertEqual(code, 206)
        self.assertEqual(raw, self.media[100:200])
        self.assertEqual(headers.get('Content-Range'), 'bytes 100-199/%d' % len(self.media))

    def test_stream_refuses_missing_expired_foreign_and_offset_requests(self):
        _, body = self.decide()
        params = body['transcodeDecision']['transcodeParams']
        # No parameters at all: the client never asked for a decision.
        self.assertEqual(self.response('getTranscodeStream', mediaId=self.ids[0],
                                       mediaType='song')['error']['code'], 10)
        # Parameters minted for another track, and an unknown offset transcode.
        self.assertEqual(self.response('getTranscodeStream', mediaId=self.ids[1], mediaType='song',
                                       transcodeParams=params)['error']['code'], 10)
        self.assertEqual(self.response('getTranscodeStream', mediaId=self.ids[0], mediaType='song',
                                       transcodeParams=params, offset=5)['error']['code'], 30)
        self.assertEqual(self.response('getTranscodeStream', mediaId=self.ids[0], mediaType='song',
                                       transcodeParams='not-a-real-token')['error']['code'], 10)

    def test_stream_never_reaches_a_track_outside_this_member_scope(self):
        _, body = self.decide()
        params = body['transcodeDecision']['transcodeParams']
        self.save('track', self.ids[0], False)
        self.assertEqual(self.response('getTranscodeStream', mediaId=self.ids[0], mediaType='song',
                                       transcodeParams=params)['error']['code'], 70)

    def test_another_member_cannot_reuse_the_parameters(self):
        _, body = self.decide()
        params = body['transcodeDecision']['transcodeParams']
        self.store.conn.execute("INSERT INTO members(id,email,role,display_name) "
                                "VALUES('u2','other@example.com','member','Other')")
        self.store.conn.execute("INSERT INTO listening_devices(id,member_id,label,digest) "
                                "VALUES('other-device','u2','Other',?)",
                                (hashlib.sha256(b'other-pass').hexdigest(),))
        self.store.conn.commit()
        query = urlencode(dict(u='other-device', p='other-pass', f='json', mediaId=self.ids[0],
                               mediaType='song', transcodeParams=params))
        code, _, raw = self.request('/rest/getTranscodeStream.view?' + query)
        self.assertEqual(json.loads(raw)['subsonic-response']['error']['code'], 10)

    def test_podcast_media_is_refused_rather_than_streamed_as_a_song(self):
        _, body = self.decide(media_type='podcast')
        self.assertEqual(body['error']['code'], 30)

    def test_decision_is_post_only_with_a_json_profile(self):
        self.assertEqual(self.response('getTranscodeDecision', mediaId=self.ids[0],
                                       mediaType='song')['error']['code'], 10)
        query = urlencode(dict(u=self.device['username'], p=self.device['password'], f='json',
                               mediaId=self.ids[0], mediaType='song'))
        code, _, raw = self.request('/rest/getTranscodeDecision.view?' + query, 'POST',
                                    {'Content-Type': 'application/json'}, '["not", "an", "object"]')
        self.assertEqual(json.loads(raw)['subsonic-response']['error']['code'], 10)

    def test_xml_answer_carries_the_decision_element(self):
        query = urlencode(dict(u=self.device['username'], p=self.device['password'], f='xml',
                               mediaId=self.ids[0], mediaType='song'))
        _, _, raw = self.request('/rest/getTranscodeDecision.view?' + query, 'POST',
                                 {'Content-Type': 'application/json'}, json.dumps(WAV_CLIENT))
        text = raw.decode()
        self.assertIn('transcodeDecision', text)
        self.assertIn('canDirectPlay="true"', text)
        self.assertIn('container="wav"', text)

    def test_the_extension_is_advertised(self):
        names = [e['name'] for e in self.response('getOpenSubsonicExtensions')['openSubsonicExtensions']]
        self.assertIn('transcoding', names)
        self.assertIn('formPost', names)
        self.assertIn('songLyrics', names)

    def test_the_response_declares_opensubsonic_support(self):
        body = self.response('ping')
        self.assertTrue(body['openSubsonic'])
        # The XML attribute is what a client actually parses.
        query = urlencode(dict(u=self.device['username'], p=self.device['password'],
                               f='xml', v='1.16.1', c='fixture'))
        _, _, raw = self.request('/rest/ping.view?' + query)
        self.assertIn(b'openSubsonic="true"', raw)

    def test_the_scope_guarantee_holds_for_the_new_endpoints(self):
        self.save('track', self.ids[0], False)
        _, body = self.decide()
        self.assertEqual(body['error']['code'], 70)


class TranscodeFormatMatching(unittest.TestCase):
    """Pure decision logic: what a stored file is called, and what a profile accepts."""

    def test_an_opus_file_matches_a_profile_that_calls_the_container_opus(self):
        import subsonic
        track = {'id': 1, 'file': '/music/a.opus', 'duration': 60, 'size': 960000}
        playable, reasons = subsonic.can_direct_play(track, {
            'directPlayProfiles': [{'containers': ['opus'], 'audioCodecs': ['opus'],
                                    'protocols': ['http']}]})
        self.assertTrue(playable, reasons)
        self.assertEqual(subsonic.stream_details(track)['container'], 'ogg')
        self.assertEqual(subsonic.stream_details(track)['codec'], 'opus')
        self.assertEqual(subsonic.stream_details(track)['audioBitrate'], 128000)

    def test_m4a_is_accepted_as_mp4_with_either_aac_or_alac(self):
        import subsonic
        track = {'id': 1, 'file': '/music/a.m4a', 'duration': 60, 'size': 900000}
        for codec in ('aac', 'alac'):
            playable, reasons = subsonic.can_direct_play(track, {
                'directPlayProfiles': [{'containers': ['mp4'], 'audioCodecs': [codec], 'protocols': []}]})
            self.assertTrue(playable, (codec, reasons))

    def test_a_mono_only_sink_is_not_promised_unasked(self):
        import subsonic
        track = {'id': 1, 'file': '/music/a.flac', 'duration': 60, 'size': 900000}
        playable, reasons = subsonic.can_direct_play(track, {
            'directPlayProfiles': [{'containers': ['flac'], 'audioCodecs': ['flac'],
                                    'protocols': ['http'], 'maxAudioChannels': 1}]})
        self.assertFalse(playable)
        self.assertEqual(reasons, ['Profile1AudioChannelsUnknown'])

    def test_a_required_sample_rate_limitation_is_not_guessed(self):
        import subsonic
        track = {'id': 1, 'file': '/music/a.flac', 'duration': 60, 'size': 900000}
        client = {'directPlayProfiles': [{'containers': ['flac'], 'audioCodecs': ['flac'],
                                          'protocols': ['http']}],
                  'codecProfiles': [{'name': 'flac', 'limitations': [
                      {'name': 'audioSamplerate', 'comparison': 'LessThanEqual',
                       'values': ['48000'], 'required': True}]}]}
        self.assertFalse(subsonic.can_direct_play(track, client)[0])
        # The same limitation as a preference is ignored, not treated as a refusal.
        client['codecProfiles'][0]['limitations'][0]['required'] = False
        self.assertTrue(subsonic.can_direct_play(track, client)[0])

    def test_a_known_bitrate_can_satisfy_or_fail_a_required_limitation(self):
        import subsonic
        track = {'id': 1, 'file': '/music/a.mp3', 'duration': 60, 'size': 960000}  # 128 kbps
        client = {'directPlayProfiles': [{'containers': ['mp3'], 'audioCodecs': ['mp3'], 'protocols': []}],
                  'codecProfiles': [{'name': 'mp3', 'limitations': [
                      {'name': 'audioBitrate', 'comparison': 'LessThanEqual',
                       'values': ['96000'], 'required': True}]}]}
        self.assertFalse(subsonic.can_direct_play(track, client)[0])
        client['codecProfiles'][0]['limitations'][0]['values'] = ['192000']
        self.assertTrue(subsonic.can_direct_play(track, client)[0])

    def test_a_profile_this_server_cannot_encode_is_refused_with_a_reason(self):
        import subsonic
        decision = subsonic.transcode_decision(
            {'id': 7, 'file': '/music/a.opus', 'duration': 60, 'size': 960000},
            {'directPlayProfiles': [{'containers': ['mp3'], 'audioCodecs': ['mp3'], 'protocols': []}],
             'transcodingProfiles': [{'container': 'asf', 'audioCodec': 'wmav2',
                                      'protocol': 'http'}]},
            'u1')['transcodeDecision']
        self.assertFalse(decision['canDirectPlay'])
        self.assertFalse(decision['canTranscode'])
        self.assertIn('errorReason', decision)
        self.assertNotIn('transcodeParams', decision)

    def test_a_client_with_no_transcoding_list_gets_the_baseline(self):
        import subsonic
        decision = subsonic.transcode_decision(
            {'id': 7, 'file': '/music/a.opus', 'duration': 60, 'size': 960000},
            {'directPlayProfiles': [{'containers': ['mp3'], 'audioCodecs': ['mp3'], 'protocols': []}]},
            'u1')['transcodeDecision']
        self.assertFalse(decision['canDirectPlay'])
        self.assertTrue(decision['canTranscode'])
        self.assertTrue(decision['transcodeParams'])

    def test_the_target_is_what_the_client_named_and_a_ceiling_caps_it(self):
        import subsonic
        self.assertEqual(subsonic.transcode_target(
            {'transcodingProfiles': [{'container': 'ogg', 'audioCodec': 'opus'}]})['codec'], 'opus')
        self.assertEqual(subsonic.transcode_target({'maxAudioBitrate': 96000})['bitrate'], 96)
        self.assertIsNone(subsonic.transcode_target(None, 'wma'))


if __name__ == '__main__':
    unittest.main()