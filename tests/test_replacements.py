"""Isolated fake acquisitions and real silent WAV validation/HTTP ranges."""
import io
import os
import json
import threading
import tempfile
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import decisions
import replacements
import server
from db import Store
from engine.queue import Queue


def wav(value):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes([value, 0]) * 32000)
    return output.getvalue()


class Replacements(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'music'
        self.root.mkdir()
        self.store = Store(Path(self.tmp.name) / 'fixture.db')
        self.addCleanup(self.store.conn.close)
        self.store.set_settings({'root': str(self.root), 'audio_format': 'wav'})
        self.queue = Queue(self.store)  # never start worker
        self.tid = self.store.track('apple', 'replacement-fixture', 'Song')
        self.jid = self.store.enqueue(self.tid, 'apple')
        self.old = self.root / 'original.wav'
        self.original = wav(1)
        self.new = wav(2)
        self.old.write_bytes(self.original)
        self.store.conn.execute('UPDATE tracks SET file=?,size=? WHERE id=?', (str(self.old),len(self.original),self.tid))
        self.store.conn.commit()
        self.store.update_job(self.jid, status='completed', resolved_url='https://example.test/old', pinned=0)

    def stage(self):
        replacements.stage(self.store, self.tid, 'https://example.test/new')
        return replacements.pending(self.store, self.jid)

    def retained_path(self):
        """Where the original media is parked while a swap is in place."""
        for path in (self.root / '.replacements' / 'retained').rglob('*'):
            if path.is_file() and path.read_bytes() == self.original:
                return path
        return self.old

    def preserved(self):
        """The library is back on the original media and records the old source."""
        self.assertEqual(Path(self.store.track_by_id(self.tid)['file']).read_bytes(), self.original)
        self.assertEqual(self.store.job(self.jid)['resolved_url'], 'https://example.test/old')

    def fake(self, cmd, **kwargs):
        row = replacements.pending(self.store, self.jid)
        self.assertEqual(self.store.track_by_id(self.tid)['file'], row['old_file'])
        # The media being replaced survives every generation, wherever it lives now.
        self.assertTrue(Path(row['old_file']).is_file())
        path = Path(cmd[cmd.index('-o') + 1].replace('%(ext)s', 'wav'))
        self.assertNotEqual(path, self.old)
        path.write_bytes(self.new)
        return 0, ''

    def run_fake(self, effect=None):
        with patch('replacements.ytdlp.run', side_effect=effect or self.fake):
            self.queue._run(self.store.job(self.jid))

    def test_success_reverse_and_range_follow_canonical(self):
        row = self.stage()
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever)
        with patch.dict(os.environ, {'HARDLYLISTENING_AUTH_REQUIRED':'0'}), patch.object(server, 'boot', return_value=(self.store,self.queue)):
            thread.start()
            try:
                def check(data):
                    request = Request(f'http://127.0.0.1:{http.server_port}/api/track/{self.tid}/audio', headers={'Range':'bytes=40-99'})
                    with urlopen(request) as response:
                        self.assertEqual(response.status,206)
                        self.assertEqual(response.read(),data[40:100])
                check(self.original)
                self.run_fake()
                self.assertEqual(Path(self.store.track_by_id(self.tid)['file']).read_bytes(),self.new)
                self.assertEqual(self.store.job(self.jid)['resolved_url'],'https://example.test/new')
                check(self.new)
                decisions.reverse(self.store,-row['id'])
                check(self.original)
                self.preserved()
                done = self.store.conn.execute(
                    "SELECT new_file FROM source_replacements WHERE id=?", (row['id'],)).fetchone()
                self.assertTrue(Path(done['new_file']).is_file())   # canonical, not staging
            finally:
                http.shutdown(); thread.join(); http.server_close()

    def test_switch_sql_failure_rolls_back_canonical(self):
        self.stage()
        self.store.conn.execute("""CREATE TRIGGER fixture_reject_switch BEFORE UPDATE ON source_replacements
            WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT, 'fixture switch failure'); END""")
        self.store.conn.commit()
        self.run_fake()
        self.preserved()
        self.assertEqual(decisions.entries(self.store)[0]['status'],'replacement: failed')

    def test_failed_verify_preserves_original_no_retry(self):
        self.stage()
        self.new = b'not audio'
        self.run_fake()
        self.preserved()
        self.assertEqual(decisions.entries(self.store)[0]['status'],'replacement: failed')
        self.assertIsNone(self.store.next_job())

    def test_download_error_and_exception_preserve_original(self):
        for effect in [lambda *a,**kw:(1,'ERROR: unavailable'), RuntimeError('fixture failure')]:
            self.stage()
            self.run_fake(effect)
            self.preserved()
            self.assertIsNone(self.store.next_job())

    def test_cancel_during_download(self):
        self.stage()
        def cancelled(*args,**kwargs):
            self.fake(*args,**kwargs)
            self.queue.cancel(self.jid)
            return 0,''
        self.run_fake(cancelled)
        self.preserved()
        self.assertEqual(decisions.entries(self.store)[0]['status'],'replacement: cancelled')

    def test_cancel_during_verify_never_switches(self):
        self.stage()
        real_verify = replacements.ytdlp.verify_audio
        def verify(path):
            self.queue.cancel(self.jid)
            return real_verify(path)
        with patch('replacements.ytdlp.verify_audio',side_effect=verify):
            self.run_fake()
        self.preserved()

    def test_restart_interrupted_and_queued_clear(self):
        row = self.stage()
        decisions.clear(self.store)
        self.assertEqual(decisions.entries(self.store),[])
        self.assertIsNotNone(replacements.pending(self.store,self.jid))
        self.store.conn.execute("UPDATE source_replacements SET status='downloading' WHERE id=?",(row['id'],))
        self.store.update_job(self.jid,status='downloading')
        other = Store(Path(self.tmp.name)/'fixture.db')
        try:
            Queue(other)
            self.assertEqual(other.job(self.jid)['status'],'completed')
            self.assertIsNone(other.next_job())
            self.preserved()
            self.assertEqual(other.conn.execute('SELECT status FROM source_replacements').fetchone()[0],'interrupted')
        finally:
            other.conn.close()

    def test_queued_restart_then_success_clear_keeps_media(self):
        self.stage()
        Queue(self.store)
        self.run_fake()
        file = self.store.track_by_id(self.tid)['file']
        before = self.store.job(self.jid)
        decisions.clear(self.store)
        self.assertEqual(decisions.entries(self.store),[])
        self.assertEqual(self.store.job(self.jid),before)
        self.assertEqual(Path(file).read_bytes(),self.new)
        self.assertEqual(self.retained_path().read_bytes(),self.original)

    def test_reverse_missing_previous_refused_and_new_survives(self):
        row = self.stage(); self.run_fake()
        self.retained_path().unlink()  # only isolated fixture
        with self.assertRaisesRegex(ValueError,'not valid'):
            replacements.reverse(self.store,row['id'])
        self.assertEqual(Path(self.store.track_by_id(self.tid)['file']).read_bytes(),self.new)

    def test_duplicate_active_stage_and_stale_reverse_refused(self):
        first = self.stage()
        with self.assertRaisesRegex(ValueError,'active'):
            self.stage()
        self.run_fake()
        second = self.stage()
        with self.assertRaisesRegex(ValueError,'active'):
            replacements.reverse(self.store,first['id'])
        self.run_fake()
        with self.assertRaisesRegex(ValueError,'latest'):
            replacements.reverse(self.store,first['id'])
        replacements.reverse(self.store,second['id'])
        replacements.reverse(self.store,first['id'])
        self.preserved()

    def test_clear_during_download_does_not_cancel(self):
        self.stage()
        def clear_while_running(*args, **kwargs):
            before = self.store.job(self.jid)
            decisions.clear(self.store)
            self.assertEqual(self.store.job(self.jid), before)
            return self.fake(*args, **kwargs)
        self.run_fake(clear_while_running)
        self.assertEqual(decisions.entries(self.store), [])
        self.assertEqual(Path(self.store.track_by_id(self.tid)['file']).read_bytes(), self.new)


