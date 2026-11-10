"""Phase 3 (internet radio) and Phase 4 (podcasts) of the Subsonic surface.

Same isolated HTTP fixture as the earlier Subsonic tests: real requests, a temporary
database, no live boot. Podcast feeds come from a loopback stub server, so no test
touches the internet.
"""
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_listening import ListeningHTTP

FEED = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Phase Thirty Four</title>
    <description>A stub feed for the podcast tests.</description>
    <itunes:image href="https://images.example/cover.jpg"/>
    <item>
      <title>Older episode</title>
      <guid isPermaLink="false">ep-older</guid>
      <pubDate>Mon, 09 Nov 2026 10:00:00 GMT</pubDate>
      <description>The first one.</description>
      <itunes:duration>1:02:03</itunes:duration>
      <enclosure url="https://audio.example/older.mp3" length="12345" type="audio/mpeg"/>
    </item>
    <item>
      <title>Newer episode</title>
      <guid isPermaLink="false">ep-newer</guid>
      <pubDate>Tue, 10 Nov 2026 11:00:00 GMT</pubDate>
      <description>The second one.</description>
      <itunes:duration>1800</itunes:duration>
      <enclosure url="https://audio.example/newer.mp3" length="67890" type="audio/mpeg"/>
    </item>
  </channel>
