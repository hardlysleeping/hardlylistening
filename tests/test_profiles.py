import json
import test_security
import unittest
from security import Denied

class Profiles(unittest.TestCase):
    setUp = test_security.SecurityHTTP.setUp
    tearDown = test_security.SecurityHTTP.tearDown
    request = test_security.SecurityHTTP.request
    user = test_security.SecurityHTTP.user
    token = test_security.SecurityHTTP.token
    def test_profile_self_only_and_admin_guard(self):
        self.user('admin'); h={'Cookie':self.token(),'Origin':'https://music.example'}
        self.store.conn.execute("INSERT INTO members(id,role,display_name) VALUES('other','admin','Other')"); self.store.conn.commit()
        for role in ('member','pending','blocked'):
            self.assertEqual(self.request('/api/members','POST',h,json.dumps({'id':'u1','role':role}))[0],400)
        self.assertEqual(self.request('/api/members/revoke','POST',h,json.dumps({'id':'u1'}))[0],400)
        for extra in ({'id':'other'},{'role':'admin'}):
            self.assertEqual(self.request('/api/profile','POST',h,json.dumps(dict(display_name='X',profile_icon='star',**extra)))[0],400)
        for name,icon in [('', 'music'),('x'*101,'music'),('X','https://evil/avatar')]:
            self.assertEqual(self.request('/api/profile','POST',h,json.dumps({'display_name':name,'profile_icon':icon}))[0],400)
        self.assertEqual(self.request('/api/profile','POST',h,json.dumps({'display_name':'<Owner>','profile_icon':'star'}))[0],200)
        self.assertEqual(json.loads(self.request('/api/profile',headers=h)[2])['display_name'],'<Owner>')
        self.assertEqual(self.store.conn.execute("SELECT display_name FROM members WHERE id='other'").fetchone()[0],'Other')
        self.assertEqual(self.request('/api/profile','POST',{'Cookie':h['Cookie']},'{}')[0],403)
        self.assertEqual(self.request('/api/profile')[0],401)

    def test_member_and_admin_equal_personal_access(self):
        for role in ('member','admin'):
            self.user(role); h={'Cookie':self.token(),'Origin':'https://music.example'}
            self.assertEqual(self.request('/api/profile','POST',h,json.dumps({'display_name':'My name','profile_icon':'headphones'}))[0],200)
            for path in ('/api/listening/saves','/api/listening/devices'):
                self.assertEqual(self.request(path,headers=h)[0],200)
            self.assertEqual(self.request('/api/profile','POST',h,json.dumps({'display_name':'x','profile_icon':[]}))[0],400)

    def test_unnamed_atomic_profile_recovery(self):
        self.user('admin'); actor={'id':'u1','role':'admin'}
        with self.assertRaises(Denied): self.sec.create_user(self.store,actor,'Admin chosen','member')
        uid=self.sec.create_user(self.store,actor,None,'member')
        token=self.sec.invite(self.store,actor,uid).split('invite=')[1]
        for name,icon in [(None,None),(' ','music'),('Friend','remote')]:
            with self.assertRaises(Denied): self.sec.exchange(self.store,token,name,icon)
            self.assertEqual(self.store.conn.execute('SELECT count(*) FROM browser_invites').fetchone()[0],1)
            self.assertEqual(self.store.conn.execute('SELECT count(*) FROM browser_sessions').fetchone()[0],0)
        self.sec.exchange(self.store,token,'Friend','sun')
        # The link is the account: opening it again signs the same person in and
        # changes nothing about their profile, whatever name the form sends.
        self.sec.exchange(self.store,token,'Imposter','moon')
        r=self.sec.member(self.store,{'id':uid}); self.assertEqual((r['display_name'],r['profile_icon']),('Friend','sun'))
        token=self.sec.invite(self.store,actor,uid).split('invite=')[1]
        self.sec.exchange(self.store,token,'Replacement','moon')
        r=self.sec.member(self.store,{'id':uid}); self.assertEqual((r['display_name'],r['profile_icon']),('Friend','sun'))
        self.sec.profile(self.store,r,{'display_name':'New name','profile_icon':'moon'})
        self.assertEqual(self.sec.member(self.store,{'id':uid})['display_name'],'New name')
