"""Activity feed and member profiles: stacking, joins, renames, votes."""
import os
import tempfile
import time
import unittest
from pathlib import Path

import activity
import profiles
import social
from db import Store
from security import Security, Denied


class Activity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.store = Store(Path(self.tmp.name) / "a.sqlite")
        Security().schema(self.store)
        social.schema(self.store)
        with self.store.lock:
            for uid, role, name in (("m-ann", "member", "Ann"), ("m-bob", "member", "Bob"),
                                    ("m-boss", "admin", "Boss")):
                self.store.conn.execute(
                    "INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                    (uid, "", role, name))
            self.store.conn.commit()

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def track(self, title):
        with self.store.lock:
            cur = self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,size,added)"
                " VALUES('soundcloud',?,?,'Artist',0,0)", (title, title))
            self.store.conn.commit()
            return cur.lastrowid

    def test_boost_slot_swaps_without_extending_and_expires_after_24h(self):
        first, second = self.track('first'), self.track('second')
        start = 10_000.0
        initial = activity.boost(self.store, 'm-ann', first, now=start)
        self.assertEqual(initial, {'track_id': first, 'boosted_at': start,
                                   'expires_at': start + activity.BOOST_TTL, 'swapped': False})
        swapped = activity.boost(self.store, 'm-ann', second, now=start + 60)
        self.assertTrue(swapped['swapped'])
        self.assertEqual(swapped['track_id'], second)
        self.assertEqual(swapped['boosted_at'], start)
        self.assertEqual(swapped['expires_at'], start + activity.BOOST_TTL)
        same = activity.boost(self.store, 'm-ann', second, now=start + 120)
        self.assertFalse(same['swapped'])
        self.assertTrue(same['already_active'])
        self.assertEqual(same['expires_at'], start + activity.BOOST_TTL)
        activity.boost(self.store, 'm-bob', first, now=start + 200)
        activity.record(self.store, activity.KIND_JOIN, member_id='m-boss')
        feed = activity.feed(self.store, now=start + 250)
        self.assertEqual([i['kind'] for i in feed[:2]], ['boost', 'boost'])
        self.assertEqual(feed[2]['kind'], 'day')
        self.assertEqual(feed[0]['member_id'], 'm-bob')
        self.assertEqual(feed[1]['track']['id'], second)
        self.assertEqual(activity.current_boost(self.store, 'm-ann', now=start + activity.BOOST_TTL - 1)['track_id'], second)
        self.assertIsNone(activity.current_boost(self.store, 'm-ann', now=start + activity.BOOST_TTL))
        renewed = activity.boost(self.store, 'm-ann', first, now=start + activity.BOOST_TTL)
        self.assertEqual(renewed['boosted_at'], start + activity.BOOST_TTL)
        self.assertEqual(renewed['expires_at'], start + activity.BOOST_TTL * 2)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM song_boosts WHERE member_id=?', ('m-ann',)).fetchone()[0], 1)

    def test_boost_rejects_unknown_tracks_and_bad_ids(self):
        with self.assertRaises(Denied) as error:
            activity.boost(self.store, 'm-ann', 12345, now=1)
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(Denied) as error:
            activity.boost(self.store, 'm-ann', True, now=1)
        self.assertEqual(error.exception.code, 400)

    def test_join_and_rename_events_are_recorded(self):
        activity.record(self.store, activity.KIND_JOIN, member_id="m-ann", detail="Ann")
        activity.record(self.store, activity.KIND_NAME, member_id="m-ann", detail="Ann B")
        items = activity.feed(self.store)
        self.assertEqual(len(items), 1, "one member, one day: one card")
        card = items[0]
        self.assertEqual(card["kind"], "day")
        self.assertEqual(card["member"], "Ann", "the new name comes from the member row")
        self.assertEqual([o["kind"] for o in card["groups"]["other"]], ["name", "join"])
        self.assertEqual(card["groups"]["other"][0]["detail"], "Ann B")
        self.assertEqual(card["counts"], {"other": 2})

    def test_listens_fold_into_one_day_card(self):
        first, second, third = (self.track(t) for t in ("one", "two", "three"))
        for tid in (first, second, third):
            activity.listen(self.store, {"id": "m-ann"}, tid)
        items = activity.feed(self.store)
        self.assertEqual(len(items), 1, "one member's day is one card, not one per sitting")
        card = items[0]
        self.assertEqual(card["counts"], {"listen": 3})
        self.assertEqual([t["title"] for t in card["groups"]["listen"]],
                         ["three", "two", "one"], "newest first, all of them kept")
        self.assertEqual(card["day"], activity.day_key(card["ts"]))

    def test_same_track_again_soon_is_not_a_second_event(self):
        tid = self.track("loop")
        activity.listen(self.store, {"id": "m-ann"}, tid)
        activity.listen(self.store, {"id": "m-ann"}, tid)
        items = activity.feed(self.store)
        self.assertEqual(items[0]["counts"]["listen"], 1)
        # outside the repeat window it counts again
        with self.store.lock:
            self.store.conn.execute(
                "UPDATE activity SET ts=? WHERE kind='listen'",
                (time.time() - activity.LISTEN_REPEAT - 5,))
            self.store.conn.commit()
        activity.listen(self.store, {"id": "m-ann"}, tid)
        items = activity.feed(self.store)
        self.assertEqual(items[0]["counts"]["listen"], 2)

    def test_a_different_day_is_a_different_card(self):
        first, second = self.track("a"), self.track("b")
        activity.listen(self.store, {"id": "m-ann"}, first)
        with self.store.lock:
            self.store.conn.execute("UPDATE activity SET ts=?", (time.time() - 2 * 86400,))
            self.store.conn.commit()
        activity.listen(self.store, {"id": "m-ann"}, second)
        cards = activity.feed(self.store)
        self.assertEqual(len(cards), 2, "yesterday and today are separate cards")
        self.assertEqual([c["counts"]["listen"] for c in cards], [1, 1])
        self.assertNotEqual(cards[0]["day"], cards[1]["day"])

    def test_each_member_gets_its_own_card(self):
        first, second = self.track("a"), self.track("b")
        activity.listen(self.store, {"id": "m-ann"}, first)
        activity.listen(self.store, {"id": "m-bob"}, second)
        cards = activity.feed(self.store)
        self.assertEqual(len(cards), 2)
        self.assertEqual({c["member"] for c in cards}, {"Ann", "Bob"})
        self.assertEqual({c["counts"]["listen"] for c in cards}, {1})

    def test_a_new_action_lifts_the_member_card_to_the_top(self):
        first, second = self.track("a"), self.track("b")
        activity.listen(self.store, {"id": "m-ann"}, first)
        activity.listen(self.store, {"id": "m-bob"}, second)
        self.assertEqual([c["member"] for c in activity.feed(self.store)], ["Bob", "Ann"])
        activity.record(self.store, activity.KIND_ADD, member_id="m-ann", track_id=first)
        cards = activity.feed(self.store)
        self.assertEqual([c["member"] for c in cards], ["Ann", "Bob"])
        self.assertEqual(cards[0]["counts"], {"listen": 1, "add": 1},
                         "the new action joins the same day card")


class Profiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.store = Store(Path(self.tmp.name) / "p.sqlite")
        Security().schema(self.store)
        social.schema(self.store)
        with self.store.lock:
            for uid, role, name in (("m-ann", "member", "Ann"), ("m-boss", "admin", "Boss")):
                self.store.conn.execute(
                    "INSERT INTO members(id,email,role,display_name,profile_icon)"
                    " VALUES(?,?,?,?,'star')", (uid, "", role, name))
            self.store.conn.commit()

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def track(self, title):
        with self.store.lock:
            cur = self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,size,added)"
                " VALUES('soundcloud',?,?,'Artist',0,0)", (title, title))
            self.store.conn.commit()
            return cur.lastrowid

    def test_admin_profile_is_public_like_any_other(self):
        tid = self.track("admin song")
        activity.listen(self.store, {"id": "m-boss"}, tid)
        found = profiles.profile(self.store, "m-boss")
        self.assertEqual(found["member"]["role"], "admin")
        self.assertEqual(found["member"]["icon"], "star")
        self.assertEqual(found["listens"][0]["title"], "admin song")
        self.assertEqual(found["counts"]["listens"], 1)

    def test_history_covers_listens_adds_playlists_and_votes(self):
        tid = self.track("song")
        activity.listen(self.store, {"id": "m-ann"}, tid)
        activity.record(self.store, activity.KIND_ADD, member_id="m-ann", track_id=tid)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO playlists(title,kind,added) VALUES('mix','manual',0)")
            pid = self.store.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.store.conn.execute("INSERT INTO member_playlists VALUES(?,?)", (pid, "m-ann"))
            self.store.conn.commit()
        activity.record(self.store, activity.KIND_PLAYLIST_NEW, member_id="m-ann",
                        playlist_id=pid, detail="mix")
        member = {"id": "m-ann", "role": "member"}
        social.summary(self.store, "track", str(tid), member)      # ensure tables ready
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO social_votes(kind,target,member_id,value) VALUES('track',?,?,1)",
                (str(tid), "m-ann"))
            self.store.conn.commit()
        found = profiles.profile(self.store, "m-ann")
        self.assertEqual(found["listens"][0]["title"], "song")
        self.assertEqual(found["adds"][0]["title"], "song")
        self.assertEqual(found["playlists"][0]["title"], "mix")
        self.assertEqual(found["votes"][0]["label"], "song", "a vote names what it voted on")
        self.assertEqual(found["votes"][0]["value"], 1)

    def test_unknown_member_is_none(self):
        self.assertIsNone(profiles.profile(self.store, "m-nobody"))


class Votes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.store = Store(Path(self.tmp.name) / "v.sqlite")
        Security().schema(self.store)
        social.schema(self.store)
        with self.store.lock:
            for uid, name in (("m-ann", "Ann"), ("m-bob", "Bob")):
                self.store.conn.execute(
                    "INSERT INTO members(id,email,role,display_name,profile_icon)"
                    " VALUES(?,?,'member',?,'headphones')", (uid, "", name))
            self.store.conn.commit()

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def test_voters_are_named_not_anonymous(self):
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO social_votes(kind,target,member_id,value) VALUES('track','7','m-ann',1)")
            self.store.conn.execute(
                "INSERT INTO social_votes(kind,target,member_id,value) VALUES('track','7','m-bob',-1)")
            self.store.conn.commit()
        data = social.summary(self.store, "track", "7", {"id": "m-boss", "role": "admin"})
        names = {v["name"]: v["value"] for v in data["voters"]}
        self.assertEqual(names, {"Ann": 1, "Bob": -1})
        self.assertEqual({v["icon"] for v in data["voters"]}, {"headphones"},
                         "the profile icon travels with the vote")


if __name__ == "__main__":
    unittest.main()