</rss>
'''

HUGE = b'<?xml version="1.0"?><rss><channel><title>Huge</title><item><title>x</title>' + b' ' * (3 * 1024 * 1024) + b'</item></channel></rss>'


class FeedHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = getattr(self.server, 'feed_body', FEED)
        if self.path.startswith('/missing'):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/rss+xml')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RadioAndPodcasts(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    request = ListeningHTTP.request
    token = ListeningHTTP.token
    user = ListeningHTTP.user
    api = ListeningHTTP.api
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save

    def setUp(self):
        ListeningHTTP.setUp(self)
        self._local_prev = os.environ.get('HARDLYLISTENING_ALLOW_LOCAL_FEEDS')
        os.environ['HARDLYLISTENING_ALLOW_LOCAL_FEEDS'] = '1'
        self.feed = ThreadingHTTPServer(('127.0.0.1', 0), FeedHandler)
        self.feed.feed_body = FEED
        self.feed_thread = threading.Thread(target=self.feed.serve_forever)
        self.feed_thread.start()
        self.feed_url = 'http://127.0.0.1:%d/feed.xml' % self.feed.server_port

    def tearDown(self):
        self.feed.shutdown()
        self.feed_thread.join()
        self.feed.server_close()
        if self._local_prev is None:
            os.environ.pop('HARDLYLISTENING_ALLOW_LOCAL_FEEDS', None)
        else:
            os.environ['HARDLYLISTENING_ALLOW_LOCAL_FEEDS'] = self._local_prev
        ListeningHTTP.tearDown(self)

    def add_member(self, member_id='u2', email='other@example.com'):
        self.store.conn.execute(
            "INSERT INTO members(id,email,role,display_name) VALUES(?,?,'member','Other')",
            (member_id, email))
        self.store.conn.commit()
        self.identity = {'id': member_id, 'email': email}
        self.device = self.api('devices', {'label': 'Other phone'})[1]
        return self.device

    def stations(self):
        return self.response('getInternetRadioStations')[
            'internetRadioStations']['internetRadioStation']

    def channels(self):
        return self.response('getPodcasts')['podcasts']['channel']

    # -- radio --------------------------------------------------------------
    def test_radio_crud_round_trip(self):
        made = self.response('createInternetRadioStation', name='Jazz FM',
                             streamUrl='https://stream.example/jazz',
                             homepageUrl='https://jazz.example')
        self.assertEqual(made['status'], 'ok')
        rows = self.stations()
        self.assertEqual(len(rows), 1)
        station = rows[0]
        self.assertTrue(station['id'].isdigit())
        self.assertEqual(station['name'], 'Jazz FM')
        self.assertEqual(station['streamUrl'], 'https://stream.example/jazz')
        self.assertEqual(station['homepageUrl'], 'https://jazz.example')

        updated = self.response('updateInternetRadioStation', id=station['id'],
                                name='Jazz FM 88.1', streamUrl='http://stream.example/live',
                                homepageUrl='https://jazz.example/about')
        self.assertEqual(updated['status'], 'ok')
        again = self.stations()[0]
        self.assertEqual((again['name'], again['streamUrl'], again['homepageUrl']),
                         ('Jazz FM 88.1', 'http://stream.example/live',
                          'https://jazz.example/about'))

        self.assertEqual(self.response('deleteInternetRadioStation', id=station['id'])['status'], 'ok')
        self.assertEqual(self.stations(), [])

    def test_radio_rejects_non_http_urls_and_oversized_fields(self):
        cases = ['ftp://stream.example/x', 'javascript:alert(1)', '/relative/stream',
                 'http://', 'https://user:pass@stream.example/x',
                 'https://stream.example/' + 'a' * 3000]
        for index, stream_url in enumerate(cases):
            reply = self.response('createInternetRadioStation', name='Bad %d' % index,
                                  streamUrl=stream_url)
            self.assertEqual(reply['status'], 'failed', stream_url)
            self.assertEqual(reply['error']['code'], 10, stream_url)
        self.assertEqual(self.response('createInternetRadioStation',
                                       streamUrl='https://stream.example/x')['error']['code'], 10)
        self.assertEqual(self.response('createInternetRadioStation', name='x' * 201,
                                       streamUrl='https://stream.example/x')['error']['code'], 10)
        self.assertEqual(self.response('updateInternetRadioStation', id='999',
                                       name='x', streamUrl='https://stream.example/x'
                                       )['error']['code'], 70)
        self.assertEqual(self.stations(), [])

    def test_radio_is_member_scoped(self):
        self.response('createInternetRadioStation', name='Mine',
                      streamUrl='https://stream.example/mine')
        station_id = self.stations()[0]['id']
        second = self.add_member()
        self.assertEqual(self.stations(), [])
        self.assertEqual(self.response('updateInternetRadioStation', id=station_id, name='Stolen',
                                       streamUrl='https://stream.example/stolen',
                                       )['error']['code'], 70)
        self.assertEqual(self.response('deleteInternetRadioStation',
                                       id=station_id)['error']['code'], 70)
        self.assertEqual(self.store.conn.execute(
            'SELECT count(*) FROM listening_radio_stations').fetchone()[0], 1)
        # A fresh device for the owner still sees the station.
        self.identity = {'id': 'u1', 'email': 'friend@example.com'}
        owner = self.api('devices', {'label': 'Second owner phone'})[1]
        self.assertEqual(len(self.response('getInternetRadioStations',
                                           device=owner)['internetRadioStations']
                             ['internetRadioStation']), 1)

    # -- podcasts -----------------------------------------------------------
    def test_podcast_subscribe_refresh_list_play_and_delete(self):
        self.assertEqual(self.response('createPodcastChannel', url=self.feed_url)['status'], 'ok')
        channel = self.channels()[0]
        self.assertEqual(channel['title'], '127.0.0.1')      # before the first fetch
        self.assertEqual(channel['episode'], [])
        self.assertEqual(channel['url'], self.feed_url)

        self.assertEqual(self.response('refreshPodcasts')['status'], 'ok')
        channel = self.channels()[0]
        self.assertEqual(channel['title'], 'Phase Thirty Four')
        self.assertEqual(channel['description'], 'A stub feed for the podcast tests.')
        self.assertEqual(channel['status'], 'completed')
        episodes = channel['episode']
        self.assertEqual([e['title'] for e in episodes], ['Newer episode', 'Older episode'])
        newer = episodes[0]
        self.assertEqual(newer['channelId'], channel['id'])
        self.assertEqual(newer['duration'], 1800)
        self.assertEqual(newer['streamUrl'], 'https://audio.example/newer.mp3')
        self.assertEqual(newer['size'], 67890)
        self.assertEqual(newer['status'], 'new')
        self.assertEqual(newer['publishDate'], '2026-11-10T11:00:00.000Z')
        self.assertEqual(episodes[1]['publishDate'], '2026-11-09T10:00:00.000Z')
        self.assertEqual(episodes[1]['duration'], 3723)

        newest = self.response('getNewestPodcasts', count=1)['newestPodcasts']['episode']
        self.assertEqual([e['title'] for e in newest], ['Newer episode'])
        one = self.response('getPodcastEpisode', id=newer['id'])['podcastEpisode']
        self.assertEqual(one, newer)
        self.assertEqual(self.response('getPodcasts', includeEpisodes='false')
                         ['podcasts']['channel'][0].get('episode'), None)

        self.assertEqual(self.response('deletePodcastEpisode', id=newer['id'])['status'], 'ok')
        self.assertEqual(self.response('getPodcastEpisode', id=newer['id'])['error']['code'], 70)
        self.assertEqual([e['title'] for e in self.channels()[0]['episode']], ['Older episode'])

        self.assertEqual(self.response('deletePodcastChannel', id=channel['id'])['status'], 'ok')
        self.assertEqual(self.channels(), [])
        self.assertEqual(self.store.conn.execute(
            'SELECT count(*) FROM listening_podcast_episodes').fetchone()[0], 0)

    def test_podcast_rejects_bad_urls_and_is_member_scoped(self):
        for bad in ('', 'ftp://feeds.example/x', 'file:///etc/passwd', 'not a url',
                    'https://user:pass@feeds.example/x'):
            reply = self.response('createPodcastChannel', url=bad)
            self.assertEqual(reply['status'], 'failed', bad)
            self.assertEqual(reply['error']['code'], 10, bad)

        self.response('createPodcastChannel', url=self.feed_url)
        self.response('refreshPodcasts')
        channel = self.channels()[0]
        episode_id = channel['episode'][0]['id']

        self.add_member()
        self.assertEqual(self.channels(), [])
        self.assertEqual(self.response('getPodcastEpisode', id=episode_id)['error']['code'], 70)
        self.assertEqual(self.response('refreshPodcasts', id=channel['id'])['error']['code'], 70)
        self.assertEqual(self.response('deletePodcastChannel',
                                       id=channel['id'])['error']['code'], 70)
        self.assertEqual(self.response('deletePodcastEpisode',
                                       id=episode_id)['error']['code'], 70)
        self.assertEqual(self.store.conn.execute(
            'SELECT count(*) FROM listening_podcast_channels').fetchone()[0], 1)
        self.assertEqual(self.store.conn.execute(
            'SELECT count(*) FROM listening_podcast_episodes').fetchone()[0], 2)

    def test_refresh_caps_feed_size_and_surfaces_the_error(self):
        self.feed.feed_body = HUGE
        self.response('createPodcastChannel', url=self.feed_url)
        self.assertEqual(self.response('refreshPodcasts')['status'], 'ok')
        channel = self.channels()[0]
        self.assertEqual(channel['status'], 'error')
        self.assertEqual(channel['errorMessage'], 'Feed is too large')
        self.assertEqual(channel['episode'], [])
        # Recovery: a later good fetch clears the error.
        self.feed.feed_body = FEED
        self.response('refreshPodcasts')
        channel = self.channels()[0]
        self.assertEqual((channel['status'], channel['errorMessage']), ('completed', ''))
        self.assertEqual(len(channel['episode']), 2)

    def test_podcast_download_still_answers_error_30(self):
        self.response('createPodcastChannel', url=self.feed_url)
        self.response('refreshPodcasts')
        episode_id = self.channels()[0]['episode'][0]['id']
        reply = self.response('downloadPodcastEpisode', id=episode_id)
        self.assertEqual(reply['status'], 'failed')
        self.assertEqual(reply['error']['code'], 30)
        self.assertIn('does not implement', reply['error']['message'])
        for endpoint in ('jukeboxControl', 'getSonicSimilarTracks', 'hls', 'addChatMessage'):
            self.assertEqual(self.response(endpoint)['error']['code'], 30, endpoint)


if __name__ == '__main__':
    unittest.main()
