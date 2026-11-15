"""Podcast downloads under a per-member cap, with no automatic pruning (HL-209).

`downloadPodcastEpisode` used to answer error 30 because there was no storage policy. The
policy is now this: five gigabytes per member, counted over the files this server actually
holds for them; at the cap the download is refused with the numbers, and the only way space
comes back is a member deleting a downloaded episode. Nothing is ever deleted on someone's
behalf to make room for something else.

Everything here runs against a loopback audio stub with the cap patched to a kilobyte, so
the refusal path is exercised in milliseconds and no real library or disk is touched.
"""
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import podcasts
from test_listening import ListeningHTTP

FEED = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Download Fixture</title>
    <description>Two episodes and a link that is not audio.</description>
    <item>
      <title>First episode</title>
      <guid isPermaLink="false">ep-one</guid>
      <pubDate>Tue, 10 Nov 2026 11:00:00 GMT</pubDate>
      <itunes:duration>600</itunes:duration>
      <enclosure url="%s/audio-one.mp3" length="600" type="audio/mpeg"/>
    </item>
    <item>
      <title>Second episode</title>
      <guid isPermaLink="false">ep-two</guid>
      <pubDate>Wed, 11 Nov 2026 11:00:00 GMT</pubDate>
      <itunes:duration>600</itunes:duration>
      <enclosure url="%s/audio-two.mp3" length="600" type="audio/mpeg"/>
    </item>
    <item>
      <title>Not audio</title>
      <guid isPermaLink="false">ep-three</guid>
      <pubDate>Thu, 12 Nov 2026 11:00:00 GMT</pubDate>
      <enclosure url="%s/not-audio" length="20" type="text/html"/>
    </item>
  </channel>
