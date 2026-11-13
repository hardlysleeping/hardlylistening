"""The one thing every hosted read and write carries: a member's session cookie.

Three modules need it — `sdk.social` for the discussion writes, `sdk.repo` for the hosted
Reads — and a fourth will. It lives here rather than in whichever module was written first, so
that a client adding a fifth surface does not import `sdk.social` to find a cookie type, and so
that the two clients that already exist do not drift into two spellings of the same header.

The value is the `__Host-hl_session` cookie `security.py` authenticates. It is the whole of the
credential: it is only ever sent in a header, it is kept out of this value's repr so a logged
object does not print a working session, and nothing in this package writes it to disk.

Stdlib only, like the rest of `sdk/`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: The session cookie `security.py` authenticates, and `server.py` expires on sign-out.
SESSION_COOKIE = '__Host-hl_session'


@dataclass(frozen=True)
class Session:
    """One signed-in member's browser session: the `__Host-hl_session` value and nothing else.

    The cookie is the whole of the credential, it is only ever sent in a header, and it is kept
    out of this value's repr so a logged object does not print a working session.
    """

    cookie: str = field(default='', repr=False)

    @classmethod
    def from_cookie(cls, value):
        """From the cookie's value, as sign-in answered it."""
        return cls(cookie=str(value or ''))

    @classmethod
    def from_cookie_header(cls, header):
        """From a `Cookie:` header a browser already holds; an absent name is an empty session."""
        for part in str(header or '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == SESSION_COOKIE:
                return cls(cookie=value)
        return cls()

    def header(self):
        """The one header this session contributes, or nothing when there is no cookie."""
        return {'Cookie': f'{SESSION_COOKIE}={self.cookie}'} if self.cookie else {}