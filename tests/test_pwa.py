"""Public PWA assets never boot a worker or touch a real database."""
import json
import os
import re
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from unittest.mock import patch
import server

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(HERE, 'web')


class PWA(unittest.TestCase):
    def test_public_static_assets_without_boot(self):
        with patch.object(server, 'boot', side_effect=AssertionError('PWA must not boot')):
            http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                origin = 'http://127.0.0.1:' + str(http.server_port)
                for path, mime in [('/app.webmanifest','application/manifest+json'),('/sw.js','javascript'),('/offline.html','text/html'),('/lifecycle.js','javascript'),('/icon-192.png','image/png'),('/icon-512.png','image/png')]:
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

    def test_stylesheet_tree_is_complete_and_precached(self):
        """The page's CSS is a tree of imports. A file that is imported but missing, or a
        stylesheet the worker does not precache, is an unstyled offline page — neither shows
        up until the network is gone, so both are asserted here from the files themselves.
        The CSS is served behind auth, so this reads disk rather than making a request."""
        with open(os.path.join(WEB, 'index.html'), encoding='utf-8') as fh:
            index = fh.read()
        entry = re.search(r'<link rel="stylesheet" href="(/styles/app.css)">', index)
        self.assertTrue(entry, 'index.html must load the stylesheet entry point')
        # One *file* entry point. `/theme.css` is the second link and the only allowed one:
        # the server answers it from the session's own palette, so there is no file to walk
        # and nothing to precache — and it must stay out of the worker's cache, which is
        # shared by whoever uses this browser next.
        self.assertEqual(index.count('rel="stylesheet"'), 2, 'app.css plus the session palette')
        self.assertEqual(index.count('href="/theme.css"'), 1, 'the palette link appears once')

        def walk(url, seen=None):
            seen = seen if seen is not None else {}
            path = os.path.join(WEB, url.lstrip('/'))
            self.assertTrue(os.path.isfile(path), 'missing stylesheet ' + url)
            with open(path, encoding='utf-8') as fh:
                css = fh.read()
            seen[url] = css
            for ref in re.findall(r'@import\s+url\("([^"]+)"\)', css):
                child = os.path.normpath(os.path.join(os.path.dirname(url), ref)).replace(os.sep, '/')
                self.assertNotIn(child, seen, 'import cycle through ' + child)
                walk(child, seen)
            return seen

        tree = walk(entry.group(1))
        self.assertGreaterEqual(len(tree), 20, 'the component tree should not collapse')
        # The entry point assigns every import to a layer; a sheet outside the declared
        # order would silently outrank the rest.
        app = tree['/styles/app.css']
        self.assertRegex(app, r'@layer\s+tokens,\s*base,\s*layout,\s*components,\s*utilities;')
        for ref, layer in re.findall(r'@import\s+url\("([^"]+)"\)\s+layer\(([\w-]+)\);', app):
            child = os.path.normpath(os.path.join('/styles', ref)).replace(os.sep, '/')
            self.assertIn(child, tree, 'app.css imports a sheet it does not load: ' + child)
        imported = {os.path.normpath(os.path.join('/styles', ref)).replace(os.sep, '/')
                    for ref in re.findall(r'@import\s+url\("([^"]+)"\)', app)}
        self.assertEqual(imported, set(tree) - {'/styles/app.css'},
                         'every stylesheet must be imported by the entry point')

        with open(os.path.join(WEB, 'sw.js'), encoding='utf-8') as fh:
            sw = fh.read()
        precached = set(re.findall(r"'(/styles/[^']+)'", sw))
        self.assertEqual(precached, set(tree), 'sw.js must precache exactly the stylesheet tree')
        self.assertRegex(sw, r"hardlylistening-static-v\d+")
        self.assertNotIn('/polish.css', sw)
        # The palette is per member, so it is the one stylesheet the cache must never hold.
        self.assertNotIn('/theme.css', sw)

    def test_feature_scripts_are_precached(self):
        """The page's scripts have moved out of index.html a component at a time. A script the
        page loads by URL but the worker does not precache is an app that cannot boot from
        the cache — the same failure the stylesheet test guards, one file type over."""
        with open(os.path.join(WEB, 'index.html'), encoding='utf-8') as fh:
            index = fh.read()
        loaded = set(re.findall(r'<script src="([^"]+)"', index))
        self.assertTrue(loaded, 'index.html loads no scripts by URL at all')
        with open(os.path.join(WEB, 'sw.js'), encoding='utf-8') as fh:
            sw = fh.read()
        static = set(re.findall(r"'(/[^']+\.js)'", sw))
        self.assertEqual(static, loaded, 'sw.js must precache exactly the scripts index.html loads')
        for url in loaded:
            self.assertTrue(os.path.isfile(os.path.join(WEB, url.lstrip('/'))), 'missing script ' + url)

    def test_artwork_is_kept_beside_the_audio(self):
        """A kept song with no cover is a title over an empty tile offline. The worker keeps
        the two same-origin artwork shapes, and the page declares the same cache name it
        fills — a name that only one side knows is a cache nothing ever reads."""
        with open(os.path.join(WEB, 'sw.js'), encoding='utf-8') as fh:
            sw = fh.read()
        with open(os.path.join(WEB, 'index.html'), encoding='utf-8') as fh:
            index = fh.read()
        self.assertIn("hardlylistening-artwork-v1", sw)
        self.assertIn("hardlylistening-artwork-v1", index)
        self.assertIn('keptArtwork', sw)
        self.assertRegex(sw, r"\\/api\\/art\\/\[a-f0-9\]\{32\}")
        self.assertRegex(sw, r"\\/api\\/track\\/\\d\+\\/artwork")
        self.assertIn('keepArtwork', index)
        self.assertIn('paintOfflineMarks', index)