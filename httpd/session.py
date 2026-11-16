"""Browser sign-in: cookies, redirects, Google OAuth and the signup flow.

The session cookie is the one credential this layer mints, so the rule that it is set with
`HttpOnly`, `SameSite` and the deployment's security settings lives in one method (`signed_in`)
rather than at every call site. `session_redirect` is what a browser gets after an OAuth
round-trip; `session_member_id` is the one place a page template asks who is signed in.

No token, code or cookie value is ever logged: `log_message` in `base.py` silences the paths
these methods answer.
"""

from __future__ import annotations

import secrets
from urllib.parse import parse_qs, urlparse

from security import Denied
from .runtime import boot, security


class BrowserSession:
    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def signed_in(self, sid: str, clear_signup: bool = False) -> None:
        """A session was minted: hand over the cookie, and end the signup flow if there was one."""
        self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Set-Cookie', f'{security().cookie_name(self, "__Host-hl_session")}={sid}; '
                         f'{security().cookie_attrs(self)}; Max-Age={security().SESSION_TTL}')
        if clear_signup:
            self.send_header('Set-Cookie', f'{security().cookie_name(self, "__Host-hl_signup")}=; '
                             f'{security().cookie_attrs(self)}; Max-Age=0')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def session_member_id(self, store) -> str:
        """The signed-in member, or '' — for deciding sign-in versus linking."""
        try:
            return security().authenticate(self, store)['id']
        except Denied:
            return ''

    def session_redirect(self, sid: str, location: str) -> None:
        """303 that also hands over the session, so the callback lands on a page."""
        self.send_response(303)
        self.send_header('Location', location)
        self.send_header('Set-Cookie',
                         f'{security().cookie_name(self, "__Host-hl_session")}={sid}; '
                         f'{security().cookie_attrs(self)}; Max-Age={security().SESSION_TTL}')
        self.send_header('Set-Cookie', f'{security().cookie_name(self, "__Host-hl_oauth")}=; '
                         f'{security().cookie_attrs(self)}; Max-Age=0')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def google_auth(self, path: str) -> None:
        """Start or finish Google sign-in.

        Signing in only ever resumes an identity somebody already linked; an
        unlinked Google account is refused, because this library is invite-only.
        Linking happens while a session is held, and `state` ties the callback to
        the browser that began it.
        """
        import oauth_google
        store, _ = boot()
        if self.command != 'GET':
            raise Denied(405, 'Method not allowed')
        if path == '/auth/google':
            if not oauth_google.available():
                self.redirect('/login?google=unavailable')
                return
            security().limit(security().client_ip(self), 'google', 30)
            url, cookie = oauth_google.start('link' if self.session_member_id(store) else 'signin')
            self.send_response(303)
            self.send_header('Location', url)
            self.send_header('Set-Cookie',
                             f'{security().cookie_name(self, "__Host-hl_oauth")}={cookie}; '
                             f'{security().cookie_attrs(self)}; Max-Age={oauth_google.COOKIE_TTL}')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        query = parse_qs(urlparse(self.path).query)
        saved = oauth_google.parse_cookie(security().cookie(self, security().cookie_name(self, oauth_google.COOKIE)))
        if not saved:
            self.redirect('/login?google=expired')
            return
        state = (query.get('state') or [''])[0]
        if not secrets.compare_digest(state, saved['state']):
            self.redirect('/login?google=state')
            return
        if (query.get('error') or [''])[0]:
            self.redirect('/login?google=denied')
            return
        try:
            claims = oauth_google.exchange((query.get('code') or [''])[0], saved['verifier'])
        except Denied:
            self.redirect('/login?google=error')
            return
        member_id = self.session_member_id(store)
        if saved['mode'] == 'link' or member_id:
            if not member_id:
                self.redirect('/login?google=signin-first')
                return
            try:
                oauth_google.link(store, member_id, claims)
            except Denied:
                self.redirect('/?google=conflict')
                return
            self.redirect('/?google=linked')
            return
        existing = oauth_google.identity_member(store, str(claims.get('sub') or ''))
        if not existing:
            self.redirect('/login?google=unknown')
            return
        try:
            security().member(store, {'id': existing})
        except Denied:
            self.redirect('/login?google=blocked')
            return
        self.session_redirect(security().create_session(store, existing, 'google'), '/')

    def signup_flow(self, digest: str) -> None:
        """A validated signup link becomes a short-lived cookie; the page erases the fragment."""
        self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Set-Cookie', f'{security().cookie_name(self, "__Host-hl_signup")}={digest}; '
                         f'{security().cookie_attrs(self)}; Max-Age={security().SIGNUP_FLOW_TTL}')
        self.send_header('Content-Length', '0')
        self.end_headers()
