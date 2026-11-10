import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class GenericArtistUrl(unittest.TestCase):
    def _resolve(self, info):
        from downloaders import ytdlp
        from providers import generic
        original = ytdlp.json_info
        ytdlp.json_info = lambda url, **kw: info
        try:
            return generic.GenericProvider().resolve("https://example.com/x")
        finally:
            ytdlp.json_info = original

    def test_uploader_url_becomes_the_artist_page(self):
        release = self._resolve({
            "id": "1", "title": "A mix", "uploader": "Someone",
            "uploader_url": "https://soundcloud.com/someone",
            "uploader_id": "42",
            "entries": [{"id": "9", "title": "Track", "uploader": "Someone",
                         "webpage_url": "https://soundcloud.com/someone/track"}]})
        self.assertEqual(release.artist_url, "https://soundcloud.com/someone")
        self.assertEqual(release.artist_id, "42")

    def test_channel_url_is_the_fallback(self):
        release = self._resolve({
            "id": "2", "title": "Video", "uploader": "Chan",
            "channel_url": "https://www.youtube.com/channel/abc",
            "channel_id": "abc"})
        self.assertEqual(release.artist_url, "https://www.youtube.com/channel/abc")
        self.assertEqual(release.artist_id, "abc")

    def test_no_uploader_page_is_not_invented(self):
        release = self._resolve({"id": "3", "title": "Local", "uploader": "Nobody"})
        self.assertEqual(release.artist_url, "")
        self.assertEqual(release.artist_id, "")


if __name__ == '__main__':
    unittest.main()
