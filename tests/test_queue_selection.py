"""Isolated automatic selection regressions; never acquire media."""
from unittest.mock import patch
import test_ai_match as fixtures
from test_ai_match import HIT
from downloaders import ai_match


class SelectionTests(fixtures.IntegrationTests):
    """The gate, not the formula — so ranking is a pass-through here and the
    stubbed `match` values stay meaningful."""

    def setUp(self):
        super().setUp()
        from unittest.mock import patch
        passthrough = lambda store, query, cands: sorted(
            [dict(c) for c in cands], key=lambda c: -(c.get("match") or 0))
        patcher = patch("engine.queue.ranking.rank", side_effect=passthrough)
        patcher.start()
        self.addCleanup(patcher.stop)
        history = patch("engine.queue.ranking.has_history", return_value=True)
        history.start()
        self.addCleanup(history.stop)
    def test_rejected_first_accepts_second(self):
        second = dict(HIT, url=HIT['url'] + '-second')
        with patch('engine.queue.ytdlp.search_candidates', return_value=[HIT, second]), patch(
                'engine.queue.ai_match.judge', side_effect=[ai_match.Judgment('reject', 'wrong'),
                                                          ai_match.Judgment('accept', 'clear metadata')]):
            self.assertEqual(self.queue._resolve_source(self.job, self.track), second['url'])

    def test_bounded_pool_never_downloads_uncertainty(self):
        pool = [dict(HIT, url=HIT['url'] + str(i)) for i in range(12)]
        with patch('engine.queue.ytdlp.search_candidates', return_value=pool), patch(
                'engine.queue.ai_match.judge', return_value=ai_match.Judgment('uncertain', 'ambiguous')) as judge:
            self.assertIsNone(self.queue._resolve_source(self.job, self.track))
            self.assertEqual(judge.call_count, 6)

    def test_below_floor_pool_is_only_for_review(self):
        weak = dict(HIT, match=.1)
        with patch('engine.queue.ytdlp.query_variants', return_value=['Song Fixture']), patch(
                'engine.queue.ytdlp.search_candidates', side_effect=[[], [weak]]), patch(
                'engine.queue.ai_match.judge') as judge:
            self.assertIsNone(self.queue._resolve_source(dict(self.job, resolved_url=HIT['url']), self.track))
            judge.assert_not_called()
            self.assertEqual(self.store.job_for_track(self.track['id'])['resolved_url'], '')

    def test_a_ninety_percent_match_is_taken_without_asking(self):
        """The rule the owner asked for: >= 90% is not a judgement call."""
        strong = dict(HIT, match=0.94)
        with patch('engine.queue.ytdlp.search_candidates', return_value=[strong]), patch(
                'engine.queue.ai_match.judge') as judge:
            self.assertEqual(self.queue._resolve_source(self.job, self.track), strong['url'])
            judge.assert_not_called()          # no model call to second-guess 94%
            row = self.store.job_for_track(self.track['id'])
            self.assertEqual(row['resolved_url'], strong['url'])
            self.assertEqual(row['status'], 'resolving')

    def test_a_ninety_percent_match_beats_a_judge_reject(self):
        rejected = dict(HIT, match=0.91)
        # The judge sees the candidates below the line first; the strong one still
        # wins even though the model said no.
        weak = dict(HIT, url=HIT['url'] + '-weak', match=0.5)
        with patch('engine.queue.ytdlp.search_candidates', return_value=[rejected, weak]), patch(
                'engine.queue.ai_match.judge', return_value=ai_match.Judgment('reject', 'different version')):
            self.assertEqual(self.queue._resolve_source(self.job, self.track), rejected['url'])
            self.assertEqual(self.store.job_for_track(self.track['id'])['resolved_url'], rejected['url'])

    def test_just_under_ninety_still_asks(self):
        edge = dict(HIT, match=0.899)
        with patch('engine.queue.ytdlp.search_candidates', return_value=[edge]), patch(
                'engine.queue.ai_match.judge', return_value=ai_match.Judgment('reject', 'unsure')):
            self.assertIsNone(self.queue._resolve_source(self.job, self.track))
            self.assertEqual(self.store.job_for_track(self.track['id'])['status'], 'review')

    def test_all_uncertain_stays_review(self):
        with patch('engine.queue.ytdlp.search_candidates', return_value=[HIT]), patch(
                'engine.queue.ai_match.judge', return_value=ai_match.Judgment('uncertain', 'ambiguous')):
            self.assertIsNone(self.queue._resolve_source(self.job, self.track))
            self.assertEqual(self.store.job_for_track(self.track['id'])['status'], 'review')
