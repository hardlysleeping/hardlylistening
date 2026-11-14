"""Alias of `repo.catalogue` (REPO-014): releases and tracklists moved behind Repo.

The old path stays because the facade imports it; it is the same module object
(`sys.modules`), not a copy, so patching one name reaches the other.
"""
import sys

from repo import catalogue as _impl

sys.modules[__name__] = _impl
