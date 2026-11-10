"""Comment likes: one per member, toggleable, counted in the payload."""
import json
import os
import unittest
from pathlib import Path
import test_security as security_tests


class CommentLikes(unittest.TestCase):
    # Bind the fixture helpers instead of inheriting SecurityHTTP, whose own tests would
    # otherwise run twice under this class.
    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request

    def setUp(self):
        security_tests.SecurityHTTP.setUp(self)
        self.store.set_settings({'root': str(Path(self.tmp.name) / 'media')})
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,added)"
                " VALUES('local','fixture','Song','Artist','Album',0)")
            self.store.conn.commit()
        self.headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}
        self.identity = self.user()

    def call(self, suffix='', data=None):
        status, _, body = self.request(
            '/api/social/track/1' + suffix,
            'GET' if data is None else 'POST', self.headers,
            None if data is None else json.dumps(data))
        return status, json.loads(body)

    def test_like_toggles_and_counts(self):
        self.assertEqual(self.call('/comments', {'body': 'first'})[0], 200)
        status, payload = self.call()
        comment = payload['items'][0]
        self.assertEqual((comment['likes'], comment['liked']), (0, False))

        self.assertEqual(self.call('/like/%d' % comment['id'], {})[0], 200)
        comment = self.call()[1]['items'][0]
        self.assertEqual((comment['likes'], comment['liked']), (1, True))

        self.assertEqual(self.call('/like/%d' % comment['id'], {})[0], 200)
        comment = self.call()[1]['items'][0]
        self.assertEqual((comment['likes'], comment['liked']), (0, False))

    def test_like_needs_an_existing_comment(self):
        self.assertEqual(self.call('/like/999', {})[0], 404)
        self.assertEqual(self.call('/like', {})[0], 404)


if __name__ == '__main__':
    unittest.main()
