"""Core: who may call, what this process is configured to do, and the one database.

Core knows about members, sessions, runtime settings and the SQLite connection. It must know
nothing about discovery, downloads, Activity or the Subsonic views — the dependency rules are in
`PLAN.md` § Platform separation, and every module's owner is in `docs/server-domains.md`.

The top-level `config.py`, `db.py` and `security.py` are aliases, not copies (ARCH-009): each one
puts *this* module object into `sys.modules` under the old name, so there is one module identity.
A copy would split attributes, and a test patching `db.DB_PATH` would then patch an object the
server never reads — which is exactly how a suite once wrote to the live library.
"""