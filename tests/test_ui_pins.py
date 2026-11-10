import os, unittest
os.environ.setdefault('TMPDIR', '/tmp')


class UIPins(unittest.TestCase):
    def setUp(self):
        import tempfile, sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        self.dir = tempfile.mkdtemp(dir='/tmp')
        import db
        self.store = db.Store(os.path.join(self.dir, 'test.db'))
        import security
        self.sec = security.Security()
        import preferences
        self.prefs = preferences
        self.prefs.ui_schema(self.store)

    def test_partial_writes_merge(self):
        self.prefs.set_ui_state(self.store, 7, {'view': 'library', 'libview': 'albums'})
        self.prefs.set_ui_state(self.store, 7, {'pins': 1})
        state = self.prefs.ui_state(self.store, 7)
        self.assertEqual(state['pins'], 1)
        self.assertEqual(state['libview'], 'albums')
        self.assertEqual(state['view'], 'library')
        self.prefs.set_ui_state(self.store, 7, {'pins': 0})
        self.assertEqual(self.prefs.ui_state(self.store, 7)['pins'], 0)
        self.assertEqual(self.prefs.ui_state(self.store, 7)['libview'], 'albums')

    def test_pins_value_validated(self):
        with self.assertRaises(Exception):
            self.prefs.set_ui_state(self.store, 7, {'pins': 'yes'})


if __name__ == '__main__':
    unittest.main()
