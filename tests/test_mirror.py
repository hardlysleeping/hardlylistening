"""The mirror: what is re-acquired, from where, and only when the mode says so.

Three cards, one file, because they are one decision chain:

* `FED-MIRROR-002` — a descriptor becomes a request. The provider that wrote it is installed, the
  page it names is a page *its own* provider claims, and a metadata-only provider is refused
  before anything expensive happens. What comes back is then judged against the descriptor: the
  provider's own id settles it, otherwise the length and the title have to agree.
* `FED-MIRROR-003` — a copy from a peer. Only an address an operator pinned, only https, no
  redirects, a hard size cap while reading, and the bytes hashed as they arrive. The serving half
  is not built and this file does not pretend otherwise.
* `FED-MIRROR-004` — the modes. `metadata` never fetches, `cache` fetches on play, `selected`
  fetches only what was picked, `all` fetches everything. An unknown value is a refusal, not a
  default.

The peer copy runs against a loopback stub server, so nothing here touches the internet. Loopback
is allowed only through `HARDLYLISTENING_ALLOW_LOCAL_PEERS`, the same escape hatch the podcast
fetcher uses for its own stub feeds.
"""
import hashlib
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import db
import paths
from repo import federation, mirror

DESCRIPTOR = {
    "version": 1, "provider": "soundcloud", "provider_track_id": "123456",
    "title": "Xtal", "artist": "Aphex Twin", "album": "Selected Ambient Works",
    "duration": 293.0, "source_url": "https://soundcloud.com/aphex-twin/xtal",
}


def descriptor(**overrides):
    body = dict(DESCRIPTOR)
    body.update(overrides)
    return body


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = db.Store(os.path.join(self.dir.name, "mirror.db"))
        self.addCleanup(lambda: self.store.conn.close())

    def track(self, title="Xtal", selected=False):
        track_id = self.store.track("soundcloud", f"id-{title}", title,
                                    artist_name="Aphex Twin", duration=293.0)
        if selected:
            mirror.select(self.store, track_id, True)
        return track_id


# -- 002: the original source ---------------------------------------------------------------------

class Plan(unittest.TestCase):
    def test_a_pages_own_provider_keeps_the_page(self):
        made = mirror.plan(descriptor())
        self.assertEqual(made["how"], "url")
        self.assertEqual(made["url"], DESCRIPTOR["source_url"])
        self.assertEqual(made["track_id"], "123456")
        self.assertEqual(made["provider"], "soundcloud")
        # Three seconds of encoder padding is normal; two per cent on a long mix is the same idea.
        self.assertEqual(made["grace"], 5.86)

    def test_a_descriptor_with_no_page_becomes_a_search(self):
        made = mirror.plan(descriptor(source_url=""))
        self.assertEqual(made["how"], "search")
        self.assertEqual(made["query"], "Aphex Twin Xtal")
        self.assertEqual(made["url"], "")

    def test_a_descriptor_that_names_another_providers_page_is_refused(self):
        # soundcloud is the claimed provider; the URL is not one soundcloud claims.
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.plan(descriptor(source_url="https://www.youtube.com/watch?v=abc"))
        self.assertIn("does not claim", str(caught.exception))

    def test_an_unknown_provider_is_refused_by_name(self):
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.plan(descriptor(provider="napster", source_url="https://napster.example/x"))
        self.assertIn("no provider of that name", str(caught.exception))

    def test_a_metadata_provider_cannot_supply_audio(self):
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.plan(descriptor(provider="apple", provider_track_id="1",
                                   source_url="https://music.apple.com/us/album/x/1"))
        self.assertIn("metadata provider", str(caught.exception))

    def test_an_untrusted_descriptor_is_checked_before_it_is_planned(self):
        # The descriptor gate runs first, so a peer cannot smuggle a field in through the mirror.
        with self.assertRaises(federation.UnsafeDescriptor):
            mirror.plan({**descriptor(), "cookie": "session=abc"})

    def test_the_digest_names_the_same_track_on_both_nodes(self):
        one = mirror.digest(descriptor())
        two = mirror.digest(dict(reversed(list(descriptor().items()))))
        self.assertEqual(one, two)                     # canonical bytes, so key order is noise
        self.assertEqual(one, hashlib.md5(
            federation.dumps(descriptor()).encode("utf-8")).hexdigest())
        self.assertNotEqual(one, mirror.digest(descriptor(provider_track_id="999")))


