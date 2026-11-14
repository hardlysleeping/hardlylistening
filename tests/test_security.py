import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import server
from db import Store
from security import Security, Denied


class SecurityHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.store = Store(Path(self.tmp.name) / 'db.sqlite')
        self.sec = Security()
        self.env = patch.dict(os.environ, {
            'HARDLYLISTENING_AUTH_REQUIRED': '1',
            'HARDLYLISTENING_SUPABASE_URL': 'https://example.supabase.co',
            'HARDLYLISTENING_SUPABASE_PUBLISHABLE_KEY': 'sb_publishable_fixture',
            'HARDLYLISTENING_PUBLIC_ORIGIN': 'https://music.example',
            'HARDLYLISTENING_BOOTSTRAP_ADMIN_EMAIL': 'owner@example.com'})
        self.env.start()
        self.patches = [patch.object(server, 'SECURITY', self.sec), patch.object(server, 'boot', return_value=(self.store, None))]
        for p in self.patches:
            p.start()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.thread.join()
        self.http.server_close()
        for p in self.patches:
            p.stop()
        self.env.stop()
        with self.store.lock:
            if self.store._projection_timer:
                self.store._projection_timer.cancel()
            self.store.conn.close()
        self.tmp.cleanup()

    def request(self, path, method='GET', headers=None, body=None):
        headers = dict(headers or {})
        if headers.get('Authorization') in ('Bearer fixture', 'Bearer good'):
            headers.pop('Authorization')
            headers['Cookie'] = self.token(self.identity['id'])
        c = HTTPConnection('127.0.0.1', self.http.server_port, timeout=4)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        result = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return result

    def user(self, role='member'):
        user = {'id': 'u1', 'email': 'friend@example.com', 'email_confirmed_at': 'yes'}
        self.sec.schema(self.store)
        self.store.conn.execute('INSERT OR REPLACE INTO members(id,email,role,display_name) VALUES(?,?,?,?)', ('u1', user['email'], role, 'Friend'))
        self.store.conn.commit()
        return user

    def token(self, uid='u1'):
        import secrets, time
        sid = secrets.token_urlsafe(32)
        self.sec.schema(self.store)
        with self.store.lock:
            self.store.conn.execute('INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)', (self.sec.digest(sid), uid, time.time()+3600))
            self.store.conn.commit()
        return '__Host-hl_session='+sid

    def test_disabled_compatibility_and_noindex(self):
        os.environ['HARDLYLISTENING_AUTH_REQUIRED'] = '0'
        status, headers, _ = self.request('/api/health')
        self.assertEqual(status, 200)
        self.assertIn('noindex', headers['X-Robots-Tag'])
        self.assertEqual(self.request('/server.py')[0], 404)

    def test_misconfigured_closed(self):
        with patch.dict(os.environ, {'HARDLYLISTENING_PUBLIC_ORIGIN': ''}):
            self.assertEqual(self.request('/api/health')[0], 200)
            for path in ('/', '/api/state'):
                self.assertEqual(self.request(path)[0],503)

    def test_missing_invalid_audio_zip_gate(self):
        for path in ('/api/state','/api/track/1/audio','/api/playlist/1/download','/api/library/download?ids=1','/api/members'):
            self.assertEqual(self.request(path)[0],401)
        self.assertEqual(self.request('/api/state',headers={'Authorization':'Bearer invalid'})[0],401)
        self.assertEqual(self.request('/api/state',headers={'X-Forwarded-User':'owner'})[0],401)

    def test_member_cannot_admin_csrf_and_cookie_exchange(self):
        self.user()
        link=self.sec.invite(self.store, {'role':'admin'}, 'u1')
        token=link.split('invite=')[1]
        for origin in ('', 'https://evil.example'):
            self.assertEqual(self.request('/api/auth/session','POST',{'Origin':origin},json.dumps({'invite':token}))[0],403)
        code,h,_=self.request('/api/auth/session','POST',{'Origin':'https://music.example'},json.dumps({'invite':token}))
        self.assertEqual(code,200)
        # Lax, deliberately: Strict withholds the cookie on a cross-site
        # top-level navigation, so opening the site from a chat link or another
        # app bounces a signed-in member to /login. CSRF is still enforced by
        # the Origin check, which Lax cannot satisfy for cross-site POSTs.
        self.assertIn('Secure; HttpOnly; SameSite=Lax',h['Set-Cookie'])
        self.assertNotIn('SameSite=Strict',h['Set-Cookie'])
        cookie=h['Set-Cookie'].split(';')[0]
        headers={'Cookie':cookie,'Origin':'https://music.example'}
        self.assertEqual(self.request('/api/state',headers=headers)[0],200)
        for path in ('/api/settings','/api/storage','/api/import','/api/library/move','/api/pause','/api/track/1/source','/api/members/create','/api/members/invite','/api/members/revoke','/api/members'):
            self.assertEqual(self.request(path,'POST',headers,'{}')[0],403)
        # /login is a public page but must never strand a member who is already
        # signed in on this device.
        self.assertEqual(self.request('/login',headers=headers)[0],303)
        self.assertEqual(self.request('/login')[0],200)
        self.store.conn.execute("UPDATE members SET role='blocked'");self.store.conn.commit()
        self.assertEqual(self.request('/api/state',headers=headers)[0],403)

    def test_boost_api_member_auth_and_owner_scoped_status(self):
        self.identity = self.user('member')
        track_id = self.store.track('local', 'boost-fixture', 'Boost fixture')
        headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}
        code, _, raw = self.request('/api/boost', 'POST', headers, json.dumps({'track_id': track_id}))
        self.assertEqual(code, 200, raw)
        result = json.loads(raw)['boost']
        self.assertEqual(result['track_id'], track_id)
        self.assertFalse(result['swapped'])
        code, _, raw = self.request('/api/boost', headers=headers)
        self.assertEqual(code, 200, raw)
        self.assertEqual(json.loads(raw)['boost']['track_id'], track_id)
        self.store.conn.execute("INSERT INTO members(id,email,role) VALUES('u2','other@example.com','member')")
        self.store.conn.commit()
        self.identity = {'id': 'u2', 'email': 'other@example.com'}
        code, _, raw = self.request('/api/boost', headers=headers)
        self.assertEqual(code, 200, raw)
        self.assertIsNone(json.loads(raw)['boost'])
        self.assertEqual(self.request('/api/boost', 'POST', {'Origin':'https://evil.example','Authorization':'Bearer fixture'},
                                       json.dumps({'track_id':track_id}))[0], 403)

    def test_valid_sessions_do_not_spend_verification_budget(self):
        self.user()
        cookie = {'Cookie': self.token()}
        for _ in range(400):
            status, _, _ = self.request('/api/state', headers=cookie)
            self.assertEqual(status, 200)

        statuses = [self.request('/api/state')[0] for _ in range(400)]
        self.assertIn(429, statuses)

    def test_rate_limit_includes_retry_after(self):
        self.user()
        cookie = {'Cookie': self.token()}
        for _ in range(601):
            status, headers, body = self.request('/api/state', headers=cookie)
            if status == 429:
                self.assertGreaterEqual(int(headers.get('Retry-After', '0')), 1)
                self.assertGreaterEqual(json.loads(body)['retry_after'], 1)
                return
        self.fail('authenticated action budget did not return 429')

    def test_rate_limited_page_load_returns_429_not_login_redirect(self):
        seen_429 = False
        for _ in range(400):
            status, headers, body = self.request('/')
            if status == 429:
                seen_429 = True
                self.assertIn('application/json', headers.get('Content-Type', ''))
                self.assertGreaterEqual(int(headers.get('Retry-After', '0')), 1)
                self.assertGreaterEqual(json.loads(body)['retry_after'], 1)
                break
            self.assertEqual(status, 303)
        self.assertTrue(seen_429, 'rate-limited page load should not keep redirecting to login')

    def test_authenticated_asset_requests_have_separate_budget(self):
        self.user()
        cookie = self.token()

        class Request:
            command = 'GET'
            path = '/api/track/1/audio'
            client_address = ('198.51.100.8', 12345)
            headers = {'Cookie': cookie}

        request = Request()
        for _ in range(self.sec.ASSET_BUDGET):
            self.sec.authenticate(request, self.store)
        self.assertNotIn(('u1', 'requests'), self.sec.rates)
        with self.assertRaises(Denied) as caught:
            self.sec.authenticate(request, self.store)
        self.assertEqual(caught.exception.code, 429)

    def test_rate_limits_and_body_bound(self):
        self.user();h={'Cookie':self.token()}
        for _ in range(10):
            self.assertEqual(self.request('/api/playlist/999/download',headers=h)[0],400)
        self.assertEqual(self.request('/api/playlist/999/download',headers=h)[0],429)
        self.assertEqual(self.request('/api/settings','POST',dict(h,**{'Content-Length':'999999999'}))[0],413)

    def test_admin_create_invite_revoke_and_last_admin(self):
        self.user('admin');h={'Cookie':self.token(),'Origin':'https://music.example'}
        code,_,raw=self.request('/api/members/create','POST',h,json.dumps({'role':'member'}))
        self.assertEqual(code,200);data=json.loads(raw)
        self.assertTrue(data['invite'].startswith('https://music.example/auth/link#invite='))
        self.assertEqual(self.request('/api/members/invite','POST',h,json.dumps({'id':data['id']}))[0],200)
        self.assertEqual(self.request('/api/members/revoke','POST',h,json.dumps({'id':data['id']}))[0],200)
        with self.assertRaises(Denied): self.sec.exchange(self.store,data['invite'].split('invite=')[1])
        self.assertEqual(self.request('/api/members','POST',h,json.dumps({'id':'u1','role':'member'}))[0],400)
        self.assertEqual(self.request('/api/auth/logout','POST',h,'{}')[0],200)
        self.assertEqual(self.request('/api/state',headers=h)[0],401)

    def test_replay_expiry_hash_persistence_concurrent(self):
        from concurrent.futures import ThreadPoolExecutor
        from types import SimpleNamespace
        self.user()
        token=self.sec.invite(self.store,{'role':'admin'},'u1').split('invite=')[1]
        def consume(_):
            try:return self.sec.exchange(self.store,token)
            except Denied:return None
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(consume,range(2)))
        # A member link is reusable by design — two concurrent opens both sign in,
        # with two distinct hash-only sessions.
        self.assertEqual(sum(r is not None for r in results),2)
        self.assertNotEqual(results[0],results[1])
        sid=results[0]
        stored=self.store.conn.execute('SELECT digest FROM browser_sessions WHERE digest=?',(self.sec.digest(sid),)).fetchone()[0]
        self.assertEqual(stored,self.sec.digest(sid));self.assertNotEqual(stored,sid)
        fresh=Security()
        handler=SimpleNamespace(headers={'Cookie':'__Host-hl_session='+sid},command='GET',path='/api/state',client_address=('fixture',0))
        self.assertEqual(fresh.authenticate(handler,self.store)['id'],'u1')
        token=fresh.invite(self.store,{'role':'admin'},'u1',ttl=-1).split('invite=')[1]
        self.assertEqual(self.store.conn.execute('SELECT digest FROM browser_invites').fetchone()[0],fresh.digest(token))
        with self.assertRaises(Denied): fresh.exchange(self.store,token)
        self.store.conn.execute('UPDATE browser_sessions SET expires=0');self.store.conn.commit()
        with self.assertRaises(Denied):fresh.authenticate(handler,self.store)

    def test_no_public_bootstrap_or_old_provider_flow(self):
        for path in ('/auth/login?provider=google','/auth/callback?code=secret'):
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.store.conn.execute("SELECT count(*) FROM sqlite_master WHERE name='members'").fetchone()[0],0)
        self.assertEqual(self.request('/api/auth/otp','POST',body='{}')[0],401)
        for path in ('/login','/auth/link','/api/health'):
            self.assertEqual(self.request(path)[0],200)

    def test_playlist_ownership_and_request_attribution(self):
        from unittest.mock import Mock
        queue=Mock()
        queue.ingest.side_effect=lambda url:{'playlist_id':self.store.playlist('new','fixture',url),'queued':0}
        self.user();h={'Cookie':self.token(),'Origin':'https://music.example'}
        # Acquisition is a grant of its own (HL-219); this test is about ownership.
        from acquisitions import grant as grant_acquisition
        grant_acquisition(self.store, 'u1')
        with patch.object(server,'boot',return_value=(self.store,queue)):
            self.assertEqual(self.request('/api/add','POST',h,'{"url":"https://example.com/set"}')[0],200)
        pid=self.store.conn.execute('SELECT playlist_id FROM member_playlists').fetchone()[0]
        self.assertEqual(self.store.conn.execute('SELECT member_id FROM member_playlists').fetchone()[0],'u1')
        self.assertEqual(self.store.conn.execute('SELECT member_id FROM member_requests').fetchone()[0],'u1')
        other=self.store.playlist('other','fixture','')
        self.assertEqual(self.request(f'/api/playlist/{other}/remove','POST',h,'{}')[0],403)
        with patch.object(server,'boot',return_value=(self.store,queue)):
            self.assertEqual(self.request(f'/api/playlist/{pid}/remove','POST',h,'{}')[0],200)

    def test_shared_pool_not_personal_saves_filter(self):
        self.user();self.store.track('local','shared','Visible to all',artist_name='Artist')
        h={'Cookie':self.token()}
        code,_,raw=self.request('/api/library',headers=h)
        self.assertEqual(code,200)
        self.assertEqual(json.loads(raw)['all_tracks'][0]['title'],'Visible to all')
        self.assertEqual(self.request('/api/feed',headers=h)[0],200)

    def test_local_mode_still_requires_admin_for_people(self):
        os.environ['HARDLYLISTENING_AUTH_REQUIRED']='0'
        for path in ('/api/members','/api/members/create','/api/members/invite','/api/members/revoke'):
            self.assertEqual(self.request(path,'POST',{'Origin':'https://music.example'},'{}')[0],401)

