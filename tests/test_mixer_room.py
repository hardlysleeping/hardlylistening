"""Shared mixer room: one authoritative board for every browser in the room.

Nothing here touches the live database or the live service: the room is pure
in-memory state, and the HTTP layer is driven by stub handlers.
"""
import json
import queue
import threading
import time
import unittest
from unittest import mock

import mixer_room
from security import Denied


def member(uid, name, icon=""):
    return {"id": uid, "display_name": name, "profile_icon": icon, "role": "member"}


class Base(unittest.TestCase):
    def setUp(self):
        self.room = mixer_room.Room()
        self.patch = mock.patch.object(mixer_room, "ROOM", self.room)
        self.patch.start()
        self.addCleanup(self.patch.stop)


class Controls(Base):
    def test_every_control_has_a_default_and_a_range(self):
        self.assertEqual(len(mixer_room.CONTROLS), 18)
        self.assertEqual(self.room.values["cross"], 50.0)
        self.assertEqual(self.room.values["A.volume"], 70.0)
        self.assertEqual(self.room.values["master"], 80.0)

    def test_values_are_clamped_and_stepped(self):
        self.assertEqual(mixer_room.clamp("A.low", -99), -20.0)
        self.assertEqual(mixer_room.clamp("A.low", 99), 12.0)
        self.assertEqual(mixer_room.clamp("A.low", 5.3), 5.5)      # 0.5 dB steps
        self.assertEqual(mixer_room.clamp("cross", 120), 100.0)
        self.assertIsNone(mixer_room.clamp("not.a.control", 1))
        self.assertIsNone(mixer_room.clamp("cross", "loud"))

    def test_only_changed_values_travel_but_every_touched_control_is_claimed(self):
        alex = member("a", "Alex")
        patch = self.room.set_values(alex, {"A.low": -4, "A.mid": 0})
        self.assertEqual(patch["values"], {"A.low": -4.0}, "A.mid was already 0")
        self.assertEqual(sorted(patch["claims"]), ["A.low", "A.mid"])

    def test_unknown_keys_are_ignored(self):
        patch = self.room.set_values(member("a", "Alex"), {"nope": 5, "A.low": 2})
        self.assertEqual(patch["values"], {"A.low": 2.0})


class Claims(Base):
    def test_a_claim_expires_on_its_own(self):
        alex = member("a", "Alex")
        self.room.set_values(alex, {"A.high": 3})
        self.assertIn("A.high", self.room.snapshot()["claims"])
        with mock.patch.object(mixer_room, "CLAIM_TTL", 0.05):
            time.sleep(0.08)
            self.room._expire()
        self.assertNotIn("A.high", self.room.snapshot()["claims"], "a closed tab must not hold a fader")

    def test_release_only_frees_your_own_control(self):
        alex, sam = member("a", "Alex"), member("b", "Sam")
        self.room.set_values(alex, {"A.high": 3})
        self.room.release(sam, ["A.high"])
        self.assertIn("A.high", self.room.claims, "Sam cannot free Alex's control")
        self.room.release(alex, ["A.high"])
        self.assertNotIn("A.high", self.room.claims)


class Presence(Base):
    def test_two_people_never_share_a_colour(self):
        self.room.touch(member("a", "Alex"))
        self.room.touch(member("b", "Sam"))
        colours = [m["colour"] for m in self.room.online()]
        self.assertEqual(len(set(colours)), 2, colours)

    def test_a_colour_sticks_while_the_member_is_here(self):
        alex = member("a", "Alex")
        self.room.touch(alex)
        first = self.room.online()[0]["colour"]
        self.room.set_values(alex, {"A.low": 1})
        self.room.touch(alex)
        self.assertEqual(self.room.online()[0]["colour"], first)

    def test_a_member_that_falls_silent_leaves_the_roster(self):
        with mock.patch.object(mixer_room, "MEMBER_TTL", 0.05):
            self.room.touch(member("a", "Alex"))
            time.sleep(0.08)
            self.assertEqual(self.room.online(), [])

    def test_presence_arrives_with_the_next_patch(self):
        token, snapshot = self.room.subscribe()
        self.room.touch(member("a", "Alex"))
        self.room.set_values(member("a", "Alex"), {"cross": 60})
        event = self.room.next_event(token, timeout=1)
        self.assertEqual([m["name"] for m in event["members"]], ["Alex"])


