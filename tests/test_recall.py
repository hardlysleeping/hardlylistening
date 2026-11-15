"""Finding the upload, not just scoring it.

The reported failure was recall, not ordering: the wanted YouTube video
(``iJmO6bpMf6s``, "Friction - What I Need") was never in the candidate pool, so
the only thing on offer was a differently-named upload of the same song. Two
causes, both pinned here:

* three results per engine is too shallow to hold the answer;
* ``"(feat. Poppy Baskcomb)"`` in the query pushed the real upload out of the
  results entirely — dropping the credit puts it back at number one.
"""
import re
import unittest
from pathlib import Path

from downloaders import ytdlp


class TitleForSearching(unittest.TestCase):
    def test_feature_credit_is_dropped(self):
        self.assertEqual(ytdlp.search_title("What I Need (feat. Poppy Baskcomb)"),
                         "What I Need")
        self.assertEqual(ytdlp.search_title("What I Need (ft Poppy Baskcomb)"),
                         "What I Need")
        self.assertEqual(ytdlp.search_title("What I Need (featuring Poppy Baskcomb)"),
                         "What I Need")
        self.assertEqual(ytdlp.search_title("What I Need feat. Poppy Baskcomb"),
                         "What I Need")

    def test_version_names_are_kept_because_they_find_the_upload(self):
        # Only guest credits are removed: "(Fredrick Remix)" is how you search for
        # that mix, so throwing it away would make recall worse, not better.
        self.assertEqual(ytdlp.search_title("New Day (Fredrick Remix)"),
                         "New Day Fredrick Remix")

    def test_a_title_that_is_only_a_credit_survives(self):
        # There must always be something left to search for.
        for title in ("Feat. Someone", "(feat. Someone)"):
            self.assertTrue(ytdlp.search_title(title).strip(), title)

    def test_plain_titles_are_untouched(self):
        self.assertEqual(ytdlp.search_title("Siren"), "Siren")
        self.assertEqual(ytdlp.search_title("Feel the Vibration"), "Feel the Vibration")


class QueryVariants(unittest.TestCase):
    def test_artist_first_then_title_first_then_title(self):
        track = {"title": "What I Need (feat. Poppy Baskcomb)", "artist_name": "Friction"}
        self.assertEqual(ytdlp.query_variants(track),
                         ["Friction What I Need", "What I Need Friction", "What I Need"])

    def test_no_artist_leaves_one_query(self):
        self.assertEqual(ytdlp.query_variants({"title": "Siren"}), ["Siren"])

    def test_duplicates_collapse(self):
        self.assertEqual(ytdlp.query_variants({"title": "Siren", "artist_name": ""}),
                         ["Siren"])

    def test_the_pick_key_keeps_the_version_words(self):
        # Scoring and searching want different strings: searching drops the guest
        # credit to find the upload, scoring keeps the whole title so a remix is
        # not confused with the original.
        import ranking
        track = {"title": "What I Need (feat. Poppy Baskcomb)", "artist_name": "Friction"}
        self.assertEqual(ranking.query_for(track),
                         "Friction What I Need (feat. Poppy Baskcomb)")
        self.assertEqual(ytdlp.query_variants(track)[0], "Friction What I Need")


class PoolDepth(unittest.TestCase):
    def test_both_engines_are_asked_for_ten(self):
        source = Path(__file__).resolve().parent.parent / "downloaders" / "ytdlp.py"
        text = source.read_text()
        self.assertIn('_search("scsearch10"', text)
        self.assertIn('_search("ytsearch10"', text)
        self.assertNotIn('scsearch3', text, "three results could not hold the answer")

    def test_engine_label_has_no_count(self):
        source = Path(__file__).resolve().parent.parent / "downloaders" / "ytdlp.py"
        text = source.read_text()
        self.assertIn('re.sub(r"\\d+$", "", spec)', text)


class QueueUsesThem(unittest.TestCase):
    def test_the_queue_searches_every_variant(self):
        source = (Path(__file__).resolve().parent.parent / "engine" / "queue.py").read_text()
        self.assertIn("ytdlp.query_variants(track)", source)
        self.assertIn("for variant in variants:", source)
        # and stops asking once something is unmistakable, rather than spending
        # every query every time
        self.assertIn('if any((h.get("match") or 0) >= AUTO_ACCEPT for h in pool):', source)


if __name__ == "__main__":
    unittest.main()
