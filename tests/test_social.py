"""No live boot, DB, provider or media. Identity verification is fixture-backed."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import test_security as security_tests
from social import album_id


class SocialHTTP(unittest.TestCase):
    tearDown = security_tests.SecurityHTTP.tearDown
    request = security_tests.SecurityHTTP.request
    token = security_tests.SecurityHTTP.token
    user = security_tests.SecurityHTTP.user

    def setUp(self):
        security_tests.SecurityHTTP.setUp(self)
        self.store.set_settings({'root': str(Path(self.tmp.name) / 'media')})
        with self.store.lock:
            self.store.conn.execute("INSERT INTO tracks(provider,provider_track_id,title,artist_name,album,added) VALUES('local','fixture','Song','Artist','Album',0)")
            self.store.conn.execute("INSERT INTO playlists(title,added) VALUES('Set',0)")
            self.store.conn.commit()
        self.headers = {'Authorization': 'Bearer fixture', 'Origin': 'https://music.example'}
        self.identity = self.user()


    def call(self, suffix='', data=None, target='track/1'):
        status, _, body = self.request('/api/social/'+target+suffix, 'GET' if data is None else 'POST', self.headers, None if data is None else json.dumps(data))
        return status, json.loads(body)

    def switch(self, member='other', role='member'):
        self.identity = dict(self.identity, id=member, email=member+'@example.com')

        self.store.conn.execute('INSERT OR REPLACE INTO members(id,email,role) VALUES(?,?,?)', (member,self.identity['email'],role))
        self.store.conn.commit()

    def test_social_always_requires_approved_verified_identity(self):
        for required in ('0','1'):
            os.environ['HARDLYLISTENING_AUTH_REQUIRED'] = required
            for method in ('GET','POST'):
                self.assertEqual(self.request('/api/social/track/1',method,body='{}' if method=='POST' else None)[0],401)
            self.switch('pending','pending')
            self.assertEqual(self.call()[0],403)
            self.switch('approved')
            self.assertEqual(self.call()[0],200)
            self.assertEqual(self.call('/comments',{'body':'verified in mode '+required})[0],200)

        self.sec.revoke(self.store,self.identity['id'])
        self.headers = {}
        self.assertEqual(self.call()[0],401)

    def test_comments_ownership_admin_and_xss(self):
        text='<img src=x onerror=alert(1)> & "hello"'
        self.assertEqual(self.call('/comments',{'body':text,'member_id':'forged'})[0],200)
        row=self.call()[1]['items'][0]
        self.assertEqual(row['body'],text)
        self.assertEqual(row['member_id'],'u1')
        self.assertEqual(self.call('/comments/'+str(row['id']),{'body':'edited'})[0],200)
        self.switch()
        self.assertFalse(self.call()[1]['items'][0]['can_edit'])
        for data in ({'body':'stolen'},{'delete':True}):
            self.assertEqual(self.call('/comments/'+str(row['id']),data)[0],403)
        self.switch('moderator','admin')
        self.assertEqual(self.call('/comments/'+str(row['id']),{'body':'moderated'})[0],200)
        self.assertEqual(self.call('/comments/'+str(row['id']),{'delete':True})[0],200)
        self.assertEqual(self.call()[1]['comments'],0)
        self.assertEqual(self.call('/comments/999',{'delete':True})[0],404)

    def test_votes_switch_reset_counts_and_community_visibility(self):
        for value,score,up,down in ((1,1,1,0),(1,1,1,0),(-1,-1,0,1),(0,0,0,0)):
            status, data=self.call('/vote',{'value':value})
            self.assertEqual(status,200)
            self.assertEqual([data[k] for k in ('score','up','down','vote')],[score,up,down,value])
        self.call('/vote',{'value':-1})
        self.switch()
        data=self.call('/vote',{'value':1})[1]
        self.assertEqual([data[k] for k in ('score','up','down','vote')],[0,1,1,1])
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM social_votes').fetchone()[0],2)
        for invalid in (True,2,-2,'1',None):
            self.assertEqual(self.call('/vote',{'value':invalid})[0],400)

    def test_csrf_rate_limits_lengths_invalid_targets(self):
        h=dict(self.headers,Origin='https://evil.example')
        self.assertEqual(self.request('/api/social/track/1/vote','POST',h,'{"value":1}')[0],403)
        for body in ('',' ','x'*2001,'\x00'):
            self.assertEqual(self.call('/comments',{'body':body})[0],400)
        for target in ('track/999','playlist/999','album/'+'a'*64):
            self.assertEqual(self.call(target=target)[0],404)
        for target in ('album/arbitrary','track/01','track/-1','bad/1'):
            self.assertIn(self.call(target=target)[0],(400,404))
        self.sec.rates.clear()
        for _ in range(30):
            self.assertEqual(self.call('/vote',{'value':1})[0],200)
        self.assertEqual(self.call('/vote',{'value':0})[0],429)
        self.assertEqual(self.call()[1]['vote'],1)

    def test_concurrent_switches_keep_one_vote(self):
        from concurrent.futures import ThreadPoolExecutor
        values = [1,-1,0,1,-1,0,1,-1,1,0,1,-1]
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda value: self.call('/vote',{'value':value})[0], values))
        self.assertEqual(statuses,[200]*len(values))
        # A final ordered write has a deterministic score after the race.
        self.assertEqual(self.call('/vote',{'value':1})[0],200)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM social_votes').fetchone()[0],1)
        result=self.call()[1]
        self.assertEqual([result[k] for k in ('score','up','down','vote')],[1,1,0,1])

    def test_batch_summary_counts_many_targets_once(self):
        self.assertEqual(self.call('/vote', {'value': 1})[0], 200)
        self.switch('other')
        self.assertEqual(self.call('/vote', {'value': -1})[0], 200)
        self.assertEqual(self.call('/comments', {'body': 'note'})[0], 200)
        ids = ['1', '2', '3']
        status, _, body = self.request('/api/social/summary?kind=track&targets=' + ','.join(ids), 'GET', self.headers)
        self.assertEqual(status, 200)
        items = json.loads(body)['items']
        self.assertEqual(items['1'], {'score': 0, 'up': 1, 'down': 1, 'vote': -1, 'comments': 1})
        self.assertEqual(items['2'], {'score': 0, 'up': 0, 'down': 0, 'vote': 0, 'comments': 0})
        self.assertEqual(self.request('/api/social/summary?kind=evil&targets=1', 'GET', self.headers)[0], 400)
        self.assertEqual(self.request('/api/social/summary?kind=track&targets=abc,1', 'GET', self.headers)[0], 200)
        self.assertEqual(self.request('/api/social/summary?kind=track&targets=1', 'POST', self.headers, '{}')[0], 405)
        self.assertEqual(self.request('/api/social/summary?kind=track&targets=1', 'GET')[0], 401)

    def test_batch_can_carry_the_newest_comments_for_card_bubbles(self):
        for body in ('first note', 'second note', 'third note'):
            self.assertEqual(self.call('/comments', {'body': body})[0], 200)
        status, _, raw = self.request(
            '/api/social/summary?kind=track&targets=1&latest=2', 'GET', self.headers)
        self.assertEqual(status, 200)
        item = json.loads(raw)['items']['1']
        self.assertEqual([c['body'] for c in item['recent']], ['third note', 'second note'])
        self.assertEqual(item['comments'], 3)          # the count still sees all three
        self.assertEqual(json.loads(self.request(
            '/api/social/summary?kind=track&targets=1', 'GET', self.headers)[2])['items']['1'].get('recent'), None)
        self.assertEqual(self.request(
            '/api/social/summary?kind=track&targets=1&latest=abc', 'GET', self.headers)[0], 400)

    def test_album_playlist_targets_and_pagination(self):
        aid=album_id({'artist_name':'Artist','album':'Album'})
        self.assertEqual(aid,album_id({'artist_name':' ARTIST ','album':'album'}))
        self.assertNotEqual(aid,album_id({'artist_name':'Someone else','album':'Album'}))
        for target in ('album/'+aid,'playlist/1'):
            self.assertEqual(self.call('/comments',{'body':'Shared'},target)[0],200)
            self.assertEqual(self.call('/vote',{'value':-1},target)[0],200)
            self.assertEqual(self.call(target=target)[1]['score'],-1)
        self.assertEqual(self.call()[1]['comments'],0)
        with self.store.conn:
            self.store.conn.executemany("INSERT INTO social_comments(kind,target,member_id,body) VALUES('track','1','u1',?)", [(str(i),) for i in range(35)])
        first=self.call()[1]
        self.assertEqual(len(first['items']),30)
        second=self.call('?after='+str(first['next_after']))[1]
        self.assertEqual(len(second['items']),5)
        self.assertIsNone(second['next_after'])
        self.assertEqual(len({r['id'] for r in first['items']+second['items']}),35)
        self.assertEqual(self.call('?after=-1')[0],400)
