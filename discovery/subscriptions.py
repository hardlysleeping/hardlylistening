"""Alias of `repo.subscriptions` (REPO-014): following an artist moved behind Repo.

The old path stays because tests patch it by name; it is the same module object
(`sys.modules`), not a copy, so `patch('discovery.subscriptions.check')` reaches the code the
Repo facade calls.
"""
import sys

from repo import subscriptions as _impl

sys.modules[__name__] = _impl
