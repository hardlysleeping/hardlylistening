"""Memory-only Auto DJ lease tests: no Store, server or media."""
import unittest
from unittest.mock import patch
from mixer_room import Room

class AutoDJLease(unittest.TestCase):
    def test_per_tab_and_expiry(self):
        r = Room()
        a = {"id": "same-member"}
        with patch('mixer_room.time.time', return_value=100):
            self.assertTrue(r.auto_claim(a, 'tab-a'))
            self.assertFalse(r.auto_claim(a, 'tab-b'))
            self.assertFalse(r.auto_claim(a, 'tab-b', False))
            self.assertTrue(r.auto_claim(a, 'tab-a'))
            self.assertEqual(r.snapshot()['autodj']['token'], 'tab-a')
        with patch('mixer_room.time.time', return_value=107):
            self.assertTrue(r.auto_claim(a, 'tab-b'))
            self.assertFalse(r.auto_claim(a, 'tab-a'))
            self.assertTrue(r.auto_claim(a, 'tab-b', False))
            self.assertIsNone(r.snapshot()['autodj'])

    def test_concurrent_claims(self):
        from concurrent.futures import ThreadPoolExecutor
        r = Room()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: r.auto_claim({'id': 'member'}, str(n)), range(8)))
        self.assertEqual(sum(results), 1)

if __name__ == '__main__':
    unittest.main()
