"""REPO-012: releases are Repo's read, not Social's Feed.

`/api/feed` used to call `store.feed_list()` from the composition root, which left
"what the follows have found" with no owner at the HTTP boundary. It now asks
`repo.discovery.releases()`. The table keeps its name in this pass — only the entry
point moves — so the tests are about fidelity: the same rows, the same order, the
same pointer filter, and the route actually going through Repo.
"""
import unittest
from unittest.mock import patch

import repo.discovery as repo_discovery
import test_security as harness


class Facade(unittest.TestCase):
    """Pure reads over a temporary SQLite file."""

    setUp = harness.SecurityHTTP.setUp
    tearDown = harness.SecurityHTTP.tearDown

    def add(self, artist, title, decision='new', release_date='2026-01-01'):
        watch = self.store.watch_add('soundcloud', artist, artist, artist_url='https://example.test/a')
        watch_id = watch if isinstance(watch, int) else watch['id']
        self.store.feed_add(watch_id, 'soundcloud', f'{artist}-{title}', title,
                            artist=artist, release_date=release_date)
        row = self.store.feed_list(1)[0]
        if decision != 'new':
            self.store.feed_mark(row['id'], decision=decision)
        return row['id']

    def test_it_answers_exactly_what_the_store_answers(self):
        self.add('Artist', 'First')
        self.add('Artist', 'Second')
        self.assertEqual(repo_discovery.releases(self.store), self.store.feed_list(200))

    def test_the_limit_and_the_decision_filter_are_the_store_s(self):
        first = self.add('Artist', 'Ignored one', decision='ignored')
        self.add('Artist', 'Fresh one')
        only = repo_discovery.releases(self.store, 200, 'ignored')
        self.assertEqual([r['id'] for r in only], [first])
        self.assertEqual(len(repo_discovery.releases(self.store, 1)), 1)

    def test_the_follow_name_rides_along(self):
        """The web row shows which artist found it; that comes from the join."""
        self.add('Late Night', 'EP')
        self.assertEqual(repo_discovery.releases(self.store)[0]['follow_name'], 'Late Night')

    def test_one_release_by_id_and_an_unknown_id_is_none(self):
        item = self.add('Artist', 'Single')
        self.assertEqual(repo_discovery.release(self.store, item)['title'], 'Single')
        self.assertIsNone(repo_discovery.release(self.store, 9999))


class Route(unittest.TestCase):
    """The HTTP boundary, on the same fixture the security suite uses."""

    tearDown = harness.SecurityHTTP.tearDown
    request = harness.SecurityHTTP.request
    user = harness.SecurityHTTP.user
    token = harness.SecurityHTTP.token
    headers = {'Authorization': 'Bearer good', 'Origin': 'https://music.example'}

    def setUp(self):
        harness.SecurityHTTP.setUp(self)
        self.identity = self.user('member')

    def test_the_route_goes_through_repo(self):
        asked = []
        real = repo_discovery.releases

        def spy(store, limit=200, decision=''):
            asked.append((limit, decision))
            return real(store, limit, decision)

        with patch.object(repo_discovery, 'releases', side_effect=spy):
            code, _headers, _body = self.request('/api/feed', headers=self.headers)
        self.assertEqual(code, 200)
        self.assertEqual(asked, [(200, '')])

    def test_the_decision_parameter_reaches_repo(self):
        asked = []

        def spy(store, limit=200, decision=''):
            asked.append(decision)
            return []

        with patch.object(repo_discovery, 'releases', side_effect=spy):
            self.request('/api/feed?decision=ignored', headers=self.headers)
        self.assertEqual(asked, ['ignored'])

    def test_the_composition_root_no_longer_reads_the_table(self):
        """Ownership is only real if the route does not keep a private copy."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / 'server.py').read_text()
        self.assertNotIn('feed_list', text, 'server.py reads releases directly; ask repo.discovery')
        self.assertNotIn('feed_item(', text, 'server.py reads one release directly; ask repo.discovery')


if __name__ == '__main__':
    unittest.main()