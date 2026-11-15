"""The repository: what this server has found, before anyone decided to keep it.

One package for Repo (ARCH-002). `service.py` is the boundary the rest of the server talks to;
the implementation sits beside it in `repository.py`, `catalogue.py`, `subscriptions.py`,
`acquisition.py` and `api.py` (REPO-014). `discovery/` keeps the old import paths as aliases of
those modules.
"""