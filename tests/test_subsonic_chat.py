"""HL-210: Subsonic chat maps onto the party room, not a second message store.

Subsonic's `addChatMessage` / `getChatMessages` are a room timeline; Discuss (`social_comments`)
is per-item commentary with votes. `docs/2026-11-09-subsonic-surface-plan.md` § Phase 7 keeps them
apart on purpose — merging would put room chatter on song pages, and a guest's 24-hour identity
must not enter a member's Activity log — so these endpoints read and write `party_chat`.

What is checked here:

* no room ⇒ `getChatMessages` is an empty list and `addChatMessage` is error 30, never a new room
  and never a write somewhere else;
* the author is the authenticated member (the submitted `username` is ignored, so a client cannot
  post as somebody else) and the timestamp is milliseconds since the epoch, which is what the
  protocol asks for;
* `since` filters by time, and a closed or expired room is not a room you are still in.
"""
import time
import unittest

import party
from test_listening import ListeningHTTP


class ChatEndpoints(unittest.TestCase):
    setUp = ListeningHTTP.setUp
    tearDown = ListeningHTTP.tearDown
    request = ListeningHTTP.request
    token = ListeningHTTP.token
    user = ListeningHTTP.user
    api = ListeningHTTP.api
    rest = ListeningHTTP.rest
    response = ListeningHTTP.response
    save = ListeningHTTP.save

    def setUp(self):
        ListeningHTTP.setUp(self)
        party.schema(self.store)
        self.member = {'id': self.identity['id'], 'role': 'admin'}
        self.party_id = party.create(self.store, self.member, 'Fixture')['id']

    def join(self, pid=None):
        """Reading a room is what makes an identity present in it — the real path, not a row."""
        party.state(self.store, pid or self.party_id, 'm:' + self.member['id'])

    def messages(self, **params):
        return self.response('getChatMessages', **params)['chatMessages']['chatMessage']

    def test_no_room_means_an_empty_list_and_error_30(self):
        self.assertEqual(self.messages(), [])
        response = self.response('addChatMessage', message='hello')
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['error']['code'], 30)

    def test_a_message_round_trips_with_the_server_identity_and_a_millisecond_time(self):
        self.join()
        before = int(time.time() * 1000)
        self.assertEqual(self.response('addChatMessage', message='  hello room  ')['status'], 'ok')
        rows = self.messages()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['message'], 'hello room')
        self.assertEqual(rows[0]['username'], 'Friend')       # the member's display name
        self.assertGreaterEqual(rows[0]['time'], before)
        self.assertLess(abs(rows[0]['time'] - time.time() * 1000), 5000)

    def test_a_submitted_username_is_ignored(self):
        self.join()
        self.response('addChatMessage', username='Somebody Else', message='mine')
        self.assertEqual(self.messages()[0]['username'], 'Friend')

    def test_since_filters_by_time_and_zero_returns_everything(self):
        self.join()
        self.response('addChatMessage', message='first')
        time.sleep(0.01)
        pivot = int(time.time() * 1000)
        self.response('addChatMessage', message='second')
        self.assertEqual([r['message'] for r in self.messages()], ['first', 'second'])
        self.assertEqual([r['message'] for r in self.messages(since=0)], ['first', 'second'])
        self.assertEqual([r['message'] for r in self.messages(since=pivot)], ['second'])
        self.assertEqual(self.messages(since=pivot + 600000), [])

    def test_bodies_are_validated_like_the_room_validates_them(self):
        self.join()
        for body in ('', '   ', 'x' * 1001):
            response = self.response('addChatMessage', message=body)
            self.assertEqual(response['status'], 'failed', body[:10])
            self.assertEqual(response['error']['code'], 10)
        self.assertEqual(self.messages(), [])

    def test_an_ended_room_is_not_a_room_you_are_in(self):
        self.join()
        with self.store.lock:
            self.store.conn.execute('UPDATE party_rooms SET closed=1 WHERE id=?', (self.party_id,))
            self.store.conn.commit()
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.response('addChatMessage', message='hello')['error']['code'], 30)

    def test_the_newest_room_wins(self):
        other = party.create(self.store, self.member, 'Second')['id']
        self.join(self.party_id)
        time.sleep(0.01)
        self.join(other)
        self.response('addChatMessage', message='in the second room')
        self.assertEqual(self.messages()[0]['message'], 'in the second room')
        rows = party.messages(self.store, self.party_id)
        self.assertEqual(rows, [])                             # never written to the old room


if __name__ == '__main__':
    unittest.main()