"""Public PWA assets never boot a worker or touch a real database."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from unittest.mock import patch
import server

class PWA(unittest.TestCase):
    def test_public_static_assets_without_boot(self):
        with patch.object(server, 'boot', side_effect=AssertionError('PWA must not boot')):
            http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                origin = 'http://127.0.0.1:' + str(http.server_port)
                for path, mime in [('/app.webmanifest','application/manifest+json'),('/sw.js','javascript'),('/offline.html','text/html'),('/polish.css','text/css'),('/lifecycle.js','javascript'),('/icon-192.png','image/png'),('/icon-512.png','image/png')]:
                    with urlopen(origin + path) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(mime, response.headers['Content-Type'])
                        body = response.read()
                        self.assertTrue(body)
                        if path == '/app.webmanifest':
                            manifest = json.loads(body)
                            self.assertEqual((manifest['scope'],manifest['start_url'],manifest['display']),('/', '/', 'standalone'))
            finally:
                http.shutdown(); http.server_close(); thread.join()
