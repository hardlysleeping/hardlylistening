"""Isolated HTTP + SQLite Party fixtures. No app Store, workers or audible media."""
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import party
import server
from security import Security, Denied


class PartyHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.root = Path(self.tmp.name)
        conn = sqlite3.connect(self.root/'isolated.sqlite', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE tracks(id INTEGER PRIMARY KEY,title TEXT,artist_name TEXT,album TEXT,duration REAL,file TEXT,source_url TEXT)')
        for i in range(1, 7):
            f = self.root/f'{i}.mp3'
            f.write_bytes(b'fixture never played')
            conn.execute('INSERT INTO tracks VALUES(?,?,?,?,?,?,?)', (i, f'Track {i}', 'Artist', 'Album', 120, str(f), 'https://private.example/source'))
        conn.execute("INSERT INTO tracks VALUES(7,'Track missing','Artist','Album',120,'/missing','secret')")
        conn.commit()
        self.store = SimpleNamespace(conn=conn, lock=threading.RLock(), setting=lambda key: str(self.root))
        self.sec = Security()
        self.env = patch.dict(os.environ, {'HARDLYLISTENING_AUTH_REQUIRED':'1', 'HARDLYLISTENING_PUBLIC_ORIGIN':'https://music.example'})
        self.env.start()
        self.sec.schema(self.store)
        party.schema(self.store)
        with self.store.lock:
            for uid, role, icon in [('m','member','moon'),('a','admin','star'),('b','blocked','sun')]:
                conn.execute('INSERT INTO members(id,role,display_name,profile_icon) VALUES(?,?,?,?)', (uid,role,uid.upper(),icon))
            conn.commit()
        self.patches = [patch.object(server, 'boot', return_value=(self.store,None)),patch.object(server,'SECURITY',self.sec)]
        for p in self.patches:p.start()
        self.http = ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.thread.start()
        self.member = self.session('m')
        self.admin = self.session('a')
        self.pid = self.req('/api/party','POST',{'title':'Friends'},self.member)[2]['id']

    def tearDown(self):
        self.http.shutdown();self.thread.join();self.http.server_close()
        for p in reversed(self.patches):p.stop()
        self.env.stop();self.store.conn.close();self.tmp.cleanup()

    def session(self, uid):
        sid=secrets.token_urlsafe(32)
        with self.store.lock:
            self.store.conn.execute('INSERT INTO browser_sessions(digest,member_id,expires) VALUES(?,?,?)',(self.sec.digest(sid),uid,time.time()+3600));self.store.conn.commit()
        return '__Host-hl_session='+sid

    def req(self,path,method='GET',body=None,cookie='',origin='https://music.example'):
        c=HTTPConnection('127.0.0.1',self.http.server_port,timeout=5)
        headers={'Cookie':cookie,'Origin':origin}
        c.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
        r=c.getresponse();data=r.read();h=dict(r.getheaders());status=r.status;c.close()
        try:data=json.loads(data)
        except (ValueError,UnicodeError):pass
        return status,h,data

    def path(self,action=''):
        return '/api/party/'+self.pid+('/'+action if action else '')

    def join(self,name='Guest',icon='headphones'):
        code,_,inv=self.req(self.path('invite'),'POST',{},self.member)
        self.assertEqual(code,200)
        token=inv['link'].split('invite=')[1]
        code,h,result=self.req('/api/party/exchange','POST',{'invite':token,'display_name':name,'profile_icon':icon})
        self.assertEqual(code,200)
        return h['Set-Cookie'].split(';')[0],token,inv,result

    def test_member_and_admin_can_create_invite_guests_never_members(self):
        self.assertEqual(self.req('/api/party','POST',{'title':'Admin'},self.admin)[0],200)
        cookie,token,inv,_=self.join()
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM members').fetchone()[0],3)
        for action in ('invite','control'):
            self.assertEqual(self.req(self.path(action),'POST',{'op':'claim'},cookie)[0],401)
        self.assertEqual(self.req('/api/party','POST',{'title':'Guest'},cookie)[0],401)
        self.assertEqual(self.req('/api/party','GET',cookie=cookie)[0],401)
        self.assertNotIn(token,str([tuple(r) for r in self.store.conn.execute('SELECT * FROM party_guests')]))

    def test_reusable_identity_sessions_fixed_expiry_and_profile(self):
        cookie,token,inv,r=self.join()
        _,h,r2=self.req('/api/party/exchange','POST',{'invite':token,'display_name':'Spoof','profile_icon':'sun'})
        second=h['Set-Cookie'].split(';')[0]
        self.assertEqual(r['expires'],r2['expires'])
        self.assertEqual(inv['expires'],r2['expires'])
        self.assertIn('Secure',h['Set-Cookie']);self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn('SameSite=Strict',h['Set-Cookie'])
        for co in (cookie,second):
            code,_,s=self.req(self.path(),cookie=co)
            self.assertEqual(code,200);self.assertEqual(s['viewer']['display_name'],'Guest');self.assertEqual(s['viewer']['profile_icon'],'headphones')
        with self.store.lock:
            self.store.conn.execute('UPDATE party_guests SET expires=?',(time.time()-1,));self.store.conn.commit()
        self.assertEqual(self.req(self.path(),cookie=cookie)[0],401)
        self.assertEqual(self.req('/api/party/exchange','POST',{'invite':token})[0],401)

    def test_scope_cannot_auth_other_app_routes_even_local_bypass(self):
        cookie,token,_,_=self.join()
        paths=['/api/state','/api/library','/api/track/1/audio','/api/track/1/download','/api/mixer','/api/members','/api/listening/saves','/api/playlists','/rest/ping.view','/dj']
        for enabled in ('1','0'):
            with patch.dict(os.environ,{'HARDLYLISTENING_AUTH_REQUIRED':enabled}):
                for path in paths:self.assertEqual(self.req(path,cookie=cookie)[0],401,path)
        self.assertEqual(self.req('/api/auth/session','POST',{'invite':token})[0],401)
        self.assertEqual(self.req(self.path('invite'),'POST',{},self.session('b'))[0],403)

    def test_cross_party_expiry_revoke_and_close(self):
        cookie,token,inv,_=self.join()
        other=self.req('/api/party','POST',{'title':'Other'},self.member)[2]['id']
        self.assertEqual(self.req('/api/party/'+other,cookie=cookie)[0],401)
        self.assertEqual(self.req('/api/party/'+other+'/chat',cookie=cookie)[0],401)
        self.assertEqual(self.req(self.path('control'),'POST',{'op':'revoke','guest_id':inv['guest_id']},self.member)[0],200)
        for action in ('','chat','search?q=Track'):
            self.assertEqual(self.req(self.path(action),cookie=cookie)[0],401)
        self.assertEqual(self.req(self.path('chat'),'POST',{'body':'No'},cookie)[0],401)
        self.assertEqual(self.req('/api/party/exchange','POST',{'invite':token})[0],401)
        cookie,token,_,_=self.join()
        self.assertEqual(self.req(self.path('control'),'POST',{'op':'close'},self.member)[0],200)
        self.assertEqual(self.req(self.path(),cookie=cookie)[0],410)
        self.assertEqual(self.req('/api/party/exchange','POST',{'invite':token})[0],410)

    def test_full_score_order_ties_change_retract_and_current_pinned(self):
        cookie,token,_,_=self.join()
        entries=[self.req(self.path('queue'),'POST',{'track_id':i},cookie)[2]['entry_id'] for i in (1,2,3,4)]
        def vote(e,v,co=cookie):return self.req(self.path('vote'),'POST',{'entry_id':e,'value':v},co)
        def order():return [r['entry_id'] for r in self.req(self.path(),cookie=cookie)[2]['queue']]
        vote(entries[3],1);self.assertEqual(order(),[entries[3],*entries[:3]])
        vote(entries[3],1);self.assertEqual(self.req(self.path(),cookie=cookie)[2]['queue'][0]['score'],1)
        vote(entries[3],-1);self.assertEqual(order(),entries)
        vote(entries[3],0);self.assertEqual(order(),entries)
        token2=self.req(self.path('control'),'POST',{'op':'claim'},self.member)[2]['host_token']
        ctl={'op':'next','host_token':token2,'expected':None}
        self.assertEqual(self.req(self.path('control'),'POST',ctl,self.member)[0],200)
        self.assertEqual(vote(entries[0],-1)[0],409)
        vote(entries[3],1);s=self.req(self.path(),cookie=cookie)[2]
        self.assertEqual(s['current']['entry_id'],entries[0]);self.assertEqual(s['queue'][0]['entry_id'],entries[3])
        ctl['expected']=entries[0];self.req(self.path('control'),'POST',ctl,self.member)
        self.assertEqual(self.req(self.path('control'),'POST',ctl,self.member)[0],409)
        self.assertEqual(self.req(self.path(),cookie=cookie)[2]['current']['entry_id'],entries[3])

    def test_playable_bounded_catalog_no_sensitive_fields_and_validation(self):
        cookie,_,_,_=self.join()
        code,_,s=self.req(self.path('search')+'?q=Track',cookie=cookie)
        self.assertEqual(code,200);self.assertEqual(len(s['tracks']),6)
        for t in s['tracks']:self.assertEqual(set(t),{'id','title','artist_name','album','duration'})
        for value in (0,True,'1 OR 1=1',7,999):self.assertIn(self.req(self.path('queue'),'POST',{'track_id':value},cookie)[0],(400,404))
        self.assertEqual(self.req(self.path('queue'),'POST',{'track_id':1},cookie)[0],200)
        self.assertEqual(self.req(self.path('queue'),'POST',{'track_id':1},cookie)[0],409)
        self.assertEqual(self.req(self.path('vote'),'POST',{'entry_id':1,'value':True},cookie)[0],400)
        self.assertEqual(self.req(self.path('search')+'?q=%25',cookie=cookie)[2]['tracks'],[])
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM tracks').fetchone()[0],7)

    def test_csrf_rate_limits_and_public_exact_assets(self):
        cookie,token,_,_=self.join()
        self.assertEqual(self.req(self.path('queue'),'POST',{'track_id':1},cookie,origin='https://evil.example')[0],403)
        self.assertEqual(self.req('/api/party/exchange','POST',{'invite':token},origin='')[0],403)
        with patch.object(server,'boot',side_effect=AssertionError('static must not boot')):
            for path in ('/party','/party.html','/party.js','/party.css'):self.assertEqual(self.req(path)[0],200)
        self.assertEqual(self.req('/party.py')[0],404)
        self.sec.limit('g:bad','party-chat',1)
        self.assertEqual(self.req(self.path('queue'),'POST',{'track_id':1},cookie)[0],200)
        with patch.object(self.sec,'limit',side_effect=Denied(429,'Rate limit exceeded')):
            self.assertEqual(self.req(self.path(),cookie=cookie)[0],429)

    def test_first_join_validation_and_expiry_cap(self):
        inv=self.req(self.path('invite'),'POST',{},self.member)[2]
        token=inv['link'].split('invite=')[1]
        self.assertEqual(self.req('/api/party/exchange','POST',{'invite':token,'display_name':'','profile_icon':'https://remote'})[0],400)
        self.assertEqual(self.req('/api/party','POST',{'hours':25},self.member)[0],400)
        self.assertEqual(self.req('/api/party','POST',{'hours':True},self.member)[0],400)
        with self.store.lock:
            self.store.conn.execute('UPDATE party_rooms SET expires=?',(time.time()-1,));self.store.conn.commit()
        self.assertEqual(self.req('/api/party/exchange','POST',{'invite':token,'display_name':'Guest','profile_icon':'music'})[0],410)

    def test_single_aux_lease_and_restart_persistence(self):
        cookie,_,_,_=self.join()
        token=self.req(self.path('control'),'POST',{'op':'claim'},self.member)[2]['host_token']
        self.assertEqual(self.req(self.path('control'),'POST',{'op':'claim'},self.admin)[0],409)
        self.assertEqual(self.req(self.path('control'),'POST',{'op':'next','host_token':'fake','expected':None},self.member)[0],403)
        self.assertEqual(self.req(self.path('control'),'POST',{'op':'heartbeat','host_token':token},self.admin)[0],403)
        party.schema(self.store)
        self.store.conn.close()
        self.store.conn = sqlite3.connect(self.root/'isolated.sqlite', check_same_thread=False)
        self.store.conn.row_factory = sqlite3.Row
        self.assertEqual(self.req(self.path(),cookie=cookie)[0],200)
        with self.store.lock:
            self.store.conn.execute('UPDATE party_rooms SET host_until=0');self.store.conn.commit()
        self.assertEqual(self.req(self.path('control'),'POST',{'op':'claim'},self.admin)[0],200)
        self.assertEqual(self.req(self.path('control'),'POST',{'op':'heartbeat','host_token':token},self.member)[0],403)

    def start_playback(self, *tracks):
        entries = [self.req(self.path('queue'), 'POST', {'track_id': i}, self.member)[2]['entry_id'] for i in tracks]
        token = self.req(self.path('control'), 'POST', {'op': 'claim'}, self.member)[2]['host_token']
        self.assertEqual(self.req(self.path('control'), 'POST', {'op': 'next', 'host_token': token, 'expected': None}, self.member)[0], 200)
        return entries, token

    def test_alone_skips_instantly_and_next_up_is_shown(self):
        cookie, _, _, _ = self.join('Solo')
        entries, _ = self.start_playback(1, 2, 3)
        s = self.req(self.path(), cookie=cookie)[2]
        self.assertEqual(s['current']['entry_id'], entries[0])
        self.assertEqual(s['next']['entry_id'], entries[1])
        self.assertEqual(s['next']['title'], 'Track 2')
        self.assertEqual(s['participants'], 1)
        self.assertEqual(s['skip'], {'count': 0, 'mine': False, 'needed': 1})
        r = self.req(self.path('skip'), 'POST', {'expected': entries[0]}, cookie)[2]
        self.assertTrue(r['skipped'])
        self.assertEqual((r['skips'], r['people']), (1, 1))
        s = self.req(self.path(), cookie=cookie)[2]
        self.assertEqual(s['current']['entry_id'], entries[1])
        self.assertEqual(s['next']['entry_id'], entries[2])

    def test_skip_needs_strictly_more_than_half_of_the_people_present(self):
        a, _, _, _ = self.join('A')
        b, _, _, _ = self.join('B')
        entries, _ = self.start_playback(1, 2)
        # Three people have the room open: the member, guest A and guest B.
        for co in (self.member, a, b):
            self.req(self.path(), cookie=co)
        s = self.req(self.path(), cookie=a)[2]
        self.assertEqual((s['participants'], s['skip']['needed']), (3, 2))
        first = self.req(self.path('skip'), 'POST', {'expected': entries[0]}, a)[2]
        self.assertFalse(first['skipped'])
        self.assertEqual((first['skips'], first['people']), (1, 3))
        self.assertEqual(self.req(self.path(), cookie=a)[2]['current']['entry_id'], entries[0])
        # One person repeating their skip must not count twice.
        again = self.req(self.path('skip'), 'POST', {'expected': entries[0]}, a)[2]
        self.assertFalse(again['skipped'])
        self.assertEqual(again['skips'], 1)
        second = self.req(self.path('skip'), 'POST', {'expected': entries[0]}, b)[2]
        self.assertTrue(second['skipped'])
        self.assertEqual(second['skips'], 2)
        self.assertEqual(self.req(self.path(), cookie=a)[2]['current']['entry_id'], entries[1])

    def test_two_people_need_both_skips_and_stale_presence_stops_counting(self):
        a, _, _, _ = self.join('A')
        entries, _ = self.start_playback(1, 2, 3)
        self.req(self.path(), cookie=self.member)
        self.req(self.path(), cookie=a)
        self.assertEqual(self.req(self.path(), cookie=a)[2]['skip']['needed'], 2)
        self.assertFalse(self.req(self.path('skip'), 'POST', {'expected': entries[0]}, a)[2]['skipped'])
        # Guest B closed the tab: their skip vote stops counting as well.
        with self.store.lock:
            self.store.conn.execute('UPDATE party_presence SET seen=?', (time.time() - party.PRESENCE_TTL - 5,))
            self.store.conn.commit()
        s = self.req(self.path(), cookie=self.member)[2]
        self.assertEqual((s['participants'], s['skip']['needed'], s['skip']['count']), (1, 1, 0))
        done = self.req(self.path('skip'), 'POST', {'expected': entries[0]}, self.member)[2]
        self.assertTrue(done['skipped'])
        self.assertEqual(self.req(self.path(), cookie=self.member)[2]['current']['entry_id'], entries[1])

    def test_skip_requires_playback_and_ignores_a_stale_expected_id(self):
        cookie, _, _, _ = self.join('A')
        self.assertEqual(self.req(self.path('skip'), 'POST', {'expected': None}, cookie)[0], 409)
        entries, _ = self.start_playback(1, 2)
        stale = self.req(self.path('skip'), 'POST', {'expected': entries[0] + 100}, cookie)[2]
        self.assertFalse(stale['skipped'])
        self.assertEqual(stale['current'], entries[0])
        self.assertEqual(self.req(self.path(), cookie=cookie)[2]['current']['entry_id'], entries[0])
        self.assertEqual(self.req(self.path('skip'), 'POST', {'expected': 'x'}, cookie)[0], 400)

    def test_chat_server_identity_icons_spoof_rejection_order_and_scope(self):
        cookie,_,_,_=self.join('<Guest>','headphones')
        bodies=[('hello',cookie),('<img src=x onerror=alert(1)>',self.member),('admin',self.admin)]
        for text,co in bodies:self.assertEqual(self.req(self.path('chat'),'POST',{'body':text},co)[0],200)
        for extra in ('display_name','profile_icon','author','id'):
            self.assertEqual(self.req(self.path('chat'),'POST',{'body':'fake',extra:'Spoof'},cookie)[0],400)
        messages=self.req(self.path('chat'),cookie=cookie)[2]['messages']
        self.assertEqual([m['body'] for m in messages],[x[0] for x in bodies])
        self.assertEqual([m['profile_icon'] for m in messages],['headphones','moon','star'])
        self.assertEqual([m['display_name'] for m in messages],['<Guest>','M','A'])
        self.assertNotIn('email',str(messages));self.assertNotIn('author',messages[0])
        self.assertEqual(self.req(self.path('chat')+'?after='+str(messages[0]['id']),cookie=cookie)[2]['messages'],messages[1:])
        self.assertEqual(self.req(self.path('chat'),'POST',{'body':'x'*1001},cookie)[0],400)
        self.assertEqual(self.req(self.path('chat'),'POST',{'body':'   '},cookie)[0],400)
        self.assertEqual(self.req(self.path('chat')+'?after=bad',cookie=cookie)[0],400)
        other=self.req('/api/party','POST',{'title':'Other'},self.member)[2]['id']
        self.assertEqual(self.req('/api/party/'+other+'/chat',cookie=self.member)[2]['messages'],[])

    def test_concurrent_rejoins_and_votes_share_one_guest_identity(self):
        from concurrent.futures import ThreadPoolExecutor
        cookie,secret,_,_=self.join()
        eid=self.req(self.path('queue'),'POST',{'track_id':1},cookie)[2]['entry_id']
        def rejoin(_):
            code,h,_=self.req('/api/party/exchange','POST',{'invite':secret})
            self.assertEqual(code,200)
            return h['Set-Cookie'].split(';')[0]
        with ThreadPoolExecutor(max_workers=4) as pool:
            cookies=list(pool.map(rejoin,range(4)))
            results=list(pool.map(lambda co:self.req(self.path('vote'),'POST',{'entry_id':eid,'value':1},co)[0],cookies))
        self.assertEqual(results,[200]*4)
        self.assertEqual(self.req(self.path(),cookie=cookie)[2]['queue'][0]['score'],1)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM party_votes').fetchone()[0],1)

    def test_chat_retention_paging_and_party_request_log_suppression(self):
        with self.store.lock:
            self.store.conn.executemany('INSERT INTO party_chat(party,author,display_name,profile_icon,body,created) VALUES(?,?,?,?,?,?)',[(self.pid,'m:m','M','moon',str(i),time.time()) for i in range(505)])
            self.store.conn.commit()
        self.req(self.path('chat'),'POST',{'body':'trim'},self.member)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM party_chat').fetchone()[0],500)
        result=self.req(self.path('chat'),cookie=self.member)[2]
        self.assertEqual(len(result['messages']),100);self.assertTrue(result['has_more'])
        out=io.StringIO()
        with contextlib.redirect_stdout(out):self.req(self.path()+'?sensitive=do-not-log',cookie=self.member)
        self.assertNotIn('do-not-log',out.getvalue())


if __name__=='__main__':unittest.main()
