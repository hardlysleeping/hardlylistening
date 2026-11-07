import json
import sqlite3
import threading
import unittest
from unittest.mock import patch, MagicMock
import genres

class GenreTests(unittest.TestCase):
    def setUp(self):
        self.store=MagicMock(); self.store.lock=threading.RLock()
        self.store.conn=sqlite3.connect(':memory:'); self.store.conn.row_factory=sqlite3.Row
        self.store.conn.execute('CREATE TABLE tracks(id INTEGER PRIMARY KEY)')
        self.store.conn.execute('INSERT INTO tracks VALUES(1)')
        self.track={'id':1,'title':'Title','artist_name':'Artist','album':'Album'}
    def tearDown(self): self.store.conn.close()
    def test_taxonomy(self):
        for tags in ([],['invented'],['house','unknown'],['house','house'],['non-edm','bass']):
            with self.assertRaises(ValueError): genres.validate(tags)
        self.assertEqual(genres.validate(['unknown']),['unknown'])
    def test_cache_and_manual(self):
        genres.save(self.store,self.track,['house'],'model')
        self.assertEqual(genres.annotate(self.store,[dict(self.track)])[0]['genres'],['house'])
        changed=dict(self.track,title='Other')
        self.assertEqual(genres.annotate(self.store,[changed])[0]['genres'],[])
        genres.save(self.store,self.track,['techno'],manual=True)
        genres.save(self.store,self.track,['house'],'model')
        self.assertEqual(genres.annotate(self.store,[changed])[0]['genres'],['techno'])
    def test_no_schema_write_on_read(self):
        self.assertEqual(genres.annotate(self.store,[dict(self.track)])[0]['genres'],[])
        self.assertIsNone(self.store.conn.execute("SELECT name FROM sqlite_master WHERE name='genre_labels'").fetchone())
    @patch.dict('os.environ',{'OPENROUTER_API_KEY':'router-secret','HARDLYLISTENING_AI_API_KEY':'gateway-secret'})
    @patch('genres.request.build_opener')
    def test_provider_scope_schema_and_caps(self,opener):
        response=opener.return_value.open.return_value.__enter__.return_value
        def reply(content): response.read.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':content}}]}).encode()
        reply('{"tags":["unknown"]}')
        self.assertEqual(genres.infer(self.track,'model','https://openrouter.ai/api/v1'),['unknown'])
        req=opener.return_value.open.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'),'Bearer router-secret')
        genres.infer(self.track,'model','https://gateway.example/v1')
        req=opener.return_value.open.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'),'Bearer gateway-secret')
        for bad in ('not json','{"tags":["fake"]}','{"tags":["house"],"confidence":0.9}','{"tags":["house"],"tags":["techno"]}'):
            reply(bad)
            with self.assertRaises((ValueError,KeyError)): genres.infer(self.track,'model','https://gateway.example/v1')
    def test_auth_always_and_admin(self):
        handler=MagicMock(); handler.command='POST'
        security=MagicMock(); security.authenticate.return_value={'id':'one'}
        security.admin.side_effect=genres.Denied(403,'admin')
        with self.assertRaises(genres.Denied): genres.dispatch(handler,self.store,security,'/api/genres/generate')
        security.authenticate.assert_called_once_with(handler,self.store)
        handler.body.assert_not_called()

if __name__=='__main__': unittest.main()
