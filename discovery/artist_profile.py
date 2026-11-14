"""Alias of `repo.repository` (REPO-014): the artist page moved behind the Repo package.

The old path stays because tests and the facade import it; it is the same module object
(`sys.modules`), not a copy, so patching one name reaches the other.
"""
import sys

from repo import repository as _impl

sys.modules[__name__] = _impl
