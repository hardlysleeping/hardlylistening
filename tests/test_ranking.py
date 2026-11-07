"""The ranking learns from the candidates the owner actually uses.

The formula is a guess; a human pressing "Use" is a fact. These tests pin that the
fact wins, that it is scoped to the artist rather than leaking everywhere, and that
his version tolerance changes the penalty instead of being ignored.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import db
import ranking
import security


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(db, "DB_PATH", os.path.join(self.dir.name, "r.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        security.Security().schema(self.store)
        ranking.schema(self.store)

    def track(self, title, artist="Alison Wonderland"):
        return {"id": 1, "title": title, "artist_name": artist}

    QUERY = "Alison Wonderland New Day (Fredrick Remix)"
    RIGHT = {"url": "https://soundcloud.com/a/new-day-fredrick",
             "artist": "Alison Wonderland", "title": "New Day (Fredrick Remix)"}
    WRONG = {"url": "https://soundcloud.com/b/new-day-bumpy",
             "artist": "Bumpÿ", "title": "Alison Wonderland - NEW DAY（Bumpÿ Remix）"}


class RecordPicks(Case):
    def test_a_use_creates_the_pick_once(self):
        track = self.track("New Day (Fredrick Remix)")
        self.assertTrue(ranking.record(self.store, track, self.RIGHT))
        self.assertFalse(ranking.record(self.store, track, self.RIGHT), "same pick twice is a no-op")
        rows = ranking.history(self.store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], self.RIGHT["url"])
        self.assertEqual(rows[0]["uploader"], "Alison Wonderland")
        self.assertEqual(rows[0]["version_match"], 1)

    def test_a_pick_of_a_different_version_is_recorded_as_loose(self):
        ranking.record(self.store, self.track("New Day (Fredrick Remix)"), self.WRONG)
        self.assertEqual(ranking.history(self.store)[0]["version_match"], 0)

    def test_no_url_is_not_a_pick(self):
        self.assertFalse(ranking.record(self.store, self.track("X"), {"url": ""}))
        self.assertEqual(ranking.history(self.store), [])


class LearnedRanking(Case):
    def test_the_chosen_upload_goes_first_and_scores_full(self):
        track = self.track("New Day (Fredrick Remix)")
        ranking.record(self.store, track, self.WRONG)          # he picked the odd one
        ranked = ranking.rank(self.store, self.QUERY, [dict(self.RIGHT), dict(self.WRONG)])
        self.assertEqual(ranked[0]["url"], self.WRONG["url"],
                         "what he already approved must not be re-litigated")
        self.assertEqual(ranked[0]["match"], 1.0)
        self.assertEqual({c["url"] for c in ranked}, {self.RIGHT["url"], self.WRONG["url"]})
        # The winner would score 0.66 on the formula alone; his pick makes it 1.0.
        self.assertLess(ranking.score(self.store, self.QUERY, dict(self.WRONG), 
                        {"chosen": set(), "uploaders": set(), "mismatch_factor": 0.7,
                         "picks": 0, "loose": False}), 0.9)

    def test_uploader_affinity_stays_inside_the_artist(self):
        ranking.record(self.store, self.track("New Day (Fredrick Remix)"), self.RIGHT)
        same = {"url": "https://soundcloud.com/a/other-song", "artist": "Alison Wonderland",
                "title": "Other Song"}
        other_artist_track = {"id": 2, "title": "Other Song", "artist_name": "Someone Else"}
        before = ranking.score(self.store, "Someone Else Other Song", same)
        ranking.record(self.store, other_artist_track,
                       {"url": "https://soundcloud.com/c/x", "artist": "Someone Else", "title": "Other Song"})
        # A trusted uploader for a *different* artist must not be lifted.
        plain = {"url": "https://soundcloud.com/a/other-song", "artist": "Alison Wonderland",
                 "title": "Other Song"}
        lifted = ranking.score(self.store, "Alison Wonderland Other Song", plain)
        self.assertLessEqual(before, lifted)

    def test_version_tolerance_follows_his_picks(self):
        track = self.track("New Day (Fredrick Remix)")
        # Only ever picks the named version -> a different one is pushed down hard.
        ranking.record(self.store, track, self.RIGHT)
        strict = ranking.score(self.store, self.QUERY, dict(self.WRONG))
        ranking.record(self.store, track, dict(self.WRONG, url="https://soundcloud.com/b/2"))
        # He starts accepting a different version -> the penalty loosens.
        loose = ranking.score(self.store, self.QUERY, dict(self.WRONG, url="https://soundcloud.com/b/3"))
        self.assertLess(strict, loose, "penalty must loosen when he picks the other version")

    def test_explain_reports_what_it_learned(self):
        ranking.record(self.store, self.track("New Day (Fredrick Remix)"), self.RIGHT)
        info = ranking.explain(self.store, self.QUERY)
        self.assertEqual(info["picks"], 1)
        self.assertIn("Alison Wonderland", info["trusted_uploaders"])
        self.assertEqual(info["mismatch_factor"], ranking.STRICT_MISMATCH)

    def test_taste_is_per_artist(self):
        ranking.record(self.store, self.track("New Day (Fredrick Remix)"), self.RIGHT)
        ranking.record(self.store, self.track("Song", "Someone Else"),
                       {"url": "https://soundcloud.com/c/y", "artist": "Someone Else", "title": "Song"})
        taste = ranking.taste(self.store, {"artist_name": "Alison Wonderland"})
        self.assertEqual(len(taste), 1)
        self.assertEqual(taste[0]["url"], self.RIGHT["url"])


class EngineUsesIt(Case):
    def queue_for(self):
        from engine.queue import Queue
        queue = Queue(self.store)
        self.addCleanup(lambda: queue.stop())
        return queue

    def job_and_track(self):
        from engine.queue import Queue
        queue = self.queue_for()
        tid = self.store.track("apple", "t1", "New Day (Fredrick Remix)",
                               artist_name="Alison Wonderland", album="A")
        job = self.store.enqueue(tid, "soundcloud", "")   # no direct source: must search
        jid = job if isinstance(job, int) else job.get("id")
        self.store.set_settings({"ai_apple_enabled": "0"})
        return queue, dict(self.store.job(jid)), self.store.track_by_id(tid)

    def test_a_pick_below_the_floor_still_wins(self):
        queue, job, track = self.job_and_track()
        weak = dict(self.RIGHT, match=0.2)          # under MIN_ACCEPT
        ranking.record(self.store, track, weak)
        with mock.patch("engine.queue.ytdlp.search_candidates", return_value=[dict(weak)]) as search:
            url = queue._resolve_source(job, track)
        self.assertEqual(url, weak["url"], "an approved upload must be reachable again")
        self.assertEqual(search.call_args.kwargs.get("min_accept"), 0.0,
                         "history means search wider than the floor")

    def test_the_pick_beats_a_higher_scoring_stranger(self):
        queue, job, track = self.job_and_track()
        stranger = dict(self.WRONG, match=0.8)
        ranking.record(self.store, track, dict(self.RIGHT, match=0.3))
        with mock.patch("engine.queue.ytdlp.search_candidates",
                        return_value=[dict(stranger), dict(self.RIGHT, match=0.3)]):
            url = queue._resolve_source(job, track)
        self.assertEqual(url, self.RIGHT["url"])


class JudgeSeesTheTaste(Case):
    def test_accepted_uploads_are_passed_as_evidence(self):
        from downloaders import ai_match
        ranking.record(self.store, dict(id=1, title="New Day (Fredrick Remix)",
                                        artist_name="Alison Wonderland"), self.RIGHT)
        examples = ranking.taste(self.store, {"artist_name": "Alison Wonderland"})
        sent = {}

        def fake_urlopen(req, timeout=0):
            sent.update(json.loads(req.data.decode()))
            import io
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps({"decision": "accept", "reason": "same version"})}}]}).encode())

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
             mock.patch.object(ai_match.request, "urlopen", side_effect=fake_urlopen):
            result = ai_match.judge({"title": "New Day (Fredrick Remix)", "artist_name": "Alison Wonderland",
                                     "album": "A", "duration": 200},
                                    {"title": "New Day (Fredrick Remix)", "artist": "Alison Wonderland",
                                     "duration": 200}, examples=examples)
        self.assertEqual(result.decision, "accept")
        body = json.loads(sent["messages"][1]["content"])
        self.assertEqual(body["previously_accepted"][0]["title"], "New Day (Fredrick Remix)")
        self.assertIn("untrusted data", sent["messages"][0]["content"])


class RouteRecordsIt(Case):
    def test_using_a_candidate_records_the_pick(self):
        import re
        import server
        tid = self.store.track("apple", "t2", "New Day (Fredrick Remix)",
                               artist_name="Alison Wonderland", album="A")
        job = self.store.enqueue(tid, "soundcloud", "")   # no direct source: must search
        jid = job if isinstance(job, int) else job.get("id")
        self.store.update_job(jid, status="review", candidates=json.dumps([dict(self.RIGHT)]))
        handler = mock.Mock()
        handler.json = mock.Mock()
        handler.body = mock.Mock(return_value={"url": self.RIGHT["url"]})
        with mock.patch.object(server, "boot", return_value=(self.store, mock.Mock())):
            server.Handler.do_track_source(handler, tid)
        picks = ranking.history(self.store)
        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["url"], self.RIGHT["url"])


if __name__ == "__main__":
    unittest.main()
