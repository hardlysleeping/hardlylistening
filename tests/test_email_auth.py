"""Local invite UI replaces retired provider email workflow."""
import unittest
from pathlib import Path
class InvitePages(unittest.TestCase):
    def test_fragment_post_no_email(self):
        root=Path(__file__).resolve().parents[1]/'web'
        login=(root/'login.html').read_text()
        link=(root/'link.html').read_text()
        self.assertIn('Invitation required',login)
        self.assertNotIn('/api/auth/otp',login)
        self.assertIn('history.replaceState',link)
        self.assertIn('JSON.stringify({invite: token, ...profile})',link)
        self.assertNotIn('access_token',link)
