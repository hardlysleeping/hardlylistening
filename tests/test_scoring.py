"""How candidates are ranked.

Two properties are load-bearing, because the >=90% auto-accept leans on them:

* a correctly split artist/title row must not score below an uploader who repeated
  the artist inside the title, and
* the version named in brackets ("Fredrick Remix") must decide between two remixes
  of the same song, instead of being deleted before scoring.

The regression these pin: for "Alison Wonderland — New Day (Fredrick Remix)" the
two other people's remixes scored 0.939 and 0.933 while the right upload scored
0.900 — lowest of the three.
"""
import unittest

from downloaders import ytdlp


QUERY = "Alison Wonderland New Day (Fredrick Remix)"


class VersionRanking(unittest.TestCase):
    def test_the_requested_version_wins(self):
        right = {"artist": "Alison Wonderland", "title": "New Day (Fredrick Remix)"}
        other = {"artist": "Bumpÿ", "title": "Alison Wonderland - NEW DAY（Bumpÿ Remix）[WINNER]"}
        other2 = {"artist": "Hellobeen", "title": "Alison Wonderland - New Day (Hellobeen Remix)"}
        scores = [ytdlp.hit_score(QUERY, hit) for hit in (right, other, other2)]
        self.assertGreater(scores[0], scores[1], "the exact upload must outrank a different remix")
        self.assertGreater(scores[0], scores[2])
        self.assertGreaterEqual(scores[0], 0.90, "the right answer must clear the auto-accept line")
        self.assertLess(scores[1], 0.90, "a different remix must not clear it")

    def test_full_width_brackets_are_read(self):
        self.assertIn("bump", ytdlp._version_tokens("Song（Bumpÿ Remix）"))
        self.assertIn("fredrick", ytdlp._version_tokens("Song (Fredrick Remix)"))

    def test_generic_bracket_words_are_not_versions(self):
        for text in ("Song (Official Video)", "Song (Audio)", "Song (Lyric Video)",
                     "Song [HD]", "Song (Remix)", "Song (Extended Mix)"):
            self.assertEqual(ytdlp._version_tokens(text), set(), text)

    def test_a_plain_original_is_not_killed_by_a_versioned_request(self):
        # Different, but not "another remix": it stays findable, just below.
        right = {"artist": "Alison Wonderland", "title": "New Day (Fredrick Remix)"}
        plain = {"artist": "Alison Wonderland", "title": "New Day"}
        self.assertGreater(ytdlp.hit_score(QUERY, right), ytdlp.hit_score(QUERY, plain))
        self.assertGreater(ytdlp.hit_score(QUERY, plain), 0.55)


class CoverageSplit(unittest.TestCase):
    def test_artist_in_the_title_is_not_a_bonus(self):
        split = {"artist": "Guns N' Roses", "title": "November Rain"}
        stuffed = {"artist": "Guns N' Roses", "title": "Guns N' Roses - November Rain"}
        a = ytdlp.hit_score("Guns N' Roses November Rain", split)
        b = ytdlp.hit_score("Guns N' Roses November Rain", stuffed)
        self.assertGreaterEqual(a, b, "a clean artist/title split must not lose to a stuffed title")

    def test_an_official_video_upload_is_not_treated_as_a_remix(self):
        studio = {"artist": "Guns N' Roses", "title": "November Rain"}
        video = {"artist": "Guns N' Roses", "title": "November Rain (Official Video)"}
        a = ytdlp.hit_score("Guns N' Roses November Rain", studio)
        b = ytdlp.hit_score("Guns N' Roses November Rain", video)
        self.assertAlmostEqual(a, b, places=2)

    def test_a_live_version_when_studio_was_asked_is_pushed_down(self):
        studio = ytdlp.hit_score("Guns N' Roses November Rain",
                                 {"artist": "Guns N' Roses", "title": "November Rain"})
        live = ytdlp.hit_score("Guns N' Roses November Rain",
                               {"artist": "Guns N' Roses", "title": "November Rain (Live at Wembley)"})
        self.assertLess(live, studio)


if __name__ == "__main__":
    unittest.main()
