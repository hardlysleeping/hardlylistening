"""Deleting playlists and albums, who is allowed to, the trash, and the disk ceiling.

Four rules are being pinned here because each one is a way to lose data:

* the drive must never be filled past ``disk_limit_pct`` (95%),
* a member may only delete what they themselves added; anything unclaimed is
  admin-only, and "no member" (local mode) is the admin case,
* a delete may only move/unlink files inside the library root — a wrong root must
  not turn "delete album" into anything else,
* a delete moves the file to ``.trash`` instead of erasing it (HL-306), so the
  only irreversible step is emptying the trash — and the ceiling purges it
  oldest-first, logging every file it takes.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
import deletion
import security
from test_security import SecurityHTTP


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
        self.assertEqual(report["trashed"], 2)
        self.assertEqual(report["bytes"], 4096)
        # Nothing is erased: both files are in `.trash`, and neither is where it was.
        held = list(self.root.rglob("*.mp3"))
        self.assertEqual(len(held), 2)
        for path in held:
            self.assertIn(deletion.TRASH_NAME, path.parts)
            self.assertTrue(path.name.endswith(("One.mp3", "Two.mp3")))
        self.assertFalse((self.root / "Artist" / "Album" / "One.mp3").exists())
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
        live = [p for p in self.root.rglob("*.mp3")
                if deletion.TRASH_NAME not in p.parts]
        self.assertEqual([p.name for p in live], ["A.mp3"])

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
        self.assertTrue(report["trashed"])
        self.assertFalse(path.exists())
        row = self.store.track_by_id(tid)
        self.assertTrue(row["deleted"])
        self.assertEqual(row["title"], "One")
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) c FROM tracks").fetchone()["c"], 1)
        self.assertEqual(self.store.stats()["trash_count"], 1)

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
        # The audio is only genuinely gone once the trash is emptied; a file still
        # sitting there comes back by rename and needs no download.
        deletion.empty_trash(self.store)
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
        # With the trash emptied there is nothing to rename back, so the import's own
        # source is the remaining way home.
        deletion.empty_trash(self.store)
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
        deletion.empty_trash(self.store)
        report = deletion.restore_track(self.store, tid)
        self.assertFalse(report["restored"])
        self.assertIn("cannot be restored", report["error"])
        self.assertTrue(self.store.track_by_id(tid)["deleted"])


class Trash(Case):
    """A delete keeps the file until something has to give (HL-306)."""

    def trashed(self):
        return [dict(r) for r in self.store.conn.execute(
            "SELECT * FROM library_trash ORDER BY id")]

    def test_a_deleted_file_is_moved_not_erased(self):
        tid = self.track("Kept")
        path = Path(self.store.track_by_id(tid)["file"])
        original = path.read_bytes()
        deletion.delete_track(self.store, tid)
        self.assertFalse(path.exists())
        rows = self.trashed()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], str(path))
        self.assertEqual(rows[0]["track_id"], tid)
        self.assertEqual(rows[0]["bytes"], len(original))
        held = Path(rows[0]["trash_path"])
        self.assertTrue(held.is_file())
        self.assertEqual(held.read_bytes(), original)
        self.assertEqual(self.store.stats()["trash_count"], 1)
        self.assertEqual(self.store.stats()["trash_bytes"], len(original))

    def test_restoring_a_song_takes_its_own_file_back_without_downloading(self):
        tid = self.track("Back", provider="soundcloud")
        path = Path(self.store.track_by_id(tid)["file"])
        original = path.read_bytes()
        deletion.delete_track(self.store, tid)
        report = deletion.restore_track(self.store, tid)
        self.assertTrue(report["restored"])
        self.assertTrue(report["from_trash"])
        self.assertFalse(report["queued"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.trashed(), [])
        self.assertEqual(self.store.stats()["trash_count"], 0)
        self.assertIsNone(self.store.job_for_track(tid))

    def test_a_playlist_delete_can_be_undone_file_by_file(self):
        tid = self.track("Gone")
        path = Path(self.store.track_by_id(tid)["file"])
        pid = self.playlist("Set", [tid])
        deletion.apply(self.store, "playlist", pid, delete_files=True)
        self.assertFalse(path.exists())
        row = self.trashed()[0]
        report = deletion.restore_one(self.store, row["id"])
        self.assertTrue(report["restored"])
        self.assertEqual(report["path"], str(path))
        self.assertTrue(path.is_file())
        self.assertEqual(self.trashed(), [])

    def test_restore_one_refuses_a_path_outside_the_library(self):
        tid = self.track("Elsewhere")
        path = Path(self.store.track_by_id(tid)["file"])
        deletion.delete_track(self.store, tid)
        row = self.trashed()[0]
        outside = Path(self.dir.name) / "outside.mp3"
        with self.store.lock:
            self.store.conn.execute("UPDATE library_trash SET path=? WHERE id=?",
                                    (str(outside), row["id"]))
            self.store.conn.commit()
        with self.assertRaises(ValueError):
            deletion.restore_one(self.store, row["id"])
        self.assertFalse(outside.exists())
        self.assertFalse(path.exists())
        self.assertTrue(Path(row["trash_path"]).is_file(), "the file stays where it is")

    def test_emptying_the_trash_is_the_one_irreversible_step(self):
        first, second = self.track("One"), self.track("Two")
        deletion.delete_track(self.store, first)
        deletion.delete_track(self.store, second)
        report = deletion.empty_trash(self.store)
        self.assertEqual(report["removed"], 2)
        self.assertEqual(report["bytes"], 4096)
        self.assertEqual(self.trashed(), [])
        self.assertEqual(list((self.root / deletion.TRASH_NAME).rglob("*.mp3")), [])
        self.assertEqual(self.store.stats()["trash_count"], 0)
        # A second call is harmless and says so.
        self.assertEqual(deletion.empty_trash(self.store)["removed"], 0)

    def test_a_file_outside_the_root_never_enters_the_trash(self):
        tid = self.track("Outside", file=False)
        stray = Path(self.dir.name) / "elsewhere.mp3"
        stray.write_bytes(b"y" * 10)
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=? WHERE id=?",
                                    (str(stray), tid))
            self.store.conn.commit()
        deletion.delete_track(self.store, tid)
        self.assertTrue(stray.exists())
        self.assertEqual(self.trashed(), [])

    def test_the_ceiling_does_nothing_while_the_drive_has_room(self):
        self.track("Safe")
        tid = self.store.conn.execute("SELECT id FROM tracks").fetchone()[0]
        deletion.delete_track(self.store, tid)
        with mock.patch.object(db, "disk_usage", return_value=(100.0, 10.0, 90.0)):
            self.assertEqual(deletion.reclaim(self.store, 95.0), 0)
        self.assertEqual(len(self.trashed()), 1)

    def test_the_ceiling_purges_the_oldest_file_until_the_drive_fits(self):
        old, new = self.track("Old"), self.track("New")
        deletion.delete_track(self.store, old)
        with self.store.lock:                      # one second apart, so the order is real
            self.store.conn.execute(
                "UPDATE library_trash SET created=created-10")
            self.store.conn.commit()
        deletion.delete_track(self.store, new)
        readings = [(100.0, 4.0, 96.0), (100.0, 6.0, 94.0)]
        with mock.patch.object(db, "disk_usage", side_effect=readings):
            freed = deletion.reclaim(self.store, 95.0)
        self.assertEqual(freed, 2048)
        self.assertEqual([row["title"] for row in self.trashed()], ["New"])
        messages = [r["msg"] for r in self.store.conn.execute(
            "SELECT msg FROM logs ORDER BY id")]
        self.assertTrue(any("Old" in message and "ceiling" in message for message in messages))

    def test_a_missing_trashed_file_is_dropped_rather_than_counted_forever(self):
        tid = self.track("Gone")
        deletion.delete_track(self.store, tid)
        row = self.trashed()[0]
        Path(row["trash_path"]).unlink()
        with mock.patch.object(db, "disk_usage", return_value=(100.0, 1.0, 99.0)):
            deletion.reclaim(self.store, 95.0)
        self.assertEqual(self.trashed(), [])


class Audit(Case):
    """Who deleted what survives the log being trimmed (the member-delete audit trail)."""

    def rows(self):
        return deletion.history(self.store)

    def test_a_track_delete_names_the_person(self):
        tid = self.track("One")
        deletion.delete_track(self.store, tid,
                              actor={"id": "m-alice", "display_name": "Alice"})
        row = self.rows()[0]
        self.assertEqual((row["member_id"], row["member_name"]), ("m-alice", "Alice"))
        self.assertEqual((row["kind"], row["target_id"], row["title"]),
                         ("track", tid, "One"))
        self.assertEqual((row["tracks"], row["files"], row["bytes"]), (1, 1, 2048))

    def test_a_playlist_delete_records_the_whole_report(self):
        a, b = self.track("One"), self.track("Two")
        pid = self.playlist("Set", [a, b])
        deletion.apply(self.store, "playlist", pid, delete_files=True,
                       actor={"id": "m-admin", "display_name": "Owner"})
        row = self.rows()[0]
        self.assertEqual((row["kind"], row["target_id"], row["title"]),
                         ("playlist", pid, "Set"))
        self.assertEqual((row["tracks"], row["files"], row["bytes"]), (2, 2, 4096))
        self.assertIn("trash", row["detail"])

    def test_local_mode_says_local_instead_of_inventing_a_member(self):
        tid = self.track("Solo")
        deletion.delete_track(self.store, tid)
        row = self.rows()[0]
        self.assertEqual((row["member_id"], row["member_name"]), ("", "local"))

    def test_a_name_is_looked_up_when_only_the_id_is_known(self):
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO members(id,email,role,display_name)"
                " VALUES('m-nia','nia@example.com','member','Nia')")
            self.store.conn.commit()
        tid = self.track("Named")
        deletion.delete_track(self.store, tid, actor={"id": "m-nia"})
        self.assertEqual(self.rows()[0]["member_name"], "Nia")

    def test_deleting_again_is_not_a_second_delete(self):
        tid = self.track("Once")
        deletion.delete_track(self.store, tid, actor={"id": "m-a", "display_name": "A"})
        deletion.delete_track(self.store, tid, actor={"id": "m-a", "display_name": "A"})
        self.assertEqual(len(self.rows()), 1)

    def test_history_is_newest_first(self):
        first, second = self.track("First"), self.track("Second")
        deletion.delete_track(self.store, first, actor={"id": "m", "display_name": "M"})
        deletion.delete_track(self.store, second, actor={"id": "m", "display_name": "M"})
        self.assertEqual([row["title"] for row in self.rows()], ["Second", "First"])

    def test_emptying_the_trash_does_not_erase_the_record(self):
        tid = self.track("Remembered")
        deletion.delete_track(self.store, tid, actor={"id": "m", "display_name": "M"})
        deletion.empty_trash(self.store)
        row = self.rows()[0]
        self.assertEqual((row["title"], row["files"]), ("Remembered", 1))

    def test_a_delete_without_files_still_records_the_act(self):
        tid = self.track("Kept file")
        deletion.delete_track(self.store, tid, delete_files=False,
                              actor={"id": "m", "display_name": "M"})
        self.assertEqual(self.rows()[0]["files"], 0)


class DeleteRouteAudit(unittest.TestCase):
    """The same record, through the route an admin actually presses.

    The helpers come from SecurityHTTP without inheriting its test methods, which
    would otherwise run a second time against this fixture.
    """

    request = SecurityHTTP.request
    token = SecurityHTTP.token
    user = SecurityHTTP.user
    tearDown = SecurityHTTP.tearDown

    def setUp(self):
        import server
        SecurityHTTP.setUp(self)
        self.queue = mock.Mock()
        boot = mock.patch.object(server, "boot",
                                 return_value=(self.store, self.queue))
        boot.start()
        self.addCleanup(boot.stop)
        self.identity = self.user("admin")
        self.root = Path(self.tmp.name) / "library"
        self.root.mkdir()
        self.store.set_settings({"root": str(self.root)})
        self.path = self.root / "Artist" / "Song.mp3"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"x" * 2048)
        self.tid = self.store.track("local", "audit-fixture", "Song", artist_name="Artist")
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET file=?,size=? WHERE id=?",
                                    (str(self.path), 2048, self.tid))
            self.store.conn.commit()
        self.headers = {"Authorization": "Bearer fixture", "Origin": "https://music.example"}

    def test_the_admin_sees_who_deleted_the_song(self):
        code, _, raw = self.request(f"/api/track/{self.tid}/delete", "POST",
                                    self.headers, "{}")
        self.assertEqual(code, 200, raw)
        code, _, raw = self.request("/api/deletions", headers=self.headers)
        self.assertEqual(code, 200, raw)
        row = json.loads(raw)["deletions"][0]
        self.assertEqual((row["member_name"], row["kind"], row["title"]),
                         ("Friend", "track", "Song"))

    def test_a_member_cannot_read_the_audit_trail(self):
        self.identity = self.user("member")
        code, _, raw = self.request("/api/deletions", headers=self.headers)
        self.assertEqual(code, 403, raw)


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
