"""The HTTP layer: routes in, JSON or a stream out.

Everything here is an adapter. It knows how a request arrives (`BaseHTTPRequestHandler`),
how a refusal becomes a status, and which session a request carries; it does not know how
music is stored, ranked, fetched or repaired — those are modules, and the adapters call
them. `server.py` stays the composition root: it boots, owns the process state, and serves.

The package is `httpd`, not `http`, because a top-level `http/` package shadows the
standard library's `http.server` — which this server and its tests import.
"""

from .handler import Handler

__all__ = ["Handler"]
