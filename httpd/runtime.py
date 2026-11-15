"""The composition root, asked at call time.

`server.py` owns the process: the store, the queue, the feature switches, and `boot()`. The
adapters in this package must never hold an import-time copy of any of those, for two reasons:

* `server.py` usually runs as `__main__` (`python3 server.py`), so a plain `import server` would
  load a *second* copy of the module with its own empty globals.
* The tests replace `server.boot` and mutate `server.FEATURES` and then make requests. A binding
  taken at import time would keep the old function and the old values, and the substitution
  would silently do nothing.

So `server.py` hands this module its own module object once (`bind`), and every accessor below
reads an attribute at call time. One binding, deferred lookups.
"""
from __future__ import annotations

from types import ModuleType

_ROOT: ModuleType | None = None


def bind(root: ModuleType) -> None:
    """Called once by the composition root, with its own module object."""
    global _ROOT
    _ROOT = root


def root() -> ModuleType:
    if _ROOT is None:
        raise RuntimeError("httpd.runtime was used before server.py bound it")
    return _ROOT


def boot():
    """The store and queue, or the one already running."""
    return root().boot()


def security():
    """The process's `SECURITY` rules. Tests replace the whole object on the root module."""
    return root().SECURITY


def features() -> dict:
    """The switches decided at boot (`server.FEATURES`, the same dict object)."""
    return root().FEATURES


def queue():
    """The live queue, or None before boot. Read per request: the page polls it."""
    return root().QUEUE
