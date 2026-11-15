"""Re-export of `core.db` (ARCH-009): the old module name stays the import every module uses.

The alias is the module object itself. `import db` hands back `core.db`, so `db.DB_PATH`,
`db.Store` and every patch target are one identity across the app. Do not replace this with
`from core.db import *`: a star-import copy splits the attributes, and a suite patching the copy
writes to the real database instead (see `tests/test_core_aliases.py`).
"""
import sys

from core import db as _impl

sys.modules[__name__] = _impl