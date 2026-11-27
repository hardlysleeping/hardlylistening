"""Temporary, party-only bearer guests; never creates members or grants media access.

See PARTY_INTEGRATION.md before wiring the HTTP adapter into server.py.
"""
import hashlib
import random
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlsplit, parse_qs

from security import Denied
from library.browser_downloads import local_path

TTL = 86400
ICONS = ('music', 'headphones', 'star', 'sun', 'moon')
COOKIE = '__Host-hl_party'
TOKEN = re.compile(r'^[A-Za-z0-9_-]{43}$')
# Anyone still polling counts as "in the party"; a skip needs strictly more than
# half of them, so one person alone always skips instantly.
PRESENCE_TTL = 45
PRESENCE_TOUCH = 4


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def schema(store):
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS party_rooms (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, creator TEXT NOT NULL,
          expires REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
          host_digest TEXT, host_member TEXT, host_until REAL NOT NULL DEFAULT 0,
          deck_playing INTEGER NOT NULL DEFAULT 0, deck_gain REAL NOT NULL DEFAULT 1,
          deck_position REAL NOT NULL DEFAULT 0, deck_updated REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS party_guests (
          id TEXT PRIMARY KEY, party TEXT NOT NULL, invite_digest TEXT UNIQUE NOT NULL,
          expires REAL NOT NULL, display_name TEXT NOT NULL DEFAULT '',
          profile_icon TEXT NOT NULL DEFAULT 'music', revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS party_sessions (
          digest TEXT PRIMARY KEY, guest TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS party_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT, party TEXT NOT NULL,
          track_id INTEGER NOT NULL, added_by TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'waiting', position INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS party_entries_room ON party_entries(party,status,id);
        CREATE UNIQUE INDEX IF NOT EXISTS party_one_current ON party_entries(party) WHERE status='playing';
        CREATE TABLE IF NOT EXISTS party_chat (
          id INTEGER PRIMARY KEY AUTOINCREMENT, party TEXT NOT NULL, author TEXT NOT NULL,
          display_name TEXT NOT NULL, profile_icon TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS party_chat_room ON party_chat(party,id);
        CREATE TABLE IF NOT EXISTS party_votes (
          entry INTEGER NOT NULL, voter TEXT NOT NULL, value INTEGER NOT NULL CHECK(value IN (-1,1)),
          PRIMARY KEY(entry,voter));
        CREATE TABLE IF NOT EXISTS party_presence (
          party TEXT NOT NULL, actor TEXT NOT NULL, seen REAL NOT NULL,
          PRIMARY KEY(party,actor));
        CREATE TABLE IF NOT EXISTS party_skips (
          entry INTEGER NOT NULL, actor TEXT NOT NULL,
          PRIMARY KEY(entry,actor));
        ''')
        # Rooms created before HL-204 have no deck. Adding the columns is the whole migration:
        # the defaults describe a room that has never had a deck, which is what these are.
        for table, column, ddl in (
                ('party_rooms', 'deck_playing', 'INTEGER NOT NULL DEFAULT 0'),
                ('party_rooms', 'deck_gain', 'REAL NOT NULL DEFAULT 1'),
                ('party_rooms', 'deck_position', 'REAL NOT NULL DEFAULT 0'),
                ('party_rooms', 'deck_updated', 'REAL NOT NULL DEFAULT 0'),
                ('party_entries', 'position', 'INTEGER NOT NULL DEFAULT 0')):
            try:
                store.conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
            except sqlite3.OperationalError:
                pass                       # already there


@contextmanager
def transaction(store):
    with store.lock:
        try:
            store.conn.execute('BEGIN IMMEDIATE')
            yield store.conn
            store.conn.commit()
        except BaseException:
            store.conn.rollback()
            raise


def integer(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]{0,17}', str(value)):
        raise Denied(400, 'Positive integer required')
    return int(value)


def room(conn, pid, now=None):
    r = conn.execute('SELECT * FROM party_rooms WHERE id=?', (pid,)).fetchone()
    if not r or r['closed'] or r['expires'] <= (time.time() if now is None else now):
        raise Denied(410, 'Party ended or expired')
    return dict(r)


def touch(c, pid, actor, now=None):
    """Record that an identity has the party open. Cheap: a 3 s poll rewrites at most every 4 s."""
    now = time.time() if now is None else now
    row = c.execute('SELECT seen FROM party_presence WHERE party=? AND actor=?', (pid, actor)).fetchone()
    if row and now - row[0] < PRESENCE_TOUCH:
        return
    c.execute('INSERT INTO party_presence(party,actor,seen) VALUES(?,?,?) '
              'ON CONFLICT(party,actor) DO UPDATE SET seen=excluded.seen', (pid, actor, now))


def people(c, pid, now=None):
    now = time.time() if now is None else now
    return c.execute('SELECT count(*) FROM party_presence WHERE party=? AND seen>=?',
                     (pid, now - PRESENCE_TTL)).fetchone()[0]


def skip_votes(c, pid, entry, now=None):
    """Skips that still count: the voter has to be present, not a closed tab."""
    now = time.time() if now is None else now
    return c.execute('''SELECT count(*) FROM party_skips s
      JOIN party_entries e ON e.id=s.entry AND e.party=?
      JOIN party_presence p ON p.party=e.party AND p.actor=s.actor
      WHERE s.entry=? AND p.seen>=?''', (pid, entry, now - PRESENCE_TTL)).fetchone()[0]


def advance(c, store, pid, expected):
    """Retire the expected current entry and promote the top-ranked waiting one."""
    current = c.execute("SELECT id FROM party_entries WHERE party=? AND status='playing'", (pid,)).fetchone()
    got = current[0] if current else None
    if got != expected:
        raise Denied(409, 'Current entry changed; refresh before advancing')
    c.execute("UPDATE party_entries SET status='done' WHERE party=? AND status='playing'", (pid,))
    if got is not None:
        c.execute('DELETE FROM party_skips WHERE entry=?', (got,))
    nxt = c.execute('''SELECT e.id,e.track_id FROM party_entries e LEFT JOIN party_votes v ON v.entry=e.id
       WHERE e.party=? AND e.status='waiting' GROUP BY e.id
       ORDER BY COALESCE(SUM(v.value),0) DESC,e.position,e.id LIMIT 1''', (pid,)).fetchone()
    if not nxt:
        return None
    track(c, store, nxt['track_id'])
    c.execute("UPDATE party_entries SET status='playing' WHERE id=?", (nxt['id'],))
    return nxt['id']


def member_identity(member):
    if not member or member.get('role') not in ('admin', 'member'):
        raise Denied(403, 'Approved membership required')
    return 'm:' + member['id']


def create(store, member, title='Party', hours=24):
    member_identity(member)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise Denied(400, 'Party title must be 1–100 characters')
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours <= 24:
        raise Denied(400, 'Party lifetime must be at most 24 hours')
    pid = secrets.token_urlsafe(12)
    with transaction(store) as c:
        c.execute('INSERT INTO party_rooms(id,title,creator,expires) VALUES(?,?,?,?)',
                  (pid, title.strip(), member['id'], time.time() + hours * 3600))
    return {'id': pid}


def invite(store, member, pid):
    member_identity(member)  # Deliberate exception: every approved member may invite.
    secret, gid = secrets.token_urlsafe(32), secrets.token_urlsafe(12)
    with transaction(store) as c:
        r = room(c, pid)
        if c.execute('SELECT count(*) FROM party_guests WHERE party=?', (pid,)).fetchone()[0] >= 200:
            raise Denied(409, 'Guest limit reached')
        expiry = min(r['expires'], time.time() + TTL)
        c.execute('INSERT INTO party_guests(id,party,invite_digest,expires) VALUES(?,?,?,?)',
                  (gid, pid, digest(secret), expiry))
    return {'guest_id': gid, 'expires': expiry, 'link': '/party#invite=' + secret}


def exchange(store, secret, display_name=None, profile_icon=None):
    if not isinstance(secret, str) or not TOKEN.fullmatch(secret):
        raise Denied(401, 'Invalid party invitation')
    sid, now = secrets.token_urlsafe(32), time.time()
    with transaction(store) as c:
        g = c.execute('SELECT * FROM party_guests WHERE invite_digest=?', (digest(secret),)).fetchone()
        if not g or g['revoked'] or g['expires'] <= now:
            raise Denied(401, 'Invalid or expired party invitation')
        r = room(c, g['party'], now)
        if not g['display_name']:
            if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 100 or profile_icon not in ICONS:
                raise Denied(400, 'Choose a name (1–100 characters) and built-in icon')
            c.execute('UPDATE party_guests SET display_name=?,profile_icon=? WHERE id=?',
                      (display_name.strip(), profile_icon, g['id']))
        expiry = min(r['expires'], g['expires'])
        # Rejoins preserve the same guest/voter identity and fixed original expiry.
        # Bound sessions per guest without making the reusable link single-use.
        c.execute('DELETE FROM party_sessions WHERE guest=? AND (expires<=? OR rowid IN (SELECT rowid FROM party_sessions WHERE guest=? ORDER BY rowid DESC LIMIT -1 OFFSET 19))', (g['id'], now, g['id']))
        c.execute('INSERT INTO party_sessions VALUES(?,?,?)', (digest(sid), g['id'], expiry))
    return {'session': sid, 'party_id': g['party'], 'expires': expiry}


def guest(store, sid, pid):
    if not isinstance(sid, str) or not TOKEN.fullmatch(sid):
        raise Denied(401, 'Party sign-in required')
    with store.lock:
        room(store.conn, pid)
        g = store.conn.execute('''SELECT g.* FROM party_guests g JOIN party_sessions s ON s.guest=g.id
          WHERE s.digest=? AND g.party=? AND s.expires>? AND g.expires>? AND g.revoked=0''',
                               (digest(sid), pid, time.time(), time.time())).fetchone()
        if not g:
            raise Denied(401, 'Party sign-in required')
        return {'id': 'g:' + g['id'], 'display_name': g['display_name'], 'profile_icon': g['profile_icon']}


def track(conn, store, tid):
    t = conn.execute('SELECT * FROM tracks WHERE id=?', (integer(tid),)).fetchone()
    if not t or not local_path(store, dict(t)):
        raise Denied(404, 'Existing playable track required')
    return dict(t)


def public_track(t):
    return {k: t.get(k) for k in ('id', 'title', 'artist_name', 'album', 'duration')}


# "A song somebody can actually hear", as SQL: not deleted, and with a file on this disk.
# Deliberately not `local_path`, which stats every file — right for the handful of tracks an add
# really queues, wrong for a list of every playlist in the library.
PLAYABLE = "t.deleted=0 AND t.file<>''"


def sets(store, pid, member=None):
    """What a person can start the room from: playlists, and the artists they actually have.

    `saved` marks the playlists this member has saved in the app, which is the only difference
    between "the library's" and "mine" — they are the same rows.
    """
    with store.lock:
        room(store.conn, pid)
        saved = set()
        if member:
            try:
                saved = {row[0] for row in store.conn.execute(
                    'SELECT playlist_id FROM listening_playlists WHERE member_id=?',
                    (member['id'],))}
            except sqlite3.OperationalError:
                saved = set()          # a library that has never saved one has no such table
        rows = store.conn.execute(
            'SELECT p.id AS id, p.title AS name, count(t.id) AS count'
            ' FROM playlists p JOIN playlist_tracks pt ON pt.playlist_id=p.id'
            ' JOIN tracks t ON t.id=pt.track_id AND ' + PLAYABLE +
            ' GROUP BY p.id ORDER BY count DESC, p.id LIMIT 50').fetchall()
        artists = store.conn.execute(
            'SELECT t.artist_name AS name, count(*) AS count FROM tracks t'
            " WHERE " + PLAYABLE + " AND t.artist_name<>''"
            ' GROUP BY t.artist_name ORDER BY count DESC, t.artist_name LIMIT 24').fetchall()
        return {'playlists': [{'id': r['id'], 'name': r['name'], 'count': r['count'],
                               'saved': r['id'] in saved} for r in rows],
                'artists': [{'name': r['name'], 'count': r['count']} for r in artists]}


def search(store, pid, query):
    """One field, three answers. Playlists and artists are what a person starts from, so a
    search that only returned tracks was answering a narrower question than the one asked."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
        raise Denied(400, 'Search must be 1–100 characters')
    # Bound both candidate work and returned metadata; never enumerate file/source URLs.
    term = query.strip()
    pattern = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    with store.lock:
        room(store.conn, pid)
        rows = store.conn.execute("SELECT * FROM tracks WHERE title LIKE ? ESCAPE '\\' OR artist_name LIKE ? ESCAPE '\\' ORDER BY id LIMIT 200", (pattern, pattern)).fetchall()
        playlists = store.conn.execute(
            "SELECT p.id AS id, p.title AS name, count(t.id) AS count"
            " FROM playlists p JOIN playlist_tracks pt ON pt.playlist_id=p.id"
            " JOIN tracks t ON t.id=pt.track_id AND " + PLAYABLE +
            " WHERE p.title LIKE ? ESCAPE '\\' GROUP BY p.id ORDER BY count DESC LIMIT 8",
            (pattern,)).fetchall()
        artists = store.conn.execute(
            'SELECT t.artist_name AS name, count(*) AS count FROM tracks t'
            " WHERE " + PLAYABLE + " AND t.artist_name LIKE ? ESCAPE '\\'"
            ' GROUP BY t.artist_name ORDER BY count DESC, t.artist_name LIMIT 8',
            (pattern,)).fetchall()
        return {'query': term,
                'playlists': [{'id': r['id'], 'name': r['name'], 'count': r['count']} for r in playlists],
                'artists': [{'name': r['name'], 'count': r['count']} for r in artists],
                'tracks': [public_track(dict(t)) for t in rows if local_path(store, dict(t))][:30]}


# The waiting queue's own cap, quoted back in a refusal so nobody has to guess why a
# 399-song playlist only added part of itself.
WAITING_LIMIT = 200


def set_tracks(c, kind, ident):
    """The tracks inside one set, in library order. Shuffling happens after this."""
    if kind == 'playlist':
        row = c.execute('SELECT title FROM playlists WHERE id=?', (integer(ident),)).fetchone()
        if not row:
            raise Denied(404, 'No such playlist')
        rows = c.execute('SELECT t.* FROM tracks t JOIN playlist_tracks pt ON pt.track_id=t.id'
                         ' WHERE pt.playlist_id=? ORDER BY pt.position, t.id',
                         (integer(ident),)).fetchall()
        return row['title'], [dict(t) for t in rows]
    if kind == 'artist':
        if not isinstance(ident, str) or not 1 <= len(ident.strip()) <= 300:
            raise Denied(400, 'An artist is required')
        name = ident.strip()
        rows = c.execute('SELECT t.* FROM tracks t WHERE t.artist_name=? AND ' + PLAYABLE +
                         ' ORDER BY id', (name,)).fetchall()
        if not rows:
            raise Denied(404, 'No playable tracks by that artist')
        return name, [dict(t) for t in rows]
    raise Denied(400, 'A set is a playlist or an artist')


def queue_set(store, pid, actor, kind, ident):
    """Shuffle a whole set into the waiting queue, skipping what is already there.

    The single-track `add` refuses a duplicate and says so, because a press is a question with
    one answer. A set is different: the interesting answer is how much of it landed, so
    duplicates are skipped rather than turned into an error on the 200th song.
    """
    with transaction(store) as c:
        room(c, pid)
        name, rows = set_tracks(c, kind, ident)
        # A deleted song can still have its file on disk, so `local_path` alone would queue a
        # song somebody removed from their library. The set keeps it in `size` and reports it
        # as unavailable rather than pretending the playlist was shorter than it is.
        playable = [row for row in rows if not row.get('deleted') and local_path(store, row)]
        random.shuffle(playable)
        have = {row[0] for row in c.execute(
            "SELECT track_id FROM party_entries WHERE party=? AND status IN ('waiting','playing')",
            (pid,))}
        waiting = c.execute(
            "SELECT count(*) FROM party_entries WHERE party=? AND status='waiting'",
            (pid,)).fetchone()[0]
        added, already, unavailable, full = 0, 0, len(rows) - len(playable), False
        for row in playable:
            if row['id'] in have:
                already += 1
                continue
            if waiting >= WAITING_LIMIT:
                full = True
                break
            queue_entry(c, store, pid, actor, row['id'])
            have.add(row['id'])
            added += 1
            waiting += 1
    return {'name': name, 'added': added, 'already': already, 'unavailable': unavailable,
            'full': full, 'limit': WAITING_LIMIT, 'size': len(rows)}


def queue_entry(c, store, pid, actor, tid):
    """Append one track to the waiting queue. The rule lives here so `add` and the jukebox agree."""
    track(c, store, tid)
    if c.execute("SELECT count(*) FROM party_entries WHERE party=? AND status='waiting'", (pid,)).fetchone()[0] >= 200:
        raise Denied(409, 'Waiting queue is full')
    if c.execute("SELECT 1 FROM party_entries WHERE party=? AND track_id=? AND status IN ('waiting','playing')", (pid, tid)).fetchone():
        raise Denied(409, 'Track is already queued')
    position = c.execute('SELECT COALESCE(MAX(position),0)+1 FROM party_entries WHERE party=?',
                         (pid,)).fetchone()[0]
    return c.execute('INSERT INTO party_entries(party,track_id,added_by,position) VALUES(?,?,?,?)',
                     (pid, tid, actor, position)).lastrowid


def add(store, pid, actor, tid):
    with transaction(store) as c:
        room(c, pid)
        eid = queue_entry(c, store, pid, actor, tid)
    return {'entry_id': eid}


def vote(store, pid, actor, eid, value):
    eid = integer(eid)
    if type(value) is not int or value not in (-1, 0, 1):
        raise Denied(400, 'Vote must be -1, 0, or 1')
    with transaction(store) as c:
        room(c, pid)
        if not c.execute("SELECT 1 FROM party_entries WHERE id=? AND party=? AND status='waiting'", (eid, pid)).fetchone():
            raise Denied(409, 'Only waiting entries can be voted on')
        if value == 0:
            c.execute('DELETE FROM party_votes WHERE entry=? AND voter=?', (eid, actor))
        else:
            c.execute('INSERT INTO party_votes VALUES(?,?,?) ON CONFLICT(entry,voter) DO UPDATE SET value=excluded.value', (eid, actor, value))
    return {'ok': True}


def state(store, pid, actor):
    with transaction(store) as c:
        r = room(c, pid)
        now = time.time()
        # Reading the room is what makes you present in it.
        touch(c, pid, actor, now)
        rows = c.execute('''SELECT e.id AS entry_id,e.status,t.id,t.title,t.artist_name,t.album,t.duration,
          COALESCE(SUM(v.value),0) AS score,
          COALESCE(MAX(CASE WHEN v.voter=? THEN v.value ELSE NULL END),0) AS vote
          FROM party_entries e JOIN tracks t ON t.id=e.track_id LEFT JOIN party_votes v ON v.entry=e.id
          WHERE e.party=? AND e.status IN ('waiting','playing') GROUP BY e.id
          ORDER BY CASE e.status WHEN 'playing' THEN 0 ELSE 1 END,score DESC,e.position ASC,e.id ASC''', (actor, pid)).fetchall()
        entries = [dict(t) for t in rows]
        # Votes are not anonymous in the room either: each entry carries the names
        # of the people who voted on it, so a score never hides behind a number.
        if entries:
            marks = ','.join('?' for _ in entries)
            voters: dict[int, list[dict]] = {}
            for vote in c.execute(
                    'SELECT v.entry AS entry, v.value AS value,'
                    ' COALESCE(g.display_name, m.display_name, "") AS name,'
                    ' COALESCE(g.profile_icon, m.profile_icon, "music") AS icon'
                    ' FROM party_votes v'
                    " LEFT JOIN party_guests g ON v.voter LIKE 'g:%' AND g.id=substr(v.voter,3)"
                    " LEFT JOIN members m ON v.voter LIKE 'm:%' AND m.id=substr(v.voter,3)"
                    ' WHERE v.entry IN (' + marks + ')',
                    [e['entry_id'] for e in entries]):
                voters.setdefault(vote['entry'], []).append(
                    {'name': (vote['name'] or '').strip() or 'Guest',
                     'icon': vote['icon'] or 'music', 'value': vote['value']})
            for entry in entries:
                entry['voters'] = voters.get(entry['entry_id'], [])
        current = next((e for e in entries if e['status'] == 'playing'), None)
        queue = [e for e in entries if e['status'] == 'waiting']
        total = people(c, pid, now)
        # Who is actually here, by name. Guests were already visible through their votes; a
        # moderator needs the person, not their vote, to be able to remove them.
        roster = []
        for row in c.execute(
                '''SELECT p.actor,
                          COALESCE(g.display_name, m.display_name, '') AS name,
                          COALESCE(g.profile_icon, m.profile_icon, 'music') AS icon
                   FROM party_presence p
                   LEFT JOIN party_guests g ON p.actor LIKE 'g:%' AND g.id=substr(p.actor,3)
                   LEFT JOIN members m ON p.actor LIKE 'm:%' AND m.id=substr(p.actor,3)
                   WHERE p.party=? AND p.seen>=? ORDER BY p.seen DESC, p.actor''',
                (pid, now - PRESENCE_TTL)):
            kind = 'guest' if row['actor'].startswith('g:') else 'member'
            held = kind == 'member' and r['host_member'] and row['actor'] == 'm:' + r['host_member']
            roster.append({'name': (row['name'] or '').strip() or ('Guest' if kind == 'guest' else 'Member'),
                           'icon': row['icon'] or 'music', 'kind': kind,
                           'guest_id': row['actor'][2:] if kind == 'guest' else None,
                           'you': row['actor'] == actor,
                           'host': bool(held and r['host_until'] > now)})
        skips = skip_votes(c, pid, current['entry_id'], now) if current else 0
        mine = bool(current) and bool(c.execute(
            'SELECT 1 FROM party_skips WHERE entry=? AND actor=?', (current['entry_id'], actor)).fetchone())
        nxt = queue[0] if queue else None
        deck = {'playing': bool(r['deck_playing']) and r['host_until'] > now,
                'gain': float(r['deck_gain']), 'position': float(r['deck_position'] or 0),
                'host_active': r['host_until'] > now, 'controlled_by': ''}
        if deck['host_active'] and r['host_member']:
            host = c.execute('SELECT display_name FROM members WHERE id=?', (r['host_member'],)).fetchone()
            deck['controlled_by'] = ((host[0] if host else '') or '').strip() or 'Member'
        return {'id': pid, 'title': r['title'], 'expires': r['expires'],
                'deck': deck,
                'host_active': r['host_until'] > now,
                # "Next up" is the entry an advance would promote right now.
                'next': ({k: nxt[k] for k in ('entry_id', 'id', 'title', 'artist_name', 'duration', 'score')} if nxt else None),
                'participants': total,
                'skip': {'count': skips, 'mine': mine, 'needed': total // 2 + 1},
                'current': current, 'queue': queue, 'icons': ICONS, 'people': roster}


def skip(store, pid, actor, expected=None):
    """Anyone in the party can ask to skip; strictly more than half of the people
    present make it happen. One person alone needs only their own click."""
    with transaction(store) as c:
        room(c, pid)
        now = time.time()
        touch(c, pid, actor, now)
        cur = c.execute("SELECT id FROM party_entries WHERE party=? AND status='playing'", (pid,)).fetchone()
        if not cur:
            raise Denied(409, 'Nothing is playing')
        eid = cur[0]
        if expected is not None:
            if integer(expected) != eid:
                # Another skip already moved the room on; report the truth, do not resurrect it.
                return {'skipped': False, 'skips': 0, 'people': people(c, pid, now), 'current': eid}
        c.execute('INSERT INTO party_skips(entry,actor) VALUES(?,?) ON CONFLICT(entry,actor) DO NOTHING', (eid, actor))
        skips, total = skip_votes(c, pid, eid, now), people(c, pid, now)
        done = skips * 2 > total
        if done:
            advance(c, store, pid, eid)
        return {'skipped': done, 'skips': skips, 'people': total,
                'current': None if done else eid}


def waiting_entries(c, pid):
    """The waiting queue in the order an advance would take it: votes first, then position."""
    return c.execute('''SELECT e.id,e.track_id FROM party_entries e
      LEFT JOIN party_votes v ON v.entry=e.id
      WHERE e.party=? AND e.status='waiting' GROUP BY e.id
      ORDER BY COALESCE(SUM(v.value),0) DESC,e.position,e.id''', (pid,)).fetchall()


def deck_status(c, pid, now=None):
    """What a Subsonic jukebox client asks for: index, playing, gain, position (whole seconds)."""
    now = time.time() if now is None else now
    r = c.execute('SELECT deck_playing,deck_gain,deck_position,host_until FROM party_rooms WHERE id=?',
                  (pid,)).fetchone()
    playing = c.execute("SELECT id FROM party_entries WHERE party=? AND status='playing'", (pid,)).fetchone()
    return {'currentIndex': 0 if playing else -1,
            'playing': bool(r['deck_playing']) and r['host_until'] > now,
            'gain': float(r['deck_gain']),
            'position': int(float(r['deck_position'] or 0))}


def clear_waiting(c, pid):
    rows = [row[0] for row in c.execute(
        "SELECT id FROM party_entries WHERE party=? AND status='waiting'", (pid,))]
    if rows:
        marks = ','.join('?' for _ in rows)
        c.execute(f'DELETE FROM party_votes WHERE entry IN ({marks})', rows)
        c.execute(f'DELETE FROM party_entries WHERE id IN ({marks})', rows)


JUKEBOX_ACTIONS = ('get', 'status', 'set', 'start', 'stop', 'add', 'clear', 'remove', 'shuffle',
                   'setGain')


def jukebox(store, pid, member, action, ids=None, index=None, gain=None):
    """Steer the room's deck without becoming a second speaker.

    The lease (`control('claim')`) decides which device makes sound; these commands decide what
    the room plays and how, so a phone can control the room it is in. Membership is required —
    guests may vote and skip, not replace the queue — and every change lands in one transaction.

    `position` on each entry is the order a jukebox client asked for; the room's votes still
    outrank it (`waiting_entries`), so a `set` order is honoured among tracks nobody has voted
    on, and a vote still moves a track up. That is the honest way to have both.
    """
    actor = member_identity(member)
    if not isinstance(action, str) or action not in JUKEBOX_ACTIONS:
        raise Denied(400, 'Unknown jukebox action')
    with transaction(store) as c:
        room(c, pid)
        now = time.time()
        if action in ('get', 'status'):
            status = deck_status(c, pid, now)
            if action == 'status':
                return {'status': status}
            # The playlist a jukebox client reads is the room's queue with the playing track at
            # index 0, which is what `currentIndex` refers to.
            playing = [row[0] for row in c.execute(
                "SELECT track_id FROM party_entries WHERE party=? AND status='playing'", (pid,))]
            return {'status': status,
                    'tracks': playing + [row['track_id'] for row in waiting_entries(c, pid)]}
        if action in ('start', 'stop'):
            if action == 'start' and not c.execute(
                    'SELECT host_until FROM party_rooms WHERE id=?', (pid,)).fetchone()[0] > now:
                # No leased device means no speaker: "playing" would be a status that lies.
                raise Denied(409, 'No aux host is playing this room')
            c.execute('UPDATE party_rooms SET deck_playing=?,deck_updated=? WHERE id=?',
                      (1 if action == 'start' else 0, now, pid))
        elif action == 'setGain':
            if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not 0 <= float(gain) <= 1:
                raise Denied(400, 'Gain must be between 0 and 1')
            c.execute('UPDATE party_rooms SET deck_gain=?,deck_updated=? WHERE id=?',
                      (float(gain), now, pid))
        elif action in ('set', 'add'):
            if isinstance(ids, (str, int)) or not isinstance(ids, (list, tuple)):
                raise Denied(400, 'A list of track ids is required')
            wanted = [integer(tid) for tid in ids]
            if len(wanted) > 200:
                raise Denied(409, 'Waiting queue is full')
            if action == 'set':
                clear_waiting(c, pid)
                for position, tid in enumerate(wanted):
                    entry = queue_entry(c, store, pid, actor, tid)
                    c.execute('UPDATE party_entries SET position=? WHERE id=?', (position, entry))
            else:
                for tid in wanted:
                    queue_entry(c, store, pid, actor, tid)
        elif action == 'clear':
            clear_waiting(c, pid)
        elif action == 'remove':
            ordering = waiting_entries(c, pid)
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(ordering):
                raise Denied(400, 'Index out of range')
            entry = ordering[index]['id']
            c.execute('DELETE FROM party_votes WHERE entry=?', (entry,))
            c.execute('DELETE FROM party_entries WHERE id=?', (entry,))
        elif action == 'shuffle':
            ordering = waiting_entries(c, pid)
            order = list(range(len(ordering)))
            random.shuffle(order)
            for position, row in zip(order, ordering):
                c.execute('UPDATE party_entries SET position=? WHERE id=?', (position, row['id']))
        return {'status': deck_status(c, pid, now)}


def control(store, member, pid, op, host_token=None, expected=None, guest_id=None, position=None, playing=None, message_id=None):
    member_identity(member)
    with transaction(store) as c:
        r, now = room(c, pid), time.time()
        if op == 'close':
            c.execute('UPDATE party_rooms SET closed=1 WHERE id=?', (pid,))
        elif op == 'revoke':
            # Revoking the invitation alone leaves the person's queue rows and votes in the room,
            # which is not what a moderator who removes somebody means. Their current track stays:
            # it is already playing, and cutting it is `next`/skip, not a removal.
            if not isinstance(guest_id, str) or not guest_id:
                raise Denied(400, 'A guest id is required')
            row = c.execute('SELECT id FROM party_guests WHERE id=? AND party=?', (guest_id, pid)).fetchone()
            if not row:
                raise Denied(404, 'No such guest in this room')
            voter = 'g:' + guest_id
            c.execute('UPDATE party_guests SET revoked=1 WHERE id=? AND party=?', (guest_id, pid))
            c.execute('DELETE FROM party_sessions WHERE guest=?', (guest_id,))
            c.execute('DELETE FROM party_presence WHERE party=? AND actor=?', (pid, voter))
            votes = c.execute('DELETE FROM party_votes WHERE voter=? AND entry IN'
                              ' (SELECT id FROM party_entries WHERE party=?)', (voter, pid)).rowcount
            entries = c.execute("DELETE FROM party_entries WHERE party=? AND added_by=?"
                                " AND status='waiting'", (pid, voter)).rowcount
            return {'ok': True, 'entries': entries, 'votes': votes}
        elif op == 'claim':
            if r['host_until'] > now:
                raise Denied(409, 'An aux host is already active')
            token = secrets.token_urlsafe(32)
            c.execute('UPDATE party_rooms SET host_digest=?,host_member=?,host_until=? WHERE id=?', (digest(token), member['id'], now+30, pid))
            return {'host_token': token}
        elif op in ('heartbeat', 'next', 'release'):
            if not host_token or not secrets.compare_digest(digest(host_token), r['host_digest'] or '') or r['host_until'] <= now or r['host_member'] != member['id']:
                raise Denied(403, 'Active aux host required')
            if op == 'release':
                c.execute('UPDATE party_rooms SET host_until=0,host_digest=NULL WHERE id=?', (pid,))
            else:
                c.execute('UPDATE party_rooms SET host_until=? WHERE id=?', (now+30, pid))
                if op == 'heartbeat' and position is not None:
                    if isinstance(position, bool) or not isinstance(position, (int, float)) \
                            or not 0 <= float(position) < 24 * 3600:
                        raise Denied(400, 'Invalid playback position')
                    c.execute('UPDATE party_rooms SET deck_position=? WHERE id=?',
                              (float(position), pid))
                if op == 'heartbeat' and playing is not None:
                    if not isinstance(playing, bool):
                        raise Denied(400, 'Invalid playback state')
                    # The device making sound is the only honest source for this flag: a remote
                    # `stop` must be able to stop it, and a local pause must not be undone by the
                    # next poll. The heartbeat therefore reports what the audio element is doing.
                    c.execute('UPDATE party_rooms SET deck_playing=? WHERE id=?',
                              (1 if playing else 0, pid))
                if op == 'next':
                    c.execute('UPDATE party_rooms SET deck_position=0 WHERE id=?', (pid,))
                    advance(c, store, pid, expected)
        elif op == 'unsend':
            # A moderator's other half: taking a message down. Deleting in place is honest —
            # the log is a room record, not an archive — and the cursor the page polls with
            # still moves forward, so nothing is re-appended.
            if isinstance(message_id, bool) or not isinstance(message_id, (int, str)):
                raise Denied(400, 'A message id is required')
            try:
                mid = int(message_id)
            except (TypeError, ValueError):
                raise Denied(400, 'A message id is required')
            gone = c.execute('DELETE FROM party_chat WHERE id=? AND party=?', (mid, pid)).rowcount
            if not gone:
                raise Denied(404, 'No such message in this room')
            return {'ok': True, 'id': mid}
        else:
            raise Denied(400, 'Unknown party control')
    return {'ok': True}


def room_for(store, actor, now=None):
    """The open room this identity last had open, or None.

    A Subsonic client carries no room id, so "the member's current room" has to come from the
    server. Reading a room is what writes presence (`state`), which makes this table the only
    honest answer: the newest room this actor is still counted as being in. `PRESENCE_TTL` is the
    room's own rule for who is there, so a member who left the party is not still in it.
    """
    now = time.time() if now is None else now
    with store.lock:
        row = store.conn.execute(
            "SELECT p.party FROM party_presence p JOIN party_rooms r ON r.id=p.party"
            " WHERE p.actor=? AND p.seen>=? AND r.closed=0 AND r.expires>?"
            " ORDER BY p.seen DESC LIMIT 1", (actor, now - PRESENCE_TTL, now)).fetchone()
        return row[0] if row else None


def control_room(store, member_id, now=None):
    """The room a member may steer from a client.

    Presence is the first answer (the room is open in front of them). A phone app has no room
    window, though, so the fallback is the room they created or whose deck they hold — never
    somebody else's room they happen to be able to reach.
    """
    now = time.time() if now is None else now
    with store.lock:
        row = store.conn.execute(
            "SELECT p.party FROM party_presence p JOIN party_rooms r ON r.id=p.party"
            " WHERE p.actor=? AND p.seen>=? AND r.closed=0 AND r.expires>?"
            " ORDER BY p.seen DESC LIMIT 1", ('m:' + member_id, now - PRESENCE_TTL, now)).fetchone()
        if row:
            return row[0]
        row = store.conn.execute(
            "SELECT id FROM party_rooms WHERE closed=0 AND expires>? AND (creator=? OR host_member=?)"
            " ORDER BY expires DESC LIMIT 1", (now, member_id, member_id)).fetchone()
        return row[0] if row else None


def messages(store, pid, since=0.0, limit=200):
    """Chat newer than a wall-clock time, oldest first — the order Subsonic's `since` asks for."""
    with store.lock:
        room(store.conn, pid)
        rows = store.conn.execute(
            'SELECT id,display_name,profile_icon,body,created FROM party_chat'
            ' WHERE party=? AND created>=? ORDER BY id LIMIT ?',
            (pid, float(since or 0), int(limit))).fetchall()
        return [dict(r) for r in rows]


def chat(store, pid, actor, payload=None, after='0'):
    if payload is not None:
        if set(payload) != {'body'} or not isinstance(payload['body'], str) or not 1 <= len(payload['body'].strip()) <= 1000:
            raise Denied(400, 'Send only a message body of 1–1000 characters')
        with transaction(store) as c:
            room(c, pid)
            if actor.startswith('g:'):
                author = c.execute('SELECT display_name,profile_icon FROM party_guests WHERE id=? AND party=? AND revoked=0 AND expires>?', (actor[2:], pid, time.time())).fetchone()
            else:
                author = c.execute("SELECT display_name,profile_icon FROM members WHERE id=? AND role IN ('member','admin')", (actor[2:],)).fetchone()
            if not author:
                raise Denied(403, 'Party identity no longer active')
            cid = c.execute('INSERT INTO party_chat(party,author,display_name,profile_icon,body,created) VALUES(?,?,?,?,?,?)',
                            (pid, actor, author['display_name'], author['profile_icon'], payload['body'].strip(), time.time())).lastrowid
            c.execute('DELETE FROM party_chat WHERE party=? AND id NOT IN (SELECT id FROM party_chat WHERE party=? ORDER BY id DESC LIMIT 500)', (pid, pid))
        return {'id': cid}
    if not re.fullmatch(r'[0-9]{1,18}', str(after)):
        raise Denied(400, 'Invalid chat cursor')
    with store.lock:
        room(store.conn, pid)
        rows = store.conn.execute('SELECT id,display_name,profile_icon,body,created FROM party_chat WHERE party=? AND id>? ORDER BY id LIMIT 101', (pid, int(after))).fetchall()
        page = rows[:100]
        return {'messages': [dict(r) for r in page], 'cursor': page[-1]['id'] if page else int(after), 'has_more': len(rows) > 100}


def dispatch(handler, store, security):
    """Call exclusively for /api/party or /api/party/* BEFORE legacy auth bypass.

    Denied propagates to Handler.boundary's existing error handler. POST framing
    must already have been checked. All success responses are no-store via json.
    """
    schema(store)
    path = urlsplit(handler.path).path
    if handler.command not in ('GET', 'POST'):
        raise Denied(405, 'Method not allowed')
    if handler.command == 'POST':
        security.csrf(handler)
    security.limit(security.client_ip(handler), 'party-ip', 180)
    payload = handler.body() if handler.command == 'POST' else {}
    if not isinstance(payload, dict):
        raise Denied(400, 'JSON object required')
    if path == '/api/party/exchange' and handler.command == 'POST':
        security.limit(security.client_ip(handler), 'party-exchange', 15)
        result = exchange(store, payload.get('invite'), payload.get('display_name'), payload.get('profile_icon'))
        sid = result.pop('session')
        handler.send_response(200)
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Set-Cookie', f'{security.cookie_name(handler, COOKIE)}={sid}; '
                            f'{security.cookie_attrs(handler, "Strict")}; '
                            f'Max-Age={max(0, int(result["expires"]-time.time()))}')
        import json
        data = json.dumps(result).encode()
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return
    if path == '/api/party' and handler.command == 'GET':
        security.authenticate(handler, store)
        with store.lock:
            result = {'parties': [dict(r) for r in store.conn.execute('SELECT id,title,expires FROM party_rooms WHERE closed=0 AND expires>? ORDER BY expires LIMIT 100', (time.time(),))]}
        handler.json(result)
        return
    if path == '/api/party' and handler.command == 'POST':
        m = security.authenticate(handler, store)
        handler.json(create(store, m, payload.get('title', 'Party'), payload.get('hours', 24)))
        return
    match = re.fullmatch(r'/api/party/([A-Za-z0-9_-]{16})'
                         r'(?:/(search|queue|queue-set|sets|vote|skip|invite|control|chat|jukebox))?',
                         path)
    if not match:
        raise Denied(404, 'Not found')
    pid, action = match.groups()
    if action in ('invite', 'control'):
        m = security.authenticate(handler, store)
        if handler.command != 'POST':
            raise Denied(405, 'Method not allowed')
        if action == 'invite':
            result = invite(store, m, pid)
        else:
            # The host's heartbeat carries where the track is, so `deck.position` is measured
            # rather than guessed. Members (not guests) steer the deck.
            result = control(store, m, pid, payload.get('op'), payload.get('host_token'),
                             payload.get('expected'), payload.get('guest_id'),
                             payload.get('position'), payload.get('playing'),
                             payload.get('message_id'))
    elif action == 'jukebox':
        # Membership is required here even when a guest cookie is present: guests shape the
        # queue with votes, not by replacing it.
        m = security.authenticate(handler, store)
        security.limit(m['id'], 'party-actions', 90)
        if handler.command == 'GET':
            result = jukebox(store, pid, m, 'get')
        else:
            result = jukebox(store, pid, m, payload.get('action'), payload.get('ids'),
                             payload.get('index'), payload.get('gain'))
    else:
        # A party cookie is never accepted by Security.authenticate or other APIs.
        sid = security.cookie(handler, security.cookie_name(handler, COOKIE))
        member = None
        if sid and not security.cookie(handler, security.cookie_name(handler, '__Host-hl_session')):
            identity = guest(store, sid, pid)
            actor = identity['id']
        else:
            m = security.authenticate(handler, store)
            member = m
            actor = member_identity(m)
            identity = {k: m.get(k) for k in ('display_name', 'profile_icon')}
        security.limit(actor, 'party-actions', 90)
        if action is None and handler.command == 'GET':
            result = state(store, pid, actor)
            result['viewer'] = identity
        elif action == 'search' and handler.command == 'GET':
            result = search(store, pid, parse_qs(urlsplit(handler.path).query).get('q', [''])[0])
        elif action == 'sets' and handler.command == 'GET':
            result = sets(store, pid, member)
        elif action == 'queue-set' and handler.command == 'POST':
            # A guest may add a song; shuffling two hundred in is shaping the room's whole
            # evening, which is what membership and votes are for.
            if member is None:
                raise Denied(403, 'Members start sets; guests add songs and vote')
            result = queue_set(store, pid, actor, payload.get('kind'), payload.get('id'))
        elif action == 'chat':
            if handler.command == 'POST':
                security.limit(actor, 'party-chat', 20)
                result = chat(store, pid, actor, payload=payload)
            else:
                result = chat(store, pid, actor, after=parse_qs(urlsplit(handler.path).query).get('after', ['0'])[0])
        elif action == 'queue' and handler.command == 'POST':
            result = add(store, pid, actor, payload.get('track_id'))
        elif action == 'vote' and handler.command == 'POST':
            result = vote(store, pid, actor, payload.get('entry_id'), payload.get('value'))
        elif action == 'skip' and handler.command == 'POST':
            result = skip(store, pid, actor, payload.get('expected'))
        else:
            raise Denied(405, 'Method not allowed')
    handler.json(result)
