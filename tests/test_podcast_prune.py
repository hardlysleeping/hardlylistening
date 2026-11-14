"""Podcast retention: a policy that frees room, and a record of what it took (HL-234).

The cap from HL-209 is only half a storage policy. It refuses a download when the member is
full and tells them to delete something by hand, so a podcast that publishes weekly can fill a
budget and then be stuck. This card adds the other half: **per channel**, keep the newest N
downloads and/or anything younger than N days, and purge the rest — with every removal written
down, and nothing deleted unless the channel actually has a policy.

The rules these tests pin:

* a channel with no policy is untouched, and the HL-209 refusal still happens (the default
  behaviour does not change under existing data);
* the policy counts the episode being downloaded now, so a full budget frees exactly the room
  a new episode needs;
* an episode older than the window goes even when the count would keep it;
* every removal is one row in the audit table and appears in the Subsonic `storage.pruned`
  report, with the bytes it freed;
* a manual delete and the policy serialise on the same member lock, so a file is never claimed
  by both (and a row already gone is never logged as a policy removal);
* the policy is set at subscribe time and reported back on the channel.

Everything runs against a loopback audio stub with a one-kilobyte cap, so nothing here touches
a real library or a real budget.
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
    <title>Retention Fixture</title>
    <description>Three dated episodes.</description>
    <item>
      <title>Oldest episode</title>
      <guid isPermaLink="false">ep-old</guid>
      <pubDate>Tue, 01 Sep 2026 11:00:00 GMT</pubDate>
      <itunes:duration>600</itunes:duration>
      <enclosure url="%s/audio-old.mp3" length="600" type="audio/mpeg"/>
    </item>
    <item>
      <title>Middle episode</title>
      <guid isPermaLink="false">ep-mid</guid>
      <pubDate>Tue, 10 Nov 2026 11:00:00 GMT</pubDate>
      <itunes:duration>600</itunes:duration>
      <enclosure url="%s/audio-mid.mp3" length="600" type="audio/mpeg"/>
    </item>
    <item>
      <title>Newest episode</title>
      <guid isPermaLink="false">ep-new</guid>
      <pubDate>Wed, 11 Nov 2026 11:00:00 GMT</pubDate>
      <itunes:duration>600</itunes:duration>
      <enclosure url="%s/audio-new.mp3" length="600" type="audio/mpeg"/>
    </item>
  </channel>
</rss>
'''

AUDIO = b'\xff\xfb' + b'\x00' * 598          # 600 bytes, enough to be a file


class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/feed'):
            body, kind = self.server.feed_body, 'application/rss+xml'
        elif self.path.startswith('/audio'):
            body, kind = AUDIO, 'audio/mpeg'
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


class PodcastPrune(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    api = ListeningHTTP.api
    token = ListeningHTTP.token
    user = ListeningHTTP.user

    def setUp(self):
        ListeningHTTP.setUp(self)
        self.uid = self.identity['id']
        self._local_prev = os.environ.get('HARDLYLISTENING_ALLOW_LOCAL_FEEDS')
        os.environ['HARDLYLISTENING_ALLOW_LOCAL_FEEDS'] = '1'
        self.feed = ThreadingHTTPServer(('127.0.0.1', 0), StubHandler)
        port = self.feed.server_port
        base = 'http://127.0.0.1:%d' % port
        self.feed.feed_body = (FEED % (base, base, base)).encode()
        self.feed_thread = threading.Thread(target=self.feed.serve_forever)
        self.feed_thread.start()
        self.feed_url = base + '/feed.xml'

        for attribute, value in (('CAP_BYTES', 10000),
                                 ('DEFAULT_KEEP_NEWEST', 0),
                                 ('DEFAULT_KEEP_DAYS', 0)):
            patcher = mock.patch.object(podcasts, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
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
    def subscribe(self, **params):
        self.assertEqual(self.response('createPodcastChannel', url=self.feed_url,
                                       **params)['status'], 'ok')
        self.assertEqual(self.response('refreshPodcasts')['status'], 'ok')
        channel = self.response('getPodcasts')['podcasts']['channel'][0]
        return channel['id'], {row['title']: row for row in channel['episode']}

    def download(self, episode_id, *, expect='ok'):
        answer = self.response('downloadPodcastEpisode', id=episode_id)
        self.assertEqual(answer['status'], expect, answer)
        return answer

    def storage(self):
        return self.response('getPodcasts')['podcasts']['storage']

    def files(self):
        return [path for path in (Path(self.tmp.name) / 'podcasts').rglob('*') if path.is_file()]

    def cap(self, size):
        patcher = mock.patch.object(podcasts, 'CAP_BYTES', size)
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_policy(self, channel_id, keep_newest=0, keep_days=0):
        return podcasts.set_retention(self.store, self.uid, str(channel_id),
                                      keep_newest=keep_newest, keep_days=keep_days)

    def age(self, episode_id, days):
        """Pretend this download happened `days` ago."""
        with self.store.lock:
            self.store.conn.execute(
                'UPDATE listening_podcast_episodes SET downloaded=? WHERE id=?',
                (__import__('time').time() - days * 86400, int(episode_id)))
            self.store.conn.commit()

    # -- the policy ---------------------------------------------------------
    def test_a_channel_keeps_only_its_newest_downloads(self):
        channel_id, episodes = self.subscribe()
        for title in ('Oldest episode', 'Middle episode', 'Newest episode'):
            self.assertEqual(self.download(episodes[title]['id'])['podcastEpisode']['bytes'], 600)
        self.assertEqual(len(self.files()), 3)

        self.set_policy(channel_id, keep_newest=1)
        report = podcasts.prune_member(self.store, self.uid)
        self.assertEqual(sorted(row['title'] for row in report['removed']),
                         ['Middle episode', 'Oldest episode'])
        self.assertEqual(report['bytes'], 1200)
        # The newest download survives; the others keep their episode rows, unheld, so they
        # can be downloaded again rather than vanishing from the client's view.
        listing = {row['title']: row for row in self.response('getPodcasts')
                   ['podcasts']['channel'][0]['episode']}
        self.assertTrue(listing['Newest episode']['downloaded'])
        self.assertFalse(listing['Middle episode']['downloaded'])
        self.assertEqual(listing['Middle episode']['status'], 'new')
        self.assertEqual(len(self.files()), 1)

    def test_the_policy_frees_exactly_the_room_a_new_episode_needs(self):
        self.cap(1000)
        channel_id, episodes = self.subscribe()
        self.set_policy(channel_id, keep_newest=1)
        self.download(episodes['Oldest episode']['id'])
        self.assertEqual(self.storage()['usedBytes'], 600)
        # The budget (1000) cannot hold a second 600-byte file. The policy keeps the newest
        # one *counting the episode being downloaded*, so the older file goes and this fits.
        kept = self.download(episodes['Newest episode']['id'])
        self.assertTrue(kept['podcastEpisode']['downloaded'])
        self.assertEqual(self.storage()['usedBytes'], 600)
        self.assertEqual([path.name for path in self.files()], ['%s.mp3' % episodes['Newest episode']['id']])

    def test_an_age_window_expires_an_old_download(self):
        channel_id, episodes = self.subscribe()
        self.download(episodes['Oldest episode']['id'])
        self.download(episodes['Newest episode']['id'])
        self.age(episodes['Oldest episode']['id'], days=30)
        self.set_policy(channel_id, keep_newest=10, keep_days=7)
        report = podcasts.prune_member(self.store, self.uid)
        self.assertEqual([row['title'] for row in report['removed']], ['Oldest episode'])
        self.assertEqual([row['reason'] for row in report['removed']], ['age'])
        self.assertEqual(len(self.files()), 1)

    def test_every_removal_is_logged_and_reported(self):
        channel_id, episodes = self.subscribe()
        self.download(episodes['Middle episode']['id'])
        self.set_policy(channel_id, keep_newest=0, keep_days=1)
        self.age(episodes['Middle episode']['id'], days=30)
        podcasts.prune_member(self.store, self.uid)

        logged = podcasts.prune_log(self.store, self.uid)
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]['title'], 'Middle episode')
        self.assertEqual(logged[0]['bytes'], 600)
        self.assertEqual(logged[0]['reason'], 'age')
        self.assertEqual(podcasts.prune_log(self.store, 'someone-else'), [])

        report = self.storage()['pruned']
        self.assertEqual([row['title'] for row in report], ['Middle episode'])
        self.assertEqual(report[0]['bytes'], 600)

    def test_keep_everything_is_still_the_default(self):
        self.cap(1000)
        _, episodes = self.subscribe()
        self.download(episodes['Middle episode']['id'])
        # No policy was set (the fixture's env defaults are 0), so the old refusal stands and
        # nothing is deleted on the member's behalf.
        refused = self.download(episodes['Newest episode']['id'], expect='failed')
        self.assertEqual(refused['error']['code'], 10)
        self.assertIn('delete a downloaded episode', refused['error']['message'].lower())
        self.assertEqual(podcasts.prune_member(self.store, self.uid)['removed'], [])
        self.assertEqual(len(self.files()), 1)

    def test_a_full_budget_with_a_policy_says_what_it_could_not_free(self):
        self.cap(600)
        channel_id, episodes = self.subscribe()
        self.set_policy(channel_id, keep_newest=1)
        self.download(episodes['Newest episode']['id'])
        # Keep-newest 1 already holds the newest file, so a second one cannot fit; the refusal
        # has to admit the policy ran rather than pretend nothing was tried.
        refused = self.download(episodes['Middle episode']['id'], expect='failed')
        self.assertEqual(refused['error']['code'], 10)
        self.assertIn('policy', refused['error']['message'].lower())
        self.assertEqual(len(self.files()), 1)

    # -- never two claims on one file ---------------------------------------
    def test_a_manual_delete_is_never_claimed_by_the_policy(self):
        channel_id, episodes = self.subscribe()
        middle = episodes['Middle episode']['id']
        self.download(middle)
        self.set_policy(channel_id, keep_newest=0, keep_days=1)
        self.age(middle, days=30)
        self.assertEqual(self.response('deletePodcastEpisode', id=middle)['status'], 'ok')
        report = podcasts.prune_member(self.store, self.uid)
        self.assertEqual(report['removed'], [])
        self.assertEqual(podcasts.prune_log(self.store, self.uid), [])
        self.assertEqual(self.files(), [])

    def test_a_file_already_taken_is_not_written_off_twice(self):
        channel_id, episodes = self.subscribe()
        middle = episodes['Middle episode']['id']
        self.download(middle)
        self.set_policy(channel_id, keep_newest=0, keep_days=1)
        self.age(middle, days=30)
        # The interleaving that matters: the policy has read the rows, and the member's own
        # delete lands before the unlink. The file is gone, so the policy must take nothing,
        # log nothing, and not raise.
        stale = podcasts._downloaded(self.store, int(channel_id))
        self.assertEqual(self.response('deletePodcastEpisode', id=middle)['status'], 'ok')
        with mock.patch.object(podcasts, '_downloaded', lambda store, channel: stale):
            report = podcasts.prune_member(self.store, self.uid)
        self.assertEqual(report['removed'], [])
        self.assertEqual(podcasts.prune_log(self.store, self.uid), [])
        self.assertEqual(self.files(), [])

    # -- the setting, and the worker ----------------------------------------
    def test_the_policy_is_set_at_subscribe_time_and_reported(self):
        channel_id, _episodes = self.subscribe(keepNewest=2, keepDays=14)
        channel = self.response('getPodcasts')['podcasts']['channel'][0]
        self.assertEqual(channel['retention'], {'keepNewest': 2, 'keepDays': 14})
        with self.store.lock:
            row = self.store.conn.execute(
                'SELECT keep_newest, keep_days FROM listening_podcast_channels WHERE id=?',
                (int(channel_id),)).fetchone()
        self.assertEqual((row['keep_newest'], row['keep_days']), (2, 14))

    def test_absurd_policies_are_refused(self):
        channel_id, _episodes = self.subscribe()
        for keep_newest, keep_days in ((-1, 0), (0, -5), (100000, 0), (0, 100000)):
            with self.assertRaises(podcasts.Denied):
                self.set_policy(channel_id, keep_newest=keep_newest, keep_days=keep_days)

    def test_the_worker_sweeps_every_member_with_a_policy(self):
        channel_id, episodes = self.subscribe()
        self.download(episodes['Oldest episode']['id'])
        self.set_policy(channel_id, keep_newest=0, keep_days=1)
        self.age(episodes['Oldest episode']['id'], days=30)
        report = podcasts.prune_all(self.store)
        self.assertEqual(report['members'], 1)
        self.assertEqual(report['bytes'], 600)
        # And the app's own periodic worker is what runs it, not a request.
        scheduler = (Path(__file__).resolve().parent.parent
                     / 'engine' / 'scheduler.py').read_text()
        self.assertIn('from podcasts import prune_all', scheduler)


if __name__ == '__main__':
    unittest.main()