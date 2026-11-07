import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import lyrics
import test_security as harness


class Parser(unittest.TestCase):
    def test_timestamps_offsets_repeat_and_malformed(self):
        lines = lyrics.parse_lrc('[offset:-500]\n[00:01.50][01:02.125]hello\n[99:99]bad\n[00:03]next\n[ar:Artist]')
        self.assertEqual(lines, [{'time':2.0,'text':'hello'}, {'time':3.5,'text':'next'}, {'time':62.625,'text':'hello'}])
        self.assertEqual(lyrics.parse_lrc('[offset:1000]\n[00:02]a')[0]['time'], 1)
        self.assertEqual(lyrics.parse_lrc('broken'), [])

    def test_provider_validation(self):
        track = {'title':'Song', 'artist_name':'Artist', 'album':'Album','duration':100}
        data = {'trackName':'Song','artistName':'Artist','albumName':'Album','duration':101,'syncedLyrics':'[00:01]Hi'}
        def response(value):
            return io.BytesIO(json.dumps(value).encode())
        with patch('lyrics.urlopen', return_value=response(data)) as request:
            self.assertEqual(lyrics.lookup(track)['status'], 'synced')
            url = request.call_args[0][0].full_url
            self.assertIn('artist_name=Artist', url)
            self.assertNotIn('source_url', url)
        for field, value in [('trackName','Other'),('artistName','Other'),('albumName','Other'),('duration',110)]:
            with patch('lyrics.urlopen', return_value=response(dict(data, **{field:value}))):
                self.assertEqual(lyrics.lookup(track)['status'], 'not_found')
        for invalid in [[], None, {'error':'broken'}]:
            with patch('lyrics.urlopen', return_value=response(invalid)):
                self.assertEqual(lyrics.lookup(track)['status'], 'not_found')
        with patch('lyrics.urlopen', return_value=io.BytesIO(b'not JSON')):
            self.assertEqual(lyrics.lookup(track)['status'], 'unavailable')
        self.assertEqual(lyrics.payload({'plainLyrics':'Hi'},'test')['status'], 'plain')
        self.assertEqual(lyrics.payload({'instrumental':True},'test')['status'], 'instrumental')


class LyricsHTTP(unittest.TestCase):
    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request
    token = harness.SecurityHTTP.token
    user = harness.SecurityHTTP.user

    def setUp(self):
        harness.SecurityHTTP.setUp(self)
        self.root = Path(self.tmp.name)/'music'
        self.root.mkdir()
        self.store.set_settings({'root':str(self.root)})
        self.track_id = self.store.track('local','lyrics','Song', artist_name='Artist', album='Album')
        self.identity = self.user('member')
        self.headers = {'Authorization':'Bearer fixture'}

    def test_authenticated_cache_and_missing(self):
        route = f'/api/track/{self.track_id}/lyrics'
        self.assertEqual(self.request(route)[0],401)
        value = lyrics.payload({'syncedLyrics':'[00:01]Hello'},'test')
        with patch('lyrics.lookup', return_value=value) as lookup:
            for _ in range(2):
                code, _, raw = self.request(route, headers=self.headers)
                self.assertEqual(code,200)
                self.assertEqual(json.loads(raw),value)
                self.assertNotIn(b'source_url',raw)
            self.assertEqual(lookup.call_count,1)
        self.assertEqual(self.request('/api/track/999999/lyrics',headers=self.headers)[0],404)
        self.assertEqual(self.request(route,'POST',dict(self.headers,Origin='https://music.example'),'{}')[0],405)

    def test_sidecar_and_escape(self):
        audio = self.root/'song.mp3'
        audio.write_bytes(b'fixture, never played')
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?',(str(audio),self.track_id))
            self.store.conn.commit()
        sidecar = audio.with_suffix('.lrc')
        sidecar.write_text('[00:01]Local')
        with patch('lyrics.lookup') as lookup:
            self.assertEqual(lyrics.get(self.store,self.track_id)['source'],'Local LRC')
            lookup.assert_not_called()
        sidecar.unlink()
        outside = Path(self.tmp.name)/'outside.lrc'
        outside.write_text('[00:01]Outside')
        sidecar.symlink_to(outside)
        with patch('lyrics.lookup', return_value=lyrics.payload({},'test')) as lookup:
            self.assertEqual(lyrics.get(self.store,self.track_id)['status'],'not_found')
            lookup.assert_called_once()


if __name__ == '__main__':
    unittest.main()
