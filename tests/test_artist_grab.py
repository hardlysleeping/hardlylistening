"""Artist pages and grabbing the holes in a release.

Two behaviours worth pinning down:

* `/api/artist` answers from stored rows only — never a network call — and offers
  the source's own RSS feed when the source publishes one.
* `/api/library/grab` queues unfinished tracks and never re-downloads a file that
  is already on disk.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_security as security_tests

AUTH = {"Authorization": "Bearer good", "Content-Type": "application/json",
        "Origin": "https://music.example"}


class ArtistAndGrab(unittest.TestCase):
    setUp = security_tests.SecurityHTTP.setUp
    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request
    token = security_tests.SecurityHTTP.token
    user = security_tests.SecurityHTTP.user

    def setUp(self):
        security_tests.SecurityHTTP.setUp(self)
        self.identity = self.user(role="admin")
        self.root = Path(self.tmp.name) / "media"
        self.root.mkdir()
        self.store.set_settings({"root": str(self.root)})
        on_disk = self.root / "have.mp3"
        on_disk.write_bytes(b"\xff\xfb\x00" * 128)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO artists(provider,artist_id,name,url) VALUES('soundcloud','12345','DJ Test','https://soundcloud.com/djtest')")
            self.store.conn.execute(
                "INSERT INTO artists(provider,artist_id,name,url) VALUES('apple','999','DJ Test','https://music.apple.com/artist/999')")
            self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,file,added)"
                " VALUES('soundcloud','1','Have','DJ Test','Set A',?,0)", (str(on_disk),))
            self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,file,added)"
                " VALUES('soundcloud','2','Missing','DJ Test','Set A','',0)")
            self.store.conn.commit()
            self.missing_id = self.store.conn.execute(
                "SELECT id FROM tracks WHERE title='Missing'").fetchone()["id"]
            self.have_id = self.store.conn.execute(
                "SELECT id FROM tracks WHERE title='Have'").fetchone()["id"]

    def tearDown(self):
        security_tests.SecurityHTTP.tearDown(self)

    def get_json(self, path, headers=None):
        status, _, body = self.request(path, headers=headers)
        return status, json.loads(body)

    def test_artist_page_builds_the_rss_url_for_soundcloud(self):
        status, data = self.get_json("/api/artist?name=dj%20test", AUTH)
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "dj test")
        self.assertEqual(data["tracks"], 2)
        self.assertEqual(data["downloaded"], 1)
        rss = [p["rss"] for p in data["providers"] if p["rss"]]
        self.assertEqual(
            rss, ["https://feeds.soundcloud.com/users/soundcloud:users:12345/sounds.rss"])
        self.assertFalse(data["subscribed"])
        apple = [p for p in data["providers"] if p["provider"] == "apple"][0]
        self.assertEqual(apple["rss"], "")      # Apple publishes no artist feed

    def test_artist_profile_uses_identity_and_only_reads_safe_stored_items(self):
        from discovery.artist_profile import profile
        right = self.store.watch_add('soundcloud', '12345', 'Renamed Artist', 'https://soundcloud.com/djtest')
        wrong = self.store.watch_add('soundcloud', '77777', 'DJ Test', 'https://soundcloud.com/other')
        self.store.feed_add(right, 'soundcloud', 'new-1', '<New song>', artist='Renamed Artist',
                            release_date='2026-11-05', source_url='https://soundcloud.com/djtest/new')
        self.store.feed_add(right, 'soundcloud', 'new-2', 'Unsafe source', source_url='javascript:alert(1)')
        self.store.feed_add(wrong, 'soundcloud', 'wrong', 'Wrong artist')
        self.store.log('check failed for Renamed Artist: fixture error', 'error', f'watch:{right}')
        before = self.store.conn.execute('SELECT COUNT(*) FROM download_jobs').fetchone()[0]
        with patch('discovery.subscriptions.check', side_effect=AssertionError('must not check')), patch('discovery.subscriptions.subscribe', side_effect=AssertionError('must not follow')):
            status, data = self.get_json('/api/artist?name=DJ%20Test', AUTH)
        self.assertEqual(status, 200)
        self.assertEqual([w['id'] for w in data['watches']], [right])
        self.assertIn('fixture error', data['watches'][0]['error'])
        items = {item['title']: item for item in data['feed']}
        self.assertNotIn('Wrong artist', items)
        self.assertEqual(items['<New song>']['release_date'], '2026-11-05')
        self.assertEqual(items['Unsafe source']['source_url'], '')
        self.assertEqual(profile(self.store, 'No catalog identity')['watches'], [])
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM download_jobs').fetchone()[0], before)

    def test_artist_page_requires_a_name(self):
        status, _ = self.get_json("/api/artist", AUTH)
        self.assertEqual(status, 400)

    def test_grab_queues_only_tracks_without_a_file(self):
        status, _, body = self.request(
            "/api/library/grab", "POST", AUTH,
            json.dumps({"ids": [self.missing_id, self.have_id]}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["queued"], 1)
        job = self.store.conn.execute(
            "SELECT COUNT(*) FROM download_jobs WHERE track_id=?", (self.missing_id,)).fetchone()[0]
        self.assertEqual(job, 1)

    def test_grab_requires_admin(self):
        self.identity = self.user(role="member")
        status, _, _ = self.request(
            "/api/library/grab", "POST", AUTH, json.dumps({"ids": [self.missing_id]}))
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
