"""`GET /api/federation`: what the mirror is set to, and nothing else.

The route is read-only on purpose. Writing the mode or the peer list is an operator action
(`repo.mirror.set_mode` / `set_peers`), not a web form, because the other half of federation —
serving a copy to a peer — needs peer authentication that does not exist yet. What this pins is
that the route answers, reports the mode the module reports, and refuses to be a write.

`test_listening` is imported as a module rather than as a class: a TestCase imported into this
module's globals is collected a second time by the loader and runs its whole suite against this
fixture.
"""
import json
import unittest
from unittest import mock

import test_listening
from repo import mirror


class FederationStatus(unittest.TestCase):
    # Borrowed by expression rather than by `from test_listening import ListeningHTTP`: an
    # imported TestCase class in this module's globals is collected by the loader a second time,
    # and its six tests then run against this fixture too.
    request = test_listening.ListeningHTTP.request
    token = test_listening.ListeningHTTP.token
    user = test_listening.ListeningHTTP.user
    api = test_listening.ListeningHTTP.api
    rest = test_listening.ListeningHTTP.rest
    response = test_listening.ListeningHTTP.response
    save = test_listening.ListeningHTTP.save
    tearDown = test_listening.ListeningHTTP.tearDown

    def setUp(self):
        test_listening.ListeningHTTP.setUp(self)
        import server
        self.queue = mock.Mock()
        # SecurityHTTP.setUp already patched `boot` with (store, None); the handler needs the
        # queue for actions this route never takes, so keep the store and make the rest inert.
        patcher = mock.patch.object(server, "boot", return_value=(self.store, self.queue))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_status_names_the_mode_and_the_peer_count(self):
        mirror.set_mode(self.store, "selected")
        mirror.set_peers(self.store, [{"base_url": "https://peer.example", "node_id": "a" * 32}])
        code, _, raw = self.request("/api/federation", headers=self.headers)
        self.assertEqual(code, 200, raw)
        body = json.loads(raw)
        self.assertEqual(body["mode"], "selected")
        self.assertEqual(body["peers"], 1)
        self.assertEqual(body["modes"], list(mirror.MODES))
        self.assertIsInstance(body["selected"], int)

    def test_the_default_mode_is_reported_without_being_written(self):
        code, _, raw = self.request("/api/federation", headers=self.headers)
        self.assertEqual(code, 200, raw)
        self.assertEqual(json.loads(raw)["mode"], "metadata")
        # Reading the route is not what stores the default.
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT value FROM settings WHERE key=?", (mirror.MODE_KEY,)).fetchone()
        self.assertIsNone(row)

    def test_the_route_is_not_a_write(self):
        code, _, _ = self.request("/api/federation", "POST", headers=self.headers, body="{}")
        self.assertNotEqual(code, 200, code)
        self.assertEqual(mirror.mode(self.store), "metadata")

    def test_an_invalid_stored_mode_is_reported_rather_than_repaired(self):
        with self.store.lock:
            self.store.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                                    (mirror.MODE_KEY, "everything"))
            self.store.conn.commit()
        code, _, raw = self.request("/api/federation", headers=self.headers)
        self.assertEqual(code, 200, raw)
        self.assertEqual(json.loads(raw)["mode"], "invalid")


if __name__ == "__main__":
    unittest.main()