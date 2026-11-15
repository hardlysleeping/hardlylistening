"""Versioned HTTP surfaces. `v1` is the contract a client may rely on.

The unversioned `/api/*` routes stay where they are; this package is the stable surface in
front of the domain services, not a replacement for them.
"""