class Decks(Base):
    def test_the_clock_runs_while_playing_and_stops_when_paused(self):
        alex = member("a", "Alex")
        self.room.set_deck(alex, "A", {"track_id": 5, "position": 30})
        self.room.set_deck(alex, "A", {"playing": True})
        time.sleep(0.25)
        self.assertGreater(self.room.position("A"), 30.2)
        self.room.set_deck(alex, "A", {"playing": False})
        frozen = self.room.position("A")
        time.sleep(0.15)
        self.assertAlmostEqual(self.room.position("A"), frozen, places=3)

    def test_rate_changes_the_clock(self):
        alex = member("a", "Alex")
        self.room.set_deck(alex, "A", {"track_id": 5, "position": 0, "playing": True, "rate": 2.0})
        time.sleep(0.2)
        self.assertGreater(self.room.position("A"), 0.3)

    def test_a_new_track_rewinds_the_clock(self):
        alex = member("a", "Alex")
        self.room.set_deck(alex, "A", {"track_id": 5, "playing": True, "position": 90})
        self.room.set_deck(alex, "A", {"track_id": 6})
        self.assertLess(self.room.position("A"), 0.05, "the new track starts at the top")
        self.assertEqual(self.room.decks["A"]["track_id"], 6)


class Queues(Base):
    def test_add_refuses_duplicates_and_respects_the_cap(self):
        alex = member("a", "Alex")
        self.room.set_queue(alex, "A", {"action": "add", "ids": [1, 2, 2]})
        self.assertEqual(self.room.queues["A"], [1, 2])
        self.room.set_queue(alex, "A", {"action": "add", "ids": list(range(3, 40))})
        self.assertEqual(len(self.room.queues["A"]), mixer_room.MAX_QUEUE)

    def test_remove_clear_and_set(self):
        alex = member("a", "Alex")
        self.room.set_queue(alex, "B", {"action": "set", "ids": [4, 5, 6]})
        self.room.set_queue(alex, "B", {"action": "remove", "ids": [5]})
        self.assertEqual(self.room.queues["B"], [4, 6])
        self.room.set_queue(alex, "B", {"action": "clear"})
        self.assertEqual(self.room.queues["B"], [])

    def test_queues_are_side_scoped(self):
        alex = member("a", "Alex")
        self.room.set_queue(alex, "A", {"action": "add", "ids": [9]})
        self.assertEqual(self.room.queues["A"], [9])
        self.assertEqual(self.room.queues["B"], [])


class Fanout(Base):
    def test_a_patch_reaches_every_subscriber(self):
        one, _ = self.room.subscribe()
        two, _ = self.room.subscribe()
        self.room.set_values(member("a", "Alex"), {"A.low": -2})
        for token in (one, two):
            event = self.room.next_event(token, timeout=1)
            self.assertEqual(event["type"], "patch")
            self.assertEqual(event["values"], {"A.low": -2.0})

    def test_a_slow_subscriber_gets_a_full_state_instead_of_a_gap(self):
        token, _ = self.room.subscribe()
        box = self.room._subscribers[token]
        while True:                              # overfill the 256-slot buffer
            try:
                box.put_nowait({"type": "patch"})
            except queue.Full:
                break
        self.room.set_values(member("a", "Alex"), {"A.low": -2})
        event = self.room.next_event(token, timeout=1)
        self.assertEqual(event["type"], "state")
        self.assertIn("values", event)

    def test_unsubscribing_stops_delivery(self):
        token, _ = self.room.subscribe()
        self.room.unsubscribe(token)
        self.assertIsNone(self.room.next_event(token, timeout=0.01))

    def test_revision_increases_with_every_change(self):
        before = self.room.rev
        self.room.set_values(member("a", "Alex"), {"cross": 20})
        self.room.set_values(member("a", "Alex"), {"cross": 21})
        self.assertGreaterEqual(self.room.rev, before + 2)


