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
        text = (Path(__file__).resolve().parent.parent / 'subsonic.py').read_text()
        for table in ('listening_favorites', 'listening_ratings', 'listening_plays'):
            self.assertNotRegex(
                text, r'SELECT[^;]*FROM %s' % table,
                f'{table} is read with SQL in subsonic.py; it belongs to library/service.py')
        self.assertNotIn('MAX(added)', text,
                         'the album sort key belongs to library/service.py (album_added)')

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