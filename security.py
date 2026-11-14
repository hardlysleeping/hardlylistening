"""Re-export of `core.security` (ARCH-009): the old module name stays the import every module uses.

Accounts, sessions, invites and rate limits are in `core/security.py`. The alias is the module
object itself, not a copy.
"""
import sys

from core import security as _impl

sys.modules[__name__] = _impl