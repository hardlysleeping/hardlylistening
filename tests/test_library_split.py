"""LIB-001: the Library split, checked at the seams rather than re-tested end to end.

The card is "the split is internal": `listening.py` and `user_playlists.py` keep their routes and
answers, and the code behind them moves into `library/`. Two things a split can silently break, so
these tests pin both: the surviving entry points must *delegate* (a copy would drift), and the
import direction must stay Library's — no acquisition, Party or Social reach-in from the new files.

The old module path is a real hazard here too: `library/playlists.py` is now the member-playlist
domain while the on-disk M3U projection moved to `library/playlist_files.py`, and `core/db.py`
schedules that projection. The test reads the import so the next edit cannot quietly re-point it.
"""
import ast
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
import listening
import user_playlists
from library import api as library_api
from library import folders as library_folders
from library import playlist_files
from library import playlists as library_playlists
from library import saves as library_saves

ROOT = Path(__file__).resolve().parents[1]


def imports(path):
    tree = ast.parse(Path(path).read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split('.')[0])
    return found


class Files(unittest.TestCase):
    def test_every_named_file_exists(self):
        for name in ('saves.py', 'folders.py', 'playlists.py', 'api.py', 'playlist_files.py'):
            self.assertTrue((ROOT / 'library' / name).is_file(), name)

    def test_the_projection_is_not_the_playlist_domain(self):
        self.assertTrue(hasattr(playlist_files, 'MARKER'))
        self.assertFalse(hasattr(library_playlists, 'MARKER'),
                         'library/playlists.py must be the member-playlist domain, not the M3U projection')

    def test_the_database_schedules_the_projection_and_not_the_domain(self):
        source = (ROOT / 'core' / 'db.py').read_text()
        self.assertIn('from library.playlist_files import sync', source)
        self.assertNotIn('from library.playlists import sync', source)

    def test_the_new_files_do_not_reach_into_acquisition_party_or_social(self):
        for name in ('saves.py', 'folders.py', 'playlists.py', 'api.py'):
            roots = imports(ROOT / 'library' / name)
            for forbidden in ('engine', 'repo', 'social', 'party', 'discovery', 'listening'):
                self.assertNotIn(forbidden, roots, f'library/{name} imports {forbidden}')


class Delegation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = db.Store(Path(self.tmp.name) / 'library.db')
        self.addCleanup(self.store.close)

    def test_the_save_helpers_on_the_surface_are_the_library_ones(self):
        with mock.patch.object(library_saves, 'want_playlist', return_value=3) as want:
            self.assertEqual(listening.want_playlist(self.store, 'm1', 7), 3)
        want.assert_called_once_with(self.store, 'm1', 7)
        with mock.patch.object(library_saves, 'deliver_track', return_value=2) as deliver:
            self.assertEqual(listening.deliver_track(self.store, 9), 2)
        deliver.assert_called_once_with(self.store, 9)
        with mock.patch.object(library_saves, 'forget_track', return_value=None) as forget:
            listening.forget_track(self.store, 9)
        forget.assert_called_once_with(self.store, 9)

    def test_the_playlist_reader_is_the_library_one(self):
        self.assertIs(user_playlists.listing, library_playlists.listing)
        self.assertIs(user_playlists.ids, library_playlists.ids)

    def test_the_surface_routes_saves_and_folders_to_the_library_adapter(self):
        handler = type('H', (), {'command': 'GET', 'path': '/api/listening/saves',
                                 'member': {'id': 'm1'}})()
        security = mock.Mock()
        security.authenticate.return_value = {'id': 'm1'}
        with mock.patch.object(listening, 'schema'), \
                mock.patch.object(library_api, 'saves', return_value='answered') as saves:
            self.assertEqual(listening.browser(handler, self.store, security), 'answered')
        saves.assert_called_once_with(handler, self.store, security)
        handler.path = '/api/listening/folders'
        security.authenticate.return_value = {'id': 'm1'}
        with mock.patch.object(listening, 'schema'), \
                mock.patch.object(library_api, 'folders', return_value='answered') as folders:
            self.assertEqual(listening.browser(handler, self.store, security), 'answered')
        folders.assert_called_once_with(handler, self.store, security)


class Folders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = db.Store(Path(self.tmp.name) / 'library.db')
        self.addCleanup(self.store.close)
        import security
        security.SECURITY.schema(self.store)
        library_saves.schema(self.store)
        library_folders.schema(self.store)
        with self.store.lock:
            self.store.conn.execute("INSERT OR IGNORE INTO members(id,email,role) VALUES('m1','one@example.test','member')")
            self.store.conn.commit()

    def test_a_folder_is_the_members_own_and_its_name_is_checked(self):
        folder = library_folders.create(self.store, 'm1', 'Mood sets', None)
        self.assertGreater(folder, 0)
        self.assertEqual([f['name'] for f in library_folders.listing(self.store, 'm1')], ['Mood sets'])
        self.assertEqual(library_folders.listing(self.store, 'm2'), [])
        with self.assertRaises(Exception) as caught:
            library_folders.create(self.store, 'm1', 'Mood sets', None)
        self.assertEqual(getattr(caught.exception, 'status', 409), 409)
        with self.assertRaises(Exception):
            library_folders.create(self.store, 'm1', '', None)


if __name__ == '__main__':
    unittest.main()