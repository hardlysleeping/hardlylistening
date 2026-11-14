"""The sonic half of audio analysis: fingerprints, neighbours, and a path (HL-205).

Tempo shipped already. This is the vector half, and the one place in the tree that imports
numpy — by decision (2026-11-14), not by accident: the analysis is a real dependency, imported
directly, with no guarded probe and no interpreted fallback. Everything else stays stdlib so
there is still no build step.

What is pinned here:

* a fingerprint is deterministic — the same file twice is byte-identical;
* it is L2-normalised and exactly `fingerprint.DIMS` long, and a silence is not a fingerprint
  at all (no row, never a zero vector that would score 0 against everything);
* cosine neighbours order a near copy above a distant tone, and never include the seed;
* asking about an unanalysed track enqueues it for the worker and answers empty rather than
  inventing neighbours;
* a path is a greedy walk with a visited set and a hop cap, so it always terminates;
* the worker reports honest progress, and the Subsonic surface serves all of it.

The audio fixtures are tones written with the `wave` module, so nothing here touches the real
library and the whole file runs in a couple of seconds.
"""
import math
import os
import random
import struct
import sys
import tempfile
import time
import unittest
import wave

from test_listening import ListeningHTTP
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RATE = 22050


def tone(path, frequency, seconds=3.0, noise=0.0, amplitude=0.6):
    """A mono sine at `frequency` with optional white noise, written as a 16-bit WAV."""
    generator = random.Random(int(frequency))
    frames = []
    for index in range(int(RATE * seconds)):
        value = amplitude * math.sin(2 * math.pi * frequency * index / RATE)
        if noise:
            value += noise * (generator.random() * 2 - 1)
        frames.append(struct.pack('<h', max(-32768, min(32767, int(value * 32767)))))
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b''.join(frames))
    return path


def silence(path, seconds=3.0):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b'\x00\x00' * int(RATE * seconds))
    return path


class SonicCase(unittest.TestCase):
    def setUp(self):
        import db
        import security
        # Her own temp directory, removed on the way out: left behind, a few thousand runs
        # of this file fill the caller's TMPDIR.
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or None)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.store = db.Store(str(self.dir / 'sonic.db'))
        try:
            db.Schema(security.Security()).apply(self.store)
        except AttributeError:
            pass
        from library import service
        service.schema(self.store)
        from library import sonic
        sonic.schema(self.store)
        self.addCleanup(self.store.close)

    def track(self, name, **paths):
        media = tone(self.dir / (name + '.wav'), **paths)
        track_id = self.store.track('local', name, name, artist_name='Artist',
                                    duration=3.0)
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?',
                                    (str(media), track_id))
            self.store.conn.commit()
        return track_id

    def analysed(self, limit=50):
        from library import sonic
        return sonic.analyse_pending(self.store, limit=limit)


class Fingerprint(SonicCase):
    def test_the_same_file_twice_is_the_same_vector(self):
        from library import fingerprint
        media = tone(self.dir / 'same.wav', frequency=440)
        first = fingerprint.vector(media)
        second = fingerprint.vector(media)
        self.assertEqual(first, second)
        self.assertEqual(len(first), fingerprint.DIMS)

    def test_the_vector_is_unit_length(self):
        from library import fingerprint
        vector = fingerprint.vector(tone(self.dir / 'unit.wav', frequency=330))
        norm = math.sqrt(sum(value * value for value in vector))
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_a_silence_is_not_a_fingerprint(self):
        from library import fingerprint
        self.assertIsNone(fingerprint.vector(silence(self.dir / 'quiet.wav')))

    def test_a_missing_or_broken_file_raises_fingerprint_error(self):
        from library import fingerprint
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.vector(self.dir / 'nope.wav')
        broken = self.dir / 'broken.wav'
        broken.write_bytes(b'not audio at all')
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.vector(broken)

    def test_a_tone_and_a_noisy_copy_are_closer_than_two_different_tones(self):
        # A pure tone is the degenerate case for this maths: its spectrum is one spike, so the
        # absolute cosine of a copy is modest and the ordering is what carries the meaning.
        # Measured on these fixtures: same tone with light noise ~0.54, a tone an octave and a
        # half away ~-0.19.
        from library import fingerprint
        from library import sonic
        clean = fingerprint.vector(tone(self.dir / 'clean.wav', frequency=440))
        noisy = fingerprint.vector(tone(self.dir / 'noisy.wav', frequency=440, noise=0.02))
        other = fingerprint.vector(tone(self.dir / 'far.wav', frequency=1200))
        near = sonic.cosine(clean, noisy)
        far = sonic.cosine(clean, other)
        self.assertGreater(near, far)
        self.assertGreater(near, 0.4)
        self.assertLess(far, 0.2)


