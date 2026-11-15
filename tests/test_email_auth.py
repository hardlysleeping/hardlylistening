"""Accounts: signup links, passwords, and what a session is allowed to change.

The two tokens are different things and the tests keep them apart: a signup link creates the
member and dies on use; a login link belongs to a member who already exists and keeps working.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import Store
from security import Denied, Security

ORIGIN = {'HARDLYLISTENING_PUBLIC_ORIGIN': 'https://music.example'}


def fresh(security=None):
    tmp = tempfile.mkdtemp(dir=os.environ['TMPDIR'])
    store = Store(Path(tmp) / 'auth.db')
    security = security or Security()
    security.schema(store)
    return store, security, tmp


def add_member(store, member_id='admin1', role='admin', name='Admin'):
    with store.lock:
        store.conn.execute('INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)',
                           (member_id, '', role, name))
        store.conn.commit()


def close(store):
    with store.lock:
        store._projection_timer.cancel()
    store._projection_timer.join()
    store.conn.close()


class Passwords(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            one = sec.hash_password('correct horse battery')
            two = sec.hash_password('correct horse battery')
            self.assertNotEqual(one, two)                      # unique salt per hash
            self.assertNotIn('correct horse battery', one[1])
            self.assertTrue(sec.verify_password('correct horse battery', *one))
            self.assertFalse(sec.verify_password('correct horse batterY', *one))
            close(store)

    def test_short_password_is_refused(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            with self.assertRaises(Denied) as error:
                sec.validate_password('nine char')
            self.assertEqual(error.exception.code, 400)
            close(store)

    def test_existing_credential_keeps_its_login_email(self):
        store, sec, _ = fresh()
        add_member(store, 'm1', 'member')
        sec.set_password(store, 'm1', 'first password', 'm1@example.com')
        sec.set_password(store, 'm1', 'second password')
        with store.lock:
            row = store.conn.execute(
                'SELECT login_email FROM member_credentials WHERE member_id=?', ('m1',)).fetchone()
        self.assertEqual(row[0], 'm1@example.com')
        self.assertTrue(sec.authenticate_password(store, 'M1@example.com', 'second password'))
        close(store)


class SignupLinks(unittest.TestCase):
    def test_link_creates_the_member_once(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store)
            token, expires = sec.mint_signup(store, 'admin1')
            self.assertGreater(expires, time.time() + 6 * 86400)
            digest = sec.open_signup(store, token)
            sid = sec.signup(store, digest, 'Alex', 'star', 'Alex@Example.com', 'correct horse battery')
            self.assertTrue(sid)
            with store.lock:
                member = store.conn.execute(
                    "SELECT id,role,display_name,email FROM members WHERE display_name='Alex'").fetchone()
                credential = store.conn.execute('SELECT login_email FROM member_credentials').fetchone()
                invite = store.conn.execute('SELECT consumed_at,consumed_by FROM signup_invites').fetchone()
                session = store.conn.execute('SELECT auth_method FROM browser_sessions').fetchone()
            self.assertEqual(member['role'], 'member')
            self.assertEqual(member['email'], 'alex@example.com')
            self.assertEqual(credential[0], 'alex@example.com')
            self.assertIsNotNone(invite['consumed_at'])
            self.assertEqual(invite['consumed_by'], member['id'])
            self.assertEqual(session[0], 'password')
            # Second use: refused, and no second member exists.
            with self.assertRaises(Denied) as error:
                sec.signup(store, digest, 'Alex Again', 'star', 'again@example.com', 'correct horse battery')
            self.assertEqual(error.exception.code, 401)
            with store.lock:
                self.assertEqual(store.conn.execute('SELECT COUNT(*) FROM members').fetchone()[0], 2)
            close(store)

    def test_expired_link_is_refused(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store)
            token, _ = sec.mint_signup(store, 'admin1', ttl=-1)
            with self.assertRaises(Denied) as error:
                sec.open_signup(store, token)
            self.assertEqual(error.exception.code, 401)
            close(store)

    def test_unknown_token_is_refused(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            for token in ('', None, 'nope', 'x' * 300):
                with self.assertRaises(Denied):
                    sec.open_signup(store, token)
            close(store)

    def test_taken_email_does_not_burn_the_link(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store)
            digest = sec.open_signup(store, sec.mint_signup(store, 'admin1')[0])
            sec.signup(store, digest, 'First', 'star', 'taken@example.com', 'correct horse battery')
            second = sec.open_signup(store, sec.mint_signup(store, 'admin1')[0])
            with self.assertRaises(Denied) as error:
                sec.signup(store, second, 'Second', 'star', 'taken@example.com', 'correct horse battery')
            self.assertEqual(error.exception.code, 409)
            # The rollback left the second link usable with another address.
            sec.signup(store, second, 'Second', 'star', 'other@example.com', 'correct horse battery')
            close(store)

    def test_one_outstanding_invite_per_member(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store, 'm1', 'member')
            member = {'id': 'm1'}
            first = sec.mint_member_signup(store, member)
            self.assertTrue(first['url'].startswith('https://music.example/signup#invite='))
            self.assertTrue(sec.signup_invite_state(store, member)['active'])
            with self.assertRaises(Denied) as error:
                sec.mint_member_signup(store, member)
            self.assertEqual(error.exception.code, 409)
            sec.revoke_member_signup(store, member)
            self.assertFalse(sec.signup_invite_state(store, member)['active'])
            sec.mint_member_signup(store, member)                    # allowed again
            # Consuming it brings Generate back, and the state never carries the secret.
            state = sec.signup_invite_state(store, member)
            self.assertEqual(set(state), {'active', 'expires_at', 'unlimited'})
            self.assertFalse(state['unlimited'])
            close(store)

    def test_admin_account_panel_mints_without_a_cap(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store, 'a1', 'admin')
            admin = {'id': 'a1', 'role': 'admin'}
            first = sec.mint_member_signup(store, admin)
            second = sec.mint_member_signup(store, admin)            # no 409 for an admin
            self.assertTrue(first['unlimited'] and second['unlimited'])
            self.assertNotEqual(first['url'], second['url'])
            state = sec.signup_invite_state(store, admin)
            self.assertTrue(state['active'])
            self.assertTrue(state['unlimited'])
            with store.lock:
                self.assertEqual(store.conn.execute(
                    'SELECT COUNT(*) FROM signup_invites WHERE consumed_at IS NULL').fetchone()[0], 2)
            close(store)

    def test_admin_mints_freely(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store)
            for _ in range(3):
                sec.mint_signup(store, 'admin1')
            with store.lock:
                self.assertEqual(store.conn.execute(
                    'SELECT COUNT(*) FROM signup_invites').fetchone()[0], 3)
            close(store)

    def test_raw_token_is_never_stored(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store)
            token, _ = sec.mint_signup(store, 'admin1')
            with store.lock:
                dump = str([tuple(r) for r in store.conn.execute('SELECT * FROM signup_invites').fetchall()])
            self.assertNotIn(token, dump)
            self.assertIn(sec.digest(token), dump)
            close(store)


class PasswordLogin(unittest.TestCase):
    def test_wrong_password_and_unknown_email_look_the_same(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store, 'm1', 'member')
            sec.set_password(store, 'm1', 'correct horse battery', 'm1@example.com')
            messages = set()
            for address, password in (('m1@example.com', 'wrong password!'), ('nobody@example.com', 'correct horse battery')):
                with self.assertRaises(Denied) as error:
                    sec.authenticate_password(store, address, password)
                self.assertEqual(error.exception.code, 401)
                messages.add(error.exception.message)
            self.assertEqual(messages, {'Invalid email or password'})
            close(store)

    def test_blocked_member_cannot_sign_in(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store, 'm1', 'pending')
            sec.set_password(store, 'm1', 'correct horse battery', 'm1@example.com')
            with self.assertRaises(Denied) as error:
                sec.authenticate_password(store, 'm1@example.com', 'correct horse battery')
            self.assertEqual(error.exception.code, 403)
            close(store)

    def test_session_records_how_it_was_minted(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store, 'm1', 'member')
            sec.create_session(store, 'm1', 'password')
            with store.lock:
                row = store.conn.execute('SELECT auth_method FROM browser_sessions').fetchone()
            self.assertEqual(row[0], 'password')
            with self.assertRaises(Denied):
                sec.create_session(store, 'm1', 'carrier-pigeon')
            close(store)


class AccountRules(unittest.TestCase):
    def _member(self, store, sec, auth_method):
        add_member(store, 'm1', 'member')
        sec.set_password(store, 'm1', 'correct horse battery', 'm1@example.com')
        with store.lock:
            row = store.conn.execute('SELECT * FROM members WHERE id=?', ('m1',)).fetchone()
        return {**dict(row), 'auth_method': auth_method}

    def test_login_link_session_changes_password_without_the_old_one(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            member = self._member(store, sec, 'login_link')
            sec.change_password(store, member, {'new_password': 'another good one', 'confirm_password': 'another good one'})
            self.assertTrue(sec.authenticate_password(store, 'm1@example.com', 'another good one'))
            # The permanent link is untouched by a password change.
            with store.lock:
                self.assertEqual(store.conn.execute('UPDATE browser_invites SET expires=0').rowcount, 0)
            close(store)

    def test_password_session_must_confirm_the_current_one(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            member = self._member(store, sec, 'password')
            with self.assertRaises(Denied) as error:
                sec.change_password(store, member,
                                    {'new_password': 'another good one', 'confirm_password': 'another good one'})
            self.assertEqual(error.exception.code, 403)
            with self.assertRaises(Denied):
                sec.change_password(store, member, {'new_password': 'another good one',
                                                    'confirm_password': 'another good one',
                                                    'current_password': 'not it at all'})
            sec.change_password(store, member, {'new_password': 'another good one',
                                                'confirm_password': 'another good one',
                                                'current_password': 'correct horse battery'})
            self.assertTrue(sec.authenticate_password(store, 'm1@example.com', 'another good one'))
            close(store)

    def test_mismatch_and_short_passwords_are_refused(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            member = self._member(store, sec, 'login_link')
            for payload in ({'new_password': 'another good one', 'confirm_password': 'another good two'},
                            {'new_password': 'short', 'confirm_password': 'short'}):
                with self.assertRaises(Denied) as error:
                    sec.change_password(store, member, payload)
                self.assertEqual(error.exception.code, 400)
            close(store)

    def test_first_password_needs_an_address(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            add_member(store, 'm1', 'member')
            with store.lock:
                member = dict(store.conn.execute('SELECT * FROM members WHERE id=?', ('m1',)).fetchone())
            member['auth_method'] = 'login_link'
            with self.assertRaises(Denied) as error:
                sec.change_password(store, member, {'new_password': 'another good one',
                                                    'confirm_password': 'another good one'})
            self.assertEqual(error.exception.code, 400)
            sec.change_password(store, member, {'new_password': 'another good one',
                                                'confirm_password': 'another good one',
                                                'email': 'm1@example.com'})
            self.assertTrue(sec.authenticate_password(store, 'm1@example.com', 'another good one'))
            close(store)

    def test_account_never_leaks_secrets(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            member = self._member(store, sec, 'password')
            with store.lock:
                store.conn.execute("INSERT INTO member_identities VALUES(?,?,?,?,?)",
                                   ('m1', 'google', 'subject-1', 'm1@example.com', time.time()))
                store.conn.commit()
            account = sec.account(store, member)
            self.assertEqual(set(account), {'email', 'has_password', 'auth_method', 'google', 'apple'})
            self.assertEqual(account['email'], 'm1@example.com')
            self.assertTrue(account['has_password'])
            self.assertTrue(account['google'])
            self.assertFalse(account['apple'])
            with store.lock:
                row = store.conn.execute('SELECT password_hash,password_salt FROM member_credentials').fetchone()
            self.assertNotIn(row[0], str(account))
            self.assertNotIn(row[1], str(account))
            self.assertNotIn('subject-1', str(account))
            close(store)

    def test_revoke_ends_sessions_and_outstanding_signups(self):
        with patch.dict(os.environ, ORIGIN):
            store, sec, _ = fresh()
            member = self._member(store, sec, 'password')
            sec.mint_member_signup(store, {'id': 'm1'})
            sec.create_session(store, 'm1', 'password')
            sec.revoke(store, 'm1')
            with store.lock:
                sessions = store.conn.execute('SELECT COUNT(*) FROM browser_sessions').fetchone()[0]
                invites = store.conn.execute(
                    'SELECT COUNT(*) FROM signup_invites WHERE consumed_at IS NULL').fetchone()[0]
                credentials = store.conn.execute('SELECT COUNT(*) FROM member_credentials').fetchone()[0]
            self.assertEqual((sessions, invites), (0, 0))
            # A credential is not a session: blocking is what stops it, not revocation.
            self.assertEqual(credentials, 1)
            close(store)


if __name__ == '__main__':
    unittest.main()