</rss>
'''

AUDIO = b'\xff\xfb' + b'\x00' * 598          # 600 bytes, enough to be a file
HITS = {}


class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        HITS[self.path] = HITS.get(self.path, 0) + 1
        if self.path.startswith('/feed'):
            body, kind = self.server.feed_body, 'application/rss+xml'
        elif self.path.startswith('/audio'):
            body, kind = AUDIO, 'audio/mpeg'
        elif self.path.startswith('/not-audio'):
            body, kind = b'<html>nope</html>', 'text/html'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class PodcastDownloads(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    token = ListeningHTTP.token
    user = ListeningHTTP.user
    api = ListeningHTTP.api
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save

    def setUp(self):
        ListeningHTTP.setUp(self)
        HITS.clear()
        self._local_prev = os.environ.get('HARDLYLISTENING_ALLOW_LOCAL_FEEDS')
        os.environ['HARDLYLISTENING_ALLOW_LOCAL_FEEDS'] = '1'
        self.feed = ThreadingHTTPServer(('127.0.0.1', 0), StubHandler)
        port = self.feed.server_port
        base = 'http://127.0.0.1:%d' % port
        self.feed.feed_body = (FEED % (base, base, base)).encode()
        self.feed_thread = threading.Thread(target=self.feed.serve_forever)
        self.feed_thread.start()
        self.feed_url = 'http://127.0.0.1:%d/feed.xml' % port

        # A kilobyte, not five gigabytes: the refusal path has to be cheap to exercise.
        patcher = mock.patch.object(podcasts, 'CAP_BYTES', 1000)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Never write into the checkout: a test's files belong in its own temp directory.
        directory = mock.patch.object(podcasts.paths, 'PODCAST_DIR',
                                      Path(self.tmp.name) / 'podcasts')
        directory.start()
        self.addCleanup(directory.stop)

    def tearDown(self):
        self.feed.shutdown()
        self.feed_thread.join()
        self.feed.server_close()
        if self._local_prev is None:
            os.environ.pop('HARDLYLISTENING_ALLOW_LOCAL_FEEDS', None)
        else:
            os.environ['HARDLYLISTENING_ALLOW_LOCAL_FEEDS'] = self._local_prev
        ListeningHTTP.tearDown(self)

    # -- helpers ------------------------------------------------------------
    def subscribe(self):
        self.assertEqual(self.response('createPodcastChannel', url=self.feed_url)['status'], 'ok')
        self.assertEqual(self.response('refreshPodcasts')['status'], 'ok')
        episodes = self.response('getPodcasts')['podcasts']['channel'][0]['episode']
        return {row['title']: row for row in episodes}

    def download(self, episode_id):
        return self.response('downloadPodcastEpisode', id=episode_id)

    def storage(self):
        return self.response('getPodcasts')['podcasts']['storage']

    def member(self, member_id='u2', email='other@example.com'):
        self.store.conn.execute(
            "INSERT INTO members(id,email,role,display_name) VALUES(?,?,'member','Other')",
            (member_id, email))
        self.store.conn.commit()
        self.identity = {'id': member_id, 'email': email}
        return self.api('devices', {'label': 'Other phone'})[1]

    # -- the happy path -----------------------------------------------------
    def test_a_downloaded_episode_is_a_file_this_member_holds(self):
        episodes = self.subscribe()
        first = episodes['First episode']
        self.assertFalse(first['downloaded'])
        self.assertEqual(first['status'], 'new')

        made = self.download(first['id'])['podcastEpisode']
        self.assertTrue(made['downloaded'])
        self.assertEqual(made['bytes'], 600)
        self.assertEqual(made['status'], 'completed')
        # `streamUrl` stays the origin: the client's own idea of the episode does not change
        # because this server happens to hold a copy.
        self.assertTrue(made['streamUrl'].startswith('http://127.0.0.1'))
        row = self.store.conn.execute(
            'SELECT file, bytes FROM listening_podcast_episodes WHERE id=?',
            (int(first['id']),)).fetchone()
        self.assertTrue(Path(row['file']).is_file())
        self.assertEqual(Path(row['file']).read_bytes(), AUDIO)
        self.assertEqual(row['bytes'], 600)

    def test_downloading_twice_does_not_fetch_twice(self):
        first = self.subscribe()['First episode']
        self.assertEqual(self.download(first['id'])['podcastEpisode']['bytes'], 600)
        before = HITS.get('/audio-one.mp3', 0)
        again = self.download(first['id'])['podcastEpisode']
        self.assertEqual(again['bytes'], 600)
        self.assertEqual(HITS.get('/audio-one.mp3', 0), before)

    def test_the_listing_reports_the_budget_and_the_files(self):
        episodes = self.subscribe()
        empty = self.storage()
        self.assertEqual((empty['usedBytes'], empty['capBytes']), (0, 1000))
        self.download(episodes['First episode']['id'])
        used = self.storage()
        self.assertEqual(used['usedBytes'], 600)
        self.assertEqual([row['bytes'] for row in used['episodes']], [600])
        self.assertEqual(used['episodes'][0]['channel'], 'Download Fixture')

    # -- the cap ------------------------------------------------------------
    def test_a_full_budget_refuses_instead_of_deleting_someone_elses_file(self):
        episodes = self.subscribe()
        self.download(episodes['First episode']['id'])
        refused = self.download(episodes['Second episode']['id'])
        self.assertEqual(refused['status'], 'failed')
        self.assertEqual(refused['error']['code'], 10)
        self.assertIn('delete a downloaded episode', refused['error']['message'].lower())
        # Nothing was pruned to make room, and the refusal did not half-write a file.
        self.assertEqual(self.storage()['usedBytes'], 600)
        files = [p for p in (Path(self.tmp.name) / 'podcasts').rglob('*') if p.is_file()]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), AUDIO)
        self.assertFalse(any(p.name.endswith('.part') for p in files))

    def test_a_link_that_is_not_audio_is_refused_and_leaves_nothing_behind(self):
        episodes = self.subscribe()
        refused = self.download(episodes['Not audio']['id'])
        self.assertEqual(refused['status'], 'failed')
        self.assertEqual(self.storage()['usedBytes'], 0)
        leftovers = [p for p in (Path(self.tmp.name) / 'podcasts').rglob('*') if p.is_file()]
        self.assertEqual(leftovers, [])

    def test_deleting_the_episode_frees_the_bytes(self):
        episodes = self.subscribe()
        made = self.download(episodes['First episode']['id'])['podcastEpisode']
        path = self.store.conn.execute(
            'SELECT file FROM listening_podcast_episodes WHERE id=?',
            (int(made['id']),)).fetchone()['file']
        self.assertTrue(Path(path).is_file())
        self.assertEqual(self.response('deletePodcastEpisode', id=made['id'])['status'], 'ok')
        self.assertFalse(Path(path).exists())
        self.assertEqual(self.storage()['usedBytes'], 0)
        # The episode returns on the next refresh, undownloaded, and can be kept again.
        self.response('refreshPodcasts')
        again = {row['title']: row for row in
                 self.response('getPodcasts')['podcasts']['channel'][0]['episode']}
        self.assertFalse(again['First episode']['downloaded'])
        self.assertEqual(self.download(again['First episode']['id'])
                         ['podcastEpisode']['bytes'], 600)

    # -- streaming what is held --------------------------------------------
    def test_the_held_file_streams_with_range_support(self):
        made = self.download(self.subscribe()['First episode']['id'])['podcastEpisode']
        query = self.rest('streamPodcastEpisode', id=made['id'])
        self.assertEqual(query[0], 200)
        self.assertEqual(query[2], AUDIO)
        self.assertTrue(query[1].get('Content-Type', '').startswith('audio/mpeg'))
        args = dict(u=self.device['username'], p=self.device['password'], f='json',
                    v='1.16.1', c='fixture', id=made['id'])
        from urllib.parse import urlencode
        code, headers, raw = self.request(
            '/rest/streamPodcastEpisode.view?' + urlencode(args),
            headers={'Range': 'bytes=0-9'})
        self.assertEqual(code, 206)
        self.assertEqual(len(raw), 10)

    def test_an_episode_that_is_not_held_cannot_be_streamed(self):
        first = self.subscribe()['First episode']
        query = self.rest('streamPodcastEpisode', id=first['id'])
        self.assertEqual(query[0], 200)               # Subsonic answers 200 with an error body
        self.assertEqual(self.response('streamPodcastEpisode', id=first['id'])
                         ['error']['code'], 70)

    # -- ownership ----------------------------------------------------------
    def test_another_member_cannot_download_or_stream_your_episode(self):
        made = self.download(self.subscribe()['First episode']['id'])['podcastEpisode']
        device = self.member()
        self.assertNotEqual(device['username'], '')
        self.assertEqual(self.response('downloadPodcastEpisode', id=made['id'],
                                       device=device)['error']['code'], 70)
        self.assertEqual(self.response('streamPodcastEpisode', id=made['id'],
                                       device=device)['error']['code'], 70)
        # Their own budget is untouched by what the first member holds.
        self.assertEqual(self.response('getPodcasts', device=device)
                         ['podcasts']['storage']['usedBytes'], 0)


if __name__ == '__main__':
    unittest.main()