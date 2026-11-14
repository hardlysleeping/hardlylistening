"""Core -> Social is an event, not an import.

`ARCH-007` left one ownership violation: `security.py` called `activity.record` on join and
rename, so the accounts code knew about the Feed, and a Social failure could fail a sign-in.
Core now publishes `events.MEMBER_JOINED` / `events.MEMBER_RENAMED`; the composition root
subscribes Social's Activity to them at boot.

These tests pin both halves: the publisher never breaks and never requires a listener, and the
subscriber still produces exactly the Feed rows the direct call used to produce.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
import events
from security import Security
from social import service


class Publisher(unittest.TestCase):
    """Core's side: no listeners needed, no exceptions escape, no duplicate handlers."""

    def setUp(self):
        events.clear()
        self.addCleanup(events.clear)
        self.store = object()

    def test_publishing_with_nobody_listening_is_a_no_op(self):
        events.publish(self.store, events.MEMBER_JOINED, member_id='u1', detail='Ann')
        self.assertEqual(events.subscribers(events.MEMBER_JOINED), [])

    def test_a_broken_subscriber_cannot_break_the_write_path(self):
        def boom(store, fields):
            raise RuntimeError('social is down')

        heard = []
        events.subscribe(events.MEMBER_JOINED, boom)
        events.subscribe(events.MEMBER_JOINED, lambda store, fields: heard.append(fields['detail']))
        events.publish(self.store, events.MEMBER_JOINED, member_id='u1', detail='Ann')
        self.assertEqual(heard, ['Ann'], 'the other subscriber still hears it')

    def test_subscribing_the_same_handler_twice_is_one_handler(self):
        heard = []
        handler = lambda store, fields: heard.append(fields)   # noqa: E731
        events.subscribe(events.MEMBER_RENAMED, handler)
        events.subscribe(events.MEMBER_RENAMED, handler)
        events.publish(self.store, events.MEMBER_RENAMED, member_id='u1', detail='Ann')
        self.assertEqual(len(heard), 1)

    def test_the_accounts_code_no_longer_imports_the_feed(self):
        import security
        source = Path(security.__file__).read_text()
        self.assertNotIn('import activity', source,
                         'security.py must publish an event, not write the Feed')
        self.assertNotIn('activity.', source, 'the Feed is not Core\'s to write')


class CompositionRoot(unittest.TestCase):
    """The one place that is allowed to know both sides has to actually wire them."""

    def test_boot_subscribes_activity(self):
        source = (Path(__file__).resolve().parent.parent / 'server.py').read_text()
        self.assertIn('social_service.subscribe_events()', source,
                      'boot() must attach Activity to the Core events')
        self.assertIn('from social import service as social_service', source)


class Subscriber(unittest.TestCase):
    """Social's side: the wired Feed rows are the ones the direct call used to make."""

    def setUp(self):
        events.clear()
        self.addCleanup(events.clear)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = db.Store(Path(self.tmp.name) / 'events.db')
        self.addCleanup(self.store.close)
        Security().schema(self.store)
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO members (id, email, role, display_name) VALUES ('u1','u1@example.com','member','Old')")
            self.store.conn.commit()

    def rows(self):
        with self.store.lock:
            return [dict(r) for r in self.store.conn.execute(
                'SELECT kind, member_id, detail FROM activity ORDER BY id')]

    def test_wiring_twice_still_records_each_event_once(self):
        service.subscribe_events()
        service.subscribe_events()          # boot re-running is not two Feed rows
        events.publish(self.store, events.MEMBER_JOINED, member_id='u1', detail='Ann')
        self.assertEqual(self.rows(), [{'kind': 'join', 'member_id': 'u1', 'detail': 'Ann'}])

    def test_a_rename_and_a_join_keep_their_own_kinds(self):
        service.subscribe_events()
        events.publish(self.store, events.MEMBER_JOINED, member_id='u1', detail='Ann')
        events.publish(self.store, events.MEMBER_RENAMED, member_id='u1', detail='Ann B')
        self.assertEqual([r['kind'] for r in self.rows()], ['join', 'name'])
        self.assertEqual([r['detail'] for r in self.rows()], ['Ann', 'Ann B'])

    def test_the_account_path_publishes_instead_of_writing(self):
        """`Security.profile()` emits one event with the new name, and writes no Feed row."""
        service.subscribe_events()
        actor = {'id': 'u1', 'role': 'member'}
        with patch.object(events, 'publish') as publish:
            Security().profile(self.store, actor, {'display_name': 'New', 'profile_icon': 'star'})
            publish.assert_called_once_with(self.store, events.MEMBER_RENAMED,
                                            member_id='u1', detail='New')
        self.assertEqual(self.rows(), [])

    def test_the_rename_reaches_the_feed_once_wired(self):
        service.subscribe_events()
        actor = {'id': 'u1', 'role': 'member'}
        Security().profile(self.store, actor, {'display_name': 'New', 'profile_icon': 'star'})
        self.assertEqual(self.rows(), [{'kind': 'name', 'member_id': 'u1', 'detail': 'New'}])

    def test_an_unchanged_name_is_not_an_event(self):
        service.subscribe_events()
        actor = {'id': 'u1', 'role': 'member'}
        Security().profile(self.store, actor, {'display_name': 'Old', 'profile_icon': 'star'})
        self.assertEqual(self.rows(), [])


if __name__ == '__main__':
    unittest.main()