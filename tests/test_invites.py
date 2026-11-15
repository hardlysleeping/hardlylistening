"""Migration, closed bootstrap and durable cross-connection replay fixtures."""
import os
import subprocess
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from db import Store
from security import Security, Denied

class DurableInvites(unittest.TestCase):
    def test_legacy_migration_cli_restart_and_atomic_connections(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp, patch.dict(os.environ, {'HARDLYLISTENING_PUBLIC_ORIGIN':'https://music.example'}):
            path=Path(tmp)/'db.sqlite';store=Store(path)
            store.conn.execute("CREATE TABLE members(id TEXT PRIMARY KEY,email TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'pending')")
            store.conn.execute("INSERT INTO members VALUES('legacy-uuid','old@example.com','admin')")
            store.conn.commit()
            sec=Security();sec.schema(store);sec.schema(store)
            row=store.conn.execute("SELECT * FROM members WHERE id='legacy-uuid'").fetchone()
            self.assertEqual(row['email'],'old@example.com');self.assertEqual(row['role'],'admin')
            self.assertEqual(row['profile_icon'],'music')
            cmd=[sys.executable,str(Path(__file__).resolve().parents[1]/'auth_admin.py'),'--db',str(path),'--member-id','legacy-uuid']
            result=subprocess.run(cmd,capture_output=True,text=True,check=True)
            token=result.stdout.strip().split('#invite=')[1]
            store.conn.close()
            a=Store(path);b=Store(path);gate=threading.Barrier(2)
            def consume(store):
                gate.wait()
                return Security().exchange(store,token,'Legacy owner','music')
            with ThreadPoolExecutor(2) as pool:values=list(pool.map(consume,[a,b]))
            # One permanent link, any number of sessions: both threads sign in and
            # the link itself survives for next time.
            self.assertTrue(all(values))
            self.assertNotEqual(values[0],values[1])
            sid=values[0]
            a.conn.close();b.conn.close();c=Store(path)
            self.assertIn(sec.digest(sid),{r[0] for r in c.conn.execute('SELECT digest FROM browser_sessions')})
            self.assertEqual(c.conn.execute('SELECT id FROM members').fetchone()[0],'legacy-uuid')
            profile=c.conn.execute('SELECT display_name,profile_icon FROM members').fetchone()
            self.assertEqual(tuple(profile),('Legacy owner','music'))
            self.assertNotIn(token,str(c.conn.execute('SELECT * FROM browser_invites').fetchall()))
            c.conn.close()

    def test_nonadmin_cannot_create_or_invite(self):
        sec=Security()
        for actor in (None,{'role':'member'},{'role':'pending'}):
            for action in (lambda:sec.create_user(None,actor,'Name','admin'),lambda:sec.invite(None,actor,'id')):
                with self.assertRaises(Denied) as error:action()
                self.assertEqual(error.exception.code,403)
