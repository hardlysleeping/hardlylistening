"""The read layer over `listen_events`: windows, tops, streak and the yearly top 100."""
import os
import tempfile
import time
import unittest
from pathlib import Path

import stats
from db import Store
from security import Security


class Stats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.store = Store(Path(self.tmp.name) / "a.sqlite")
        Security().schema(self.store)
        with self.store.lock:
            for uid, name in (("m-ann", "Ann"), ("m-bob", "Bob")):
                self.store.conn.execute(
                    "INSERT INTO members(id,email,role,display_name) VALUES(?,?,'member',?)",
                    (uid, "", name))
            self.store.conn.commit()
        stats.clear_year_cache()

    def tearDown(self):
        self.store.close() if hasattr(self.store, "close") else None
        self.store.conn.close()
        self.tmp.cleanup()

    def track(self, title, artist="Artist", album="", duration=180, artwork=""):
        with self.store.lock:
            cur = self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,"
                " duration,artwork,size,added) VALUES('soundcloud',?,?,?,?,?,?,0,0)",
                (title, title, artist, album, duration, artwork))
            self.store.conn.commit()
            return cur.lastrowid

    def play(self, track_id, when, member="m-ann", source="web"):
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO listen_events(ts,member_id,track_id,source) VALUES(?,?,?,?)",
                (when, member, track_id, source))
            self.store.conn.commit()

    # -- windows ---------------------------------------------------------------
    def test_rolling_windows_are_relative_to_now(self):
        now = 1_800_000_000.0
        start, end = stats.window("week", now)
        self.assertEqual(end, now)
        self.assertEqual(now - start, 7 * 86400)
        self.assertEqual(stats.window("month", now)[0], now - 30 * 86400)
        self.assertEqual(stats.window("year", now)[0], now - 365 * 86400)
        self.assertEqual(stats.window("all", now), (0.0, now))

    def test_a_play_outside_the_window_is_not_counted(self):
        now = time.time()
        in_window = self.track("inside")
        old = self.track("old")
        self.play(in_window, now - 3600)
        self.play(old, now - 8 * 86400)
        self.assertEqual(stats.totals(self.store, "m-ann", *stats.window("week", now))["plays"], 1)
        self.assertEqual(stats.totals(self.store, "m-ann", *stats.window("month", now))["plays"], 2)

    def test_a_play_exactly_on_the_boundary_belongs_to_the_window(self):
        now = 1_800_000_000.0
        track = self.track("edge")
        self.play(track, now - 7 * 86400)
        self.assertEqual(stats.totals(self.store, "m-ann", *stats.window("week", now))["plays"], 1)
        self.play(track, now - 7 * 86400 - 1)
        self.assertEqual(stats.totals(self.store, "m-ann", *stats.window("week", now))["plays"], 1)

    # -- totals and tops -------------------------------------------------------
    def test_totals_distinct_and_unknown_duration(self):
        now = time.time()
        known, unknown = self.track("known", duration=300), self.track("no dur", duration=0)
        self.play(known, now - 60)
        self.play(known, now - 50)
        self.play(unknown, now - 40)
        total = stats.totals(self.store, "m-ann", *stats.window("week", now))
        self.assertEqual(total["plays"], 3)
        self.assertEqual(total["seconds"], 600)
        self.assertEqual(total["untimed_plays"], 1)
        self.assertEqual(total["tracks"], 2)

    def test_top_tracks_break_a_tie_on_the_most_recent_play(self):
        now = time.time()
        first, second = self.track("first"), self.track("second")
        self.play(first, now - 100)
        self.play(second, now - 50)
        order = [r["title"] for r in
                 stats.top_tracks(self.store, "m-ann", *stats.window("week", now))]
        self.assertEqual(order, ["second", "first"])

    def test_top_artists_and_albums_group_across_tracks(self):
        now = time.time()
        a = self.track("a", artist="Aphex Twin", album="Drukqs")
        b = self.track("b", artist="Aphex Twin", album="Drukqs")
        c = self.track("c", artist="Burial", album="Untrue")
        for track, when in ((a, 10), (b, 20), (c, 30)):
            self.play(track, now - when)
        artists = stats.top_artists(self.store, "m-ann", *stats.window("week", now))
        self.assertEqual([r["artist_name"] for r in artists], ["Aphex Twin", "Burial"])
        self.assertEqual(artists[0]["plays"], 2)
        albums = stats.top_albums(self.store, "m-ann", *stats.window("week", now))
        self.assertEqual(albums[0]["album"], "Drukqs")
        self.assertEqual(albums[0]["tracks"], 2)

    def test_another_members_plays_are_not_mine(self):
        now = time.time()
        track = self.track("shared")
        self.play(track, now - 60, member="m-bob")
        self.assertEqual(stats.totals(self.store, "m-ann", *stats.window("week", now))["plays"], 0)
        self.assertEqual(stats.totals(self.store, "m-bob", *stats.window("week", now))["plays"], 1)

    # -- streak and histogram --------------------------------------------------
    def test_streak_survives_today_but_not_a_missed_day(self):
        now = time.time()
        track = self.track("t")
        day = 86400
        for offset in (0, 1, 2):
            self.play(track, now - offset * day)
        self.assertEqual(stats.streak(self.store, "m-ann", now)["current"], 3)
        # The run is over once two whole days have passed with nothing played.
        self.assertEqual(stats.streak(self.store, "m-ann", now + 2 * day)["current"], 0)
        self.assertEqual(stats.streak(self.store, "m-ann", now)["longest"], 3)

    def test_histogram_has_a_zero_for_a_quiet_day(self):
        # Anchored to midday: `now - 60` one minute after midnight belongs to yesterday, which
        # is how this test failed every time the suite happened to run across midnight.
        now = time.mktime((time.localtime().tm_year, time.localtime().tm_mon,
                           time.localtime().tm_mday, 12, 0, 0, 0, 0, -1))
        track = self.track("t")
        self.play(track, now - 60)
        bars = stats.day_histogram(self.store, "m-ann", *stats.window("week", now), days=7)
        self.assertEqual(len(bars), 7)
        self.assertEqual(bars[-1]["plays"], 1)
        self.assertEqual(sum(b["plays"] for b in bars[:-1]), 0)

    def test_new_artists_only_counts_a_first_ever_play_inside_the_window(self):
        now = time.time()
        early = self.track("early", artist="Old Hand")
        late = self.track("late", artist="Newcomer")
        self.play(early, now - 20 * 86400)
        self.play(late, now - 60)
        fresh = stats.new_artists(self.store, "m-ann", *stats.window("week", now))
        self.assertEqual([r["artist_name"] for r in fresh], ["Newcomer"])

    # -- yearly top 100 --------------------------------------------------------
    def test_year_window_is_a_calendar_year(self):
        start, end = stats.year_window(2026)
        self.assertEqual(time.localtime(start)[:4], (2026, 1, 1, 0, 0, 0)[:4])
        self.assertEqual(time.localtime(end)[:4], (2027, 1, 1, 0, 0, 0)[:4])

    def test_year_tracks_are_capped_at_the_limit_and_cached(self):
        start, _ = stats.year_window(2026)
        ids = [self.track("song %d" % i) for i in range(5)]
        for index, track in enumerate(ids):
            self.play(track, start + 1000 + index)
        top = stats.year_tracks(self.store, "m-ann", 2026, limit=3)
        self.assertEqual(len(top), 3)
        # A play added afterwards is not visible until the cache is dropped: that is the
        # "refreshed at most once a day" the playlist promises.
        newest = self.track("brand new")
        self.play(newest, start + 5000)
        self.assertEqual(stats.year_tracks(self.store, "m-ann", 2026, limit=3), top)
        stats.clear_year_cache()
        self.assertIn(newest, stats.year_tracks(self.store, "m-ann", 2026, limit=3))

    def test_year_cards_list_only_years_with_plays(self):
        start, _ = stats.year_window(2026)
        track = self.track("t", artwork="/api/track/1/artwork")
        self.play(track, start + 10)
        cards = stats.year_cards(self.store, "m-ann")
        self.assertEqual([c["year"] for c in cards], [2026])
        self.assertEqual(cards[0]["plays"], 1)
        self.assertEqual(cards[0]["artwork"], ["/api/track/1/artwork"])

    def test_summary_for_an_empty_history_is_honest(self):
        summary = stats.summary(self.store, "m-ann", "week")
        self.assertTrue(summary["empty"])
        self.assertEqual(summary["top_tracks"], [])
        self.assertIsNone(summary["favourite_artist"])
        self.assertEqual(summary["years"], [])
    # -- the community chart ---------------------------------------------------
    def test_calendar_windows_start_where_the_calendar_does(self):
        # Wednesday noon, local: the chart's "today" is midnight to now, "this week"
        # starts on Monday, "this month" on the 1st and "this year" on January 1st — not a
        # trailing 24h/7d/30d/365d. All time has no start at all.
        now = time.mktime((2026, 3, 18, 12, 0, 0, 0, 0, -1))
        midnight = time.mktime((2026, 3, 18, 0, 0, 0, 0, 0, -1))
        monday = time.mktime((2026, 3, 16, 0, 0, 0, 0, 0, -1))
        first = time.mktime((2026, 3, 1, 0, 0, 0, 0, 0, -1))
        january = time.mktime((2026, 1, 1, 0, 0, 0, 0, 0, -1))
        self.assertEqual(stats.calendar_window("day", now), (midnight, now))
        self.assertEqual(stats.calendar_window("week", now), (monday, now))
        self.assertEqual(stats.calendar_window("month", now), (first, now))
        self.assertEqual(stats.calendar_window("year", now), (january, now))
        self.assertEqual(stats.calendar_window("all", now), (0.0, now))

    def test_all_time_counts_a_play_from_any_year(self):
        now = time.time()
        old = self.track("old", artist="Long gone")
        fresh = self.track("fresh")
        self.play(old, now - 900 * 86400)     # two and a half years back
        self.play(fresh, now - 60)
        # Equal plays, so the tie is broken on the most recent one.
        self.assertEqual([r["title"] for r in
                          stats.popular_tracks(self.store, *stats.calendar_window("all", now))],
                         ["fresh", "old"])
        # This year does not reach it, which is what makes the two options different.
        self.assertEqual([r["title"] for r in
                          stats.popular_tracks(self.store, *stats.calendar_window("year", now))],
                         ["fresh"])

    def test_a_chart_period_that_is_not_one_is_rejected(self):
        for wrong in ("decade", "", "Day"):
            with self.assertRaises(ValueError):
                stats.calendar_window(wrong)
        # The two long options are chart windows too, not trailing spans of their own.
        for right in ("year", "all"):
            self.assertEqual(stats.calendar_window(right)[1] > 0, True)

    def test_popular_counts_every_member_not_one(self):
        now = time.time()
        shared = self.track("shared", artist="Both")
        solo = self.track("solo", artist="Ann")
        self.play(shared, now - 300, member="m-ann")
        self.play(shared, now - 200, member="m-bob")
        self.play(solo, now - 100, member="m-bob")
        rows = stats.popular_tracks(self.store, now - 3600, now)
        self.assertEqual([r["title"] for r in rows], ["shared", "solo"])
        self.assertEqual(rows[0]["plays"], 2)
        self.assertEqual(rows[0]["listeners"], 2)
        self.assertEqual(rows[1]["listeners"], 1)
        self.assertEqual(rows[0]["artist_name"], "Both")

    def test_popular_ignores_plays_before_the_window(self):
        now = time.time()
        old = self.track("old")
        fresh = self.track("fresh")
        self.play(old, now - 90000)
        self.play(old, now - 89000)
        self.play(fresh, now - 60)
        rows = stats.popular_tracks(self.store, now - 3600, now)
        self.assertEqual([r["title"] for r in rows], ["fresh"])

    def test_popular_is_capped_at_the_limit(self):
        now = time.time()
        ids = [self.track("song %d" % i) for i in range(4)]
        for track in ids:
            self.play(track, now - 60)
        self.assertEqual(len(stats.popular_tracks(self.store, now - 3600, now, limit=2)), 2)