class Neighbours(SonicCase):
    def test_similar_orders_a_near_copy_above_a_distant_tone(self):
        from library import sonic
        seed = self.track('seed', frequency=440)
        twin = self.track('twin', frequency=442)
        far = self.track('far', frequency=1500)
        self.analysed()
        for track_id in (seed, twin, far):
            self.assertIsNotNone(sonic.analysed_vector(self.store, track_id))
        rows = sonic.similar(self.store, seed, limit=5)
        self.assertEqual([row['id'] for row in rows][:2], [twin, far])
        self.assertNotIn(seed, [row['id'] for row in rows])
        self.assertGreater(rows[0]['score'], rows[1]['score'])

    def test_an_unanalysed_track_is_enqueued_and_answers_empty(self):
        from library import sonic
        seed = self.track('pending', frequency=440)
        self.track('other', frequency=441)
        self.assertEqual(sonic.similar(self.store, seed, limit=5), [])
        self.assertIn(seed, sonic.queue_ids())
        self.assertGreaterEqual(sonic.status()['queued'], 1)
        sonic.clear_queue()

    def test_unknown_track_answers_nothing_rather_than_guessing(self):
        from library import sonic
        self.assertEqual(sonic.similar(self.store, 10 ** 9, limit=5), [])


class Paths(SonicCase):
    def test_a_path_walks_the_nearest_neighbours_and_ends_on_the_target(self):
        from library import sonic
        first = self.track('a', frequency=440)
        middle = self.track('b', frequency=442)
        last = self.track('c', frequency=444)
        self.track('z', frequency=2000)
        self.analysed()
        walk = sonic.path(self.store, first, last, hops=6)
        self.assertEqual(walk[0], first)
        self.assertEqual(walk[-1], last)
        self.assertEqual(len(walk), len(set(walk)))
        self.assertIn(middle, walk)

    def test_a_path_that_cannot_reach_the_target_stops_instead_of_looping(self):
        from library import sonic
        first = self.track('lonely', frequency=440)
        far = self.track('unreachable', frequency=3000)
        self.analysed()
        # Nothing sits between them, so the walk has one candidate and then nothing new.
        walk = sonic.path(self.store, first, far, hops=2)
        self.assertEqual(walk[0], first)
        self.assertEqual(len(walk), len(set(walk)))
        self.assertLessEqual(len(walk), 3)

    def test_a_path_to_an_unanalysed_track_is_refused(self):
        from library import sonic
        first = self.track('known', frequency=440)
        self.analysed()
        unknown = self.track('unknown', frequency=500)
        with self.assertRaises(ValueError):
            sonic.path(self.store, first, unknown, hops=4)


class Worker(SonicCase):
    def test_the_worker_analyses_pending_tracks_and_reports_progress(self):
        from library import sonic
        for index, frequency in enumerate((440, 550, 660)):
            self.track('w%d' % index, frequency=frequency)
        sonic.clear_status()
        result = sonic.start(self.store)
        self.assertTrue(result['started'])
        deadline = time.time() + 60
        while sonic.status()['running'] and time.time() < deadline:
            time.sleep(0.05)
        state = sonic.status()
        self.assertFalse(state['running'])
        self.assertEqual(state['done'], 3)
        self.assertEqual(state['failed'], 0)
        self.assertEqual(sonic.count(self.store), 3)

    def test_status_is_honest_before_anything_runs(self):
        from library import sonic
        sonic.clear_status()
        state = sonic.status()
        self.assertFalse(state['running'])
        self.assertEqual(state['total'], 0)
        self.assertEqual(state['done'], 0)
        self.assertEqual(state['percent'], 0.0)

    def test_re_analysis_can_be_forced(self):
        from library import sonic
        track = self.track('again', frequency=440)
        self.analysed()
        first = sonic.analysed_vector(self.store, track)
        self.assertIsNotNone(first)
        self.assertEqual(sonic.pending(self.store), [])
        self.assertIn(track, sonic.pending(self.store, force=True))


