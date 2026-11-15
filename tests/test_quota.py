"""Per-member storage allowance: attribution, totals, and the cap."""
import os
import tempfile
import unittest
from pathlib import Path

from db import Store
from library import quota
from security import Security


class Quota(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.store = Store(Path(self.tmp.name) / "q.sqlite")
        Security().schema(self.store)
        with self.store.lock:
            for uid, role in (("m-ann", "member"), ("m-bob", "member"), ("m-boss", "admin")):
                self.store.conn.execute(
                    "INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                    (uid, "", role, uid))
            self.store.conn.commit()

    def tearDown(self):
        self.store.conn.close()
        self.tmp.cleanup()

    def track(self, title, size):
        with self.store.lock:
            cur = self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,size,added)"
                " VALUES('soundcloud',?,?,'A',?,0)", (title, title, size))
            self.store.conn.commit()
            return cur.lastrowid

    def playlist(self, title):
        with self.store.lock:
            cur = self.store.conn.execute(
                "INSERT INTO playlists(title,kind,added) VALUES(?,'url',0)", (title,))
            self.store.conn.commit()
            return cur.lastrowid

    def claim(self, kind, target, member):
        Security.claim(self.store, kind, target, member)

    def test_direct_claim_counts_bytes(self):
        first = self.track("one", 3 * 1024 ** 3)
        self.track("loose", 5 * 1024 ** 3)
        self.claim("track", first, "m-ann")
        self.assertEqual(quota.used(self.store, "m-ann"), 3 * 1024 ** 3)
        self.assertEqual(quota.used(self.store, "m-bob"), 0, "unclaimed track belongs to nobody")

    def test_playlist_claim_counts_its_tracks(self):
        pid = self.playlist("set")
        for index, size in enumerate((1024 ** 3, 2 * 1024 ** 3), start=1):
            tid = self.track(f"t{index}", size)
            with self.store.lock:
                self.store.conn.execute(
                    "INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES(?,?,?)",
                    (pid, tid, index))
                self.store.conn.commit()
        self.claim("playlist", pid, "m-bob")
        self.assertEqual(quota.used(self.store, "m-bob"), 3 * 1024 ** 3)

    def test_direct_claim_wins_over_playlist(self):
        pid = self.playlist("set")
        tid = self.track("shared", 2 * 1024 ** 3)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES(?,?,1)",
                (pid, tid))
            self.store.conn.commit()
        self.claim("playlist", pid, "m-bob")
        self.claim("track", tid, "m-ann")
        self.assertEqual(quota.used(self.store, "m-ann"), 2 * 1024 ** 3)
        self.assertEqual(quota.used(self.store, "m-bob"), 0, "a track is counted once")

    def test_member_is_refused_past_ten_gb_admin_is_not(self):
        self.assertEqual(quota.cap(self.store), 10 * 1024 ** 3)
        big = self.track("big", 9 * 1024 ** 3)
        self.claim("track", big, "m-ann")
        self.assertTrue(quota.refuse(self.store, {"id": "m-ann", "role": "member"},
                                     2 * 1024 ** 3).startswith("storage allowance reached"))
        self.assertEqual(quota.refuse(self.store, {"id": "m-ann", "role": "member"},
                                      512 * 1024 ** 2), "", "half a gig still fits")
        self.assertEqual(quota.refuse(self.store, {"id": "m-boss", "role": "admin"},
                                      500 * 1024 ** 3), "", "admins are measured, never blocked")
        self.assertEqual(quota.refuse(self.store, None, 1), "", "anonymous/local adds are not capped")

    def test_cap_is_edited_from_settings_in_gb(self):
        # The admin-facing control: a GB number in Settings, not raw bytes.
        self.store.set_settings({"member_quota_gb": "25"})
        self.assertEqual(quota.cap(self.store), 25 * 1024 ** 3)
        tid = self.track("x", 26 * 1024 ** 3)
        self.claim("track", tid, "m-bob")
        self.assertTrue(quota.refuse(self.store, {"id": "m-bob", "role": "member"}).startswith(
            "storage allowance reached"))
        self.store.set_settings({"member_quota_gb": "50"})
        self.assertEqual(quota.refuse(self.store, {"id": "m-bob", "role": "member"}), "",
                         "raising the limit unblocks them again")

    def test_zero_means_unlimited(self):
        self.store.set_settings({"member_quota_gb": "0"})
        self.assertEqual(quota.cap(self.store), 0)
        tid = self.track("huge", 500 * 1024 ** 3)
        self.claim("track", tid, "m-bob")
        self.assertEqual(quota.refuse(self.store, {"id": "m-bob", "role": "member"}), "")

    def test_broken_value_falls_back_to_ten_gb(self):
        self.store.set_settings({"member_quota_gb": "banana"})
        self.assertEqual(quota.cap(self.store), 10 * 1024 ** 3)


if __name__ == "__main__":
    unittest.main()
