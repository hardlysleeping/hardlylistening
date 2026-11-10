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


class EmbeddedGenreTests(unittest.TestCase):
    """The downloader writes the uploader's genre into the file; read it back."""

    def setUp(self):
        self.store = MagicMock()
        self.store.lock = threading.RLock()
        self.store.conn = sqlite3.connect(':memory:')
        self.store.conn.row_factory = sqlite3.Row
        self.store.conn.execute('CREATE TABLE tracks(id INTEGER PRIMARY KEY, title TEXT, artist_name TEXT, album TEXT, file TEXT)')
        self.store.conn.execute("INSERT INTO tracks VALUES(1,'Title','Artist','Album','/tmp/one.mp3')")
        self.store.conn.execute("INSERT INTO tracks VALUES(2,'Two','Artist','Album','')")
        self.store.conn.commit()
        self.track = {'id': 1, 'title': 'Title', 'artist_name': 'Artist', 'album': 'Album'}

    def tearDown(self):
        self.store.conn.close()

    def label(self, track_id=1):
        row = self.store.conn.execute('SELECT * FROM genre_labels WHERE track_id=?', (track_id,)).fetchone()
        return dict(row) if row else None

    def test_mapping_keeps_only_taxonomy_words(self):
        self.assertEqual(genres.map_tags('Dubstep'), ['dubstep'])
        self.assertEqual(genres.map_tags('Drum & Bass'), ['drum-and-bass'])
        self.assertEqual(genres.map_tags('Techno, House'), ['techno', 'house'])
        self.assertEqual(genres.map_tags('Electronic'), [])
        self.assertEqual(genres.map_tags(''), [])
        self.assertEqual(genres.map_tags('totally invented'), [])

    def test_embedded_outranks_ai_but_not_a_correction(self):
        genres.save(self.store, self.track, ['house'], 'model')                        # ai
        genres.save(self.store, self.track, ['dubstep'], 'embedded', source='metadata')  # file
        self.assertEqual(self.label()['source'], 'metadata')
        self.assertEqual(genres.annotate(self.store, [dict(self.track)])[0]['genres'], ['dubstep'])
        genres.save(self.store, self.track, ['techno'], 'model')                       # ai again
        self.assertEqual(self.label()['source'], 'metadata')                            # not overwritten
        genres.save(self.store, self.track, ['tech-house'], manual=True)                 # human wins
        self.assertEqual(self.label()['source'], 'manual')
        genres.save(self.store, self.track, ['dubstep'], 'embedded', source='metadata')
        self.assertEqual(self.label()['source'], 'manual')

    def test_save_from_file_reads_the_tag(self):
        with patch.object(genres, 'embedded', return_value=['techno']):
            tags = genres.save_from_file(self.store, 1, '/tmp/one.mp3')
        self.assertEqual(tags, ['techno'])
        self.assertEqual(self.label()['source'], 'metadata')
        with patch.object(genres, 'embedded', return_value=[]):
            self.assertEqual(genres.save_from_file(self.store, 1, '/tmp/one.mp3'), [])

    def test_backfill_only_touches_downloaded_untagged_files(self):
        genres.save(self.store, self.track, ['house'], 'model')      # ai-labelled, may be replaced
        with patch.object(genres, 'embedded', return_value=['dubstep']):
            result = genres.backfill(self.store)
        self.assertEqual(result['scanned'], 1)                       # only track 1 has a file
        self.assertEqual(result['tagged'], 1)
        self.assertEqual(self.label()['source'], 'metadata')

    def test_backfill_leaves_a_correction_alone(self):
        genres.save(self.store, self.track, ['tech-house'], manual=True)
        with patch.object(genres, 'embedded', return_value=['dubstep']):
            result = genres.backfill(self.store)
        self.assertEqual(result['scanned'], 0)
        self.assertEqual(self.label()['tags'], '["tech-house"]')
