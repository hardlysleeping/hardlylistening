"""The Artists feed's "My" filter needs to know whose follow is whose."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('TMPDIR', '/tmp')


class SubscriptionOwnership(unittest.TestCase):
    def setUp(self):
        import db
        self.store = db.Store(os.path.join(tempfile.mkdtemp(dir='/tmp'), 'w.db'))

    def add(self, artist_id, member_id=''):
        return self.store.watch_add(
            'apple', artist_id, 'Artist ' + artist_id,
            f'https://music.apple.com/us/artist/{artist_id}', member_id=member_id)

    def test_a_follow_remembers_who_asked(self):
        first = self.add('900', 'm-a')
        self.assertEqual(self.store.watch_get(first)['member_id'], 'm-a')
        # Someone else following the same artist joins the existing watch; the
        # first person keeps the credit for it.
        second = self.add('900', 'm-b')
        self.assertEqual(second, first)
        self.assertEqual(self.store.watch_get(first)['member_id'], 'm-a')

    def test_an_unowned_subscription_belongs_to_nobody(self):
        # Inheriting legacy follows for the admin is what ticked every artist as
        # soon as one was followed, so an ownerless row is nobody's until claimed.
        import server
        owner = self.add('901', 'm-a')
        legacy = self.add('902')
        admin = types.SimpleNamespace(member={'id': 'admin', 'role': 'admin'})
        mine = {r['id']: r['mine']
                for r in server.Handler.subscriptions_with_ownership(admin, self.store)}
        self.assertFalse(mine[legacy])
        self.assertFalse(mine[owner])

    def test_asking_again_claims_an_unowned_subscription(self):
        legacy = self.add('906')
        self.assertEqual(self.store.watch_get(legacy)['member_id'], '')
        claimed = self.add('906', 'm-c')
        self.assertEqual(claimed, legacy)
        self.assertEqual(self.store.watch_get(legacy)['member_id'], 'm-c')
        # And a follow that already has an owner is not taken over.
        self.add('906', 'm-d')
        self.assertEqual(self.store.watch_get(legacy)['member_id'], 'm-c')

    def test_a_member_sees_only_their_own(self):
        import server
        owner = self.add('903', 'm-a')
        legacy = self.add('904')
        member = types.SimpleNamespace(member={'id': 'm-a', 'role': 'member'})
        mine = {r['id']: r['mine']
                for r in server.Handler.subscriptions_with_ownership(member, self.store)}
        self.assertTrue(mine[owner])
        self.assertFalse(mine[legacy])

    def test_unsubscribing_keeps_the_subscription(self):
        # The red minus is "take this artist out of MY list", not a delete: the
        # row survives, still enabled, and still on the feed for everyone else.
        mine = self.add('903', 'm-e')
        self.store.watch_mark(mine, enabled=True)
        self.assertTrue(self.store.watch_release(mine, 'm-e'))
        row = self.store.watch_get(mine)
        self.assertEqual(row['member_id'], '')
        self.assertEqual(row['enabled'], 1)
        # and it cannot clear somebody else's claim
        other = self.add('904', 'm-f')
        self.assertFalse(self.store.watch_release(other, 'm-e'))
        self.assertEqual(self.store.watch_get(other)['member_id'], 'm-f')

    def test_no_session_means_everything_is_mine(self):
        import server
        legacy = self.add('905')
        local = types.SimpleNamespace(member=None)
        mine = {r['id']: r['mine']
                for r in server.Handler.subscriptions_with_ownership(local, self.store)}
        self.assertTrue(mine[legacy])


if __name__ == '__main__':
    unittest.main()