class Verify(unittest.TestCase):
    def test_the_providers_own_id_settles_it(self):
        ok, reasons = mirror.matches(descriptor(), {
            "provider": "soundcloud", "provider_track_id": "123456",
            "title": "Xtal (Remastered)", "duration": 400.0})
        self.assertTrue(ok, reasons)          # a different length does not override a fixed id

    def test_a_different_length_is_a_different_recording(self):
        ok, reasons = mirror.matches(descriptor(), {"duration": 320.0, "title": "Xtal"})
        self.assertFalse(ok)
        self.assertTrue(any("duration differs" in reason for reason in reasons), reasons)

    def test_a_length_inside_the_tolerance_passes(self):
        ok, reasons = mirror.matches(descriptor(), {"duration": 296.0, "title": "Xtal"})
        self.assertTrue(ok, reasons)

    def test_a_different_title_is_a_different_song(self):
        ok, reasons = mirror.matches(descriptor(), {
            "title": "Windowlicker", "duration": 293.0})
        self.assertFalse(ok)
        self.assertTrue(any("different song" in reason for reason in reasons), reasons)

    def test_an_unknown_length_is_unverified_rather_than_wrong(self):
        ok, reasons = mirror.matches(descriptor(duration=0), {"title": "Xtal", "duration": 0})
        self.assertTrue(ok)
        self.assertIn("duration unverified", reasons)

    def test_another_provider_answering_is_a_mismatch(self):
        ok, reasons = mirror.matches(descriptor(), {
            "provider": "youtube", "title": "Xtal", "duration": 293.0})
        self.assertFalse(ok)
        self.assertTrue(any("different provider" in reason for reason in reasons), reasons)


# -- 003: a trusted peer --------------------------------------------------------------------------

class PeerList(StoreCase):
    def test_no_peers_configured_means_nobody_to_ask(self):
        self.assertEqual(mirror.peers(self.store), [])
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.peer_request(descriptor(), mirror.peers(self.store))
        self.assertIn("no peer is trusted", str(caught.exception))

    def test_a_peer_is_https_and_named(self):
        mirror.set_peers(self.store, [{"base_url": "https://peer.example/",
                                       "node_id": "a" * 32}])
        rows = mirror.peers(self.store)
        self.assertEqual(rows, [{"base_url": "https://peer.example", "node_id": "a" * 32}])

    def test_http_and_credentials_are_refused(self):
        for base_url in ("http://peer.example", "https://user:pass@peer.example"):
            with self.assertRaises(mirror.MirrorRefused):
                mirror.set_peers(self.store, [{"base_url": base_url, "node_id": "a" * 32}])
        self.assertEqual(mirror.peers(self.store), [])

    def test_a_peer_with_no_pinned_node_id_is_refused(self):
        with self.assertRaises(mirror.MirrorRefused):
            mirror.set_peers(self.store, [{"base_url": "https://peer.example"}])

    def test_a_stored_list_that_is_not_json_is_not_repaired(self):
        with self.store.lock:
            self.store.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                                    (mirror.PEERS_KEY, "{not json"))
            self.store.conn.commit()
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.peers(self.store)
        self.assertIn("not JSON", str(caught.exception))
        # and the status route says so rather than reporting a healthy zero
        self.assertEqual(mirror.status(self.store)["peers"], -1)

    def test_the_request_carries_the_descriptor_digest(self):
        mirror.set_peers(self.store, [{"base_url": "https://peer.example",
                                       "node_id": "a" * 32}])
        request = mirror.peer_request(descriptor(), mirror.peers(self.store))
        self.assertEqual(request["url"],
                         "https://peer.example/federation/audio/" + mirror.digest(descriptor()))
        self.assertEqual(request["node_id"], "a" * 32)


