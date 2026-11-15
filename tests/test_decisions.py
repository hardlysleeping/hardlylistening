"""Scratch-only decision durability and non-destructive reversal tests."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import decisions
from db import Store
from downloaders import ai_match
import test_ai_match as fixtures


import test_security as security_tests


class DecisionHTTP(unittest.TestCase):
    setUp = security_tests.SecurityHTTP.setUp
    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request
    user = security_tests.SecurityHTTP.user
    token = security_tests.SecurityHTTP.token

    def test_admin_only_csrf_and_readback(self):
        self.identity = self.user('admin')
        headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}
        self.assertEqual(self.request('/api/decisions')[0], 401)
        tid = self.store.track('apple', 'audit', 'Song', artist_name='Artist')
        self.store.enqueue(tid, 'apple', '')
        job = dict(self.store.job_for_track(tid)); track = self.store.track_by_id(tid)
        decisions.record(self.store, job, track, fixtures.HIT, [fixtures.HIT], 'Metadata', 'selected')
        code, _, body = self.request('/api/decisions', headers=headers)
        self.assertEqual(code, 200); row = json.loads(body)['entries'][0]
        no_origin = {'Authorization': 'Bearer fixture'}
        self.assertEqual(self.request('/api/decisions/clear', 'POST', no_origin, '{}')[0], 403)
        self.identity = self.user('member')
        self.assertEqual(self.request('/api/decisions', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/decisions/clear', 'POST', headers, '{}')[0], 403)
        self.identity = self.user('admin')
        self.assertEqual(self.request('/api/decisions/clear', 'POST', headers, json.dumps({'id':row['id']}))[0], 200)
        self.assertEqual(json.loads(self.request('/api/decisions', headers=headers)[2])['entries'], [])
        self.assertIsNotNone(self.store.track_by_id(tid)); self.assertIsNotNone(self.store.job_for_track(tid))

    def test_source_change_and_reverse_keep_existing_audio(self):
        self.identity = self.user('admin')
        headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}
        tid = self.store.track('apple', 'audit', 'Song', artist_name='Artist')
        self.store.enqueue(tid, 'apple', '')
        audio = Path(self.tmp.name) / 'preserved.mp3'; audio.write_bytes(b'fixture-audio')
        self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(audio),tid)); self.store.conn.commit()
        job = dict(self.store.job_for_track(tid)); track = self.store.track_by_id(tid)
        decisions.record(self.store, job, track, None, [], 'Metadata', 'review')
        row = decisions.entries(self.store)[0]
        self.assertEqual(self.request(f'/api/track/{tid}/source','POST',headers,json.dumps({'url':'https://example.test/source'}))[0],409)
        self.assertEqual(self.request('/api/decisions/reverse','POST',headers,json.dumps({'id':row['id']}))[0],409)
        self.assertEqual(audio.read_bytes(),b'fixture-audio')


class DecisionsTests(fixtures.IntegrationTests):
    def select(self):
        with patch('engine.queue.ytdlp.search_candidates', return_value=[fixtures.HIT]), patch(
                'engine.queue.ai_match.judge', return_value=ai_match.Judgment('accept', 'Metadata comparison: accept')):
            self.assertEqual(self.queue._resolve_source(self.job, self.track), fixtures.HIT['url'])
        return decisions.entries(self.store)[0]

    def test_durable_history_and_clear_does_not_change_jobs_or_audio(self):
        row = self.select()
        other = Store(Path(self.tmp.name) / 'fixture.db')
        try:
            self.assertEqual(decisions.entries(other)[0]['chosen_url'], fixtures.HIT['url'])
            self.assertEqual(json.loads(row['candidates'])[0]['metadata_decision'], 'accept')
        finally:
            other.conn.close()
        audio = Path(self.tmp.name) / 'preserved.mp3'; audio.write_bytes(b'fixture-preserve')
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(audio), self.track['id']))
            self.store.conn.commit()
        before = dict(self.store.job_for_track(self.track['id']))
        decisions.clear(self.store, row['id'])
        self.assertEqual(decisions.entries(self.store), [])
        self.assertEqual(dict(self.store.job_for_track(self.track['id'])), before)
        self.assertEqual(audio.read_bytes(), b'fixture-preserve')
        self.select(); decisions.clear(self.store)
        self.assertEqual(decisions.entries(self.store), [])
        self.assertEqual(audio.read_bytes(), b'fixture-preserve')

    def test_reverse_pending_goes_to_review(self):
        row = self.select()
        self.store.update_job(self.job['id'], status='retry_wait')
        decisions.reverse(self.store, row['id'])
        job = self.store.job_for_track(self.track['id'])
        self.assertEqual(job['status'], 'review'); self.assertEqual(job['resolved_url'], '')
        self.assertEqual(decisions.entries(self.store)[0]['status'], 'reversed')

    def test_reverse_downloaded_and_active_refused(self):
        row = self.select()
        for status in ['resolving', 'downloading']:
            self.store.update_job(self.job['id'], status=status)
            with self.assertRaisesRegex(ValueError, 'Stop the active'):
                decisions.reverse(self.store, row['id'])
        self.store.update_job(self.job['id'], status='completed')
        audio = Path(self.tmp.name) / 'preserved.mp3'; audio.write_bytes(b'fixture-preserve')
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (str(audio), self.track['id']))
            self.store.conn.commit()
        with self.assertRaisesRegex(ValueError, 'preserved'):
            decisions.reverse(self.store, row['id'])
        self.assertEqual(audio.read_bytes(), b'fixture-preserve')
        self.assertEqual(self.store.job_for_track(self.track['id'])['status'], 'completed')

    def test_wrong_versions_cannot_be_accepted_by_model(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'fixture-not-secret'}):
            for version in ['Remix', 'Cover', 'Live', 'Acoustic', 'Clean', 'Explicit', 'Sped Up']:
                with self.subTest(version=version), patch.object(ai_match.request, 'urlopen', return_value=fixtures.response()):
                    self.assertEqual(ai_match.judge(fixtures.TRACK, dict(fixtures.HIT, title=f'Song ({version})')).decision, 'reject')

    def test_fallback_after_soundcloud_rejection_keeps_floor(self):
        first = dict(fixtures.HIT, engine='scsearch')
        second = dict(fixtures.HIT, url='https://youtube.com/watch?v=fixture', engine='ytsearch')
        with patch('engine.queue.ytdlp.query_variants', return_value=['Song Fixture']), patch(
                'engine.queue.ytdlp.search_candidates', side_effect=[[first], [first, second]]) as search, patch(
                'engine.queue.ai_match.judge', side_effect=[ai_match.Judgment('reject', 'wrong'), ai_match.Judgment('accept', 'match')]):
            self.assertEqual(self.queue._resolve_source(self.job, self.track), second['url'])
            self.assertEqual(search.call_args.kwargs['sc_trust'], 1.1)
            self.assertNotIn('min_accept', search.call_args.kwargs)
