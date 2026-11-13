"""A share's URL survives its creation (HL-207).

The token used to exist only in the response that minted it: the row stored its SHA-256,
so a listing could describe a link but never show it again. Now the token is kept beside
the hash, which means the member's own listing can hand the URL back — and `getShares`,
whose payload has a `url` field, stops sending an empty one.

Lookup still goes through the hash, and a row created before the column existed says
`recreate` instead of showing a broken `/s/`.
"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import shares
from test_listening import ListeningHTTP


class ShareListing(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    token = ListeningHTTP.token
    user = ListeningHTTP.user
    api = ListeningHTTP.api
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save

    def make(self, kind='track', target=None, **extra):
        body = {'kind': kind, 'id': target if target is not None else self.ids[0], **extra}
        code, _, raw = self.request('/api/shares', 'POST', self.headers, json.dumps(body))
        self.assertEqual(code, 200, raw)
        return json.loads(raw)

    def listing(self):
        code, _, raw = self.request('/api/shares', 'GET', self.headers)
        self.assertEqual(code, 200, raw)
        return json.loads(raw)['shares']

    def test_the_listing_shows_the_url_that_was_minted(self):
        made = self.make(note='for you')
        row = next(r for r in self.listing() if r['id'] == made['id'])
        self.assertEqual(row['url'], made['url'])
        self.assertTrue(row['url'].endswith(made['token']))
        self.assertFalse(row['recreate'])

    def test_the_token_is_kept_beside_its_hash_not_instead_of_it(self):
        made = self.make()
        with self.store.lock:
            row = self.store.conn.execute(
                'SELECT token, token_hash FROM listening_shares WHERE id=?',
                (made['id'],)).fetchone()
        self.assertEqual(row['token'], made['token'])
        self.assertEqual(row['token_hash'],
                         hashlib.sha256(made['token'].encode()).hexdigest())
        # Lookup is still by hash: the bearer value is what the public page checks.
        self.assertIsNotNone(shares.lookup(self.store, made['token']))

    def test_a_row_from_before_the_token_was_kept_says_recreate(self):
        with self.store.lock:
            cursor = self.store.conn.execute(
                'INSERT INTO listening_shares'
                '(token_hash,token,member_id,kind,target,note,created,expires) '
                "VALUES(?,?,?,?,?,?,?,?)",
                (hashlib.sha256(b'gone').hexdigest(), '', 'u1', 'track',
                 str(self.ids[0]), '', __import__('time').time(),
                 __import__('time').time() + 3600))
            self.store.conn.commit()
            old_id = cursor.lastrowid
        row = next(r for r in self.listing() if r['id'] == old_id)
        self.assertEqual(row['url'], '')
        self.assertTrue(row['recreate'])

    def test_get_shares_carries_the_url(self):
        made = self.make()
        listed = self.response('getShares')['shares']['share']
        row = next(r for r in listed if r['id'] == str(made['id']))
        self.assertEqual(row['url'], made['url'])

    def test_an_old_store_gains_the_column_and_keeps_working(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'old.db'
        conn = sqlite3.connect(path)
        conn.executescript('''
            CREATE TABLE listening_shares (
              id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
              member_id TEXT NOT NULL, kind TEXT NOT NULL,
              target TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
              created REAL NOT NULL, expires REAL NOT NULL,
              hits INTEGER NOT NULL DEFAULT 0);
        ''')
        conn.execute("INSERT INTO listening_shares(token_hash,member_id,kind,target,"
                     "created,expires) VALUES('h','u1','track','1',1,2)")
        conn.commit()
        conn.close()

        class Store:
            def __init__(self, connection):
                import threading
                self.conn = connection
                self.lock = threading.Lock()
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        shares.schema(Store(connection))
        columns = {r[1] for r in connection.execute('PRAGMA table_info(listening_shares)')}
        self.assertIn('token', columns)
        row = connection.execute('SELECT token FROM listening_shares').fetchone()
        self.assertEqual(row['token'], '')
        connection.close()


if __name__ == '__main__':
    unittest.main()