"""Acquisition is a grant, and no role implies it (HL-219).

The rule the plan states, and that these tests pin:

* membership does not imply acquisition,
* access to the player does not imply it,
* being an administrator does not imply it.

What that means in practice is that every decision is one table read. The tests below
cover the table itself, the first-run migration that keeps the existing administrator
working, and the route gate for a member, a granted member and an administrator whose
grant has been revoked.

No provider, worker or file is involved: a refused request never reaches the queue, and
an allowed one is proven by the *next* refusal it gets ("paste a playlist link"), which
is past the gate.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import acquisitions
import server
from db import Store
from security import Security


class Grants(unittest.TestCase):
    """The table and its one migration, without HTTP."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.store = Store(Path(self.dir.name) / 'db.sqlite')
        self.addCleanup(lambda: self.store.conn.close())
        self.sec = Security()
        self.sec.schema(self.store)
        self.member('a1', 'admin')
        self.member('m1', 'member')

    def member(self, mid, role, name=''):
        with self.store.lock:
            self.store.conn.execute(
                'INSERT OR REPLACE INTO members(id,email,role,display_name) VALUES(?,?,?,?)',
                (mid, mid + '@example.test', role, name or mid))
            self.store.conn.commit()

    def test_an_existing_administrator_keeps_working_through_the_migration(self):
        acquisitions.schema(self.store)
        self.assertTrue(acquisitions.may_acquire(self.store, {'id': 'a1', 'role': 'admin'}))
        self.assertFalse(acquisitions.may_acquire(self.store, {'id': 'm1', 'role': 'member'}))
        grants = acquisitions.grants(self.store)
        self.assertEqual([g['member_id'] for g in grants], ['a1'])
        self.assertEqual(grants[0]['granted_by'], 'migration')

    def test_a_role_is_not_consulted_at_check_time(self):
        acquisitions.schema(self.store)
        acquisitions.revoke(self.store, 'a1')
        self.assertFalse(acquisitions.may_acquire(self.store, {'id': 'a1', 'role': 'admin'}),
                         'an administrator without a grant may not acquire')
        acquisitions.grant(self.store, 'm1', 'a1')
        self.assertTrue(acquisitions.may_acquire(self.store, {'id': 'm1', 'role': 'member'}),
                        'a member with a grant may')

    def test_nobody_signed_in_never_acquires(self):
        acquisitions.schema(self.store)
        self.assertFalse(acquisitions.may_acquire(self.store, None))
        self.assertFalse(acquisitions.may_acquire(self.store, {}))

    def test_revoking_everyone_is_final(self):
        acquisitions.schema(self.store)
        acquisitions.revoke(self.store, 'a1')
        # A later boot must not re-grant: the migration ran once, and a marker proves it.
        acquisitions.schema(self.store)
        self.assertFalse(acquisitions.may_acquire(self.store, {'id': 'a1', 'role': 'admin'}))

    def test_the_migration_waits_for_an_administrator_to_exist(self):
        fresh = Store(Path(self.dir.name) / 'fresh.sqlite')
        self.addCleanup(lambda: fresh.conn.close())
        acquisitions.schema(fresh)                    # no members table yet: nothing to grant
        self.sec.schema(fresh)
        with fresh.lock:
            fresh.conn.execute("INSERT INTO members(id,email,role,display_name)"
                               " VALUES('late','late@example.test','admin','Late')")
            fresh.conn.commit()
        acquisitions.schema(fresh)                    # now it lands
        self.assertTrue(acquisitions.may_acquire(fresh, {'id': 'late', 'role': 'admin'}))

    def test_granting_an_unknown_member_is_refused(self):
        acquisitions.schema(self.store)
        with self.assertRaises(ValueError):
            acquisitions.grant(self.store, 'nobody')

    def test_revoke_answers_whether_it_removed_a_row(self):
        acquisitions.schema(self.store)
        self.assertTrue(acquisitions.revoke(self.store, 'a1'))
        self.assertFalse(acquisitions.revoke(self.store, 'a1'))


