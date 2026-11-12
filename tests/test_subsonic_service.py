"""The Subsonic surface asks the library service, it does not keep its own query layer.

`subsonic.handle()` used to read the member's stars, ratings and play ledger with SQL of its
own, next to the identical queries in `library/service.py`. Two copies of "what has this
member starred" drift the moment one of them changes, and nothing would notice. These tests
pin the surface to the service: the four questions are answered by calling it, and no query
for those tables is left in the file.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from library import service
from test_listening import ListeningHTTP


class ServiceOwnsTheQueries(unittest.TestCase):
    # The HTTP fixture is a test case, not a base class: bind what this suite uses without
    # inheriting (and re-running) its own tests.
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    token = ListeningHTTP.token
    user = ListeningHTTP.user
    api = ListeningHTTP.api
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save

    def test_the_surface_reads_member_state_through_the_service(self):
        asked = []
        real = {name: getattr(service, name)
                for name in ('starred', 'ratings', 'plays', 'album_added')}

        def record(name):
            def wrapper(*args, **kwargs):
                asked.append(name)
                return real[name](*args, **kwargs)
            return wrapper

        with patch.multiple(service, **{name: record(name) for name in real}):
            self.assertEqual(self.response('ping')['status'], 'ok')
        self.assertEqual(sorted(set(asked)), ['album_added', 'plays', 'ratings', 'starred'])

    def test_no_query_layer_of_its_own(self):
        """Neither a read nor a write: the adapter translates, the service owns the rows."""
        text = (Path(__file__).resolve().parent.parent / 'subsonic.py').read_text()
        for table in ('listening_favorites', 'listening_ratings', 'listening_plays',
                      'listening_bookmarks'):
            self.assertNotIn(table, text,
                             f'{table} is touched in subsonic.py; it belongs to library/service.py')
        self.assertNotIn('MAX(added)', text,
                         'the album sort key belongs to library/service.py (album_added)')

    def test_a_scrobble_and_a_bookmark_round_trip(self):
        """The write side of the service is the same row the read side answers."""
        service.record_play(self.store, 'u1', self.ids[0])
        service.record_play(self.store, 'u1', self.ids[0])
        self.assertEqual(service.plays(self.store, 'u1')[self.ids[0]][0], 2)
        service.save_bookmark(self.store, 'u1', self.ids[0], 1234, 'intro')
        service.save_bookmark(self.store, 'u1', self.ids[0], 4321, 'verse')
        rows = service.bookmark_rows(self.store, 'u1')
        self.assertEqual([(r['position_ms'], r['comment']) for r in rows], [(4321, 'verse')])
        self.assertEqual(service.bookmarks(self.store, 'u1')[self.ids[0]], 4321)
        service.remove_bookmark(self.store, 'u1', self.ids[0])
        self.assertEqual(service.bookmarks(self.store, 'u1'), {})
        service.star(self.store, 'u1', self.ids[0], True)
        self.assertEqual(service.starred(self.store, 'u1'), {self.ids[0]})
        service.star(self.store, 'u1', self.ids[0], False)
        self.assertEqual(service.starred(self.store, 'u1'), set())

    def test_a_deleted_song_does_not_age_its_album(self):
        """`album_added` feeds the album wall's sort: a tombstone is not a new addition."""
        self.save('track', self.ids[0])
        new = self.store.track('local', 'sibling', 'Earlier', artist_name='Artist 0', album='Album')
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET added=1000 WHERE id=?', (new,))
            self.store.conn.execute('UPDATE tracks SET added=2000 WHERE id=?', (self.ids[0],))
            self.store.conn.commit()
        self.assertEqual(service.album_added(self.store)['Artist 0', 'Album'], 2000)
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET deleted=1 WHERE id=?', (self.ids[0],))
            self.store.conn.commit()
        self.assertEqual(service.album_added(self.store)['Artist 0', 'Album'], 1000)


if __name__ == '__main__':
    unittest.main()