if __name__ == '__main__': unittest.main()


class CsrfOrigin(unittest.TestCase):
    """The login form posts to whatever hostname the page was opened on."""

    class Handler:
        def __init__(self, headers):
            self.headers = headers

    def setUp(self):
        self.env = patch.dict(os.environ, {'HARDLYLISTENING_PUBLIC_ORIGIN': 'https://music.example'})
        self.env.start()
        self.sec = Security()

    def tearDown(self):
        self.env.stop()

    def test_configured_public_origin_is_accepted(self):
        self.sec.csrf(self.Handler({'Origin': 'https://music.example', 'Host': 'music.example'}))

    def test_the_request_own_host_is_accepted(self):
        for host in ('127.0.0.1:8470', '192.168.1.20:8470', 'devbox.example.ts.net:8470'):
            self.sec.csrf(self.Handler({'Origin': 'http://' + host, 'Host': host}))
            self.sec.csrf(self.Handler({'Origin': 'https://' + host, 'Host': host}))

    def test_another_site_is_refused(self):
        refused = [
            {'Origin': 'https://evil.example', 'Host': '127.0.0.1:8470'},
            {'Origin': 'http://127.0.0.1:8471', 'Host': '127.0.0.1:8470'},
            {'Origin': 'null', 'Host': '127.0.0.1:8470'},
            {'Origin': 'http://127.0.0.1:8470', 'Host': ''},
            {'Host': '127.0.0.1:8470'},
        ]
        for headers in refused:
            with self.assertRaises(Denied, msg=str(headers)):
                self.sec.csrf(self.Handler(headers))


