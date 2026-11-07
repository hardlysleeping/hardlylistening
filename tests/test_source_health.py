"""Source availability: a playlist entry that outlives its recording.

Nothing here deletes a track or a file. The checks only label rows, and a failed
fetch must never be read as "everything is gone".
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_security as security_tests
from library import source_health
from providers.base import TrackRef


def track(provider="apple", tid="123", **kwargs):
    return TrackRef(provider=provider, provider_track_id=tid,
                    title=kwargs.get("title", "Song"), artist="Artist",
                    album="Album", track_number=1, duration=200.0,
                    artwork="", source_url="")


class FakeRelease:
    def __init__(self, tracks, title="Set"):
        self.tracks = tracks
        self.title = title


class SourceHealth(unittest.TestCase):
    tearDown = security_tests.SecurityHTTP.tearDown

    def setUp(self):
        security_tests.SecurityHTTP.setUp(self)
        self.root = Path(self.tmp.name) / "media"
        self.root.mkdir()
        self.store.set_settings({"root": str(self.root)})
        self.ids = {}
        with self.store.lock:
            for index, tid in enumerate(["111", "222", "333"]):
                path = self.root / f"t{index}.mp3"
                path.write_bytes(b"\xff\xfb\x00" * 128)
                cursor = self.store.conn.execute(
                    "INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,"
                    "duration,file,added) VALUES('apple',?,?,?,?,200,?,0)",
                    (tid, f"Song {tid}", "Artist", "Album", str(path)))
                self.ids[tid] = (cursor.lastrowid, path)
            self.store.conn.execute(
                "INSERT INTO playlists(title,provider,source_url,added) VALUES('Mix','apple',?,0)",
                ("https://music.apple.com/us/playlist/mix/pl.1",))
            for tid, (track_id, _) in self.ids.items():
                self.store.conn.execute(
                    "INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES(1,?,?)",
                    (track_id, int(tid)))
            self.store.conn.commit()

    def status(self, tid):
        return self.store.conn.execute(
            "SELECT source_status FROM tracks WHERE provider_track_id=?", (tid,)).fetchone()[0]

    def test_playlist_reread_marks_only_the_missing_tracks(self):
        release = FakeRelease([track(tid="111"), track(tid="222")])
        with patch("providers.base.for_url") as resolver:
            resolver.return_value.resolve.return_value = release
            result = source_health.verify_playlist(self.store, 1)
        self.assertEqual(result["present"], 2)
        self.assertEqual(result["gone"], 1)
        self.assertEqual(result["kept_locally"], 1)      # the gone one is still on disk
        self.assertEqual(self.status("333"), "gone")
        self.assertEqual(self.status("111"), "present")
        self.assertEqual(self.ids["333"][1].exists(), True)   # never deleted

    def test_empty_read_changes_nothing(self):
        with patch("providers.base.for_url") as resolver:
            resolver.return_value.resolve.return_value = FakeRelease([])
            with self.assertRaises(RuntimeError):
                source_health.verify_playlist(self.store, 1)
        self.assertEqual({self.status(t) for t in self.ids}, {""})

    def test_playlist_without_source_url_is_refused(self):
        with self.store.lock:
            self.store.conn.execute("UPDATE playlists SET source_url='' WHERE id=1")
            self.store.conn.commit()
        with self.assertRaises(ValueError):
            source_health.verify_playlist(self.store, 1)

    def test_catalog_lookup_labels_missing_ids(self):
        def fake_lookup(ids, storefront):
            self.assertEqual(storefront, "us")
            return {"111", "222"}            # 333 is no longer sold
        with patch.object(source_health, "_itunes_lookup", side_effect=fake_lookup):
            result = source_health.verify_catalog(self.store)
        self.assertEqual(result["checked"], 3)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(self.status("333"), "missing")
        self.assertEqual(self.status("111"), "present")

    def test_catalog_failure_is_not_treated_as_removal(self):
        with patch.object(source_health, "_itunes_lookup", side_effect=OSError("no network")):
            result = source_health.verify_catalog(self.store)
        self.assertEqual(result["checked"], 0)
        self.assertIn("no network", result["error"])
        self.assertEqual({self.status(t) for t in self.ids}, {""})

    def test_track_already_gone_is_not_rechecked(self):
        with self.store.lock:
            self.store.conn.execute("UPDATE tracks SET source_status='gone' WHERE provider_track_id='333'")
            self.store.conn.commit()
        seen = []

        def fake_lookup(ids, storefront):
            seen.extend(ids)
            return set(ids)
        with patch.object(source_health, "_itunes_lookup", side_effect=fake_lookup):
            source_health.verify_catalog(self.store)
        self.assertNotIn("333", seen)


class SourceHealthHTTP(unittest.TestCase):
    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request
    token = security_tests.SecurityHTTP.token
    user = security_tests.SecurityHTTP.user

    def setUp(self):
        security_tests.SecurityHTTP.setUp(self)
        self.headers = {"Authorization": "Bearer fixture", "Origin": "https://music.example"}
        self.identity = self.user(role='admin')      # these reads hit the provider
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,duration,added)"
                " VALUES('apple','999','Song','Artist','Album',200,0)")
            self.store.conn.execute(
                "INSERT INTO playlists(title,provider,source_url,added) VALUES('Mix','apple','https://music.apple.com/x',0)")
            self.store.conn.commit()

    def call(self, path):
        status, _, body = self.request(path, "POST", dict(self.headers, **{"Content-Type": "application/json"}), "{}")
        return status, json.loads(body or "{}")

    def test_routes_require_membership_and_admin(self):
        self.assertEqual(self.request("/api/library/verify", "POST", body="{}")[0], 401)
        self.assertEqual(self.request("/api/playlist/1/verify", "POST", body="{}")[0], 401)
        self.identity = dict(self.identity, id="u2", email="friend2@example.com")
        self.store.conn.execute("INSERT OR REPLACE INTO members(id,email,role) VALUES('u2','friend2@example.com','member')")
        self.store.conn.commit()
        headers = {'Cookie': self.token('u2'), 'Origin': 'https://music.example'}
        self.assertEqual(self.request("/api/library/verify", "POST", headers, "{}")[0], 403)

    def test_catalog_route_reports_failure_honestly(self):
        with patch.object(source_health, "_itunes_lookup", side_effect=OSError("dns")):
            status, body = self.call("/api/library/verify")
        self.assertEqual(status, 502)
        self.assertIn("dns", body["error"])

    def test_playlist_route_validates_input(self):
        status, body = self.call("/api/playlist/99/verify")
        self.assertEqual(status, 400)
        self.assertIn("unknown playlist", body["error"])


if __name__ == "__main__":
    unittest.main()
