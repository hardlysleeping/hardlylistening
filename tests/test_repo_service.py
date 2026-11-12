"""Repo's facade: same answers, one entry point, and no reach into Library or Social.

`ARCH-002`. The card is "no behaviour change", so these tests are written the only way that can
mean something: the facade's answer is compared with the answer from the function it delegates to,
on real rows. A facade that returns a slightly different dict is worse than no facade, because the
next card (`ARCH-006`) is going to point the routes at it and nobody will re-check the shapes.

The last test is the boundary itself. `docs/server-domains.md` forbids `Repo -> Library` and
`Repo -> Social`; an import check is how that stays true after the next edit, rather than a note
in a document nobody reads at 2am.
"""
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
from repo import service


class Facade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = db.Store(Path(self.tmp.name) / 'repo.db')
        self.addCleanup(self.store.close)
        with self.store.lock:
            self.store.conn.executescript('''
                INSERT INTO artists (provider, artist_id, name, url)
                VALUES ('soundcloud', '1', 'Aphex Twin', 'https://soundcloud.com/aphex');
                INSERT INTO watches (provider, artist_id, artist_name, artist_url, member_id,
                                     watermark, added)
                VALUES ('soundcloud', '1', 'Aphex Twin', 'https://soundcloud.com/aphex', 'u1', 0, 1);
                INSERT INTO watches (provider, artist_id, artist_name, artist_url, member_id,
                                     watermark, added)
                VALUES ('soundcloud', '2', 'Nobody Owns Me', '', '', 0, 1);
            ''')
            self.store.conn.commit()

    # -- the snapshot -----------------------------------------------------------------------------

    def test_the_snapshot_is_the_store_snapshot(self):
        """Anything else would be a second query layer wearing a facade's name."""
        facade, direct = service.snapshot(self.store), self.store.state()
        # The snapshot carries the clock it was taken at, inside `stats`; everything else must be
        # identical, because the whole point is that there is one query set and not two.
        for snapshot in (facade, direct):
            snapshot['stats'].pop('now', None)
        self.assertEqual(facade, direct)
        self.assertIn('playlists', direct)

    # -- subscriptions ----------------------------------------------------------------------------

    def test_subscriptions_mark_ownership_exactly_as_before(self):
        rows = service.subscriptions(self.store, 'u1')
        self.assertEqual([r['artist_name'] for r in rows], ['Aphex Twin', 'Nobody Owns Me'])
        self.assertEqual([r['mine'] for r in rows], [True, False])

        # A follow made before ownership existed belongs to nobody, so a signed-in member cannot
        # call it theirs...
        rows = service.subscriptions(self.store, 'u2')
        self.assertEqual([r['mine'] for r in rows], [False, False])

        # ...and with no member id (sign-in off, or an anonymous read) everything is visible.
        rows = service.subscriptions(self.store)
        self.assertEqual([r['mine'] for r in rows], [True, True])

    def test_watch_refuses_what_the_adapters_used_to_check_by_hand(self):
        self.assertEqual(service.watch(self.store, 1)['artist_name'], 'Aphex Twin')
        with self.assertRaises(ValueError):
            service.watch(self.store, 999)

    def test_toggle_and_remove_keep_their_old_replies(self):
        self.assertEqual(service.set_enabled(self.store, 1, False), {'id': 1, 'enabled': False})
        self.assertEqual(service.watch(self.store, 1)['enabled'], 0)
        self.assertEqual(service.remove(self.store, 2), {'removed': 2})
        with self.assertRaises(ValueError):
            service.watch(self.store, 2)

    def test_leaving_keeps_the_follow_and_returns_the_seat(self):
        self.assertEqual(service.release_seat(self.store, 1, 'u1'), {'unsubscribed': 1})
        row = service.watch(self.store, 1)          # still tracked for everybody else
        self.assertEqual(row['artist_name'], 'Aphex Twin')
        self.assertEqual(row['member_id'], '')
        # A released follow belongs to nobody again, so it is not somebody else's by accident;
        # it becomes theirs again by picking it up, which is what the "+" does.
        self.assertEqual([r['mine'] for r in service.subscriptions(self.store, 'u2')], [False, False])

    def test_leaving_someone_elses_follow_is_refused(self):
        with self.assertRaises(ValueError):
            service.release_seat(self.store, 1, 'u2')

    def test_subscribe_by_link_and_by_name_go_to_the_same_place(self):
        with patch.object(service._subscriptions, 'subscribe',
                          return_value={'id': 3}) as by_link:
            self.assertEqual(service.subscribe(self.store, 'https://soundcloud.com/x'), {'id': 3})
            by_link.assert_called_once_with(self.store, 'https://soundcloud.com/x', member_id='')
        with patch.object(service._subscriptions, 'subscribe_by_name',
                          return_value={'id': 4}) as by_name:
            self.assertEqual(service.subscribe(self.store, name='Aphex Twin',
                                               provider='soundcloud', member_id='u1'), {'id': 4})
            by_name.assert_called_once_with(self.store, 'Aphex Twin', 'soundcloud', member_id='u1')

    # -- artists and releases ---------------------------------------------------------------------

    def test_an_artist_page_is_the_read_only_projection(self):
        page = service.artist(self.store, 'aphex twin')     # case-insensitive, as before
        self.assertEqual(page['name'], 'aphex twin')
        self.assertEqual([p['artist_id'] for p in page['providers']], ['1'])
        with self.assertRaises(ValueError):
            service.artist(self.store, '   ')

    def test_releases_and_tracks_are_passed_through_untouched(self):
        with patch.object(service._tracklist, 'catalogue',
                          return_value={'releases': [], 'offset': 0}) as catalogue:
            self.assertEqual(service.releases(self.store, 7, offset=60, limit=30),
                             {'releases': [], 'offset': 0})
            catalogue.assert_called_once_with(self.store, 7, offset=60, limit=30)
        with patch.object(service._tracklist, 'tracks', return_value=[{'title': 'X'}]) as tracks:
            self.assertEqual(service.tracks('https://example/album'), [{'title': 'X'}])
            tracks.assert_called_once_with('https://example/album')

    def test_checks_are_passed_through(self):
        watch = service.watch(self.store, 1)
        with patch.object(service._subscriptions, 'check', return_value={'added': 0}) as check:
            self.assertEqual(service.check(self.store, watch), {'added': 0})
            check.assert_called_once_with(self.store, watch)
        with patch.object(service._subscriptions, 'check_all', return_value=[]) as check_all:
            self.assertEqual(service.check_all(self.store, budget=1), [])
            check_all.assert_called_once_with(self.store, budget=1)


class Boundary(unittest.TestCase):
    """Repo imports neither Library nor Social, and says so in the file."""

    def imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split('.')[0])
        return found

    def test_repo_does_not_import_library_social_or_security(self):
        path = Path(__file__).resolve().parents[1] / 'repo' / 'service.py'
        roots = self.imports(path)
        for forbidden in ('social', 'library', 'security', 'subsonic', 'listening'):
            self.assertNotIn(forbidden, roots, f'repo/service.py imports {forbidden}')


if __name__ == '__main__':
    unittest.main()