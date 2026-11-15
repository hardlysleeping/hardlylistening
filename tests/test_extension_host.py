"""The extension loader: what runs, what it may ask for, and what is written down.

The rule these tests pin is the one the plan states: an extension is inert until an administrator
trusts it by name, every call it makes is answered by the server against the grant in force at that
moment, and what it asked for is a query afterwards rather than a guess.

Two things are *not* tested because they are not true: this is not a sandbox (the child runs as the
same user), and the write capabilities are not offered (`library.write`, `social.write`,
`background` are refused by name until something implements them). Both are asserted as refusals so
a future change has to say so out loud.

No provider, no network and no real library: every child is started against a temporary directory
and a database built by the test.
"""
import json
import os
import tempfile
import textwrap
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import extension_host
import extensions
import server
from db import Store
from library import saves
from security import Security


class HostCase(unittest.TestCase):
    """A store, an extensions root of its own, and the two helpers every test needs."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR', '/tmp'))
        self.addCleanup(self.dir.cleanup)
        base = Path(self.dir.name)
        self.store = Store(base / 'db.sqlite')
        self.addCleanup(lambda: self.store.conn.close())
        self.root = base / 'extensions'
        self.root.mkdir()
        anchor = patch.object(extension_host, 'EXTENSIONS_DIR', self.root)
        anchor.start()
        self.addCleanup(anchor.stop)
        Security().schema(self.store)
        saves.schema(self.store)
        self.member('m1')

    def member(self, mid, role='member'):
        with self.store.lock:
            self.store.conn.execute(
                'INSERT OR REPLACE INTO members(id,email,role,display_name) VALUES(?,?,?,?)',
                (mid, mid + '@example.test', role, mid))
            self.store.conn.commit()

    def track(self, title='Song', mid='m1'):
        with self.store.lock:
            cursor = self.store.conn.execute(
                "INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,"
                " duration,artwork,size,added,source_url,file)"
                " VALUES('soundcloud',?,?,'Artist','Album',1.0,'',0,0,"
                " 'https://provider.example/secret?token=abc','/music/inside.mp3')",
                (title, title))
            self.store.conn.execute('INSERT INTO listening_tracks VALUES(?,?)',
                                    (mid, cursor.lastrowid))
            self.store.conn.commit()
        return cursor.lastrowid

    def extension(self, ext_id='demo.one', code='def main(extension):\n    return {}\n',
                  capabilities=('library.read',), hosts=(), entry='main.py'):
        folder = self.root / ext_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'manifest.json').write_text(json.dumps({
            'id': ext_id, 'name': 'Demo', 'version': '1.0.0', 'entry': entry,
            'capabilities': list(capabilities), 'hosts': list(hosts)}), encoding='utf-8')
        if entry:
            (folder / entry).write_text(textwrap.dedent(code), encoding='utf-8')
        return folder

    def install(self, ext_id='demo.one', enable=True, **kwargs):
        self.extension(ext_id, **kwargs)
        record = extension_host.install_from_disk(self.store, ext_id, by='a1')
        if enable:
            extensions.enable(self.store, ext_id, 'a1')
        return record

    def execute(self, ext_id='demo.one', timeout=10.0, member=None):
        return extension_host.run(self.store, ext_id, timeout=timeout,
                                  member={'id': 'm1'} if member is None else member)


class Loading(HostCase):
    def test_installation_reads_the_directory_and_grants_nothing(self):
        record = self.install(enable=False)
        self.assertEqual(record['id'], 'demo.one')
        self.assertFalse(extensions.installed(self.store)[0]['enabled'])
        with self.assertRaises(extensions.ExtensionError) as caught:
            self.execute()
        self.assertIn('not enabled', str(caught.exception))

    def test_a_run_reports_output_and_records_itself(self):
        self.install(code='def main(extension):\n    return {"done": 42}\n')
        result = self.execute()
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['output'], {'done': 42})
        runs = extension_host.runs(self.store, 'demo.one')
        self.assertEqual(runs[0]['status'], 'finished')
        self.assertEqual(runs[0]['member_id'], 'm1')

    def test_the_extension_cannot_import_this_application(self):
        # `python3 -I` plus the extension's own directory as cwd: `server`, `db` and `security`
        # are not importable, so a read grant cannot be turned into a database handle.
        self.install(capabilities=(), code='''
            def main(extension):
                try:
                    import server
                    return {"imported": "yes"}
                except ImportError:
                    return {"imported": "no"}
            ''')
        self.assertEqual(self.execute()['output'], {'imported': 'no'})

    def test_prints_go_to_stderr_and_do_not_corrupt_the_protocol(self):
        self.install(capabilities=(), code='''
            import sys
            def main(extension):
                for n in range(200):
                    print("noise", n)
                sys.stdout.write("more noise\\n")
                return {"ok": True}
            ''')
        result = self.execute()
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['output'], {'ok': True})
        self.assertIn('noise 199', result['stderr'])

    def test_an_entry_point_that_is_a_symlink_out_of_the_extension_is_refused(self):
        folder = self.extension(entry='link.py')
        (folder / 'link.py').unlink()
        (folder / 'link.py').symlink_to('/etc/hostname')
        with self.assertRaises(extensions.ExtensionError) as caught:
            extension_host.install_from_disk(self.store, 'demo.one', by='a1')
        self.assertIn('outside', str(caught.exception))
        self.assertEqual(extensions.installed(self.store), [])

    def test_a_directory_name_cannot_escape_the_extensions_root(self):
        for bad in ('..', '../etc', 'demo/../other', ''):
            with self.assertRaises(extensions.ExtensionError, msg=bad):
                extension_host.directory(bad)

    def test_a_broken_manifest_on_disk_is_reported_not_hidden(self):
        folder = self.extension(ext_id='demo.two')
        (folder / 'manifest.json').write_text(json.dumps({
            'id': 'something.else', 'name': 'X', 'version': '1.0.0'}), encoding='utf-8')
        listed = {item['id']: item for item in extension_host.on_disk()}
        self.assertIn('demo.two', listed)
        self.assertIn('does not match', listed['demo.two']['error'])

    def test_output_that_is_not_json_fails_the_run(self):
        self.install(capabilities=(), code='''
            def main(extension):
                return object()
            ''')
        result = self.execute()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('TypeError', result['message'])

    def test_a_run_that_never_returns_is_killed(self):
        self.install(capabilities=(), code='''
            def main(extension):
                while True:
                    pass
            ''')
        started = time.time()
        result = self.execute(timeout=1.0)
        self.assertEqual(result['status'], 'timeout')
        self.assertLess(time.time() - started, 10.0)
        self.assertEqual(extension_host.runs(self.store)[0]['status'], 'timeout')


class Mediation(HostCase):
    def test_a_library_read_is_scoped_and_hides_paths_and_source_urls(self):
        self.track('Mine')
        self.member('m2')
        self.track('Theirs', mid='m2')
        self.install(code='''
            def main(extension):
                return extension.library()
            ''')
        page = self.execute()['output']
        self.assertEqual(page['total'], 1, 'only the member the run belongs to')
        track = page['tracks'][0]
        self.assertEqual(track['title'], 'Mine')
        self.assertNotIn('source_url', track)
        self.assertNotIn('file', track)
        self.assertNotIn('/music', json.dumps(page))
        self.assertNotIn('token=abc', json.dumps(page))

    def test_a_call_without_the_capability_is_refused_and_audited(self):
        self.install(capabilities=(), code='''
            def main(extension):
                try:
                    extension.library()
                except RuntimeError as error:
                    return {"refused": str(error)}
                return {"refused": "no"}
            ''')
        result = self.execute()
        self.assertIn('does not hold the library.read capability', result['output']['refused'])
        calls = extension_host.calls(self.store, 'demo.one')
        self.assertEqual([(row['method'], row['ok']) for row in calls],
                         [('library.read', 0)])

    def test_a_library_read_needs_a_member(self):
        self.install(capabilities=('library.read',), code='''
            def main(extension):
                try:
                    extension.library()
                except RuntimeError as error:
                    return {"refused": str(error)}
            ''')
        result = self.execute(member={})
        self.assertIn('needs the member', result['output']['refused'])
        self.assertEqual(extension_host.calls(self.store)[0]['ok'], 0)

    def test_the_write_capabilities_are_named_refusals(self):
        self.install(capabilities=(), code='''
            def main(extension):
                out = {}
                for method in ("library.write", "social.write", "background"):
                    try:
                        extension.call(method)
                    except RuntimeError as error:
                        out[method] = str(error)
                return out
            ''')
        out = self.execute()['output']
        for method in ('library.write', 'social.write', 'background'):
            self.assertIn('not offered by this host yet', out[method])
        self.assertEqual(len(extension_host.calls(self.store)), 3)

    def test_an_unknown_method_is_refused_by_name(self):
        self.install(capabilities=(), code='''
            def main(extension):
                try:
                    extension.call("teleport")
                except RuntimeError as error:
                    return {"refused": str(error)}
            ''')
        self.assertIn('unknown method', self.execute()['output']['refused'])

    def test_the_audit_keeps_only_the_newest_rows(self):
        self.track()
        self.install(code='''
            def main(extension):
                for _ in range(4):
                    extension.library()
                return {}
            ''')
        with patch.object(extension_host, 'KEEP_CALLS', 2):
            self.execute()
        self.assertEqual(len(extension_host.calls(self.store, 'demo.one')), 2)


class _QuietHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.1/elsewhere')
            self.end_headers()
            return
        body = b'hello from a declared host'
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Network(HostCase):
    """The network capability against a real local server, through the test hatch."""

    def setUp(self):
        super().setUp()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), _QuietHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.http.server_port

    def fetch(self, url, hosts=('127.0.0.1',), allow_local='1'):
        self.install(capabilities=('network',), hosts=hosts, code='''
            def main(extension):
                try:
                    return {"answer": extension.fetch(%r)}
                except RuntimeError as error:
                    return {"refused": str(error)}
            ''' % url)
        environment = {} if allow_local is None else {
            'HARDLYLISTENING_EXTENSION_ALLOW_LOCAL': allow_local}
        with patch.dict(os.environ, environment):
            return self.execute()

    def test_a_declared_host_is_fetched(self):
        result = self.fetch(self.base + '/page')
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['output']['answer']['status'], 200)
        self.assertIn('hello from a declared host', result['output']['answer']['body'])

    def test_an_undeclared_host_is_refused_before_the_wire(self):
        result = self.fetch(self.base + '/page', hosts=('api.example.org',))
        self.assertIn('not a host this extension declared', result['output']['refused'])
        self.assertEqual(extension_host.calls(self.store)[0]['ok'], 0)

    def test_plain_http_needs_the_loopback_hatch(self):
        result = self.fetch(self.base + '/page', allow_local=None)
        self.assertIn('only https is fetched', result['output']['refused'])

    def test_a_redirect_is_refused_rather_than_chased(self):
        result = self.fetch(self.base + '/redirect')
        self.assertIn('redirects are not followed', result['output']['refused'])

    def test_the_audit_keeps_the_host_and_path_but_not_the_query(self):
        # A token in a query string must not become a log line.
        self.fetch(self.base + '/page?token=abc123')
        detail = extension_host.calls(self.store)[0]['detail']
        self.assertIn('/page', detail)
        self.assertNotIn('abc123', detail)
        self.assertNotIn('?', detail)


class Routes(HostCase):
    """The administrator surface over HTTP."""

    def setUp(self):
        super().setUp()
        self.sec = Security()
        self.env = patch.dict(os.environ, {
            'HARDLYLISTENING_AUTH_REQUIRED': '1',
            'HARDLYLISTENING_SUPABASE_URL': 'https://example.supabase.co',
            'HARDLYLISTENING_SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_fixture',
            'HARDLYLISTENING_PUBLIC_ORIGIN': 'https://music.example',
            'HARDLYLISTENING_BOOTSTRAP_ADMIN_EMAIL': 'owner@example.com'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.patches = [patch.object(server, 'SECURITY', self.sec),
                        patch.object(server, 'boot', return_value=(self.store, None))]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def tearDown(self):
        with self.store.lock:
            if self.store._projection_timer:
                self.store._projection_timer.cancel()
        self.store.conn.close()
        self.dir.cleanup()

    def token(self, mid):
        import secrets
        sid = secrets.token_urlsafe(32)
        self.sec.schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                'INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)',
                (self.sec.digest(sid), mid, time.time() + 3600))
            self.store.conn.commit()
        return {'Cookie': '__Host-hl_session=' + sid, 'Origin': 'https://music.example'}

    def request(self, path, method='GET', headers=None, body=None):
        connection = HTTPConnection('127.0.0.1', self.http.server_port, timeout=30)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = (response.status, response.read())
        connection.close()
        return result

    def test_a_member_cannot_see_or_touch_the_host(self):
        self.member('a1', 'admin')
        headers = self.token('m1')
        self.assertEqual(self.request('/api/extensions', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/extensions/install', 'POST', headers,
                                      json.dumps({'id': 'demo.one'}))[0], 403)

    def test_an_admin_installs_enables_runs_and_reads_the_audit(self):
        self.member('a1', 'admin')
        self.extension(code='def main(extension):\n    return {"ok": True}\n')
        headers = self.token('a1')

        status, raw = self.request('/api/extensions', headers=headers)
        self.assertEqual(status, 200, raw)
        listed = json.loads(raw)
        self.assertEqual(listed['extensions'], [])
        self.assertEqual([item['id'] for item in listed['on_disk']], ['demo.one'])

        status, raw = self.request('/api/extensions/install', 'POST', headers,
                                   json.dumps({'id': 'demo.one'}))
        self.assertEqual(status, 200, raw)
        self.assertFalse(json.loads(raw)['extension']['enabled'])

        self.assertEqual(self.request('/api/extensions/demo.one/run', 'POST', headers)[0], 400)
        self.assertEqual(self.request('/api/extensions/demo.one/enable', 'POST', headers)[0], 200)

        status, raw = self.request('/api/extensions/demo.one/run', 'POST', headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['status'], 'finished')

        status, raw = self.request('/api/extensions/demo.one/calls', headers=headers)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['runs'][0]['status'], 'finished')

        self.assertEqual(self.request('/api/extensions/demo.one/revoke', 'POST', headers)[0], 200)
        status, raw = self.request('/api/extensions/demo.one/run', 'POST', headers)
        self.assertEqual(status, 400)
        self.assertIn(b'not enabled', raw)

    def test_a_broken_manifest_is_answered_not_dropped(self):
        self.member('a1', 'admin')
        folder = self.extension(ext_id='demo.two')
        (folder / 'manifest.json').write_text(json.dumps({
            'id': 'demo.one', 'name': 'X', 'version': '1.0.0'}), encoding='utf-8')
        status, raw = self.request('/api/extensions/install', 'POST', self.token('a1'),
                                   json.dumps({'id': 'demo.two'}))
        self.assertEqual(status, 400, raw)
        self.assertIn(b'does not match', raw)

    def test_remove_forgets_the_row_and_leaves_the_code(self):
        self.member('a1', 'admin')
        self.extension()
        headers = self.token('a1')
        self.request('/api/extensions/install', 'POST', headers, json.dumps({'id': 'demo.one'}))
        self.assertEqual(self.request('/api/extensions/demo.one/remove', 'POST', headers)[0], 200)
        status, raw = self.request('/api/extensions', headers=headers)
        self.assertEqual(json.loads(raw)['extensions'], [])
        self.assertTrue((self.root / 'demo.one' / 'main.py').is_file(),
                        'a POST does not delete the administrator\'s files')


if __name__ == '__main__':
    unittest.main()