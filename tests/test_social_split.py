"""SOCIAL-001: Social splits by domain, and every old import still resolves to the same object.

The card's rule is the same as REPO-014's: the split is internal. `social/service.py` stays the only
entry point, `social.discussion` stays importable for `social/__init__.py` and `social/api.py`, and
`activity.py` stays importable for the queue, Subsonic and the web routes. What moves is ownership:
the log is `social/activity.py`, the 24-hour slot is `social/boosts.py`, comments and votes are
`social/comments.py`, and a like is `social/likes.py`.

Two things a split can silently break, and both are checked here:

* **identity** — an old path that is a *copy* splits the module, so a patch through the old name
  misses the code that runs. `activity` must be `social.activity` itself, and the re-exports inside
  it must be the objects `social.boosts` defines;
* **delegation** — a facade that reimplements drifts. `social.service` must call the new modules,
  and `social.discussion` must not carry a second copy of a function it re-exports.
"""
import ast
import importlib
import unittest
from pathlib import Path
from unittest import mock

import activity
import social.activity
import social.boosts
import social.discussion
import social.likes
import social.service

# `social/__init__.py` re-exports the function `comments`, and that attribute shadows the submodule
# of the same name — the same hazard `social/service.py` works around. Ask the loader directly.
social_comments = importlib.import_module('social.comments')

ROOT = Path(__file__).resolve().parents[1]


def modules(path):
    """Every module a file imports, dotted (`from social.boosts import boost` → `social.boosts`)."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


class Files(unittest.TestCase):
    def test_every_named_file_exists_beside_the_facade(self):
        for name in ('activity.py', 'boosts.py', 'comments.py', 'likes.py', 'discussion.py'):
            self.assertTrue((ROOT / 'social' / name).is_file(), name)

    def test_the_old_top_level_path_is_the_new_module(self):
        self.assertIs(activity, social.activity)

    def test_the_boost_names_are_the_ones_boosts_defines(self):
        for name in ('BOOST_TTL', 'boost', 'current_boost', 'active_boosts'):
            self.assertIs(getattr(activity, name), getattr(social.boosts, name), name)
            self.assertEqual(getattr(social.activity, name), getattr(social.boosts, name), name)

    def test_every_name_the_old_module_promised_is_still_there(self):
        for name in ('BOOST_TTL', 'LISTEN_REPEAT', 'KIND_ADD', 'KIND_JOIN', 'KIND_NAME',
                     'active_boosts', 'boost', 'current_boost', 'day_key', 'feed', 'listen',
                     'log_play', 'record'):
            self.assertTrue(hasattr(activity, name), name)

    def test_the_discussion_facade_reexports_the_split_objects(self):
        for name in ('MAX_TEXT', 'PAGE_SIZE', 'album_id', 'annotate', 'batch', 'comment',
                     'comments', 'schema', 'summary', 'validate_target', 'vote'):
            self.assertIs(getattr(social.discussion, name),
                          getattr(social_comments, name), name)
        self.assertIs(social.discussion.like, social.likes.like)


class Ownership(unittest.TestCase):
    """Each file owns what its name says, and a facade defines nothing it re-exports."""

    def test_the_boost_slot_lives_in_boosts(self):
        for name in ('boost', 'current_boost', 'active_boosts'):
            self.assertEqual(getattr(social.boosts, name).__module__, 'social.boosts', name)
            self.assertEqual(getattr(activity, name).__module__, 'social.boosts', name)

    def test_a_like_lives_in_likes_and_comments_do_not_import_it(self):
        self.assertEqual(social.likes.like.__module__, 'social.likes')
        self.assertNotIn('social.likes', modules(ROOT / 'social' / 'comments.py'))

    def test_the_activity_module_no_longer_defines_the_boost_slot(self):
        tree = ast.parse((ROOT / 'social' / 'activity.py').read_text(encoding='utf-8'))
        defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertFalse(defined & {'boost', 'current_boost', 'active_boosts'})

    def test_no_new_social_module_reaches_outside_social(self):
        for name in ('activity.py', 'boosts.py', 'comments.py', 'likes.py'):
            roots = {module.split('.')[0] for module in modules(ROOT / 'social' / name)}
            for forbidden in ('library', 'subsonic', 'listening', 'server', 'party', 'web'):
                self.assertNotIn(forbidden, roots, f'social/{name} imports {forbidden}')


class Delegation(unittest.TestCase):
    """`social.service` calls the module that owns the query, with the arguments it was given."""

    def test_boosts_are_called_on_the_boost_module(self):
        with mock.patch.object(social.boosts, 'boost', return_value={}) as boost:
            social.service.boost('store', 'u1', 7, 5.0)
        boost.assert_called_once_with('store', 'u1', 7, 5.0)
        with mock.patch.object(social.boosts, 'current_boost', return_value=None) as current:
            social.service.current_boost('store', 'u1', 5.0)
        current.assert_called_once_with('store', 'u1', 5.0)
        with mock.patch.object(social.boosts, 'active_boosts', return_value=[]) as active:
            social.service.active_boosts('store', 10, 2.0)
        active.assert_called_once_with('store', 10, 2.0)

    def test_a_like_and_a_comment_are_called_on_their_own_modules(self):
        with mock.patch.object(social.likes, 'like', return_value={}) as like:
            social.service.like('store', 'track', '7', {'id': 'u1'}, 3)
        like.assert_called_once_with('store', 'track', '7', {'id': 'u1'}, 3)
        with mock.patch.object(social_comments, 'comment', return_value={}) as comment:
            social.service.comment('store', 'track', '7', {'id': 'u1'}, body='hi')
        comment.assert_called_once_with('store', 'track', '7', {'id': 'u1'}, body='hi',
                                        comment_id=None, delete=False)

    def test_the_entry_point_no_longer_imports_a_top_level_activity(self):
        self.assertNotIn('activity', modules(ROOT / 'social' / 'service.py'))


if __name__ == '__main__':
    unittest.main()