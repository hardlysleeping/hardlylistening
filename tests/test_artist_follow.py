import os, sys, unittest, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('TMPDIR', '/tmp')


class ArtistProfileUrl(unittest.TestCase):
    def setUp(self):
        import db, security
        self.dir = tempfile.mkdtemp(dir='/tmp')
        self.store = db.Store(os.path.join(self.dir, 'a.db'))
        db.Schema(security.Security()).apply(self.store) if hasattr(db, 'Schema') else None
        self.store.artist('soundcloud', '4242', 'Someone', 'https://soundcloud.com/someone')

    def test_profile_carries_the_url_and_rss(self):
        from discovery.artist_profile import profile
        data = profile(self.store, 'Someone')
        row = data['providers'][0]
        self.assertEqual(row['url'], 'https://soundcloud.com/someone')
        self.assertIn('feeds.soundcloud.com/users/soundcloud:users:4242', row['rss'])
        self.assertFalse(data['following'])

    def test_unsafe_urls_are_dropped(self):
        self.store.artist('generic', 'x1', 'Sneaky', 'javascript:alert(1)')
        from discovery.artist_profile import profile
        self.assertEqual(profile(self.store, 'Sneaky')['providers'][0]['url'], '')


if __name__ == '__main__':
    unittest.main()
