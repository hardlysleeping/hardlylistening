"""Bypass is a switch, not a one-way door: whatever the member sets is what is stored."""
from test_listening import ListeningHTTP


class EqHTTP(ListeningHTTP):
    def eq(self, data=None):
        return self.api('eq', data)

    def test_bypass_toggles_both_ways_over_a_flat_curve(self):
        code, body = self.eq()
        self.assertEqual(code, 200)
        self.assertFalse(body['eq']['enabled'])
        self.assertEqual(body['eq']['gains'], [0] * 6)
        # Leaving bypass with an untouched (flat) curve must stick. This used to come back
        # disabled, so the button could be pressed on but never off again.
        code, body = self.eq({'enabled': True, 'gains': [0] * 6, 'loudness': False})
        self.assertEqual(code, 200)
        self.assertTrue(body['eq']['enabled'])
        self.assertTrue(self.eq()[1]['eq']['enabled'])
        # And back again.
        code, body = self.eq({'enabled': False, 'gains': [0] * 6, 'loudness': False})
        self.assertEqual(code, 200)
        self.assertFalse(body['eq']['enabled'])
        self.assertFalse(self.eq()[1]['eq']['enabled'])

    def test_flat_is_off_but_a_curve_survives_bypass(self):
        curve = [6, 3, 0, -2, 0, 4]
        self.assertEqual(self.eq({'enabled': True, 'gains': curve, 'loudness': False})[1]['eq'],
                         {'enabled': True, 'gains': curve, 'loudness': False})
        # Bypass keeps the curve: it is a switch, not a reset.
        self.assertTrue(self.eq({'enabled': False, 'gains': curve, 'loudness': False})[1]
                        ['eq']['gains'] == curve)
        self.assertEqual(self.eq({'enabled': False, 'gains': [0] * 6,
                                  'loudness': True})[1]['eq'],
                         {'enabled': False, 'gains': [0] * 6, 'loudness': True})

    def test_still_validates(self):
        self.assertEqual(self.eq({'enabled': 'yes', 'gains': [0] * 6})[0], 400)
        self.assertEqual(self.eq({'enabled': True, 'gains': [0] * 5})[0], 400)
        self.assertEqual(self.eq({'enabled': True, 'gains': [0, 0, 0, 0, 0, 13]})[0], 400)