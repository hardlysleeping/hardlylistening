"""Social: what the group said about Repo objects.

`ARCH-005`. The package keeps the existing names at the top level, so `import social` and
`from social import annotate` keep working unchanged while the code gains an owner and an entry
point: `social.service`. `discussion.py` holds the domain functions, `dispatch` stays the thin HTTP
adapter, and Activity/profiles remain their own modules until a later card moves them in.
"""
from social.api import dispatch            # noqa: F401  (re-exported on purpose)
from social.discussion import (            # noqa: F401  (re-exported on purpose)
    MAX_TEXT, PAGE_SIZE, album_id, annotate, batch, comment, comments, like,
    schema, summary, validate_target, vote,
)

__all__ = ['MAX_TEXT', 'PAGE_SIZE', 'album_id', 'annotate', 'batch', 'comment', 'comments',
           'dispatch', 'like', 'schema', 'summary', 'validate_target', 'vote']