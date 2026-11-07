import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import zipfile
from http.server import ThreadingHTTPServer

from db import Store
import server


class BrowserDownloads(unittest.TestCase):
    def test_http_exports_read_only(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp) / 'music'
            root.mkdir()
            store = Store(Path(tmp) / 'fixture.db')
            store.set_settings({'root': str(root)})
            audio = root / 'tone.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=1.1', str(audio)], check=True)
            original = audio.read_bytes()
            pid = store.playlist('../../evil\r\nName', 'test', '')
            ids = []
            for i, file in enumerate((str(audio), '', str(root/'missing.wav'), str(Path(tmp)/'outside.wav'))):
                tid = store.track('test', str(i), '../../evil\r\n.wav', artist_name='a/b\\c')
                ids.append(tid)
                store.add_to_playlist(pid, tid, i)
                store.enqueue(tid, 'test')
                with store.lock:
                    store.conn.execute('UPDATE tracks SET file=? WHERE id=?', (file, tid))
                    store.conn.commit()
            (Path(tmp)/'outside.wav').write_bytes(original)
            before = [tuple(r) for r in store.conn.execute('SELECT * FROM download_jobs')]
            http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever)
            with patch.dict(os.environ, {'HARDLYLISTENING_AUTH_REQUIRED':'0'}), patch.object(server, 'boot', return_value=(store, None)):
                thread.start()
                base = f'http://127.0.0.1:{http.server_port}'
                try:
                    with urlopen(base + f'/api/track/{ids[0]}/download') as response:
                        self.assertEqual(response.headers['Content-Type'], 'audio/x-wav')
                        self.assertIn('attachment;', response.headers['Content-Disposition'])
                        self.assertNotIn('\r', response.headers['Content-Disposition'])
                        self.assertEqual(response.read(), original)
                    for tid in ids[1:]:
                        with self.assertRaises(HTTPError) as exc:
                            urlopen(base + f'/api/track/{tid}/download')
                        self.assertEqual(exc.exception.code, 404)
                        exc.exception.close()
                    for route in (f'/api/playlist/{pid}/download', '/api/library/download?ids=' + ','.join(map(str, ids))):
                        with urlopen(base + route) as response:
                            self.assertEqual(response.headers['Content-Type'], 'application/zip')
                            self.assertIn('attachment;', response.headers['Content-Disposition'])
                            data = response.read()
                        with zipfile.ZipFile(io.BytesIO(data)) as z:
                            self.assertEqual(len(z.namelist()), 2)
                            name = next(n for n in z.namelist() if n != 'MANIFEST.txt')
                            self.assertNotIn('/', name)
                            self.assertNotIn('\\', name)
                            self.assertNotIn('\n', name)
                            self.assertEqual(z.read(name), original)
                            manifest = z.read('MANIFEST.txt').decode()
                            for tid in ids[1:]:
                                self.assertIn(f'Omitted track {tid}', manifest)
                            self.assertNotIn(tmp, manifest)
                    with self.assertRaises(HTTPError) as exc:
                        urlopen(base + '/api/library/download?ids=../1')
                    exc.exception.close()
                    self.assertEqual(before, [tuple(r) for r in store.conn.execute('SELECT * FROM download_jobs')])
                    self.assertEqual(audio.read_bytes(), original)
                    self.assertEqual(len(list(root.glob('*.wav'))), 1)
                    with urlopen(base + f'/api/track/{ids[0]}/audio') as response:
                        self.assertEqual(response.read(), original)
                    with urlopen(Request(base + f'/api/track/{ids[0]}/audio', headers={'Range': 'bytes=4-19'})) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.headers['Content-Range'], f'bytes 4-19/{len(original)}')
                        self.assertEqual(response.read(), original[4:20])
                finally:
                    http.shutdown()
                    thread.join()
                    http.server_close()
                    with store.lock:
                        timer = store._projection_timer
                        if timer:
                            timer.cancel()
                    if timer:
                        timer.join()
                    store.conn.close()
