"""REPO-014: the Repo package splits into named files, and the facade keeps its signatures.

The card's rule is "the split is internal": `repo/service.py` is still the only import a caller
needs, and every answer is the one the moved module already gave. These tests check the two things
a split can silently break:

* the old import paths must be the *same objects* as the new ones (a copy splits identity, and a
  patch through the old name would then miss the code that runs);
* the two new facades must delegate rather than reimplement — `acquisition.ingest` calls the
  worker, `api.subscriptions_for` reads the caller and calls the service.
"""
import ast
import unittest
from pathlib import Path
from unittest import mock

import discovery.artist_profile
import discovery.subscriptions
import discovery.tracklist
import repo.acquisition
import repo.api
import repo.catalogue
import repo.repository
import repo.service
import repo.subscriptions

ROOT = Path(__file__).resolve().parents[1]


def imports(path):
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split('.')[0])
    return found


class Files(unittest.TestCase):
    def test_every_named_file_exists_beside_the_facade(self):
        for name in ('repository.py', 'catalogue.py', 'subscriptions.py',
                     'acquisition.py', 'api.py'):
            self.assertTrue((ROOT / 'repo' / name).is_file(), name)

    def test_the_old_paths_are_the_new_modules(self):
        self.assertIs(discovery.artist_profile, repo.repository)
        self.assertIs(discovery.tracklist, repo.catalogue)
        self.assertIs(discovery.subscriptions, repo.subscriptions)

    def test_the_facade_no_longer_reaches_past_its_own_package(self):
        roots = imports(ROOT / 'repo' / 'service.py')
        self.assertNotIn('discovery', roots)
        self.assertIn('repo', roots)


class Acquisition(unittest.TestCase):
    def test_ingest_is_the_workers_own_answer(self):
        queue = mock.Mock()
        queue.ingest.return_value = {'queued': 3, 'playlist_id': 9}
        self.assertEqual(repo.acquisition.ingest(queue, 'https://example.test/x'),
                         {'queued': 3, 'playlist_id': 9})
        queue.ingest.assert_called_once_with('https://example.test/x')

    def test_a_refusal_passes_through_unchanged(self):
        queue = mock.Mock()
        queue.ingest.side_effect = ValueError('paste a playlist link')
        with self.assertRaises(ValueError):
            repo.acquisition.ingest(queue, '')


class Adapter(unittest.TestCase):
    def test_subscriptions_for_reads_the_caller(self):
        handler = type('H', (), {'member': {'id': 'member-1'}})()
        with mock.patch.object(repo.service, 'subscriptions', return_value=[]) as subscriptions:
            repo.api.subscriptions_for(handler, 'store')
        subscriptions.assert_called_once_with('store', 'member-1')

    def test_an_anonymous_caller_passes_no_member(self):
        handler = type('H', (), {'member': None})()
        with mock.patch.object(repo.service, 'subscriptions', return_value=[]) as subscriptions:
            repo.api.subscriptions_for(handler, 'store')
        subscriptions.assert_called_once_with('store', '')


if __name__ == '__main__':
    unittest.main()