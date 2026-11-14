"""Accounts and browser sessions: invite-only signup, passwords, and the permanent login link.

A member is the account. There are several ways to prove you are that member — a personal login
link, an email and password, and (later) Google or Apple — and every one of them mints the same
`__Host-hl_session`. Nothing downstream knows how someone signed in.

The two tokens are deliberately different things:

* `/auth/link#invite=...` — the **login link**. It belongs to a member who already exists, it is
  reusable, and it never expires until it is regenerated or revoked. It is also the recovery path.
* `/signup#invite=...` — a **signup link**. It creates the member, it is single use, and it dies
  after a week. Only the digest is stored, so the link can be shown once and never again.
"""
import os
import sqlite3
import time
import hashlib
import hmac
import secrets
import threading
import ipaddress
from collections import OrderedDict
from http.cookies import SimpleCookie
from urllib.parse import urlparse

class Denied(Exception):
    def __init__(self, code, message, retry_after=None):
        self.code, self.message, self.retry_after = code, message, retry_after

class Security:
    # The link IS the account: a member's profile link never expires, and the
    # session it creates is renewed on every authenticated request, so nobody is
    # logged out for age. Party guest links keep their own 24-hour TTL in
    # party.py — that is the only temporary credential in the app.
    SESSION_TTL = 10 * 365 * 86400
    # Sentinel stored in the `expires` column for "never expires".
    FOREVER = 0.0
    # A signup link is temporary by construction: a week is long enough to send it to someone and
    # short enough that a leaked link is not a standing door.
    SIGNUP_TTL = 7 * 86400
    # The short-lived proof that this browser's signup link was already validated. The fragment is
    # erased on the page, and the raw token is never carried again.
    SIGNUP_FLOW_TTL = 900
    PASSWORD_MIN = 10
    # scrypt parameters (N, r, p): the interactive-login profile, ~100 ms per hash here.
    SCRYPT = {'n': 2 ** 14, 'r': 8, 'p': 1, 'dklen': 32}
    AUTH_METHODS = ('login_link', 'password', 'google', 'apple')
    PROVIDERS = ('google', 'apple')
    def __init__(self):
        self.lock = threading.RLock()
        self.rates = OrderedDict()

    # Behind the Cloudflare tunnel every connection arrives from loopback, so the socket
    # peer is 127.0.0.1 for the entire internet. Keying rate limits on that gave every
    # stranger one shared bucket: 20 logins a minute total, and anybody could spend them
    # and lock the real people out. Cloudflare writes CF-Connecting-IP at its edge and a
    # client cannot override it there, so it is used — but only while the peer really is
    # loopback, which is also the only way a request can reach this process now that it
    # binds 127.0.0.1. A non-loopback peer keeps its own address and its own bucket.
    LOOPBACK = ('127.0.0.1', '::1', '::ffff:127.0.0.1')

    @classmethod
    def client_ip(cls, handler):
        peer = ''
        address = getattr(handler, 'client_address', None)
        if address:
            peer = (address[0] or '')
        if peer not in cls.LOOPBACK:
            return peer or 'unknown'
        forwarded = (handler.headers.get('CF-Connecting-IP', '') or '').strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:      # absent, duplicated, or nonsense: fall back to the peer
            return peer or 'unknown'

    @property
    def required(self):
        return os.getenv('HARDLYLISTENING_AUTH_REQUIRED', '1').lower() not in ('0', 'false', 'no')

    def config(self):
        origin = os.getenv('HARDLYLISTENING_PUBLIC_ORIGIN', '')
        p = urlparse(origin)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.path or p.query or p.fragment:
            raise Denied(503, 'Authentication is not configured')
        return '', '', origin

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def schema(self, store):
        with store.lock:
            store.conn.executescript('''CREATE TABLE IF NOT EXISTS members (
                id TEXT PRIMARY KEY, email TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'pending');
                CREATE TABLE IF NOT EXISTS member_playlists (playlist_id INTEGER PRIMARY KEY, member_id TEXT NOT NULL);
                -- Who added what: a playlist someone pasted, or a track someone
                -- uploaded. Absent row = nobody claims it, so only an admin may
                -- delete it.
                CREATE TABLE IF NOT EXISTS member_items (kind TEXT NOT NULL, target_id INTEGER NOT NULL, member_id TEXT NOT NULL, PRIMARY KEY (kind, target_id));
                CREATE TABLE IF NOT EXISTS member_requests (id INTEGER PRIMARY KEY, member_id TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS browser_invites (digest TEXT PRIMARY KEY, member_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS browser_sessions (digest TEXT PRIMARY KEY, member_id TEXT NOT NULL, expires REAL NOT NULL);
                -- Signup links: the member is created when one is redeemed, so this table belongs
                -- to nobody until it is consumed. Only the digest is stored.
                CREATE TABLE IF NOT EXISTS signup_invites (
                    digest TEXT PRIMARY KEY, issued_by TEXT NOT NULL, created_at REAL NOT NULL,
                    expires_at REAL NOT NULL, consumed_at REAL, consumed_by TEXT);
                -- One row per member who can sign in with a password. `login_email` is the login
                -- identifier; `members.email` stays the contact address and may differ.
                CREATE TABLE IF NOT EXISTS member_credentials (
                    member_id TEXT PRIMARY KEY, login_email TEXT UNIQUE NOT NULL,
                    password_hash TEXT, password_salt TEXT, password_changed_at REAL);
                -- Google and Apple identities. Equal emails are never a reason to merge members:
                -- linking happens only while somebody is already signed in.
                CREATE TABLE IF NOT EXISTS member_identities (
                    member_id TEXT NOT NULL, provider TEXT NOT NULL, provider_subject TEXT NOT NULL,
                    email TEXT, created_at REAL NOT NULL, PRIMARY KEY(provider, provider_subject));''')
            cols = {r[1] for r in store.conn.execute('PRAGMA table_info(members)')}
            if 'display_name' not in cols:
                store.conn.execute("ALTER TABLE members ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
            if 'profile_icon' not in cols:
                store.conn.execute("ALTER TABLE members ADD COLUMN profile_icon TEXT NOT NULL DEFAULT 'music'")
            # An existing session was necessarily minted by a login link, which is the honest
            # default for rows written before the column existed.
            session_cols = {r[1] for r in store.conn.execute('PRAGMA table_info(browser_sessions)')}
            if 'auth_method' not in session_cols:
                store.conn.execute("ALTER TABLE browser_sessions ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'login_link'")
            store.conn.commit()

    @staticmethod
    def claim(store, kind, target_id, member_id):
        """Record that `member_id` added this thing, if we know who did."""
        if not member_id:
            return
        try:
            with store.lock:
                store.conn.execute(
                    'INSERT OR IGNORE INTO member_items(kind,target_id,member_id) VALUES(?,?,?)',
                    (kind, int(target_id), member_id))
                store.conn.commit()
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def owner_of(store, kind, target_id):
        try:
            with store.lock:
                row = store.conn.execute(
                    'SELECT member_id FROM member_items WHERE kind=? AND target_id=?',
                    (kind, int(target_id))).fetchone()
        except sqlite3.OperationalError:      # auth never set up on this install
            return ''
        return row[0] if row else ''

    @classmethod
    def may_delete(cls, store, member, kind, target_id):
        """Admin, or the person who added it. Anything unclaimed is admin-only —
        the default for a delete has to be "no"."""
        if not member:
            return False
        if member.get('role') == 'admin':
            return True
        return bool(member.get('id')) and cls.owner_of(store, kind, target_id) == member['id']

    ICONS = ('music', 'headphones', 'star', 'sun', 'moon')

    def validate_profile(self, name, icon):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise Denied(400, 'Display name must be 1–100 characters')
        if not isinstance(icon, str) or icon not in self.ICONS:
            raise Denied(400, 'Choose a built-in profile icon')
        return name.strip(), icon

    def profile(self, store, actor, payload):
        if not isinstance(payload, dict) or set(payload) != {'display_name', 'profile_icon'}:
            raise Denied(400, 'Only display_name and profile_icon may be changed')
        name, icon = self.validate_profile(payload['display_name'], payload['profile_icon'])
        member = self.member(store, actor)
        with store.lock:
            store.conn.execute('UPDATE members SET display_name=?,profile_icon=? WHERE id=?', (name, icon, member['id']))
            store.conn.commit()
        # A rename is feed-worthy, but only when the name actually changed. Core says what
        # happened; Social subscribes to it and writes the Feed row (no Core → Social edge).
        if (member.get('display_name') or '').strip() != name:
            import events
            events.publish(store, events.MEMBER_RENAMED, member_id=member['id'], detail=name)
        return {'display_name': name, 'profile_icon': icon}

    def member(self, store, user):
        self.schema(store)
        with store.lock:
            r = store.conn.execute('SELECT * FROM members WHERE id=?', (user['id'],)).fetchone()
        if not r or r['role'] not in ('member', 'admin'):
            raise Denied(403, 'Membership blocked or not approved')
        return dict(r)

    def admin(self, actor):
        if not actor or actor['role'] != 'admin':
            raise Denied(403, 'Administrator required')

    def create_user(self, store, actor, name, role):
        self.admin(actor)
        if name not in (None, '') or role not in ('member', 'admin'):
            raise Denied(400, 'Only role may be chosen by administrators')
        name = ''
        self.schema(store)
        uid = secrets.token_hex(16)
        with store.lock:
            store.conn.execute('INSERT INTO members(id,email,role,display_name) VALUES(?,?,?,?)', (uid, '', role, name))
            store.conn.commit()
        return uid

    def invite(self, store, actor, uid, ttl=None):
        """The member's permanent link. One per profile, valid until revoked.

        `ttl` stays for the explicit-temporary callers (none today), but the
        default is FOREVER: a person's link *is* their account, so there is no
        reason for it to stop working. Issuing again replaces the old one, and
        `revoke()` is what kills it.
        """
        self.admin(actor)
        origin = self.config()[2]
        self.member(store, {'id': uid})
        token = secrets.token_urlsafe(32)
        expires = self.FOREVER if ttl is None else time.time() + ttl
        with store.lock:
            store.conn.execute('DELETE FROM browser_invites WHERE member_id=?', (uid,))
            store.conn.execute('INSERT INTO browser_invites VALUES(?,?,?)',
                               (self.digest(token), uid, expires))
            store.conn.commit()
        return origin + '/auth/link#invite=' + token

    def exchange(self, store, token, display_name=None, profile_icon=None):
        self.schema(store)
        if not isinstance(token, str) or not token or len(token) > 256:
            raise Denied(401, 'Invalid or expired invitation')
        joined = ""
        # Reads the permanent link and issues a session, in one write
        # transaction. The link row is NOT deleted: a person's link is their
        # account, so it keeps working every time they open it.
        with store.lock:
            try:
                store.conn.execute('BEGIN IMMEDIATE')
                r = store.conn.execute(
                    'SELECT member_id FROM browser_invites WHERE digest=? '
                    'AND (expires=? OR expires>?)',
                    (self.digest(token), self.FOREVER, time.time())).fetchone()
                if not r:
                    raise Denied(401, 'Invalid or expired invitation')
                member = store.conn.execute('SELECT role,display_name FROM members WHERE id=?', (r[0],)).fetchone()
                if not member or member[0] not in ('member', 'admin'):
                    raise Denied(403, 'Membership blocked or not approved')
                if not member['display_name']:
                    name, icon = self.validate_profile(display_name, profile_icon)
                    store.conn.execute('UPDATE members SET display_name=?,profile_icon=? WHERE id=?', (name, icon, r[0]))
                    joined = name
                sid = self._issue_session(store.conn, r[0], 'login_link')
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        # Somebody new just chose their name: that is the "joined" event. Written
        # after the transaction commits, so the feed can never roll one back.
        if joined:
            import events
            events.publish(store, events.MEMBER_JOINED, member_id=r[0], detail=joined)
        return sid

    # -- sessions ----------------------------------------------------------
    def _issue_session(self, connection, member_id, auth_method='login_link'):
        """Write one session row and return the value the cookie carries.

        The caller owns the transaction, so signup can mint the session in the same commit that
        creates the member and consumes the invite.
        """
        if auth_method not in self.AUTH_METHODS:
            raise Denied(400, 'Unknown authentication method')
        sid = secrets.token_urlsafe(32)
        now = time.time()
        connection.execute(
            'INSERT INTO browser_sessions(digest,member_id,expires,auth_method) VALUES(?,?,?,?)',
            (self.digest(sid), member_id, now + self.SESSION_TTL, auth_method))
        # One link, many devices: each open mints its own session, so the table needs a ceiling.
        # Drop expired rows, then keep the newest 20 per member (a phone, a laptop, a tablet).
        connection.execute('DELETE FROM browser_sessions WHERE expires<=?', (now,))
        connection.execute(
            'DELETE FROM browser_sessions WHERE member_id=? AND rowid NOT IN '
            '(SELECT rowid FROM browser_sessions WHERE member_id=? '
            'ORDER BY rowid DESC LIMIT 20)', (member_id, member_id))
        return sid

    def create_session(self, store, member_id, auth_method='login_link'):
        """The one place a `__Host-hl_session` is minted, for every way in."""
        self.schema(store)
        with store.lock:
            try:
                sid = self._issue_session(store.conn, member_id, auth_method)
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        return sid

    # -- passwords ---------------------------------------------------------
    @staticmethod
    def normalize_email(value):
        return value.strip().lower() if isinstance(value, str) else ''

    def validate_email(self, value):
        email = self.normalize_email(value)
        if not 5 <= len(email) <= 254 or email.count('@') != 1 or any(c.isspace() for c in email):
            raise Denied(400, 'Enter an email address')
        local, _, domain = email.partition('@')
        if not local or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
            raise Denied(400, 'Enter an email address')
        return email

    def validate_password(self, password):
        if not isinstance(password, str) or len(password) < self.PASSWORD_MIN:
            raise Denied(400, 'Password must be at least %d characters' % self.PASSWORD_MIN)
        if len(password) > 1024:
            raise Denied(400, 'Password is too long')
        return password

    def hash_password(self, password):
        """Salt and hash, both hex. Self-hosted: scrypt from the standard library."""
        salt = secrets.token_hex(16)
        hashed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **self.SCRYPT)
        return salt, hashed.hex()

    def verify_password(self, password, salt, stored):
        if not isinstance(password, str) or not salt or not stored:
            return False
        try:
            expected = bytes.fromhex(stored)
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                    dklen=len(expected), n=self.SCRYPT['n'],
                                    r=self.SCRYPT['r'], p=self.SCRYPT['p'])
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)

    def set_password(self, store, member_id, password, email=None):
        """Set or replace a password. `email` is only needed for a first password."""
        password = self.validate_password(password)
        salt, hashed = self.hash_password(password)
        now = time.time()
        self.schema(store)
        with store.lock:
            try:
                row = store.conn.execute(
                    'SELECT login_email FROM member_credentials WHERE member_id=?', (member_id,)).fetchone()
                if row:
                    store.conn.execute(
                        'UPDATE member_credentials SET password_hash=?,password_salt=?,'
                        'password_changed_at=? WHERE member_id=?', (hashed, salt, now, member_id))
                else:
                    # First password for a member who joined through a login link: it needs a login
                    # identifier, and the address they type is it. `members.email` only follows when
                    # nobody set a contact address.
                    address = self.validate_email(email)
                    if store.conn.execute('SELECT 1 FROM member_credentials WHERE login_email=?',
                                          (address,)).fetchone():
                        raise Denied(409, 'That email is already in use')
                    store.conn.execute(
                        'INSERT INTO member_credentials(member_id,login_email,password_hash,'
                        'password_salt,password_changed_at) VALUES(?,?,?,?,?)',
                        (member_id, address, hashed, salt, now))
                    store.conn.execute(
                        "UPDATE members SET email=? WHERE id=? AND email=''", (address, member_id))
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        return True

    def authenticate_password(self, store, email, password):
        """Email and password in, a member out. Every failure looks the same."""
        self.schema(store)
        address = self.normalize_email(email)
        with store.lock:
            row = store.conn.execute(
                'SELECT c.member_id, c.password_hash, c.password_salt, m.role '
                'FROM member_credentials c JOIN members m ON m.id=c.member_id '
                'WHERE c.login_email=?', (address,)).fetchone()
        if not row:
            # Hash anyway: an unknown address and a wrong password should cost the same.
            self.verify_password(password or '', secrets.token_hex(16), secrets.token_hex(32))
            raise Denied(401, 'Invalid email or password')
        if not self.verify_password(password, row['password_salt'], row['password_hash']):
            raise Denied(401, 'Invalid email or password')
        if row['role'] not in ('member', 'admin'):
            raise Denied(403, 'Membership blocked or not approved')
        return self.member(store, {'id': row['member_id']})

    def account(self, store, member):
        """Safe account metadata: no hashes, salts, provider subjects or tokens."""
        self.schema(store)
        with store.lock:
            cred = store.conn.execute(
                'SELECT login_email, password_hash FROM member_credentials WHERE member_id=?',
                (member['id'],)).fetchone()
            providers = {r[0] for r in store.conn.execute(
                'SELECT provider FROM member_identities WHERE member_id=?', (member['id'],))}
        return {
            'email': cred['login_email'] if cred else '',
            'has_password': bool(cred and cred['password_hash']),
            'auth_method': member.get('auth_method') or 'login_link',
            'google': 'google' in providers,
            'apple': 'apple' in providers,
        }

    def change_password(self, store, member, payload):
        if not isinstance(payload, dict):
            raise Denied(400, 'Object required')
        if payload.get('new_password') != payload.get('confirm_password'):
            raise Denied(400, 'The two passwords do not match')
        self.schema(store)
        with store.lock:
            cred = store.conn.execute(
                'SELECT login_email, password_hash, password_salt FROM member_credentials '
                'WHERE member_id=?', (member['id'],)).fetchone()
        # A login-link session already proves the account — possession of the permanent link is the
        # whole credential — so only the other methods ask for the current password. A password
        # change never revokes the login link: the link stays the recovery path.
        if cred and member.get('auth_method') != 'login_link':
            if not self.verify_password(payload.get('current_password'),
                                       cred['password_salt'], cred['password_hash']):
                raise Denied(403, 'Current password is incorrect')
        self.set_password(store, member['id'], payload.get('new_password'),
                          None if cred else payload.get('email'))
        return {'ok': True}

    # -- signup links ------------------------------------------------------
    def signup_url(self, token):
        return self.config()[2] + '/signup#invite=' + token

    def mint_signup(self, store, issued_by, ttl=None):
        """Mint one single-use signup link. Returns the raw token and its expiry."""
        self.schema(store)
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires = now + (self.SIGNUP_TTL if ttl is None else ttl)
        with store.lock:
            # Expired rows are swept here rather than by a job: "active" is a query, not a state.
            store.conn.execute('DELETE FROM signup_invites WHERE consumed_at IS NULL AND expires_at<=?', (now,))
            store.conn.execute(
                'INSERT INTO signup_invites(digest,issued_by,created_at,expires_at) VALUES(?,?,?,?)',
                (self.digest(token), issued_by, now, expires))
            store.conn.commit()
        return token, expires

    def signup_state(self, store, digest):
        """Whether this browser still holds a valid signup flow."""
        if not isinstance(digest, str) or not digest:
            return {'active': False, 'expires_at': None}
        self.schema(store)
        now = time.time()
        with store.lock:
            row = store.conn.execute(
                'SELECT expires_at FROM signup_invites WHERE digest=? AND consumed_at IS NULL '
                'AND expires_at>?', (digest, now)).fetchone()
        return {'active': bool(row), 'expires_at': row['expires_at'] if row else None}

    def open_signup(self, store, token):
        """Validate a signup token and return the digest the flow cookie carries."""
        self.schema(store)
        if not isinstance(token, str) or not token or len(token) > 256:
            raise Denied(401, 'That signup link is no longer valid')
        digest = self.digest(token)
        if not self.signup_state(store, digest)['active']:
            raise Denied(401, 'That signup link is no longer valid')
        return digest

    def signup_invite_state(self, store, member):
        self.schema(store)
        now = time.time()
        with store.lock:
            row = store.conn.execute(
                'SELECT expires_at FROM signup_invites WHERE issued_by=? AND consumed_at IS NULL '
                'AND expires_at>? ORDER BY created_at DESC', (member['id'], now)).fetchone()
        return {'active': bool(row), 'expires_at': row['expires_at'] if row else None,
                'unlimited': member.get('role') == 'admin'}

    def mint_member_signup(self, store, member):
        """A regular member holds one outstanding invite at a time; an administrator is not capped."""
        unlimited = member.get('role') == 'admin'
        if not unlimited and self.signup_invite_state(store, member)['active']:
            raise Denied(409, 'You already have a signup link. Revoke it to make another.')
        token, expires = self.mint_signup(store, member['id'])
        return {'url': self.signup_url(token), 'expires_at': expires, 'unlimited': unlimited}

    def revoke_member_signup(self, store, member):
        with store.lock:
            store.conn.execute('DELETE FROM signup_invites WHERE issued_by=? AND consumed_at IS NULL',
                               (member['id'],))
            store.conn.commit()
        return True

    def signup(self, store, digest, display_name, profile_icon, email, password):
        """Redeem a signup link: create the member, the credential, the session — or nothing."""
        name, icon = self.validate_profile(display_name, profile_icon)
        address = self.validate_email(email)
        password = self.validate_password(password)
        salt, hashed = self.hash_password(password)
        self.schema(store)
        now = time.time()
        member_id = secrets.token_hex(16)
        with store.lock:
            try:
                store.conn.execute('BEGIN IMMEDIATE')
                # Consuming first is what makes the link single use: the UPDATE is the lock, so two
                # concurrent redemptions cannot both come out with a member.
                used = store.conn.execute(
                    'UPDATE signup_invites SET consumed_at=?, consumed_by=? '
                    'WHERE digest=? AND consumed_at IS NULL AND expires_at>?',
                    (now, member_id, digest, now)).rowcount
                if used != 1:
                    raise Denied(401, 'That signup link is no longer valid')
                if store.conn.execute('SELECT 1 FROM member_credentials WHERE login_email=?',
                                      (address,)).fetchone():
                    raise Denied(409, 'That email is already in use')
                store.conn.execute(
                    'INSERT INTO members(id,email,role,display_name,profile_icon) VALUES(?,?,?,?,?)',
                    (member_id, address, 'member', name, icon))
                store.conn.execute(
                    'INSERT INTO member_credentials(member_id,login_email,password_hash,'
                    'password_salt,password_changed_at) VALUES(?,?,?,?,?)',
                    (member_id, address, hashed, salt, now))
                sid = self._issue_session(store.conn, member_id, 'password')
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        import events
        events.publish(store, events.MEMBER_JOINED, member_id=member_id, detail=name)
        return sid

    def revoke(self, store, uid):
        self.schema(store)
        with store.lock:
            store.conn.execute('DELETE FROM browser_sessions WHERE member_id=?', (uid,))
            store.conn.execute('DELETE FROM browser_invites WHERE member_id=?', (uid,))
            # Links this member handed out die with their sessions; a password and a provider
            # identity are not credentials to revoke — blocking is what stops those.
            store.conn.execute('DELETE FROM signup_invites WHERE issued_by=? AND consumed_at IS NULL', (uid,))
            store.conn.commit()

    def cookie(self, handler, name):
        try:
            return SimpleCookie(handler.headers.get('Cookie', ''))[name].value
        except (KeyError, ValueError):
            return ''

    def csrf(self, handler):
        """Every POST must come from a page this app served.

        The configured public origin is the canonical one, but the same site is also
        opened on http://127.0.0.1:8470 while developing, on the machine's LAN address
        from a phone and on a tailnet name — and a browser answers every one of those
        with the Origin of the page it is on, so a single literal origin rejects the
        app's own login form. A hostile page can only ever name itself, so accepting the
        request's own scheme://host alongside the configured origin keeps this a CSRF
        check instead of a domain allowlist. A rebinding page still cannot present our
        session cookie: it is `__Host-hl_session`, bound to the origin that set it.
        """
        origin = handler.headers.get('Origin', '')
        if origin == self.config()[2]:
            return
        parsed = urlparse(origin)
        host = handler.headers.get('Host', '')
        if host and parsed.scheme in ('http', 'https') and parsed.netloc == host:
            return
        raise Denied(403, 'Same-origin request required')

    def bounded(self, mapping, key, value):
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > 2048:
            mapping.popitem(last=False)

    VERIFY_BUDGET = 300
    ACTION_BUDGET = 600
    ASSET_BUDGET = 6000

    def limit(self, identity, bucket, maximum):
        now = time.monotonic()
        key = (identity, bucket)
        with self.lock:
            start, count = self.rates.get(key, (now, 0))
            if now-start >= 60:
                start, count = now, 0
            self.bounded(self.rates, key, (start, count+1))
        if count >= maximum:
            retry_after = max(1, int(60 - (now - start)) + 1)
            raise Denied(429, 'Rate limit exceeded', retry_after=retry_after)

    def authenticate(self, handler, store, requests_limit=None):
        self.config()
        self.schema(store)
        sid = self.cookie(handler, '__Host-hl_session')
        digest = self.digest(sid)
        now = time.time()
        with store.lock:
            r = store.conn.execute('SELECT member_id, expires, auth_method FROM browser_sessions WHERE digest=? AND expires>?', (digest, now)).fetchone()
            # Sliding session: using it extends it, so a signed-in member is never
            # logged out for age. Written at most hourly rather than per request.
            if r and r['expires'] < now + self.SESSION_TTL - 3600:
                store.conn.execute('UPDATE browser_sessions SET expires=? WHERE digest=?',
                                   (now + self.SESSION_TTL, digest))
                store.conn.commit()
        if not r:
            self.limit(self.client_ip(handler), 'verification', self.VERIFY_BUDGET)
            raise Denied(401, 'Sign in required')
        member = self.member(store, {'id': r[0]})
        # How this browser got in follows the session, not the member: the same account may be
        # open through a login link on one device and a password on another.
        member['auth_method'] = r['auth_method'] or 'login_link'
        if handler.command != 'GET':
            self.csrf(handler)
        path = getattr(handler, 'path', '').split('?', 1)[0]
        asset = handler.command == 'GET' and (path.startswith('/artwork/') or path.endswith('/audio'))
        bucket = 'assets' if asset else 'requests'
        maximum = self.ASSET_BUDGET if asset else (self.ACTION_BUDGET if requests_limit is None else requests_limit)
        self.limit(member['id'], bucket, maximum)
        if '/download' in path:
            self.limit(member['id'], 'exports', 10)
        return member

    def peek(self, handler, store):
        """The member this request is signed in as, or None. Never raises, never counts.

        The browser asks for `/theme.css` from `<head>`, before any script runs and before
        the page has had a chance to redirect a signed-out visitor. That request must not
        401 the page, and it must not let anonymous traffic spend the verification budget
        that a real sign-in attempt needs.
        """
        self.config()
        self.schema(store)
        sid = self.cookie(handler, '__Host-hl_session')
        with store.lock:
            row = store.conn.execute(
                'SELECT member_id FROM browser_sessions WHERE digest=? AND expires>?',
                (self.digest(sid), time.time())).fetchone()
        return self.member(store, {'id': row[0]}) if row else None

SECURITY = Security()
