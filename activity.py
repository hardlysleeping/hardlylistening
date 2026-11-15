"""Alias of `social.activity` (SOCIAL-001): the Activity log moved into the Social package.

The old path stays because the queue, Subsonic, the web routes and the tests import it; it is the
same module object (`sys.modules`), not a copy, so patching one name reaches the other. Boosts are
re-exported by `social.activity` from `social/boosts.py`, so `activity.boost` is still here.
"""
import sys

from social import activity as _impl

sys.modules[__name__] = _impl