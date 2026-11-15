"""Facade over a Repo object's discussion (SOCIAL-001).

The domain functions moved to named files: `social/comments.py` owns comments, votes and the
counts a grid needs, and `social/likes.py` owns a like on a comment. This module keeps every old
import working — `social/__init__.py`, `social/api.py` and `social/service.py` still name it — and
re-exports rather than reimplementing, so there is one implementation and one object identity.

No identity or target supplied by a client is trusted; that rule lives with the code that enforces
it (`validate_target`, `comment`, `vote`, `like`). No request object appears here: the HTTP adapter
is `social/api.py` (`ARCH-006`).
"""
from social.comments import (  # noqa: F401  (re-exported on purpose)
    MAX_TEXT, PAGE_SIZE, album_id, annotate, batch, comment, comments, schema, summary,
    validate_target, vote,
)
from social.likes import like  # noqa: F401  (re-exported on purpose)

__all__ = ['MAX_TEXT', 'PAGE_SIZE', 'album_id', 'annotate', 'batch', 'comment', 'comments', 'like',
           'schema', 'summary', 'validate_target', 'vote']