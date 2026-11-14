"""Re-export of `core.config` (ARCH-009): the old module name stays the import every module uses.

The implementation, its environment variables and the reasoning behind them are in
`core/config.py`. The alias is the module object itself, not a copy.
"""
import sys

from core import config as _impl

sys.modules[__name__] = _impl