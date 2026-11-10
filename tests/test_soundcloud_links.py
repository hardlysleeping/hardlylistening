"""A share link must be read like the page it points at.

`on.soundcloud.com/<token>` is what the app's own "copy link" button produces,
and the flat listing behind a large set carries only numeric track ids. Both
used to end in "no tracks"/"no usable title", so both are pinned down here.
"""
import unittest
from unittest import mock

from providers import base
from providers.soundcloud import SoundcloudProvider, _artist_url


FLAT_SET = {
    "title": "Shuffle",
    "uploader": "Dilroop S. Gill",
    "uploader_url": "https://soundcloud.com/dilroop-s-gill",
    "id": "2247016790",
    "entries": [
        {"id": "2010751751", "_type": "url_transparent",
         "url": "https://api-v2.soundcloud.com/tracks/2010751751"},
        {"id": "2274630962", "_type": "url_transparent",
         "url": "https://soundcloud.com/spoonemusic/zeds-dead-symphony"},
    ],
}

API_TRACKS = {
    "2010751751": {
        "id": 2010751751,
        "title": "XXXTentacion - Look At Me! (Flip)",
        "duration": 150880,
        "permalink_url": "https://soundcloud.com/overeazybeats/look-at-me",
        "artwork_url": "https://i1.sndcdn.com/artworks-a-large.jpg",
        "user": {"username": "OVEREAZY"},
    },
    "2274630962": {
        "id": 2274630962,
        "title": "Zeds Dead - Symphony (Spoone Flip)",
        "duration": 211000,
        "permalink_url": "https://soundcloud.com/spoonemusic/zeds-dead-symphony",
        "artwork_url": "https://i1.sndcdn.com/artworks-b-large.jpg",
        "user": {"username": "Spoone"},
    },
}


class ShareLinkTest(unittest.TestCase):
    def test_short_link_belongs_to_soundcloud(self):
        provider = base.for_url("https://on.soundcloud.com/uTNxWSKmuePCFr7oiu")
        self.assertEqual(provider.name, "soundcloud")

    def test_short_link_is_never_read_as_an_artist_slug(self):
        self.assertEqual(_artist_url("https://on.soundcloud.com/abc123"), "")

    def test_canonical_url_follows_the_redirect(self):
        from providers.soundcloud import canonical_url

        handler = mock.MagicMock()
        handler.open.return_value.__enter__.return_value.headers = {
            "Location": "https://soundcloud.com/dilroop-s-gill/sets/shuffle"
                        "?ref=clipboard&si=abc&utm_source=x"}
        with mock.patch("urllib.request.build_opener", return_value=handler):
            self.assertEqual(canonical_url("https://on.soundcloud.com/tok"),
                             "https://soundcloud.com/dilroop-s-gill/sets/shuffle")

    def test_flat_ids_are_filled_from_the_api(self):
        provider = SoundcloudProvider()
        with mock.patch("providers.soundcloud.ytdlp.json_info", return_value=FLAT_SET), \
             mock.patch("providers.soundcloud._api_tracks", return_value=API_TRACKS):
            release = provider.resolve("https://soundcloud.com/dilroop-s-gill/sets/shuffle")

        self.assertEqual(release.track_count, 2)
        self.assertEqual(release.artist_url, "https://soundcloud.com/dilroop-s-gill")
        first, second = release.tracks
        self.assertEqual(first.title, "XXXTentacion - Look At Me! (Flip)")
        self.assertEqual(first.artist, "OVEREAZY")
        self.assertEqual(first.duration, 150.88)
        self.assertEqual(first.source_url, "https://soundcloud.com/overeazybeats/look-at-me")
        self.assertEqual(first.artwork, "https://i1.sndcdn.com/artworks-a-large.jpg")
        self.assertEqual(second.source_url,
                         "https://soundcloud.com/spoonemusic/zeds-dead-symphony")


if __name__ == "__main__":
    unittest.main()
