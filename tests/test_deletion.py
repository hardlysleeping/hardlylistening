"""Deleting playlists and albums, who is allowed to, and the disk ceiling.

Three rules are being pinned here because each one is a way to lose data:

* the drive must never be filled past ``disk_limit_pct`` (95%),
* a member may only delete what they themselves added; anything unclaimed is
  admin-only, and "no member" (local mode) is the admin case,
* a delete may only unlink files inside the library root — a wrong root must not
  turn "delete album" into anything else.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
import deletion
import security


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "library"
        self.root.mkdir()
        patcher = mock.patch.object(db, "DB_PATH", os.path.join(self.dir.name, "d.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = db.Store()
        self.addCleanup(lambda: self.store.conn.close())
        self.store.set_settings({"root": str(self.root)})
        security.Security().schema(self.store)   # member_items / member_playlists

    def track(self, title, *, release_id=None, file=True, provider="local"):
        tid = self.store.track(provider, title, title, artist_name="Artist",
                               album="Album", release_id=release_id)
        if file:
            path = self.root / "Artist" / "Album" / f"{title}.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 2048)
            with self.store.lock:
                self.store.conn.execute("UPDATE tracks SET file=?, size=200 WHERE id=?",
                                        (str(path), tid))
                self.store.conn.commit()
        return tid

    def playlist(self, title, tracks, source="https://soundcloud.com/set/x"):
        pid = self.store.playlist(title, "soundcloud", source)
        for position, tid in enumerate(tracks, start=1):
            self.store.add_to_playlist(pid, tid, position)
        return pid

    def release(self, rid="r1"):
        return self.store.conn.execute(
            "SELECT id FROM releases WHERE release_id=?", (rid,)).fetchone()[0] \
            if self.store.conn.execute("SELECT id FROM releases WHERE release_id=?",
                                       (rid,)).fetchone() else self._make_release(rid)

    def _make_release(self, rid):
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO releases (provider,release_id,title,first_seen)"
                " VALUES ('apple',?,?,'2026-01-01')", (rid, rid))
            self.store.conn.commit()
        return self.store.conn.execute(
            "SELECT id FROM releases WHERE release_id=?", (rid,)).fetchone()[0]


class PlaylistDelete(Case):
    def test_deletes_records_files_and_reports_bytes(self):
        a, b = self.track("One"), self.track("Two")
        pid = self.playlist("Set", [a, b])
        before = {p.name for p in self.root.rglob("*.mp3")}
        report = deletion.apply(self.store, "playlist", pid, delete_files=True)
        self.assertEqual(report["deleting"], 2)
        self.assertEqual(report["deleted_files"], 2)
        self.assertEqual(report["bytes"], 4096)
        self.assertEqual({p.name for p in self.root.rglob("*.mp3")}, set())
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) c FROM tracks").fetchone()["c"], 0)
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) c FROM playlists").fetchone()["c"], 0)
        self.assertTrue(before)

    def test_files_are_kept_when_not_asked_for(self):
        tid = self.track("Only")
        pid = self.playlist("Set", [tid])
        report = deletion.apply(self.store, "playlist", pid, delete_files=False)
        self.assertEqual(report["deleting"], 1)
        self.assertEqual(report["deleted_files"], 0)
        self.assertEqual(len(list(self.root.rglob("*.mp3"))), 1)

    def test_a_track_in_two_playlists_survives_one_delete(self):
        tid = self.track("Shared")
        first = self.playlist("A", [tid], source="https://soundcloud.com/set/a")
        second = self.playlist("B", [tid], source="https://soundcloud.com/set/b")
        report = deletion.apply(self.store, "playlist", first, delete_files=True)
        self.assertEqual(report["kept"], 1)
        self.assertEqual(report["deleting"], 0)
        self.assertEqual(len(list(self.root.rglob("*.mp3"))), 1)
        remaining = self.store.conn.execute(
            "SELECT COUNT(*) c FROM playlist_tracks WHERE playlist_id=?",
            (second,)).fetchone()["c"]
        self.assertEqual(remaining, 1)

    def test_plan_is_a_dry_run(self):
        tid = self.track("Seed")
        pid = self.playlist("Set", [tid])
        report = deletion.plan(self.store, "playlist", pid)
        self.assertEqual(report["deleting"], 1)
        self.assertEqual(len(list(self.root.rglob("*.mp3"))), 1)
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) c FROM tracks").fetchone()["c"], 1)

    def test_files_outside_the_root_are_never_unlinked(self):
        tid = self.track("Outside", file=False)
        stray = Path(self.dir.name) / "elsewhere.mp3"
        stray.write_bytes(b"y" * 10)
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?",
                                    (str(stray), tid))
            self.store.conn.commit()
        pid = self.playlist("Set", [tid])
        report = deletion.apply(self.store, "playlist", pid, delete_files=True)
        self.assertTrue(stray.exists(), "a file outside the root must be left alone")
        self.assertEqual(report["outside_root"], [str(stray)])
        self.assertEqual(report["deleted_files"], 0)


class AlbumDelete(Case):
    def test_deletes_the_release_and_its_files(self):
        rid = self._make_release("album-1")
        a = self.track("A", release_id=rid)
        b = self.track("B", release_id=rid)
        report = deletion.apply(self.store, "release", rid, delete_files=True)
        self.assertEqual(report["deleting"], 2)
        self.assertEqual(report["deleted_files"], 2)
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) c FROM releases").fetchone()["c"], 0)

    def test_a_track_kept_by_a_playlist_survives_the_album_delete(self):
        rid = self._make_release("album-2")
        a = self.track("A", release_id=rid)
        b = self.track("B", release_id=rid)
        self.playlist("Keep", [a])
        report = deletion.apply(self.store, "release", rid, delete_files=True)
        self.assertEqual(report["deleting"], 1)
        self.assertEqual(report["kept"], 1)
        self.assertEqual(len(list(self.root.rglob("*.mp3"))), 1)

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            deletion.plan(self.store, "artist", 1)


class TrackDelete(Case):
    """One song off the shelf: file and visibility, never identity."""

    def test_the_file_goes_and_the_row_stays_as_a_tombstone(self):
        tid = self.track("One")
        path = self.root / "Artist" / "Album" / "One.mp3"
        report = deletion.delete_track(self.store, tid)
        self.assertTrue(report["removed_file"])
        self.assertFalse(path.exists())
        row = self.store.track_by_id(tid)
        self.assertTrue(row["deleted"])
        self.assertEqual(row["title"], "One")
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) c FROM tracks").fetchone()["c"], 1)

    def test_a_deleted_song_is_invisible_to_the_library_and_the_queue(self):
        tid = self.track("Gone")
        pid = self.playlist("Set", [tid])
        deletion.delete_track(self.store, tid)
        self.assertEqual(self.store.library(), [])
        self.assertEqual(self.store.library(include_pending=True), [])
        playlists = [p for p in self.store.state()["playlists"] if p["id"] == pid]
        self.assertEqual(playlists[0]["tracks"], [])
        self.assertEqual(self.store.stats()["tracks"], 0)

    def test_a_queued_retry_is_cancelled_so_it_cannot_come_back(self):
        tid = self.track("Later", file=False)
        self.store.enqueue(tid, "local", "")
        deletion.delete_track(self.store, tid)
        self.assertIsNone(self.store.job_for_track(tid))
        # A playlist refresh that lists it again is refused, not downloaded again.
        self.assertIsNone(self.store.enqueue(tid, "local", ""))
        self.assertIsNone(self.store.job_for_track(tid))

    def test_a_file_another_row_still_plays_is_not_unlinked(self):
        one, two = self.track("One"), self.track("Two")
        shared = self.root / "Artist" / "Album" / "One.mp3"
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?",
                                    (str(shared), two))
            self.store.conn.commit()
        report = deletion.delete_track(self.store, one)
        self.assertFalse(report["removed_file"])
        self.assertEqual(report["kept_by"], two)
        self.assertTrue(shared.exists())

    def test_a_file_outside_the_root_is_never_unlinked(self):
        tid = self.track("Outside", file=False)
        stray = Path(self.dir.name) / "elsewhere.mp3"
        stray.write_bytes(b"y" * 10)
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?",
                                    (str(stray), tid))
            self.store.conn.commit()
        report = deletion.delete_track(self.store, tid)
        self.assertFalse(report["removed_file"])
        self.assertTrue(stray.exists())

    def test_restore_puts_it_back_without_downloading_a_file_that_is_still_here(self):
        tid = self.track("Back")
        deletion.delete_track(self.store, tid, delete_files=False)
        report = deletion.restore_track(self.store, tid)
        self.assertTrue(report["restored"])
        self.assertFalse(report["queued"])
        self.assertEqual([t["id"] for t in self.store.library()], [tid])

    def test_restore_queues_a_download_when_the_audio_is_gone(self):
        tid = self.track("Again", provider="soundcloud")
        deletion.delete_track(self.store, tid)
        report = deletion.restore_track(self.store, tid)
        self.assertTrue(report["restored"])
        self.assertTrue(report["queued"])
        self.assertFalse(self.store.track_by_id(tid)["deleted"])

    def test_an_import_is_copied_back_from_where_it_came_from(self):
        tid = self.track("Imported")
        source = Path(self.dir.name) / "downloads" / "Imported.mp3"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"z" * 4096)
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET source_url=? WHERE id=?",
                                    (str(source), tid))
            self.store.conn.commit()
        path = Path(self.store.track_by_id(tid)["file"])
        deletion.delete_track(self.store, tid)
        report = deletion.restore_track(self.store, tid)
        self.assertTrue(report["copied"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), source.read_bytes())

    def test_an_import_with_no_source_left_is_not_restored(self):
        tid = self.track("Nowhere")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET source_url=? WHERE id=?",
                                    (str(Path(self.dir.name) / "gone.mp3"), tid))
            self.store.conn.commit()
        deletion.delete_track(self.store, tid)
        report = deletion.restore_track(self.store, tid)
        self.assertFalse(report["restored"])
        self.assertIn("cannot be restored", report["error"])
        self.assertTrue(self.store.track_by_id(tid)["deleted"])


class Permissions(Case):
    admin = {"id": "m-admin", "role": "admin", "email": "a@b"}
    alice = {"id": "m-alice", "role": "member", "email": "alice@b"}
    bob = {"id": "m-bob", "role": "member", "email": "bob@b"}

    def test_admin_may_delete_anything(self):
        pid = self.playlist("Set", [self.track("T")])
        self.assertTrue(security.Security.may_delete(self.store, self.admin, "playlist", pid))

    def test_a_member_may_delete_only_their_own(self):
        mine = self.playlist("Mine", [self.track("T")], source="https://soundcloud.com/set/mine")
        theirs = self.playlist("Theirs", [self.track("U")], source="https://soundcloud.com/set/theirs")
        security.Security.claim(self.store, "playlist", mine, "m-alice")
        security.Security.claim(self.store, "playlist", theirs, "m-bob")
        self.assertTrue(security.Security.may_delete(self.store, self.alice, "playlist", mine))
        self.assertFalse(security.Security.may_delete(self.store, self.alice, "playlist", theirs))
        self.assertFalse(security.Security.may_delete(self.store, self.bob, "playlist", mine))

    def test_unclaimed_things_are_admin_only(self):
        pid = self.playlist("Nobody", [self.track("T")])
        self.assertFalse(security.Security.may_delete(self.store, self.alice, "playlist", pid))
        self.assertTrue(security.Security.may_delete(self.store, self.admin, "playlist", pid))

    def test_no_member_means_no(self):
        pid = self.playlist("Set", [self.track("T")])
        self.assertFalse(security.Security.may_delete(self.store, None, "playlist", pid))

    def test_tracks_imported_by_a_member_belong_to_them(self):
        tid = self.track("Upload")
        security.Security.claim(self.store, "track", tid, "m-alice")
        self.assertEqual(security.Security.owner_of(self.store, "track", tid), "m-alice")
        self.assertFalse(security.Security.may_delete(self.store, self.bob, "track", tid))


class DiskCeiling(Case):
    def test_default_limit_is_95_percent(self):
        self.assertEqual(self.store.setting_float("disk_limit_pct", 95.0), 95.0)

    def test_blocked_once_used_passes_the_limit(self):
        with mock.patch.object(db, "disk_usage", return_value=(100.0, 4.0, 96.0)), \
             mock.patch.object(db, "disk_free_gb", return_value=4.0):
            stats = self.store.stats()
        self.assertTrue(stats["disk_blocked"])
        self.assertEqual(stats["disk_used_pct"], 96.0)

    def test_blocked_at_exactly_the_limit(self):
        # 95 % used is the ceiling, not a target: nothing new starts there.
        with mock.patch.object(db, "disk_usage", return_value=(100.0, 5.0, 95.0)), \
             mock.patch.object(db, "disk_free_gb", return_value=5.0):
            stats = self.store.stats()
        self.assertTrue(stats["disk_blocked"])

    def test_just_under_the_limit_still_downloads(self):
        with mock.patch.object(db, "disk_usage", return_value=(100.0, 5.5, 94.9)), \
             mock.patch.object(db, "disk_free_gb", return_value=5.5):
            stats = self.store.stats()
        self.assertFalse(stats["disk_blocked"])

    def test_free_space_floor_still_stops_it(self):
        with mock.patch.object(db, "disk_usage", return_value=(100.0, 1.0, 40.0)), \
             mock.patch.object(db, "disk_free_gb", return_value=1.0):
            stats = self.store.stats()
        self.assertTrue(stats["disk_blocked"])

    def test_limit_is_configurable(self):
        self.store.set_settings({"disk_limit_pct": "80"})
        with mock.patch.object(db, "disk_usage", return_value=(100.0, 15.0, 85.0)), \
             mock.patch.object(db, "disk_free_gb", return_value=15.0):
            self.assertTrue(self.store.stats()["disk_blocked"])


if __name__ == "__main__":
    unittest.main()