class Stub:
    """Just enough of server.Handler for dispatch()."""

    command = "POST"

    def __init__(self, path="/api/mixer", body=None):
        self.path = path
        self._body = body or {}
        self.sent = {}
        self.status = None
        self.headers = {}
        self.client_address = ("127.0.0.1", 1234)

    def body(self):
        return self._body

    def json(self, data, code=200):
        self.sent = data
        self.status = code


class Dispatch(Base):
    def test_a_value_post_returns_ok_and_moves_the_room(self):
        handler = Stub(body={"op": "values", "values": {"A.mid": 4}})
        mixer_room.dispatch(handler, member("a", "Alex"), "/api/mixer")
        self.assertTrue(handler.sent["ok"])
        self.assertEqual(self.room.values["A.mid"], 4.0)

    def test_an_unknown_operation_is_refused(self):
        handler = Stub(body={"op": "explode"})
        with self.assertRaises(Denied) as raised:
            mixer_room.dispatch(handler, member("a", "Alex"), "/api/mixer")
        self.assertEqual(raised.exception.code, 400)

    def test_an_unknown_path_is_refused(self):
        handler = Stub(body={})
        with self.assertRaises(Denied) as raised:
            mixer_room.dispatch(handler, member("a", "Alex"), "/api/mixer/nope")
        self.assertEqual(raised.exception.code, 404)

    def test_get_returns_the_snapshot_with_the_caller(self):
        handler = Stub(path="/api/mixer")
        handler.command = "GET"
        mixer_room.dispatch(handler, member("a", "Alex"), "/api/mixer")
        self.assertEqual(handler.sent["you"], "a")
        self.assertIn("values", handler.sent)

    def test_queue_and_deck_ops_route_by_side(self):
        mixer_room.dispatch(Stub(body={"op": "queue", "side": "B", "action": "add", "ids": [3]}),
                            member("a", "Alex"), "/api/mixer")
        mixer_room.dispatch(Stub(body={"op": "deck", "side": "B", "track_id": 3}),
                            member("a", "Alex"), "/api/mixer")
        self.assertEqual(self.room.queues["B"], [3])
        self.assertEqual(self.room.decks["B"]["track_id"], 3)


class Stream(unittest.TestCase):
    """The SSE route: first event is a full state, later ones are patches."""

    def test_stream_writes_state_then_patches(self):
        room = mixer_room.Room()
        with mock.patch.object(mixer_room, "ROOM", room):
            written = []

            class Wfile:
                def write(self, data):
                    written.append(data.decode())

                def flush(self):
                    pass

            class Handler:
                command = "GET"
                path = "/api/mixer/stream"
                wfile = Wfile()
                close_connection = False

                class connection_:
                    def settimeout(self, value):
                        pass

                connection = connection_()

                def send_response(self, code):
                    self.code = code

                def send_header(self, key, value):
                    pass

                def end_headers(self):
                    pass

            handler = Handler()

            def later():
                time.sleep(0.15)
                room.set_values(member("a", "Alex"), {"cross": 10})
                time.sleep(0.2)
                for open_token in list(room._subscribers):
                    room.unsubscribe(open_token)

            with mock.patch.object(mixer_room, "ROOM", room):
                threading.Thread(target=later, daemon=True).start()
                mixer_room.stream(handler, member("a", "Alex"))

        body = "".join(written)
        self.assertIn("retry: 1500", body)
        self.assertIn('"type":"state"', body)
        self.assertRegex(body, r'"type":"patch".*"cross":\s*10', body[:500])
        self.assertEqual(handler.code, 200)


if __name__ == "__main__":
    unittest.main()