class RateLimitIdentity(unittest.TestCase):
    """Behind the Cloudflare tunnel every peer is 127.0.0.1, so limits must key on the
    edge-reported client address — otherwise the whole internet shares one bucket and a
    stranger can spend it and lock real members out. The header is trusted only when the
    socket peer is loopback, which is the only way a request reaches a 127.0.0.1 bind."""

    class Handler:
        def __init__(self, peer, forwarded=None):
            self.client_address = (peer, 1234)
            self.headers = {} if forwarded is None else {'CF-Connecting-IP': forwarded}

    def test_edge_address_is_used_for_a_loopback_peer(self):
        for peer, forwarded, want in (
                ('127.0.0.1', '203.0.113.9', '203.0.113.9'),
                ('::1', '198.51.100.7', '198.51.100.7'),
                ('::ffff:127.0.0.1', '192.0.2.4', '192.0.2.4'),
                ('127.0.0.1', '2001:0db8:0000::1', '2001:db8::1')):
            self.assertEqual(Security.client_ip(self.Handler(peer, forwarded)), want)

    def test_missing_or_malformed_header_falls_back_to_the_peer(self):
        for forwarded in (None, '', 'not-an-ip', '1.2.3.4, 5.6.7.8', '::1:extra'):
            self.assertEqual(Security.client_ip(self.Handler('127.0.0.1', forwarded)), '127.0.0.1')

    def test_a_direct_peer_cannot_choose_its_own_address(self):
        self.assertEqual(Security.client_ip(self.Handler('192.168.1.50', '203.0.113.9')), '192.168.1.50')

    def test_each_edge_client_gets_its_own_bucket(self):
        sec = Security()
        sec.limit('203.0.113.9', 'login', 2)
        sec.limit('203.0.113.9', 'login', 2)
        with self.assertRaises(Denied):
            sec.limit('203.0.113.9', 'login', 2)
        sec.limit('198.51.100.7', 'login', 2)   # a different visitor is unaffected
