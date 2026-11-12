"""Google sign-in, and linking a Google account to an existing member.

Two rules shape the whole flow:

* **Linking is not merging.** A Google identity is attached only while somebody
  is already signed in. Equal email addresses are never a reason to join two
  members — the owner of an account decides what it is connected to.
* **Signing in does not create anybody.** This library is invite-only, so a
  Google account nobody has linked yet is refused, with a message that says so.
  Joining still needs a signup link.

The ID token is verified locally: signature against Google's published keys,
issuer, audience, and expiry. An unverified token is just a JSON blob a stranger
wrote, so none of its claims are used before that check passes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from security import Denied, SECURITY

AUTHORIZE = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
CERTS = 'https://www.googleapis.com/oauth2/v3/certs'
ISSUERS = ('accounts.google.com', 'https://accounts.google.com')
SCOPE = 'openid email profile'
COOKIE = '__Host-hl_oauth'
COOKIE_TTL = 600                 # ten minutes is plenty to consent and come back
JWT_LEEWAY = 120

_keys = {'fetched': 0.0, 'by_kid': {}}
_lock_kid = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def settings() -> tuple[str, str, str]:
    """(client id, client secret, redirect uri) or a denial that says what is missing."""
    client_id = (os.getenv('GOOGLE_CLIENT_ID') or '').strip()
    client_secret = (os.getenv('GOOGLE_CLIENT_SECRET') or '').strip()
    if not client_id or not client_secret:
        raise Denied(503, 'Google sign-in is not configured')
    origin = SECURITY.config()[2]
    return client_id, client_secret, origin + '/auth/google/callback'


def available() -> bool:
    """Can the UI offer the button? Never raises: a login page must still render."""
    try:
        settings()
        return True
    except Denied:
        return False


# -- the round trip -------------------------------------------------------

def start(mode: str) -> tuple[str, str]:
    """Return (url to send the browser to, value for the state cookie)."""
    client_id, _secret, redirect_uri = settings()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    query = urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'prompt': 'select_account',
        'access_type': 'online',
    })
    cookie = '|'.join((state, verifier, mode if mode in ('link', 'signin') else 'signin',
                       str(int(time.time() + COOKIE_TTL))))
    return AUTHORIZE + '?' + query, cookie


def parse_cookie(value: str) -> dict | None:
    parts = (value or '').split('|')
    if len(parts) != 4:
        return None
    state, verifier, mode, expires = parts
    try:
        deadline = int(expires)
    except ValueError:
        return None
    if deadline < time.time() or not state or not verifier:
        return None
    return {'state': state, 'verifier': verifier, 'mode': mode, 'expires': deadline}


def _post_form(url: str, form: dict) -> dict:
    request = urllib.request.Request(
        url, data=urlencode(form).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode() or '{}')
    except urllib.error.HTTPError as error:
        detail = ''
        try:
            detail = json.loads(error.read().decode() or '{}').get('error_description', '')
        except Exception:                                  # noqa: BLE001 - best effort
            pass
        raise Denied(502, detail or f'Google refused the sign-in ({error.code})') from None
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise Denied(502, f'Could not reach Google: {error}') from None


def exchange(code: str, verifier: str) -> dict:
    """Trade the one-time code for an ID token, and return its verified claims."""
    client_id, client_secret, redirect_uri = settings()
    if not code:
        raise Denied(400, 'Google returned no code')
    payload = _post_form(TOKEN, {
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
        'code_verifier': verifier,
    })
    token = payload.get('id_token') or ''
    if not token:
        raise Denied(502, 'Google did not return an identity token')
    return verify(token, client_id)


# -- ID token -------------------------------------------------------------

def _keys_by_kid(force: bool = False) -> dict:
    now = time.time()
    if not force and _keys['by_kid'] and now - _keys['fetched'] < 3600:
        return _keys['by_kid']
    request = urllib.request.Request(CERTS, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            document = json.loads(response.read().decode() or '{}')
    except (urllib.error.URLError, TimeoutError, ValueError):
        if _keys['by_kid']:                    # a stale key still beats no key
            return _keys['by_kid']
        raise Denied(502, 'Could not fetch Google’s signing keys') from None
    keys = {}
    for key in document.get('keys', []):
        if key.get('kty') == 'RSA' and key.get('kid') and key.get('n') and key.get('e'):
            keys[key['kid']] = key
    if not keys:
        raise Denied(502, 'Google published no signing keys')
    _keys.update({'fetched': now, 'by_kid': keys})
    return keys


def _public_key(jwk: dict):
    from cryptography.hazmat.primitives.asymmetric import rsa
    numbers = rsa.RSAPublicNumbers(
        int.from_bytes(_unb64(jwk['e']), 'big'),
        int.from_bytes(_unb64(jwk['n']), 'big'))
    return numbers.public_key()


def verify(token: str, client_id: str) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    parts = token.split('.')
    if len(parts) != 3:
        raise Denied(401, 'Malformed identity token')
    header = json.loads(_unb64(parts[0]) or b'{}')
    claims = json.loads(_unb64(parts[1]) or b'{}')
    if header.get('alg') != 'RS256':
        raise Denied(401, 'Unexpected token algorithm')
    kid = header.get('kid') or ''
    keys = _keys_by_kid()
    if kid not in keys:
        keys = _keys_by_kid(force=True)        # Google rotates its keys
    if kid not in keys:
        raise Denied(401, 'Unknown signing key')
    signed = (parts[0] + '.' + parts[1]).encode()
    try:
        _public_key(keys[kid]).verify(_unb64(parts[2]), signed,
                                      padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise Denied(401, 'Identity token signature is not valid') from None

    now = time.time()
    if claims.get('iss') not in ISSUERS:
        raise Denied(401, 'Identity token came from somewhere else')
    if claims.get('aud') != client_id:
        raise Denied(401, 'Identity token was issued for another application')
    try:
        expires = float(claims.get('exp') or 0)
        issued = float(claims.get('iat') or 0)
    except (TypeError, ValueError):
        raise Denied(401, 'Identity token has no usable lifetime') from None
    if expires + JWT_LEEWAY < now:
        raise Denied(401, 'Identity token has expired')
    if issued - JWT_LEEWAY > now:
        raise Denied(401, 'Identity token is not valid yet')
    if not claims.get('sub'):
        raise Denied(401, 'Identity token names no account')
    return claims


# -- identities -----------------------------------------------------------

def identity_member(store, subject: str) -> str | None:
    with store.lock:
        row = store.conn.execute(
            'SELECT member_id FROM member_identities WHERE provider=? AND provider_subject=?',
            ('google', subject)).fetchone()
    return row[0] if row else None


def link(store, member_id: str, claims: dict) -> str:
    """Attach this Google identity to `member_id`. Returns 'linked' or 'already'."""
    subject = str(claims.get('sub') or '')
    if not subject:
        raise Denied(401, 'Identity token names no account')
    email = str(claims.get('email') or '').strip().lower()[:254]
    SECURITY.schema(store)
    with store.lock:
        row = store.conn.execute(
            'SELECT member_id FROM member_identities WHERE provider=? AND provider_subject=?',
            ('google', subject)).fetchone()
        if row and row[0] != member_id:
            raise Denied(409, 'That Google account is already linked to another member')
        if row:
            store.conn.execute(
                'UPDATE member_identities SET email=? WHERE provider=? AND provider_subject=?',
                (email, 'google', subject))
            store.conn.commit()
            return 'already'
        store.conn.execute(
            'INSERT INTO member_identities(member_id,provider,provider_subject,email,created_at)'
            ' VALUES(?,?,?,?,?)', (member_id, 'google', subject, email, time.time()))
        store.conn.commit()
    return 'linked'


def unlink(store, member_id: str) -> bool:
    """Detach Google. The login link stays valid, so nobody is locked out by this."""
    with store.lock:
        changed = store.conn.execute(
            'DELETE FROM member_identities WHERE member_id=? AND provider=?',
            (member_id, 'google')).rowcount
        store.conn.commit()
    return bool(changed)