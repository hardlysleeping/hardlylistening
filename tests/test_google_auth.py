"""Google sign-in, and linking a Google account to an existing member.

Two properties matter more than the plumbing:

* an identity is attached only while somebody is signed in, so equal email
  addresses never merge two members;
* a Google account nobody linked is refused, because this library is
  invite-only — signing in must not quietly create an account.

Everything here talks to Google's endpoints only through a patched seam, so the
suite runs offline. The signature check is exercised against a real locally
generated RSA key, not a fake verifier.
"""
import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import oauth_google
import server
from db import Store
from security import SECURITY, Security, Denied

ORIGIN = 'https://music.example'
CLIENT_ID = 'client-id.apps.googleusercontent.com'
ENV = {'HARDLYLISTENING_PUBLIC_ORIGIN': ORIGIN,
       'GOOGLE_CLIENT_ID': CLIENT_ID,
       'GOOGLE_CLIENT_SECRET': 'client-secret',
       'GOOGLE_CLIENT_SECRET_FILE': ''}


# -- tokens ---------------------------------------------------------------

def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def keypair(kid='test-kid'):
    """A private key plus the JWK Google would publish for it."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {'kty': 'RSA', 'kid': kid, 'alg': 'RS256', 'use': 'sig',
           'n': b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, 'big')),
           'e': b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, 'big'))}
    return key, jwk


def sign(key, kid='test-kid', header=None, **claims):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(time.time())
    payload = {'iss': 'https://accounts.google.com', 'aud': CLIENT_ID,
               'sub': 'google-subject-1', 'email': 'person@example.com',
               'iat': now, 'exp': now + 600, 'email_verified': True}
    payload.update(claims)
    head = header if header is not None else {'alg': 'RS256', 'kid': kid, 'typ': 'JWT'}
    signing = b64(json.dumps(head).encode()) + '.' + b64(json.dumps(payload).encode())
    signature = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing + '.' + b64(signature)


class TokenVerification(unittest.TestCase):
    """An unverified token is just JSON a stranger wrote, so nothing is trusted before the check."""

    def setUp(self):
        self.key, self.jwk = keypair()
        self.keys = patch.object(oauth_google, '_keys_by_kid',
                                 return_value={'test-kid': self.jwk})
        self.keys.start()
        self.addCleanup(self.keys.stop)

    def test_a_token_google_signed_is_accepted_and_its_claims_returned(self):
        claims = oauth_google.verify(sign(self.key), CLIENT_ID)
        self.assertEqual(claims['sub'], 'google-subject-1')
        self.assertEqual(claims['email'], 'person@example.com')

    def test_a_token_signed_by_someone_else_is_refused(self):
        other, _ = keypair()                       # same kid, different private key
        with self.assertRaises(Denied) as error:
            oauth_google.verify(sign(other), CLIENT_ID)
        self.assertEqual(error.exception.code, 401)
        self.assertIn('signature', str(error.exception).lower())

    def test_an_unsigned_token_is_refused_without_looking_at_its_claims(self):
        head = b64(json.dumps({'alg': 'none', 'kid': 'test-kid'}).encode())
        body = b64(json.dumps({'sub': 'attacker', 'aud': CLIENT_ID,
                               'iss': 'https://accounts.google.com',
                               'exp': time.time() + 600}).encode())
        with self.assertRaises(Denied) as error:
            oauth_google.verify(head + '.' + body + '.', CLIENT_ID)
        self.assertEqual(error.exception.code, 401)

    def test_a_token_for_another_application_is_refused(self):
        with self.assertRaises(Denied):
            oauth_google.verify(sign(self.key, aud='somebody-else'), CLIENT_ID)

    def test_an_expired_token_is_refused(self):
        with self.assertRaises(Denied):
            oauth_google.verify(sign(self.key, exp=int(time.time()) - 3600), CLIENT_ID)

    def test_a_token_from_another_issuer_is_refused(self):
        with self.assertRaises(Denied):
            oauth_google.verify(sign(self.key, iss='https://evil.example'), CLIENT_ID)

    def test_a_key_google_does_not_publish_is_refused(self):
        with self.assertRaises(Denied):
            oauth_google.verify(sign(self.key, kid='nope'), CLIENT_ID)

    def test_a_malformed_token_is_refused(self):
        with self.assertRaises(Denied):
            oauth_google.verify('not-a-token', CLIENT_ID)


class StateCookie(unittest.TestCase):
    def test_the_state_and_verifier_survive_the_round_trip(self):
        with patch.dict(os.environ, ENV):
            url, cookie = oauth_google.start('link')
            saved = oauth_google.parse_cookie(cookie)
        self.assertIsNotNone(saved)
        self.assertEqual(saved['mode'], 'link')
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query['state'][0], saved['state'])   # the callback is tied to this browser
        self.assertEqual(query['client_id'][0], CLIENT_ID)
        self.assertEqual(query['code_challenge_method'][0], 'S256')
        self.assertEqual(query['redirect_uri'][0], ORIGIN + '/auth/google/callback')
        # PKCE: the challenge is the hash of a verifier that never leaves the cookie.
        import hashlib
        self.assertEqual(query['code_challenge'][0],
                         b64(hashlib.sha256(saved['verifier'].encode()).digest()))

    def test_an_expired_or_garbled_cookie_is_refused(self):
        self.assertIsNone(oauth_google.parse_cookie(''))
        self.assertIsNone(oauth_google.parse_cookie('state|verifier|link|soon'))
        stale = '|'.join(('state', 'verifier', 'link', str(int(time.time()) - 5)))
        self.assertIsNone(oauth_google.parse_cookie(stale))

    def test_an_unknown_mode_falls_back_to_signing_in(self):
        with patch.dict(os.environ, ENV):
            _url, cookie = oauth_google.start('something-else')
        self.assertEqual(oauth_google.parse_cookie(cookie)['mode'], 'signin')

    def test_nothing_is_offered_without_credentials(self):
        with patch.dict(os.environ, {'GOOGLE_CLIENT_ID': '', 'GOOGLE_CLIENT_SECRET': ''}):
            self.assertFalse(oauth_google.available())
            with self.assertRaises(Denied) as error:
                oauth_google.start('signin')
        self.assertEqual(error.exception.code, 503)


# -- store-level identity handling ---------------------------------------

def fresh():
    tmp = tempfile.mkdtemp(dir=os.environ['TMPDIR'])
    store = Store(Path(tmp) / 'google.db')
    security = Security()
    security.schema(store)
    with store.lock:
        store.conn.execute("INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                           ('member-1', 'one@example.com', 'member', 'One'))
        store.conn.execute("INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                           ('member-2', 'two@example.com', 'member', 'Two'))
        store.conn.commit()
    return store


def close(store):
    with store.lock:
        store._projection_timer.cancel()
    store._projection_timer.join()
    store.conn.close()


class Identities(unittest.TestCase):
    def setUp(self):
        self.store = fresh()
        self.addCleanup(close, self.store)

    def test_linking_files_the_identity_under_the_member(self):
        with patch.dict(os.environ, ENV):
            outcome = oauth_google.link(self.store, 'member-1',
                                        {'sub': 'google-1', 'email': 'One@Example.com'})
        self.assertEqual(outcome, 'linked')
        self.assertEqual(oauth_google.identity_member(self.store, 'google-1'), 'member-1')
        with self.store.lock:
            row = self.store.conn.execute(
                'SELECT email FROM member_identities WHERE provider_subject=?',
                ('google-1',)).fetchone()
        self.assertEqual(row[0], 'one@example.com')     # stored normalised, not as typed

    def test_linking_again_is_harmless(self):
        with patch.dict(os.environ, ENV):
            oauth_google.link(self.store, 'member-1', {'sub': 'google-1', 'email': 'a@example.com'})
            again = oauth_google.link(self.store, 'member-1', {'sub': 'google-1', 'email': 'b@example.com'})
        self.assertEqual(again, 'already')

    def test_an_identity_belonging_to_another_member_is_not_stolen(self):
        with patch.dict(os.environ, ENV):
            oauth_google.link(self.store, 'member-1', {'sub': 'google-1', 'email': 'a@example.com'})
            with self.assertRaises(Denied) as error:
                oauth_google.link(self.store, 'member-2', {'sub': 'google-1', 'email': 'a@example.com'})
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(oauth_google.identity_member(self.store, 'google-1'), 'member-1')

    def test_equal_emails_never_merge_members(self):
        """member-2's address is the Google account's address, but member-1 asked to link it."""
        with patch.dict(os.environ, ENV):
            oauth_google.link(self.store, 'member-1', {'sub': 'google-2', 'email': 'two@example.com'})
        self.assertEqual(oauth_google.identity_member(self.store, 'google-2'), 'member-1')
        with self.store.lock:
            count = self.store.conn.execute('SELECT count(*) FROM members').fetchone()[0]
        self.assertEqual(count, 2)                      # nobody was folded into anybody

    def test_unlinking_leaves_the_member_and_its_login_link_alone(self):
        with patch.dict(os.environ, ENV):
            oauth_google.link(self.store, 'member-1', {'sub': 'google-1', 'email': 'a@example.com'})
        self.assertTrue(oauth_google.unlink(self.store, 'member-1'))
        self.assertIsNone(oauth_google.identity_member(self.store, 'google-1'))
        self.assertFalse(oauth_google.unlink(self.store, 'member-1'))   # nothing left to remove
        with self.store.lock:
            count = self.store.conn.execute('SELECT count(*) FROM members').fetchone()[0]
        self.assertEqual(count, 2)


# -- the routes -----------------------------------------------------------

class Routes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(dir=os.environ['TMPDIR'])
        self.store = Store(Path(tmp) / 'routes.db')
        SECURITY.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                ('member-1', 'one@example.com', 'member', 'One'))
            self.store.conn.commit()
        env = patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)
        self.boot = patch.object(server, 'boot', return_value=(self.store, None))
        self.boot.start()
        self.addCleanup(self.boot.stop)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.http.shutdown()
        self.thread.join(timeout=10)
        self.http.server_close()
        close(self.store)

    def request(self, path, cookie='', method='GET', body=None, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=15)
        headers = {'Connection': 'close'}
        if cookie:
            headers['Cookie'] = cookie
        if origin:
            headers['Origin'] = origin
        payload = b''
        if body is not None:
            payload = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        result = {'status': response.status, 'location': response.getheader('Location') or '',
                  'cookies': response.headers.get_all('Set-Cookie') or [],
                  'body': response.read()}
        connection.close()
        return result

    def start(self, mode='signin', session=''):
        """Begin a round trip the way the browser would, and hand back what to send."""
        url, value = oauth_google.start(mode)
        state = parse_qs(urlparse(url).query)['state'][0]
        cookie = f'__Host-hl_oauth={value}'
        if session:
            cookie += f'; __Host-hl_session={session}'
        return state, cookie

    def session(self, member_id='member-1'):
        return SECURITY.create_session(self.store, member_id)

    # -- what the pages can offer -----------------------------------------

    def test_providers_reports_google_only_when_it_is_configured(self):
        self.assertEqual(json.loads(self.request('/api/auth/providers')['body']), {'google': True})
        with patch.dict(os.environ, {'GOOGLE_CLIENT_ID': '', 'GOOGLE_CLIENT_SECRET': ''}):
            self.assertEqual(json.loads(self.request('/api/auth/providers')['body']),
                             {'google': False})

    def test_starting_without_credentials_says_so_instead_of_failing(self):
        with patch.dict(os.environ, {'GOOGLE_CLIENT_ID': '', 'GOOGLE_CLIENT_SECRET': ''}):
            result = self.request('/auth/google')
        self.assertEqual(result['status'], 303)
        self.assertEqual(result['location'], '/login?google=unavailable')

    def test_starting_sends_the_browser_to_google_with_a_state_cookie(self):
        result = self.request('/auth/google')
        self.assertEqual(result['status'], 303)
        self.assertTrue(result['location'].startswith('https://accounts.google.com/o/oauth2/v2/auth?'))
        self.assertTrue(any('__Host-hl_oauth=' in c for c in result['cookies']))

    # -- signing in -------------------------------------------------------

    def test_an_unlinked_google_account_is_refused_and_creates_nobody(self):
        state, cookie = self.start('signin')
        claims = {'sub': 'stranger', 'email': 'stranger@example.com'}
        with patch.object(oauth_google, 'exchange', return_value=claims):
            result = self.request(f'/auth/google/callback?state={state}&code=abc', cookie=cookie)
        self.assertEqual(result['status'], 303)
        self.assertEqual(result['location'], '/login?google=unknown')
        self.assertFalse(any('__Host-hl_session' in c for c in result['cookies']))
        with self.store.lock:
            members = self.store.conn.execute('SELECT count(*) FROM members').fetchone()[0]
        self.assertEqual(members, 1)                    # invite-only: nobody appeared

    def test_a_linked_account_signs_in_as_its_own_member(self):
        with patch.dict(os.environ, ENV):
            oauth_google.link(self.store, 'member-1', {'sub': 'known', 'email': 'one@example.com'})
        state, cookie = self.start('signin')
        with patch.object(oauth_google, 'exchange', return_value={'sub': 'known'}):
            result = self.request(f'/auth/google/callback?state={state}&code=abc', cookie=cookie)
        self.assertEqual(result['status'], 303)
        self.assertEqual(result['location'], '/')
        self.assertTrue(any('__Host-hl_session=' in c for c in result['cookies']))
        with self.store.lock:
            row = self.store.conn.execute(
                'SELECT member_id, auth_method FROM browser_sessions').fetchone()
        self.assertEqual((row[0], row[1]), ('member-1', 'google'))

    def test_a_callback_with_the_wrong_state_changes_nothing(self):
        _state, cookie = self.start('signin')
        with patch.object(oauth_google, 'exchange', return_value={'sub': 'known'}) as exchange:
            result = self.request('/auth/google/callback?state=forged&code=abc', cookie=cookie)
        self.assertEqual(result['location'], '/login?google=state')
        exchange.assert_not_called()                    # no code is spent on a request we cannot trust

    def test_a_cancelled_google_prompt_is_reported_as_cancelled(self):
        state, cookie = self.start('signin')
        result = self.request(f'/auth/google/callback?state={state}&error=access_denied', cookie=cookie)
        self.assertEqual(result['location'], '/login?google=denied')

    def test_a_callback_without_a_cookie_is_refused(self):
        result = self.request('/auth/google/callback?state=abc&code=abc')
        self.assertEqual(result['location'], '/login?google=expired')

    # -- linking ----------------------------------------------------------

    def test_linking_while_signed_in_attaches_the_identity(self):
        state, cookie = self.start('link', session=self.session())
        claims = {'sub': 'attached', 'email': 'one@example.com'}
        with patch.object(oauth_google, 'exchange', return_value=claims):
            result = self.request(f'/auth/google/callback?state={state}&code=abc', cookie=cookie)
        self.assertEqual(result['status'], 303)
        self.assertEqual(result['location'], '/?google=linked')
        self.assertEqual(oauth_google.identity_member(self.store, 'attached'), 'member-1')
        # Linking must not quietly re-mint the session as a Google one.
        with self.store.lock:
            methods = {r[0] for r in self.store.conn.execute(
                'SELECT auth_method FROM browser_sessions')}
        self.assertEqual(methods, {'login_link'})

    def test_linking_a_google_account_that_belongs_to_someone_else_is_refused(self):
        with patch.dict(os.environ, ENV):
            oauth_google.link(self.store, 'member-1', {'sub': 'taken'})
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)",
                ('member-2', 'two@example.com', 'member', 'Two'))
            self.store.conn.commit()
        state, cookie = self.start('link', session=self.session('member-2'))
        with patch.object(oauth_google, 'exchange', return_value={'sub': 'taken'}):
            result = self.request(f'/auth/google/callback?state={state}&code=abc', cookie=cookie)
        self.assertEqual(result['location'], '/?google=conflict')
        self.assertEqual(oauth_google.identity_member(self.store, 'taken'), 'member-1')

    def test_a_link_round_trip_that_lost_its_session_says_to_sign_in_first(self):
        state, cookie = self.start('link')
        with patch.object(oauth_google, 'exchange', return_value={'sub': 'attached'}):
            result = self.request(f'/auth/google/callback?state={state}&code=abc', cookie=cookie)
        self.assertEqual(result['location'], '/login?google=signin-first')
        self.assertIsNone(oauth_google.identity_member(self.store, 'attached'))

    def test_unlinking_removes_the_identity_and_keeps_the_account(self):
        with patch.dict(os.environ, ENV):
            oauth_google.link(self.store, 'member-1', {'sub': 'attached'})
        result = self.request('/api/account/google/unlink', cookie=f'__Host-hl_session={self.session()}',
                              method='POST', body={}, origin=ORIGIN)
        self.assertEqual(result['status'], 200)
        self.assertEqual(json.loads(result['body']), {'ok': True})
        self.assertIsNone(oauth_google.identity_member(self.store, 'attached'))
        with self.store.lock:
            members = self.store.conn.execute('SELECT count(*) FROM members').fetchone()[0]
        self.assertEqual(members, 1)

    def test_a_cross_site_unlink_is_refused(self):
        with patch.dict(os.environ, ENV):
            oauth_google.link(self.store, 'member-1', {'sub': 'attached'})
        result = self.request('/api/account/google/unlink', cookie=f'__Host-hl_session={self.session()}',
                              method='POST', body={}, origin='https://evil.example')
        self.assertEqual(result['status'], 403)
        self.assertEqual(oauth_google.identity_member(self.store, 'attached'), 'member-1')

    def test_the_profile_says_whether_google_can_be_offered(self):
        profile = json.loads(self.request('/api/profile',
                                          cookie=f'__Host-hl_session={self.session()}')['body'])
        self.assertTrue(profile['google_available'])
        self.assertFalse(profile['google'])


if __name__ == '__main__':
    unittest.main()