class PeerCopy(unittest.TestCase):
    """A real transfer against a loopback stub, so the cap and the digest are measured."""

    def setUp(self):
        self.previous = os.environ.get("HARDLYLISTENING_ALLOW_LOCAL_PEERS")
        os.environ["HARDLYLISTENING_ALLOW_LOCAL_PEERS"] = "1"
        self.addCleanup(self._restore)
        self.payload = b"ID3" + os.urandom(4096)
        self.status = 200
        self.redirect = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if outer.redirect:
                    self.send_response(302)
                    self.send_header("Location", "https://elsewhere.example/x")
                    self.end_headers()
                    return
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(outer.payload)))
                self.end_headers()
                self.wfile.write(outer.payload)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.start()
        self.addCleanup(self._stop)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _stop(self):
        self.httpd.shutdown()
        self.thread.join()
        self.httpd.server_close()

    def _restore(self):
        if self.previous is None:
            os.environ.pop("HARDLYLISTENING_ALLOW_LOCAL_PEERS", None)
        else:
            os.environ["HARDLYLISTENING_ALLOW_LOCAL_PEERS"] = self.previous

    def test_a_copy_is_measured_and_hashed_as_it_arrives(self):
        got = mirror.copy_from_peer(self.base + "/federation/audio/" + "0" * 32)
        self.assertEqual(got["bytes"], len(self.payload))
        self.assertEqual(got["sha256"], hashlib.sha256(self.payload).hexdigest())

    def test_a_copy_over_the_limit_is_refused_and_never_written(self):
        dest = os.path.join(self.dir.name, "copy.mp3")
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.copy_from_peer(self.base + "/x", dest=dest, limit=1024)
        self.assertIn("larger than the limit", str(caught.exception))
        self.assertFalse(os.path.exists(dest))
        self.assertEqual([name for name in os.listdir(self.dir.name)], [])

    def test_a_redirect_is_refused_rather_than_followed(self):
        self.redirect = True
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.copy_from_peer(self.base + "/x")
        self.assertIn("redirect", str(caught.exception))

    def test_a_peer_that_answers_badly_is_a_refusal_not_a_crash(self):
        self.status = 404
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.copy_from_peer(self.base + "/x")
        self.assertIn("404", str(caught.exception))

    def test_a_completed_copy_lands_where_a_completed_one_belongs(self):
        dest = os.path.join(self.dir.name, "one.mp3")
        mirror.copy_from_peer(self.base + "/x", dest=dest)
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), self.payload)
        self.assertEqual(os.listdir(self.dir.name), ["one.mp3"])

    def test_the_cache_entry_is_named_by_the_digest(self):
        self.assertEqual(mirror.cache_path(descriptor()).name, mirror.digest(descriptor()))
        self.assertEqual(mirror.cache_path(descriptor()).parent, paths.FEDERATION_CACHE_DIR)

    def test_an_http_peer_is_only_allowed_for_loopback(self):
        with mock.patch.dict(os.environ, {"HARDLYLISTENING_ALLOW_LOCAL_PEERS": "0"}):
            with self.assertRaises(mirror.MirrorRefused):
                mirror.copy_from_peer(self.base + "/x")


# -- 004: the modes ------------------------------------------------------------------------------

class Modes(StoreCase):
    def test_the_default_is_metadata_and_it_fetches_nothing(self):
        self.assertEqual(mirror.mode(self.store), "metadata")
        self.assertFalse(mirror.should_fetch(self.store, event="import"))
        self.assertFalse(mirror.should_fetch(self.store, event="play", selected=True))

    def test_cache_fetches_on_play_only(self):
        mirror.set_mode(self.store, "cache")
        self.assertFalse(mirror.should_fetch(self.store, event="import"))
        self.assertTrue(mirror.should_fetch(self.store, event="play"))

    def test_selected_fetches_only_what_was_picked(self):
        mirror.set_mode(self.store, "selected")
        track_id = self.track("Seven", selected=False)
        self.assertFalse(mirror.should_fetch(self.store, event="play", selected=False))
        self.assertTrue(mirror.should_fetch(self.store, event="play", selected=True))
        mirror.select(self.store, track_id, True)
        self.assertTrue(mirror.is_selected(self.store, track_id))
        mirror.select(self.store, track_id, False)
        self.assertFalse(mirror.is_selected(self.store, track_id))

    def test_all_fetches_every_import(self):
        mirror.set_mode(self.store, "all")
        self.assertTrue(mirror.should_fetch(self.store, event="import"))
        self.assertTrue(mirror.should_fetch(self.store, event="play"))

    def test_an_unknown_mode_is_refused_not_defaulted(self):
        with self.assertRaises(mirror.MirrorRefused) as caught:
            mirror.set_mode(self.store, "everything")
        self.assertIn("mode must be one of", str(caught.exception))
        # A value written by hand is reported, not quietly turned back into metadata.
        with self.store.lock:
            self.store.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                                    (mirror.MODE_KEY, "everything"))
            self.store.conn.commit()
        with self.assertRaises(mirror.MirrorRefused):
            mirror.mode(self.store)
        self.assertEqual(mirror.status(self.store)["mode"], "invalid")

    def test_an_unknown_event_is_refused(self):
        with self.assertRaises(mirror.MirrorRefused):
            mirror.should_fetch(self.store, event="download")

    def test_selecting_a_track_that_does_not_exist_is_a_refusal(self):
        with self.assertRaises(mirror.MirrorRefused):
            mirror.select(self.store, 999999)

    def test_selection_is_node_state_not_a_members_list(self):
        track_id = self.track("Xtal")
        mirror.select(self.store, track_id, True)
        mirror.set_peers(self.store, [{"base_url": "https://peer.example",
                                       "node_id": "a" * 32}])
        got = mirror.status(self.store)
        self.assertEqual(got["mode"], "metadata")
        self.assertEqual(got["peers"], 1)
        self.assertEqual(got["selected"], 1)
        self.assertEqual(got["modes"], list(mirror.MODES))
        # A status read must not mint key material: an install that never federates stays empty.
        self.assertEqual(got["node_id"], "")


if __name__ == "__main__":
    unittest.main()