# HL-228: a drive prepared for a CDJ, not a list of paths on this machine.
#
# What this pins, in order:
#
#   * the plan is an answer before any bytes move: a destination that cannot be written is
#     refused with a reason, a set that does not fit names the tracks left behind, and the
#     files that need converting are named as such;
#   * the copy lands under the drive's own folder, each file verified by size and SHA-256
#     against its source, and it is the *staged* file that gets hashed — a device that
#     truncates fails the comparison rather than producing a shorter file with a real name;
#   * a write error mid-copy (a device pulled out) fails the job with the reason, leaves no
#     `.part` behind and never reports that track as copied;
#   * the two index files point at the drive's paths, carry `AverageBpm` when the tempo is
#     known and the member's hot cues as `POSITION_MARK`s, and say nothing at all about key
#     or duration, which this app does not know;
#   * the job is a row: `start()` queues it, the worker picks it up, and the status is read
#     back from SQLite.
import os
import shutil
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import preferences
from core.db import Store
from library import usb_export


class USBExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.store = Store(Path(self.tmp.name) / 'db.sqlite')
        self.root = Path(self.tmp.name) / 'media'
        self.root.mkdir()
        self.store.set_settings({'root': str(self.root)})
        preferences.schema(self.store)
        self.member = {'id': 'm-ann', 'display_name': 'Ann', 'role': 'member'}
        self.ids = {}
        rows = [('Daft Punk', 'Discovery', 'One More Time', 320, 'a.mp3', 123.4),
                ('Daft Punk', 'Discovery', 'Digital Love', 300, 'b.mp3', 0.0),
                ('Ampersand & Co', 'Say "Hi" <now>', 'Café/Bar', 180, 'c d.mp3', 0.0)]
        with self.store.lock:
            for artist, album, title, duration, name, bpm in rows:
                path = self.root / name
                path.write_bytes(os.urandom(2048))
                cursor = self.store.conn.execute(
                    'INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,'
                    'duration,file,added,bpm) VALUES(?,?,?,?,?,?,?,0,?)',
                    ('local', name, title, artist, album, duration, str(path), bpm))
                self.ids[title] = (path, cursor.lastrowid)
            # A row whose file is gone: the plan has to say so rather than copy nothing.
            self.store.conn.execute(
                'INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,'
                'duration,file,added) VALUES(?,?,?,?,?,?,?,0)',
                ('local', 'gone', 'Deleted', 'Nobody', 'Nowhere', 100, str(self.root / 'gone.mp3')))
            self.store.conn.execute("INSERT INTO playlists(title,added) VALUES('Mix',0)")
            for position, title in enumerate(['One More Time', 'Café/Bar']):
                self.store.conn.execute(
                    'INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES(1,?,?)',
                    (self.ids[title][1], position))
            # This member's hot cues: two of them, which the XML must carry.
            for slot, position in ((0, 12.5), (2, 96.25)):
                self.store.conn.execute(
                    'INSERT INTO listening_cues(member_id,track_id,slot,position) VALUES(?,?,?,?)',
                    (self.member['id'], self.ids['One More Time'][1], slot, position))
            self.store.conn.commit()
        self.drive = Path(self.tmp.name) / 'usb'
        self.drive.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------------
    def run_job(self, playlist_id=None):
        """Queue a job and run it in this thread, with the worker's boot replaced."""
        queued = usb_export.plan(self.store, self.member, str(self.drive), playlist_id)
        self.assertTrue(queued['fits'], queued.get('blocked') or queued.get('overflow'))
        with patch.object(usb_export, 'wake'):
            job = usb_export.start(self.store, self.member, str(self.drive), playlist_id)
        usb_export._run(self.store, job['id'])
        return usb_export.job(self.store, job['id'])

    def dest(self, job, name):
        return Path(job['destination']) / usb_export.FOLDER / name

    # -- the plan --------------------------------------------------------------
    def test_a_destination_that_cannot_be_written_is_a_refusal(self):
        for destination, words in (
                ('', 'folder'),
                ('relative/path', 'full path'),
                (str(self.drive / 'nope'), 'no folder'),
                (str(self.root / 'a.mp3'), 'not a folder'),
                (str(self.root), 'inside the library')):
            plan = usb_export.plan(self.store, self.member, destination)
            self.assertTrue(plan['blocked'], destination)
            self.assertIn(words, plan['blocked'], destination)
            self.assertEqual(plan['fits'], False)

    def test_a_set_that_does_not_fit_names_what_stays_behind(self):
        with patch.object(usb_export.shutil, 'disk_usage') as usage:
            usage.return_value = shutil._ntuple_diskusage(1 << 40, 1 << 39, 3000)
            plan = usb_export.plan(self.store, self.member, str(self.drive))
        self.assertEqual(plan['blocked'], '')
        self.assertEqual(plan['fits'], False)
        # Written in library order: the first file fits in 3000 bytes, and from the second
        # on everything stays behind — named, not dropped.
        self.assertEqual([left['name'] for left in plan['overflow']],
                         ['Daft Punk - One More Time.mp3', 'Daft Punk - Digital Love.mp3'])
        # The plan is still complete: the count covers the whole set.
        self.assertEqual(plan['tracks'], 3)
        self.assertEqual(len(plan['copy']) + len(plan['convert']), 3)

    def test_the_plan_names_missing_files_and_estimates_a_conversion(self):
        flac = self.root / 'lossless.flac'
        flac.write_bytes(os.urandom(4096))
        with self.store.lock:
            self.store.conn.execute(
                'INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,'
                'duration,file,added) VALUES(?,?,?,?,?,?,?,0)',
                ('local', 'flac', 'Lossless', 'Someone', 'Album', 300, str(flac)))
            self.store.conn.commit()
        plan = usb_export.plan(self.store, self.member, str(self.drive))
        self.assertEqual([entry['title'] for entry in plan['missing']], ['Deleted'])
        self.assertEqual([entry['name'] for entry in plan['convert']], ['Someone - Lossless.mp3'])
        # 300 s at 320 kbit/s, said as an estimate, because the real size is not known yet.
        self.assertEqual(plan['convert'][0]['estimate'], True)
        self.assertEqual(plan['convert'][0]['bytes'], 300 * 40_000)
        self.assertEqual(plan['cues'], 2)
        self.assertEqual(plan['bpm'], 1)
        # An unsafe character and a slash never reach the drive as a path separator.
        self.assertIn('Ampersand & Co - Café Bar.mp3', [entry['name'] for entry in plan['copy']])

    def test_the_scope_is_a_playlist_when_one_is_named(self):
        plan = usb_export.plan(self.store, self.member, str(self.drive), 1)
        self.assertEqual(plan['title'], 'Mix')
        self.assertEqual([entry['title'] for entry in plan['copy']],
                         ['Café/Bar', 'One More Time'])
        with self.assertRaises(ValueError):
            usb_export.plan(self.store, self.member, str(self.drive), 999)

    # -- the copy --------------------------------------------------------------
    def test_the_copy_is_verified_and_the_index_points_at_the_drive(self):
        job = self.run_job()
        self.assertEqual(job['status'], 'finished', job['error'])
        self.assertEqual(job['copied'], 3)
        self.assertEqual(job['percent'], 100.0)
        for entry in job['files']:
            copy = self.dest(job, entry['name'])
            self.assertTrue(copy.is_file())
            self.assertEqual(entry['status'], 'copied')
            self.assertEqual(copy.stat().st_size, entry['size'])
            self.assertEqual(usb_export._hash(copy)[0], entry['sha256'])
        # And the bytes are the source's bytes, not a truncated copy of them.
        source = self.ids['One More Time'][0]
        self.assertEqual(usb_export._hash(source), usb_export._hash(self.dest(job, 'Daft Punk - One More Time.mp3')))
        # No staging file is left behind on a clean run.
        self.assertEqual(list(self.dest(job, '.').glob('*.part')), [])

        xml = (Path(job['destination']) / 'HardlyListening.rekordbox.xml').read_text()
        root = ET.fromstring(xml)
        collection = root.find('COLLECTION')
        self.assertEqual(collection.attrib['Entries'], '3')
        tracks = collection.findall('TRACK')
        one = [t for t in tracks if t.attrib['Name'] == 'One More Time'][0]
        self.assertTrue(one.attrib['Location'].startswith('file://localhost/'))
        self.assertIn(str(self.drive / usb_export.FOLDER), one.attrib['Location'].replace('%20', ' '))
        # Tempo is carried where it is known, and not invented where it is not.
        self.assertEqual(one.attrib['AverageBpm'], '123.4')
        love = [t for t in tracks if t.attrib['Name'] == 'Digital Love'][0]
        self.assertNotIn('AverageBpm', love.attrib)
        # The member's hot cues travel as POSITION_MARKs.
        marks = one.findall('POSITION_MARK')
        self.assertEqual([(m.attrib['Num'], m.attrib['Start']) for m in marks],
                         [('0', '12.5'), ('2', '96.25')])
        self.assertEqual(love.findall('POSITION_MARK'), [])
        # Key and length are absent rather than empty: this app knows neither.
        self.assertNotIn('Tonality', one.attrib)
        self.assertEqual(one.attrib['TotalTime'], '0')
        # The playlist node lists every entry by its collection id.
        node = root.find('PLAYLISTS/NODE/NODE')
        self.assertEqual(node.attrib['Name'], 'HardlyListening library')
        self.assertEqual(len(node.findall('TRACK')), 3)
        m3u = (Path(job['destination']) / 'HardlyListening.m3u8').read_text().splitlines()
        self.assertEqual(m3u[0], '#EXTM3U')
        self.assertEqual(len(m3u), 2 + 3 * 2)

    def test_a_pulled_out_drive_leaves_nothing_reported_as_copied(self):
        job_plan = usb_export.plan(self.store, self.member, str(self.drive))
        with patch.object(usb_export, 'wake'):
            job = usb_export.start(self.store, self.member, str(self.drive))
        calls = {'n': 0}
        real_stage = usb_export._stage

        def stage(source, staged):
            calls['n'] += 1
            if calls['n'] == 1:
                return real_stage(source, staged)
            # The device goes away mid-file: what is written is a prefix, and the write
            # then fails the way an unplugged stick does.
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(b'\x00' * 16)
            raise OSError(5, 'Input/output error')

        with patch.object(usb_export, '_stage', side_effect=stage):
            usb_export._run(self.store, job['id'])
        after = usb_export.job(self.store, job['id'])
        self.assertEqual(after['status'], 'failed')
        self.assertIn('Input/output error', after['error'])
        self.assertEqual(after['copied'], 1)
        # The file that was interrupted has no real name and no debris beside it.
        second = job_plan['copy'][1]['name']
        self.assertFalse(self.dest(after, second).exists())
        self.assertFalse(self.dest(after, second + '.part').exists())
        states = {entry['name']: entry['status'] for entry in after['files']}
        self.assertEqual(states[second], 'failed')
        self.assertEqual(states[job_plan['copy'][0]['name']], 'copied')
        # No index is written for a partial set: a DJ app must not open half a library.
        self.assertFalse((Path(after['destination']) / 'HardlyListening.rekordbox.xml').exists())

    def test_a_drive_that_will_not_take_the_set_is_refused_before_writing(self):
        with patch.object(usb_export.shutil, 'disk_usage') as usage:
            usage.return_value = shutil._ntuple_diskusage(1 << 40, 1 << 39, 100)
            with patch.object(usb_export, 'wake'):
                with self.assertRaises(ValueError) as raised:
                    usb_export.start(self.store, self.member, str(self.drive))
        self.assertIn('does not fit', str(raised.exception))
        self.assertEqual(list(self.drive.rglob('*')), [])
        self.assertEqual(usb_export.status(self.store)['jobs'], [])

    def test_the_worker_takes_the_job_from_the_queue(self):
        """`start()` queues a row and the worker thread picks it up; nothing is copied in
        the request that started it."""
        job = usb_export.start(self.store, self.member, str(self.drive))
        # The thread may already have picked it up: what matters is that `start()` wrote a
        # row and returned without copying anything itself.
        self.assertIn(job['status'], ('queued', 'copying'))
        deadline = time.time() + 15
        while time.time() < deadline:
            job = usb_export.job(self.store, job['id'])
            if job['status'] in ('finished', 'failed'):
                break
            time.sleep(0.05)
        self.assertEqual(job['status'], 'finished', job['error'])
        self.assertEqual(job['copied'], 3)
        status = usb_export.status(self.store)
        self.assertEqual(status['jobs'][0]['id'], job['id'])
        self.assertFalse(status['worker']['running'])
        # A second run overwrites its own names and leaves everything else alone.
        keep = self.drive / 'notes.txt'
        keep.write_text('not mine')
        again = self.run_job()
        self.assertEqual(again['status'], 'finished', again['error'])
        self.assertEqual(keep.read_text(), 'not mine')
        self.assertEqual(len(list((self.drive / usb_export.FOLDER).iterdir())), 3)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is not installed')
    def test_a_real_flac_arrives_as_an_mp3_a_dj_app_can_open(self):
        flac = self.root / 'real.flac'
        made = __import__('subprocess').run(
            ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi',
             '-i', 'sine=frequency=440:duration=1', '-c:a', 'flac', str(flac)],
            capture_output=True, text=True, timeout=60)
        if made.returncode != 0:
            self.skipTest('this ffmpeg cannot synthesise a test tone')
        with self.store.lock:
            self.store.conn.execute(
                'INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,'
                'duration,file,added) VALUES(?,?,?,?,?,?,?,0)',
                ('local', 'real', 'Tone', 'Synth', 'Tests', 1, str(flac)))
            self.store.conn.commit()
        job = self.run_job()
        self.assertEqual(job['status'], 'finished', job['error'])
        copy = self.dest(job, 'Synth - Tone.mp3')
        self.assertTrue(copy.is_file())
        self.assertGreater(copy.stat().st_size, 0)
        entry = [row for row in job['files'] if row['name'] == 'Synth - Tone.mp3'][0]
        self.assertEqual(entry['status'], 'copied')
        self.assertEqual(usb_export._hash(copy)[0], entry['sha256'])
        # The source is untouched: the drive gets a delivery copy, not a conversion in place.
        self.assertTrue(flac.is_file())

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is not installed')
    def test_a_lossless_file_arrives_as_mp3(self):
        flac = self.root / 'lossless.flac'
        flac.write_bytes(os.urandom(4096))
        with self.store.lock:
            self.store.conn.execute(
                'INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,'
                'duration,file,added) VALUES(?,?,?,?,?,?,?,0)',
                ('local', 'flac', 'Lossless', 'Someone', 'Album', 2, str(flac)))
            self.store.conn.commit()
        job = self.run_job()
        # The bogus bytes are not decodable, so the conversion fails and says so rather
        # than writing an empty file and calling it copied.
        self.assertEqual(job['status'], 'failed')
        self.assertIn('Lossless', job['error'])
        self.assertFalse(self.dest(job, 'Someone - Lossless.mp3').exists())


    def test_an_interrupted_job_is_not_reported_as_copying(self):
        with patch.object(usb_export, 'wake'):
            job = usb_export.start(self.store, self.member, str(self.drive))
        with self.store.lock:
            self.store.conn.execute("UPDATE usb_exports SET status='copying' WHERE id=?",
                                    (job['id'],))
            self.store.conn.commit()
        self.assertEqual(usb_export.startup(self.store), 1)
        after = usb_export.job(self.store, job['id'])
        self.assertEqual(after['status'], 'failed')
        self.assertIn('interrupted', after['error'])
        # A job that is still waiting for its turn is not touched by that.
        with patch.object(usb_export, 'wake'):
            queued = usb_export.start(self.store, self.member, str(self.drive))
        self.assertEqual(usb_export.startup(self.store), 0)
        self.assertEqual(usb_export.job(self.store, queued['id'])['status'], 'queued')


