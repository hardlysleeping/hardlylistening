"""Wrong song? — the versions a person chooses between, and the undo beside them.

Read-only: nothing here changes a source, deletes audio or starts a download. The
change stays on /api/track/<id>/source, which stages and verifies before switching.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import replacements
import test_security as security_tests


class Candidates(unittest.TestCase):
    # The security fixture is a test case, not a base class: bind the parts this suite uses
    # without inheriting (and re-running) its own tests. tearDown is not optional — a live
    # ThreadingHTTPServer thread otherwise keeps the process from ever exiting.
    setUp = security_tests.SecurityHTTP.setUp
    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request
    user = security_tests.SecurityHTTP.user
    token = security_tests.SecurityHTTP.token

    def setUp(self):
        security_tests.SecurityHTTP.setUp(self)
        self.identity = self.user(role='admin')
        self.root = Path(self.tmp.name) / 'media'
        self.root.mkdir()
        self.store.set_settings({'root': str(self.root)})
        self.tid = self.store.track('soundcloud', 'sc-abc', 'Atmosphere',
                                    artist_name='FISHER', duration=221)
        self.jid = self.store.enqueue(self.tid, 'soundcloud', 'https://soundcloud.com/x/wrong')
        self.store.update_job(self.jid, status='completed',
                              resolved_url='https://soundcloud.com/x/wrong', pinned=0)
        self.hits = [
            {'url': 'https://soundcloud.com/x/right', 'title': 'Atmosphere', 'artist': 'FISHER',
             'duration': 220.5, 'artwork': '', 'engine': 'scsearch', 'provider': 'soundcloud',
             'match': 0.91},
            {'url': 'https://soundcloud.com/x/remix', 'title': 'Atmosphere (Remix)',
             'artist': 'Someone', 'duration': 310, 'artwork': '', 'engine': 'scsearch',
             'provider': 'soundcloud', 'match': 0.62},
        ]

    def get(self, query=''):
        path = f'/api/track/{self.tid}/candidates' + (f'?q={query}' if query else '')
        return self.request(path, headers={'Authorization': 'Bearer fixture'})

    def test_lists_versions_for_the_tracks_own_metadata(self):
        with patch('search.find', return_value=self.hits) as engine:
            code, _, body = self.get()
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(engine.call_args[0][0], 'FISHER Atmosphere')
        self.assertEqual([r['url'] for r in data['results']],
                         ['https://soundcloud.com/x/right', 'https://soundcloud.com/x/remix'])
        self.assertEqual(data['track']['title'], 'Atmosphere')
        self.assertEqual(data['track']['resolved_url'], 'https://soundcloud.com/x/wrong')
        self.assertIsNone(data['replacement'])

    def test_edited_words_replace_the_query(self):
        with patch('search.find', return_value=self.hits) as engine:
            code, _, body = self.get('fisher%20atmosphere%20original')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['query'], 'fisher atmosphere original')
        self.assertEqual(engine.call_args[0][0], 'fisher atmosphere original')

    def test_retained_previous_file_is_offered_as_the_undo(self):
        old = self.root / 'old.mp3'
        old.write_bytes(b'\xff\xfb' * 400)
        self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(old), self.tid))
        self.store.conn.commit()
        replacements.stage(self.store, self.tid, 'https://soundcloud.com/x/right')
        with patch('search.find', return_value=self.hits):
            code, _, body = self.get()
        self.assertEqual(code, 200)
        replacement = json.loads(body)['replacement']
        self.assertEqual(replacement['status'], 'queued')
        self.assertEqual(replacement['prior_url'], 'https://soundcloud.com/x/wrong')
        self.assertGreater(replacement['id'], 0)

    def test_members_cannot_read_the_options(self):
        self.user(role='member')
        self.identity = {'id': 'u1', 'email': 'friend@example.com', 'email_confirmed_at': 'yes'}
        with patch('search.find', return_value=self.hits):
            code, _, _ = self.get()
        self.assertEqual(code, 403)

    def test_a_local_import_has_no_job_to_change(self):
        local = self.store.track('local', 'content-key', 'Imported', artist_name='Me')
        with patch('search.find', return_value=self.hits):
            code, _, _ = self.request(f'/api/track/{local}/candidates',
                                      headers={'Authorization': 'Bearer fixture'})
        self.assertEqual(code, 400)
