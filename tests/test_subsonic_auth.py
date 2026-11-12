"""Subsonic password auth, MD5 token auth, and the OpenSubsonic capability flag.

Token auth needs the device secret recoverable (`t = md5(password + s)` cannot be derived
from the stored SHA-256), so devices created now keep it in the Subsonic `enc:` hex form.
These tests pin both auth paths and the "old rows stay password-only" behaviour.
"""
import hashlib
import json
import unittest
from urllib.parse import urlencode

from test_listening import ListeningHTTP


class SubsonicAuth(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    api = ListeningHTTP.api
    response = ListeningHTTP.response
    token = ListeningHTTP.token
    user = ListeningHTTP.user

    def call(self, **args):
        query = urlencode(dict(f='json', v='1.16.1', c='fixture', **args))
        _, _, raw = self.request('/rest/ping.view?' + query)
        return json.loads(raw)['subsonic-response']

    def test_a_new_device_keeps_its_secret_for_token_auth(self):
        self.assertTrue(self.device['token_auth'])
        row = self.store.conn.execute('SELECT token_secret FROM listening_devices WHERE id=?',
                                      (self.device['username'],)).fetchone()
        self.assertEqual(bytes.fromhex(row[0]).decode(), self.device['password'])
        self.assertEqual(self.call(u=self.device['username'], p=self.device['password'])['status'], 'ok')

    def test_token_auth_matches_md5_of_password_plus_salt(self):
        salt = 'abc123'
        digest = hashlib.md5((self.device['password'] + salt).encode()).hexdigest()
        self.assertEqual(self.call(u=self.device['username'], t=digest, s=salt)['status'], 'ok')
        # Clients are inconsistent about hex case.
        self.assertEqual(self.call(u=self.device['username'], t=digest.upper(), s=salt)['status'], 'ok')
        self.assertEqual(self.call(u=self.device['username'], t='0' * 32, s=salt)['error']['code'], 40)
        self.assertEqual(self.call(u=self.device['username'], t=digest)['error']['code'], 40)

    def test_a_device_without_a_kept_secret_stays_password_only(self):
        self.store.conn.execute('UPDATE listening_devices SET token_secret=NULL WHERE id=?',
                                (self.device['username'],))
        self.store.conn.commit()
        digest = hashlib.md5((self.device['password'] + 'x').encode()).hexdigest()
        self.assertEqual(self.call(u=self.device['username'], t=digest, s='x')['error']['code'], 40)
        self.assertEqual(self.call(u=self.device['username'], p=self.device['password'])['status'], 'ok')

    def test_a_wrong_password_is_one_generic_refusal(self):
        refused = self.call(u=self.device['username'], p='nope')
        self.assertEqual(refused['error']['code'], 40)
        self.assertEqual(refused['error']['message'], 'Invalid device credentials')
        self.assertEqual(self.call(u='hl_unknown', p='nope')['error']['code'], 40)

    def test_a_revoked_device_cannot_use_either_method(self):
        self.api('devices/revoke', {'id': self.device['username']})
        salt = 'zz'
        digest = hashlib.md5((self.device['password'] + salt).encode()).hexdigest()
        self.assertEqual(self.call(u=self.device['username'], t=digest, s=salt)['error']['code'], 40)
        self.assertEqual(self.call(u=self.device['username'], p=self.device['password'])['error']['code'], 40)

    def test_credentials_never_appear_in_the_response(self):
        _, _, raw = self.request('/rest/ping.view?' + urlencode(dict(
            u=self.device['username'], p=self.device['password'], f='json')))
        self.assertNotIn(self.device['password'].encode(), raw)


if __name__ == '__main__':
    unittest.main()