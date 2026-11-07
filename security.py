"""Local invitation enrollment and persistent browser sessions. No upstream identity."""
import os
import sqlite3
import time
import hashlib
import secrets
import threading
from collections import OrderedDict
from http.cookies import SimpleCookie
from urllib.parse import urlparse

class Denied(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message

class Security:
    # The link IS the account: a member's profile link never expires, and the
    # session it creates is renewed on every authenticated request, so nobody is
    # logged out for age. Party guest links keep their own 24-hour TTL in
    # party.py — that is the only temporary credential in the app.
    SESSION_TTL = 10 * 365 * 86400
    # Sentinel stored in the `expires` column for "never expires".
    FOREVER = 0.0
    def __init__(self):
        self.lock = threading.RLock()
        self.rates = OrderedDict()

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
                CREATE TABLE IF NOT EXISTS browser_sessions (digest TEXT PRIMARY KEY, member_id TEXT NOT NULL, expires REAL NOT NULL);''')
            cols = {r[1] for r in store.conn.execute('PRAGMA table_info(members)')}
            if 'display_name' not in cols:
                store.conn.execute("ALTER TABLE members ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
            if 'profile_icon' not in cols:
                store.conn.execute("ALTER TABLE members ADD COLUMN profile_icon TEXT NOT NULL DEFAULT 'music'")
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
        # A rename is feed-worthy, but only when the name actually changed.
        if (member.get('display_name') or '').strip() != name:
            import activity
            activity.record(store, activity.KIND_NAME, member_id=member['id'],
                            detail=name)
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
        sid = secrets.token_urlsafe(32)
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
                store.conn.execute('INSERT INTO browser_sessions VALUES(?,?,?)', (self.digest(sid), r[0], time.time()+self.SESSION_TTL))
                # One link, many devices: each open mints its own session, so the
                # table needs a ceiling. Drop expired rows, then keep the newest 20
                # per member (a phone, a laptop, a tablet, re-installs).
                store.conn.execute('DELETE FROM browser_sessions WHERE expires<=?',
                                   (time.time(),))
                store.conn.execute(
                    'DELETE FROM browser_sessions WHERE member_id=? AND rowid NOT IN '
                    '(SELECT rowid FROM browser_sessions WHERE member_id=? '
                    'ORDER BY rowid DESC LIMIT 20)', (r[0], r[0]))
                store.conn.commit()
            except Exception:
                store.conn.rollback()
                raise
        # Somebody new just chose their name: that is the "joined" event. Written
        # after the transaction commits, so the feed can never roll one back.
        if joined:
            try:
                import activity
                activity.record(store, activity.KIND_JOIN, member_id=r[0], detail=joined)
            except Exception:
                pass
        return sid

    def revoke(self, store, uid):
        self.schema(store)
        with store.lock:
            store.conn.execute('DELETE FROM browser_sessions WHERE member_id=?', (uid,))
            store.conn.execute('DELETE FROM browser_invites WHERE member_id=?', (uid,))
            store.conn.commit()

    def cookie(self, handler, name):
        try:
            return SimpleCookie(handler.headers.get('Cookie', ''))[name].value
        except (KeyError, ValueError):
            return ''

    def csrf(self, handler):
        if handler.headers.get('Origin') != self.config()[2]:
            raise Denied(403, 'Same-origin request required')

    def bounded(self, mapping, key, value):
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > 2048:
            mapping.popitem(last=False)

    def limit(self, identity, bucket, maximum):
        now = time.monotonic()
        key = (identity, bucket)
        with self.lock:
            start, count = self.rates.get(key, (now, 0))
            if now-start >= 60:
                start, count = now, 0
            self.bounded(self.rates, key, (start, count+1))
        if count >= maximum:
            raise Denied(429, 'Rate limit exceeded')

    def authenticate(self, handler, store, requests_limit=240):
        self.config()
        self.limit(handler.client_address[0], 'verification', 300)
        self.schema(store)
        sid = self.cookie(handler, '__Host-hl_session')
        digest = self.digest(sid)
        now = time.time()
        with store.lock:
            r = store.conn.execute('SELECT member_id, expires FROM browser_sessions WHERE digest=? AND expires>?', (digest, now)).fetchone()
            # Sliding session: using it extends it, so a signed-in member is never
            # logged out for age. Written at most hourly rather than per request.
            if r and r['expires'] < now + self.SESSION_TTL - 3600:
                store.conn.execute('UPDATE browser_sessions SET expires=? WHERE digest=?',
                                   (now + self.SESSION_TTL, digest))
                store.conn.commit()
        if not r:
            raise Denied(401, 'Sign in required')
        member = self.member(store, {'id': r[0]})
        if handler.command != 'GET':
            self.csrf(handler)
        self.limit(member['id'], 'requests', requests_limit)
        if '/download' in handler.path:
            self.limit(member['id'], 'exports', 10)
        return member

SECURITY = Security()
