"""Social: what the group said about Repo objects.

`ARCH-005`. The package keeps the existing names at the top level, so `import social` and
`from social import annotate` keep working unchanged while the code gains an owner and an entry
point: `social.service`. The domain functions are split by ownership (`SOCIAL-001`): `activity.py`
is the log, `boosts.py` the 24-hour slot, `comments.py` comments and votes, and `likes.py` a like
on a comment; `discussion.py` is a re-export facade for the names below. `dispatch` stays the thin
HTTP adapter, and `profiles.py` stays its own module.

Careful with the name `comments`: this module re-exports the *function* under that name, and the
attribute shadows the `social.comments` submodule, so callers that need the module must ask the
loader (`importlib.import_module('social.comments')`) rather than `import social.comments as ...`.
"""
from social.api import dispatch            # noqa: F401  (re-exported on purpose)
from social.discussion import (            # noqa: F401  (re-exported on purpose)
    MAX_TEXT, PAGE_SIZE, album_id, annotate, batch, comment, comments, like,
    schema, summary, validate_target, vote,
)

__all__ = ['MAX_TEXT', 'PAGE_SIZE', 'album_id', 'annotate', 'batch', 'comment', 'comments',
           'dispatch', 'like', 'schema', 'summary', 'validate_target', 'vote']