"""Social's facade: one entry point, same answers, and no reach into Library or Playback.

`ARCH-005`. Two kinds of test here, because "a facade" can fail in two different ways. Wiring tests
prove each function still calls the module it used to call, with the arguments it used to pass —
that is what catches a delegation that quietly normalises a value. Behaviour tests drive a real
store through the facade: a comment, a vote and a like have to land exactly as they did through the
HTTP adapter, which `test_social.py` covers from the other side.

The boundary test is the one that keeps the card honest: `docs/server-domains.md` forbids
`Social -> Library` and `Playback -> Social`, and an import check is how that stays true after the
next edit.
"""
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
from security import Security
from social import service


class Wiring(unittest.TestCase):
    """Every function delegates, and delegates with the arguments it was given."""

    def test_activity_and_profiles_are_passed_through(self):
        store = object()
        member = {'id': 'u1', 'role': 'member'}
        with patch.object(service._activity, 'record') as record:
            service.record(store, 'listen', member_id='u1', track_id=7, detail='x', commit=False)
            record.assert_called_once_with(store, 'listen', member_id='u1', track_id=7,
                                          playlist_id=0, detail='x', commit=False)
        with patch.object(service._activity, 'feed', return_value=[]) as feed:
            service.feed(store, 40, 100.0)
            feed.assert_called_once_with(store, 40, 100.0)
        with patch.object(service._activity, 'listen') as listen:
            service.listen(store, member, 7)
            listen.assert_called_once_with(store, member, 7)
        with patch.object(service._activity, 'current_boost', return_value=None) as current:
            service.current_boost(store, 'u1', 5.0)
            current.assert_called_once_with(store, 'u1', 5.0)
        with patch.object(service._activity, 'active_boosts', return_value=[]) as active:
            service.active_boosts(store, 10, 2.0)
            active.assert_called_once_with(store, 10, 2.0)
        with patch.object(service._profiles, 'profile', return_value=None) as profile:
            service.profile(store, 'u1', 20)
            profile.assert_called_once_with(store, 'u1', 20)
        with patch.object(service._profiles, 'recent', return_value=[]) as recent:
            service.recent(store, 5)
            recent.assert_called_once_with(store, 5)

    def test_discussion_is_passed_through(self):
        store = object()
        member = {'id': 'u1', 'role': 'member'}
        with patch.object(service._discussion, 'summary', return_value={}) as summary:
            service.summary(store, 'track', '7', member)
            summary.assert_called_once_with(store, 'track', '7', member)
        with patch.object(service._discussion, 'batch', return_value={}) as batch:
            service.batch(store, 'track', ['7'], member, latest=3)
            batch.assert_called_once_with(store, 'track', ['7'], member, latest=3)
        with patch.object(service._discussion, 'vote', return_value={}) as vote:
            service.vote(store, 'track', '7', member, 1)
            vote.assert_called_once_with(store, 'track', '7', member, 1)
        with patch.object(service._discussion, 'like', return_value={}) as like:
            service.like(store, 'track', '7', member, 3)
            like.assert_called_once_with(store, 'track', '7', member, 3)
        with patch.object(service._discussion, 'comment', return_value={}) as comment:
            service.comment(store, 'track', '7', member, comment_id=3, delete=True)
            comment.assert_called_once_with(store, 'track', '7', member, body=None,
                                            comment_id=3, delete=True)


class Discussion(unittest.TestCase):
    """The facade is usable, not just a re-export: real rows, real answers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = db.Store(Path(self.tmp.name) / 'social.db')
        self.addCleanup(self.store.close)
        Security().schema(self.store)          # accounts tables: comments name their author
        with self.store.lock:
            self.store.conn.executescript('''
                INSERT INTO tracks (provider, provider_track_id, title, artist_name, album, added)
                VALUES ('local', 'fixture', 'Song', 'Artist', 'Album', 0);
                INSERT INTO members (id, email, role) VALUES ('u1', 'u1@example.com', 'member');
                INSERT INTO members (id, email, role, display_name, profile_icon)
                VALUES ('u2', 'u2@example.com', 'admin', 'Second', 'star');
            ''')
            self.store.conn.commit()
        self.author = {'id': 'u1', 'role': 'member'}
        self.other = {'id': 'u2', 'role': 'admin'}

    def test_a_comment_a_vote_and_a_like_land_through_the_facade(self):
        page = service.comments(self.store, 'track', '1', self.author)
        self.assertEqual(page['items'], [])
        self.assertEqual(page['comments'], 0)

        result = service.comment(self.store, 'track', '1', self.author, body='hello')
        self.assertEqual(result['comments'], 1)
        page = service.comments(self.store, 'track', '1', self.author)
        self.assertEqual([c['body'] for c in page['items']], ['hello'])
        self.assertEqual(page['items'][0]['name'], 'Member')      # no display name set
        self.assertTrue(page['items'][0]['can_edit'])

        comment_id = page['items'][0]['id']
        service.like(self.store, 'track', '1', self.author, comment_id)
        page = service.comments(self.store, 'track', '1', self.author)
        self.assertEqual((page['items'][0]['likes'], page['items'][0]['liked']), (1, True))
        service.like(self.store, 'track', '1', self.author, comment_id)   # toggle off
        page = service.comments(self.store, 'track', '1', self.author)
        self.assertEqual((page['items'][0]['likes'], page['items'][0]['liked']), (0, False))

        summary = service.vote(self.store, 'track', '1', self.author, 1)
        self.assertEqual((summary['score'], summary['up'], summary['vote']), (1, 1, 1))
        summary = service.vote(self.store, 'track', '1', self.other, -1)
        self.assertEqual((summary['score'], summary['up'], summary['down']), (0, 1, 1))
        summary = service.vote(self.store, 'track', '1', self.author, 0)
        self.assertEqual((summary['score'], summary['vote']), (-1, 0))

        batch = service.batch(self.store, 'track', ['1', '999'], self.author)
        self.assertEqual(batch['1']['comments'], 1)
        self.assertEqual(batch['999'], dict(score=0, up=0, down=0, vote=0, comments=0))

    def test_moderation_rules_survive_the_move(self):
        service.comment(self.store, 'track', '1', self.author, body='mine')
        comment_id = service.comments(self.store, 'track', '1', self.author)['items'][0]['id']
        stranger = {'id': 'u3', 'role': 'member'}
        with self.assertRaises(Exception):
            service.comment(self.store, 'track', '1', stranger, body='stolen',
                            comment_id=comment_id)
        with self.assertRaises(Exception):
            service.comment(self.store, 'track', '1', stranger, comment_id=comment_id,
                            delete=True)
        # An administrator may moderate anybody's comment.
        service.comment(self.store, 'track', '1', self.other, comment_id=comment_id, delete=True)
        self.assertEqual(service.summary(self.store, 'track', '1', self.author)['comments'], 0)

    def test_an_unknown_target_is_refused_before_anything_is_written(self):
        with self.assertRaises(Exception):
            service.comment(self.store, 'track', '999', self.author, body='nowhere')
        with self.assertRaises(Exception):
            service.vote(self.store, 'track', '999', self.author, 1)


class Boundary(unittest.TestCase):
    def test_social_does_not_import_library_playback_or_the_http_adapter(self):
        path = Path(__file__).resolve().parents[1] / 'social' / 'service.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split('.')[0])
        for forbidden in ('library', 'subsonic', 'listening', 'server', 'security', 'party'):
            self.assertNotIn(forbidden, roots, f'social/service.py imports {forbidden}')


if __name__ == '__main__':
    unittest.main()