class SubsonicSurface(unittest.TestCase):
    """The endpoints, driven over HTTP the way a client drives them."""

    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    api = ListeningHTTP.api
    request = ListeningHTTP.request
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    token = ListeningHTTP.token
    user = ListeningHTTP.user

    def setUp(self):
        ListeningHTTP.setUp(self)
        from library import sonic
        sonic.schema(self.store)
        self.dir = Path(self.tmp.name)
        self.seed = self.track('s1', 440)
        self.twin = self.track('s2', 442)
        self.last = self.track('s3', 444)
        self.far = self.track('s4', 2000)
        report = sonic.analyse_pending(self.store, limit=20)
        self.assertGreaterEqual(report['done'], 4, report)

    def track(self, name, frequency):
        media = tone(self.dir / (name + '.wav'), frequency=frequency)
        track_id = self.store.track('local', 'sonic-' + name, name, artist_name='Artist',
                                    duration=3.0)
        with self.store.lock:
            self.store.conn.execute('UPDATE tracks SET file=? WHERE id=?',
                                    (str(media), track_id))
            # A Subsonic answer only carries songs in the member's own scope.
            self.store.conn.execute('INSERT OR IGNORE INTO listening_tracks VALUES(?,?)',
                                    (self.identity['id'], track_id))
            self.store.conn.commit()
        return track_id

    def test_similar_songs_answers_the_neighbour(self):
        answer = self.response('getSimilarSongs2', id=str(self.seed), count='5')
        ids = [int(row['id']) for row in answer['similarSongs2']['song']]
        self.assertEqual(ids[0], self.twin)
        self.assertNotIn(self.seed, ids)

    def test_sonic_similar_tracks_is_the_same_answer_under_its_own_key(self):
        answer = self.response('getSonicSimilarTracks', id=str(self.seed), count='5')
        ids = [int(row['id']) for row in answer['sonicSimilarTracks']['song']]
        self.assertEqual(ids[0], self.twin)

    def test_a_page_that_is_not_a_song_is_refused(self):
        refused = self.response('getSimilarSongs2')      # no id at all
        self.assertEqual(refused['error']['code'], 70)

    def test_a_sonic_path_comes_back_as_songs(self):
        answer = self.response('findSonicPath', startId=str(self.seed), endId=str(self.last))
        ids = [int(row['id']) for row in answer['sonicPath']['song']]
        self.assertEqual(ids[0], self.seed)
        self.assertEqual(ids[-1], self.last)

    def test_an_unknown_id_is_error_70(self):
        for endpoint, params in (('getSimilarSongs2', {'id': '999999'}),
                                 ('getSonicSimilarTracks', {'id': '999999'}),
                                 ('findSonicPath', {'startId': '999999', 'endId': '999998'})):
            refused = self.response(endpoint, **params)
            self.assertEqual(refused['error']['code'], 70, endpoint)

    def test_an_unanalysed_track_answers_empty_and_is_queued(self):
        from library import sonic
        fresh = self.track('pending', 300)
        answer = self.response('getSonicSimilarTracks', id=str(fresh))
        self.assertEqual(answer['sonicSimilarTracks']['song'], [])
        self.assertIn(fresh, sonic.queue_ids())
        sonic.clear_queue()


class WebApi(SubsonicSurface):
    """The app's own /api/sonic surface. Same fixture as the Subsonic class above."""

    def get(self, path):
        import json as json_module
        code, _headers, raw = self.request(path, 'GET', self.headers)
        return code, json_module.loads(raw)

    def test_status_reports_the_worker_and_how_many_are_analysed(self):
        code, body = self.get('/api/sonic/status')
        self.assertEqual(code, 200, body)
        self.assertFalse(body['running'])
        self.assertGreaterEqual(body['analysed'], 4)

    def test_a_database_that_has_never_analysed_anything_still_answers(self):
        # A fresh library has no `track_features` table at all; the first status read has to
        # create it rather than answer 500.
        with self.store.lock:
            self.store.conn.execute('DROP TABLE IF EXISTS track_features')
            self.store.conn.commit()
        code, body = self.get('/api/sonic/status')
        self.assertEqual(code, 200, body)
        self.assertEqual(body['analysed'], 0)
        code, body = self.get('/api/sonic/similar/%d' % self.seed)
        self.assertEqual(code, 200, body)
        self.assertEqual(body['similar'], [])

    def test_similar_answers_neighbours_with_what_a_row_needs(self):
        code, body = self.get('/api/sonic/similar/%d' % self.seed)
        self.assertEqual(code, 200, body)
        self.assertEqual(body['similar'][0]['id'], self.twin)
        self.assertEqual(body['similar'][0]['title'], 's2')
        self.assertNotIn(self.seed, [row['id'] for row in body['similar']])

    def test_a_path_between_two_tracks_is_a_list_of_rows(self):
        code, body = self.get('/api/sonic/path?from=%d&to=%d' % (self.seed, self.last))
        self.assertEqual(code, 200, body)
        self.assertEqual(body['path'][0]['id'], self.seed)
        self.assertEqual(body['path'][-1]['id'], self.last)

    def test_a_path_without_both_ends_is_a_400(self):
        code, body = self.get('/api/sonic/path?from=%d' % self.seed)
        self.assertEqual(code, 400, body)

    def test_a_path_from_an_unanalysed_track_is_a_409(self):
        fresh = self.track('fresh', 300)
        code, body = self.get('/api/sonic/path?from=%d&to=%d' % (self.seed, fresh))
        self.assertEqual(code, 409, body)
        self.assertIn('analysed', body['error'])

    def test_analyse_with_nothing_left_to_do_says_so(self):
        import acquisitions
        import json as json_module
        acquisitions.schema(self.store)
        acquisitions.grant(self.store, self.identity['id'])
        code, _headers, raw = self.request('/api/sonic/analyse', 'POST', self.headers,
                                          json_module.dumps({}))
        body = json_module.loads(raw)
        self.assertEqual(code, 200, body)
        self.assertFalse(body['started'])
        self.assertEqual(body['reason'], 'nothing to analyse')


if __name__ == '__main__':
    unittest.main()