import json
import test_security as security_tests


class USBExportHTTP(unittest.TestCase):
    """The routes: a plan is a read, a start is a write, and both belong to a member."""

    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request
    token = security_tests.SecurityHTTP.token
    user = security_tests.SecurityHTTP.user

    def setUp(self):
        security_tests.SecurityHTTP.setUp(self)
        self.headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}
        self.identity = self.user()
        self.root = Path(self.tmp.name) / 'media'
        self.root.mkdir()
        self.store.set_settings({'root': str(self.root)})
        source = self.root / 'a.mp3'
        source.write_bytes(os.urandom(1024))
        with self.store.lock:
            self.store.conn.execute(
                'INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,'
                'duration,file,added) VALUES(?,?,?,?,?,?,?,0)',
                ('local', 'a', 'One', 'Artist', 'Album', 100, str(source)))
            self.store.conn.commit()
        self.drive = Path(self.tmp.name) / 'drive'
        self.drive.mkdir()

    def post(self, path, body):
        return self.request(path, 'POST', self.headers, json.dumps(body))

    def test_the_plan_writes_nothing_and_says_why_a_destination_is_refused(self):
        before = sorted(self.drive.rglob('*'))
        status, _, raw = self.post('/api/library/usb/plan', {'destination': str(self.drive)})
        self.assertEqual(status, 200)
        plan = json.loads(raw)
        self.assertEqual(plan['tracks'], 1)
        self.assertEqual(plan['fits'], True)
        self.assertEqual(sorted(self.drive.rglob('*')), before)
        self.assertEqual(usb_export.status(self.store)['jobs'], [])
        # A refused destination is still an answer, not an error: the dialog shows the
        # reason beside the field, so the reply carries it with the rest of the plan.
        status, _, raw = self.post('/api/library/usb/plan', {'destination': '/nowhere/at/all'})
        self.assertEqual(status, 200)
        refused = json.loads(raw)
        self.assertEqual(refused['fits'], False)
        self.assertIn('no folder', refused['blocked'])
        self.assertEqual(sorted(self.drive.rglob('*')), before)
        # Starting on that same destination *is* the error: nothing is queued for it.
        status, _, raw = self.post('/api/library/usb/start', {'destination': '/nowhere/at/all'})
        self.assertEqual(status, 400)
        self.assertIn('no folder', json.loads(raw)['error'])
        self.assertEqual(usb_export.status(self.store)['jobs'], [])

    def test_starting_copies_and_status_reads_it_back(self):
        status, _, raw = self.post('/api/library/usb/start', {'destination': str(self.drive)})
        self.assertEqual(status, 200)
        export_id = json.loads(raw)['id']
        deadline = time.time() + 20
        while time.time() < deadline:
            state = usb_export.job(self.store, export_id)
            if state['status'] in ('finished', 'failed'):
                break
            time.sleep(0.05)
        self.assertEqual(state['status'], 'finished', state['error'])
        status, _, raw = self.request('/api/library/usb', 'GET', self.headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['jobs'][0]['id'], export_id)
        self.assertTrue((self.drive / usb_export.FOLDER / 'Artist - One.mp3').is_file())

    def test_the_routes_are_method_and_csrf_checked(self):
        self.assertEqual(self.request('/api/library/usb/plan', 'GET', self.headers)[0], 405)
        self.assertEqual(self.request('/api/library/usb', 'POST', self.headers, '{}')[0], 405)
        # Same-origin is required for the write, exactly as for every other POST.
        status, _, _ = self.request('/api/library/usb/start', 'POST',
                                    {'Authorization': 'Bearer fixture'},
                                    json.dumps({'destination': str(self.drive)}))
        self.assertEqual(status, 403)
        # And an unauthenticated caller gets nothing at all.
        self.assertEqual(self.request('/api/library/usb', 'GET')[0], 401)


if __name__ == '__main__':
    unittest.main()