import test_security as security_tests


class ReplacementHTTP(unittest.TestCase):
    setUp = security_tests.SecurityHTTP.setUp
    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request
    user = security_tests.SecurityHTTP.user
    token = security_tests.SecurityHTTP.token

    def test_explicit_admin_stage_cancel_and_reverse(self):
        self.identity = self.user('admin')
        headers = {'Authorization':'Bearer fixture', 'Origin':'https://music.example'}
        root = Path(self.tmp.name) / 'media'; root.mkdir()
        self.store.set_settings({'root':str(root), 'audio_format':'wav'})
        old = root / 'old.wav'; old.write_bytes(wav(1))
        tid = self.store.track('apple','fixture','Song')
        jid = self.store.enqueue(tid,'apple')
        self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?',(str(old),tid)); self.store.conn.commit()
        self.store.update_job(jid,status='completed',resolved_url='https://example.test/old')
        queue = Queue(self.store)
        def post(path, data):
            return self.request(path,'POST',headers,json.dumps(data))[0]
        with patch.object(server,'boot',return_value=(self.store,queue)):
            self.identity = self.user('member')
            self.assertEqual(post(f'/api/track/{tid}/source',{'url':'https://example.test/new'}),403)
            self.identity = self.user('admin')
            self.assertEqual(post(f'/api/track/{tid}/source',{'url':''}),409)
            self.assertEqual(post(f'/api/track/{tid}/source',{'url':'https://example.test/new'}),200)
            row = replacements.pending(self.store,jid)
            self.assertEqual(post(f'/api/track/{tid}/skip',{}),200)
            self.assertIsNone(replacements.pending(self.store,jid))
            self.assertEqual(self.store.job(jid)['status'],'completed')
            self.assertEqual(self.store.track_by_id(tid)['file'],str(old))
            # A stale claimed job must not perform a cancelled replacement.
            with patch('replacements.ytdlp.run') as downloader:
                replacements.run(queue,self.store.job(jid),row)
                downloader.assert_not_called()
            self.assertEqual(post(f'/api/track/{tid}/source',{'url':'https://example.test/new'}),200)
            def fake(cmd, **kwargs):
                Path(cmd[cmd.index('-o')+1].replace('%(ext)s','wav')).write_bytes(wav(2))
                return 0,''
            with patch('replacements.ytdlp.run',side_effect=fake):
                queue._run(self.store.job(jid))
            row = decisions.entries(self.store)[0]
            self.assertEqual(row['status'],'replacement: completed')
            self.assertEqual(post('/api/decisions/reverse',{'id':row['id']}),200)
            self.assertEqual(self.store.track_by_id(tid)['file'],str(old))
            self.assertEqual(old.read_bytes(),wav(1))