class Gate(unittest.TestCase):
    """The HTTP surface: /api/add for members, /api/acquisitions for administrators."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        self.store = Store(Path(self.dir.name) / 'db.sqlite')
        self.sec = Security()
        self.env = patch.dict(os.environ, {
            'HARDLYLISTENING_AUTH_REQUIRED': '1',
            'HARDLYLISTENING_SUPABASE_URL': 'https://example.supabase.co',
            'HARDLYLISTENING_SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_fixture',
            'HARDLYLISTENING_PUBLIC_ORIGIN': 'https://music.example',
            'HARDLYLISTENING_BOOTSTRAP_ADMIN_EMAIL': 'owner@example.com'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.patches = [patch.object(server, 'SECURITY', self.sec),
                        patch.object(server, 'boot', return_value=(self.store, None))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def tearDown(self):
        with self.store.lock:
            if self.store._projection_timer:
                self.store._projection_timer.cancel()
            self.store.conn.close()
        self.dir.cleanup()

    def member(self, mid, role):
        self.sec.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                'INSERT OR REPLACE INTO members(id,email,role,display_name) VALUES(?,?,?,?)',
                (mid, mid + '@example.test', role, mid))
            self.store.conn.commit()

    def token(self, mid):
        import secrets
        sid = secrets.token_urlsafe(32)
        self.sec.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                'INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)',
                (self.sec.digest(sid), mid, time.time() + 3600))
            self.store.conn.commit()
        return {'Cookie': '__Host-hl_session=' + sid, 'Origin': 'https://music.example'}

    def request(self, path, method='GET', headers=None, body=None):
        c = HTTPConnection('127.0.0.1', self.http.server_port, timeout=4)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        result = (r.status, r.read())
        c.close()
        return result

    def add(self, mid, role, url=''):
        self.member(mid, role)
        return self.request('/api/add', 'POST', self.token(mid),
                            json.dumps({'url': url}))

    # -- the member path -----------------------------------------------------
    def test_a_member_without_a_grant_is_refused_before_the_queue(self):
        status, body = self.add('m1', 'member')
        self.assertEqual(status, 403, body)
        self.assertIn(b'separate permission', body)

    def test_a_member_with_a_grant_reaches_the_endpoint(self):
        self.member('m1', 'member')
        acquisitions.schema(self.store)
        acquisitions.grant(self.store, 'm1', 'admin')
        status, body = self.request('/api/add', 'POST', self.token('m1'), '{}')
        self.assertEqual(status, 400, body)          # "paste a playlist link", not a refusal
        self.assertIn(b'paste a playlist link', body)

    # -- the administrator path, which a role must not open ------------------
    def test_an_administrator_holds_the_migration_grant(self):
        self.member('a1', 'admin')
        acquisitions.schema(self.store)
        status, body = self.request('/api/add', 'POST', self.token('a1'), '{}')
        self.assertEqual(status, 400, body)
        self.assertIn(b'paste a playlist link', body)

    def test_an_administrator_whose_grant_was_revoked_is_refused(self):
        self.member('a1', 'admin')
        acquisitions.schema(self.store)
        acquisitions.revoke(self.store, 'a1')
        status, body = self.request('/api/add', 'POST', self.token('a1'), '{}')
        self.assertEqual(status, 403, body)
        self.assertIn(b'separate permission', body)

    # -- the admin surface --------------------------------------------------
    def test_a_member_cannot_read_or_change_grants(self):
        self.member('m1', 'member')
        headers = self.token('m1')
        self.assertEqual(self.request('/api/acquisitions', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/acquisitions', 'POST', headers,
                                      json.dumps({'member_id': 'm1'}))[0], 403)

    def test_an_administrator_grants_and_revokes_through_the_route(self):
        self.member('a1', 'admin')
        self.member('m2', 'member')
        headers = self.token('a1')

        status, raw = self.request('/api/acquisitions', headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual([g['member_id'] for g in json.loads(raw)['grants']], ['a1'])

        status, raw = self.request('/api/acquisitions', 'POST', headers,
                                   json.dumps({'member_id': 'm2'}))
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)['changed'])
        self.assertTrue(acquisitions.may_acquire(self.store, {'id': 'm2'}))
        # And the member can now act on it.
        status, body = self.request('/api/add', 'POST', self.token('m2'), '{}')
        self.assertEqual(status, 400, body)

        status, raw = self.request('/api/acquisitions', 'POST', headers,
                                   json.dumps({'member_id': 'm2', 'grant': False}))
        self.assertEqual(status, 200, raw)
        self.assertFalse(acquisitions.may_acquire(self.store, {'id': 'm2'}))

    def test_the_route_refuses_an_unknown_member(self):
        self.member('a1', 'admin')
        status, body = self.request('/api/acquisitions', 'POST', self.token('a1'),
                                    json.dumps({'member_id': 'ghost'}))
        self.assertEqual(status, 400, body)


if __name__ == '__main__':
    unittest.main()