"""Preferences use only scratch sessions/database/media from listening fixtures."""
import json
from test_listening import ListeningHTTP
import social


class PreferencesHTTP(ListeningHTTP):
    def pref(self, action='favorite', kind='track', identity=None, enabled=True, **extra):
        return self.api('preferences', dict(action=action, kind=kind, id=identity if identity is not None else self.ids[0], enabled=enabled, **extra))

    def test_preferences_isolated_idempotent_undo_shared_pool(self):
        before = [tuple(r) for r in self.store.conn.execute('SELECT * FROM tracks')]
        for _ in range(2):
            self.assertEqual(self.pref()[0], 200)
        self.assertEqual(self.api('preferences')[1]['favorites'], [self.ids[0]])
        self.assertEqual(self.api('saves')[1]['tracks'], [self.ids[0]])
        self.assertEqual(self.response('getPlaylist', id='favorites')['playlist']['songCount'], 1)
        self.assertEqual(self.response('getPlaylist', id='favorites')['playlist']['entry'][0]['id'], str(self.ids[0]))
        first = self.device
        self.identity = dict(self.identity, id='second')
        with self.store.lock:
            self.store.conn.execute("INSERT INTO members(id,email,role) VALUES('second','','member')")
            self.store.conn.commit()
        second = self.api('devices', {'label':'Other'})[1]
        self.assertEqual(self.api('preferences')[1]['favorites'], [])
        self.assertEqual(self.api('preferences')[1]['pins'], [])
        self.assertEqual(json.loads(self.rest('getPlaylist', device=second, id='favorites')[2])['subsonic-response']['playlist']['entry'], [])
        self.pref(identity=self.ids[1])
        self.identity = dict(self.identity,id='u1')
        self.assertEqual(self.api('preferences')[1]['favorites'], [self.ids[0]])
        self.assertEqual(self.pref(enabled=False)[0], 200)
        self.assertEqual(self.pref(enabled=False)[0], 200)
        self.assertEqual(self.api('preferences')[1]['favorites'], [])
        self.assertEqual(self.api('saves')[1]['tracks'], [self.ids[0]])
        self.assertEqual([tuple(r) for r in self.store.conn.execute('SELECT * FROM tracks')], before)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM playlists').fetchone()[0], 1)
        self.assertEqual(self.rest('stream', device=first, id=self.ids[0])[2], self.media)

    def test_mixed_pins_snapshot_and_validation(self):
        album = social.album_id(dict(self.store.conn.execute('SELECT * FROM tracks WHERE id=?',(self.ids[0],)).fetchone()))
        for kind, identity in [('track',self.ids[0]),('album',album),('playlist',self.pid)]:
            for _ in range(2):
                self.assertEqual(self.pref('pin',kind,identity)[0],200)
        self.assertEqual(len(self.api('preferences')[1]['pins']),3)
        self.assertEqual(self.api('saves')[1]['playlists'],[self.pid])
        self.store.add_to_playlist(self.pid,self.ids[2],1)
        self.assertEqual(len(self.api('saves')[1]['playlist_tracks']),1)
        for kind, identity in [('track',self.ids[0]),('album',album),('playlist',self.pid)]:
            self.assertEqual(self.pref('pin',kind,identity,False)[0],200)
        self.assertEqual(self.api('preferences')[1]['pins'],[])
        self.assertEqual(self.pref(kind='album',identity=album)[0],400)
        self.assertEqual(self.pref(identity=999999)[0],404)
        self.assertEqual(self.pref(identity=True)[0],400)
        self.assertEqual(self.pref(member_id='second')[0],400)
        self.assertEqual(self.pref('pin','album','bad')[0],400)
        self.assertEqual(self.api('preferences',headers={})[0],401)
        self.assertEqual(self.api('preferences',{'action':'favorite','kind':'track','id':self.ids[0],'enabled':True},headers={'Authorization':'Bearer fixture'})[0],403)
