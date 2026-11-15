"""Following any RSS/Atom feed as an artist source (HL-232).

Following supported SoundCloud and Apple only, so a label, a Bandcamp-hosted artist or a podcast
that publishes a feed could not be tracked at all. This card adds a feed provider: the entry list is
the release list, nothing else. It is not a scraper — the URL has to be a feed, a page is refused
and no page is ever parsed for links.

The rules the tests pin, one per cell of the card:

* an Atom fixture and an RSS fixture both produce feed items;
* entries dedupe by their own id, falling back to the link when a feed has no guid, so a second
  check reports nothing new and a genuinely new entry reports once;
* an entry's body never becomes data — a feed full of `<script>` and `<b>` produces titles only,
  and nothing stored contains markup;
* a dead feed says so plainly instead of looking like an artist with no releases.

No network: `providers.feed.fetch` is the one function that owns the socket, and it is patched.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from repo import subscriptions
import test_security as harness

FEEDS = Path(__file__).resolve().parent / 'fixtures' / 'feeds'
ATOM_URL = 'https://example.test/night-drive/feed.xml'
RSS_URL = 'https://example.test/slow-hands/rss'


def feed_bytes(name):
    return (FEEDS / name).read_bytes()


class FeedFollow(unittest.TestCase):
    setUp = harness.SecurityHTTP.setUp
    tearDown = harness.SecurityHTTP.tearDown

    def serve(self, body):
        """Patch the provider's only network call, and keep the URL it was asked for."""
        asked = []

        def fetch(url, timeout=None):
            asked.append(url)
            if callable(body):
                return body(url)
            return body
        patch('providers.feed.fetch', fetch).start()
        self.addCleanup(patch.stopall)
        return asked

    def test_an_atom_feed_becomes_a_follow_with_its_entries(self):
        self.serve(feed_bytes('atom.xml'))
        result = subscriptions.subscribe(self.store, ATOM_URL)
        self.assertEqual(result['watch']['provider'], 'feed')
        self.assertEqual(result['watch']['artist_name'], 'Night Drive Recordings')
        self.assertEqual(result['listed'], 3)
        titles = {row['title'] for row in self.store.feed_list(50)}
        self.assertEqual(titles, {'Third Signal', 'Second Light', 'First Light'})

    def test_an_rss_feed_becomes_a_follow_with_dates_and_artwork(self):
        self.serve(feed_bytes('rss.xml'))
        result = subscriptions.subscribe(self.store, RSS_URL)
        self.assertEqual(result['listed'], 2)
        rows = {row['title']: row for row in self.store.feed_list(50)}
        self.assertEqual(set(rows), {'Episode & One', 'Episode Two'})
        self.assertTrue(rows['Episode & One']['release_date'].startswith('2026-10-30'))
        self.assertEqual(rows['Episode & One']['artwork'], 'https://example.test/art/1.jpg')

    def test_an_entry_body_never_becomes_data(self):
        self.serve(feed_bytes('rss.xml'))
        subscriptions.subscribe(self.store, RSS_URL)
        for row in self.store.feed_list(50):
            for value in row.values():
                if isinstance(value, str):
                    self.assertNotIn('<', value, f'markup reached a stored field: {value!r}')
                    self.assertNotIn('alert(1)', value, f'entry body reached a stored field: {value!r}')

    def test_a_second_check_reports_nothing_and_a_new_entry_reports_once(self):
        served = [feed_bytes('atom.xml')]
        self.serve(lambda url: served[0])
        subscriptions.subscribe(self.store, ATOM_URL)
        watch = self.store.watch_get(self.store.watch_add('feed', ATOM_URL, 'Night Drive Recordings', ATOM_URL))
        self.assertEqual(subscriptions.check(self.store, watch)['new'], 0)

        body = feed_bytes('atom.xml').decode().replace(
            '</feed>',
            '  <entry><id>tag:example.test,2026:release/4</id><title>Fourth Signal</title>'
            '<published>2026-11-02T00:00:00Z</published>'
            '<link rel="alternate" href="https://example.test/night-drive/fourth-signal"/></entry>'
            '</feed>')
        served[0] = body.encode()
        self.assertEqual(subscriptions.check(self.store, watch)['new'], 1)
        self.assertEqual(self.store.feed_list(1)[0]['title'], 'Fourth Signal')
        self.assertEqual(subscriptions.check(self.store, watch)['new'], 0)

    def test_an_entry_without_a_guid_dedupes_by_its_link(self):
        self.serve(feed_bytes('rss.xml'))
        subscriptions.subscribe(self.store, RSS_URL)
        watch = self.store.watch_get(self.store.watch_add('feed', RSS_URL, 'Slow Hands', RSS_URL))
        rows = {row['title']: row for row in self.store.feed_list(50)}
        self.assertTrue(rows['Episode Two']['release_id'])          # no <guid>: the link stands in
        self.assertEqual(subscriptions.check(self.store, watch)['new'], 0)

    def test_a_dead_feed_says_so(self):
        # The transport itself fails, so the provider's own wrapping is what answers.
        with patch('providers.feed.urllib.request.urlopen',
                   side_effect=OSError('connection refused')):
            with self.assertRaises(ValueError) as caught:
                subscriptions.subscribe(self.store, ATOM_URL)
        message = str(caught.exception).lower()
        self.assertIn('feed', message)
        self.assertIn('unavailable', message)

    def test_a_failed_check_is_recorded_not_raised(self):
        patch('providers.feed.urllib.request.urlopen',
              side_effect=OSError('timed out')).start()
        self.addCleanup(patch.stopall)
        watch_id = self.store.watch_add('feed', ATOM_URL, 'Night Drive Recordings', ATOM_URL)
        self.store.watch_mark(watch_id, checked=False)
        result = subscriptions.check_all(self.store, budget=2)
        self.assertTrue(result)
        self.assertTrue(all('error' in row for row in result))

    def test_the_follow_box_advertises_feeds(self):
        """The one place a person learns what they may paste has to name feeds."""
        page = (Path(__file__).resolve().parent.parent / 'web' / 'index.html').read_text()
        start = page.index('id="followurl"')
        field = page[max(0, start - 120):start + 260]
        self.assertIn('feed', field.lower(), 'the follow field mentions feeds')
        self.assertNotIn('SoundCloud or Apple Music artist link first', page,
                         'the hint no longer claims SoundCloud/Apple are the only sources')

    def test_a_page_is_not_a_feed(self):
        from providers import base as provider_base
        self.assertEqual(provider_base.for_url('https://example.test/night-drive').name, 'generic')
        self.assertEqual(provider_base.for_url(ATOM_URL).name, 'feed')
        self.assertEqual(provider_base.for_url('https://example.test/feed/').name, 'feed')
        self.assertEqual(provider_base.for_url('https://soundcloud.com/someone').name, 'soundcloud')


if __name__ == '__main__':
    unittest.main()