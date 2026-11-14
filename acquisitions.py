"""Who may start acquisition work on this server (HL-219).

Acquisition is spending: this server reaches out to a provider, runs yt-dlp, writes
files to disk and takes a slot in the one queue. Everything else a member can do —
listen, save, star, build a playlist, steer the party deck — costs nothing of that.
So it is a grant of its own, and the check reads the grant, never the role:

* membership does not imply it,
* access to the player does not imply it,
* being an administrator does not imply it.

The grant table is the only source of truth. `acquisitions` is one row per member who
may ask for work; revoking is deleting the row, which takes effect on the next request
because nothing caches the answer.

**The one migration.** When the table is created for the first time, every existing
administrator is granted a row marked `granted_by='migration'`. That is a data
migration, not a rule: it keeps the person who already had the ability from losing it
on upgrade. It is visible in `grants()` and can be revoked like any other. The backfill
runs only on creation, so revoking everybody does not silently re-grant on the next
boot.

Nothing here starts work. `may_acquire()` answers a question; the routes ask it.
"""

from __future__ import annotations

import time

SCHEMA = """\
CREATE TABLE IF NOT EXISTS acquisitions (
  member_id TEXT PRIMARY KEY,
  granted_by TEXT NOT NULL DEFAULT '',
  granted_at REAL NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT ''
);"""


SEEDED = "CREATE TABLE IF NOT EXISTS acquisitions_seeded (seeded INTEGER PRIMARY KEY)"

SEED_NOTE = 'existing administrator when acquisition became a grant'


def schema(store) -> None:
    """Create the table; grant the administrators that existed before it, once.

    The marker is its own row, not the table's existence: a first request can arrive
    before any member exists (a health check, an anonymous read), and the grant should
    still land for the administrator who is created a moment later. Once the marker is
    set, revoking is final — nothing re-grants on a later boot.
    """
    with store.lock:
        store.conn.execute(SCHEMA)
        store.conn.execute(SEEDED)
        seeded = store.conn.execute('SELECT 1 FROM acquisitions_seeded').fetchone()
        if not seeded:
            has_members = store.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='members'").fetchone()
            admins = 0
            if has_members:
                admins = store.conn.execute(
                    "SELECT COUNT(*) FROM members WHERE role='admin'").fetchone()[0]
            if admins:
                store.conn.execute(
                    "INSERT OR IGNORE INTO acquisitions(member_id, granted_by, granted_at, note)"
                    " SELECT id, 'migration', ?, ? FROM members WHERE role='admin'",
                    (time.time(), SEED_NOTE))
                store.conn.execute('INSERT INTO acquisitions_seeded(seeded) VALUES(1)')
        store.conn.commit()


def may_acquire(store, member) -> bool:
    """True only when this member holds a grant. No role is consulted."""
    if not member:
        return False
    schema(store)
    with store.lock:
        row = store.conn.execute('SELECT 1 FROM acquisitions WHERE member_id=?',
                                 (str(member['id']),)).fetchone()
    return bool(row)


def grant(store, member_id: str, by: str = '', note: str = '') -> bool:
    """Give one member the grant. Idempotent; answers whether it changed anything."""
    schema(store)
    member_id = str(member_id)
    with store.lock:
        known = store.conn.execute('SELECT 1 FROM members WHERE id=?', (member_id,)).fetchone()
        if not known:
            raise ValueError('no such member')
        prior = store.conn.execute('SELECT 1 FROM acquisitions WHERE member_id=?',
                                   (member_id,)).fetchone()
        store.conn.execute(
            'INSERT INTO acquisitions(member_id, granted_by, granted_at, note) VALUES(?,?,?,?)'
            ' ON CONFLICT(member_id) DO UPDATE SET granted_by=excluded.granted_by,'
            ' granted_at=excluded.granted_at, note=excluded.note',
            (member_id, str(by or ''), time.time(), str(note or '')))
        store.conn.commit()
    return prior is None


def revoke(store, member_id: str) -> bool:
    """Take the grant away. Answers whether a row was removed."""
    schema(store)
    with store.lock:
        cur = store.conn.execute('DELETE FROM acquisitions WHERE member_id=?', (str(member_id),))
        store.conn.commit()
    return cur.rowcount > 0


def grants(store) -> list[dict]:
    """Who may acquire, with the name beside the id so a person can read it."""
    schema(store)
    with store.lock:
        rows = store.conn.execute(
            "SELECT a.member_id, a.granted_by, a.granted_at, a.note,"
            "       COALESCE(m.display_name, '') AS name"
            "  FROM acquisitions a LEFT JOIN members m ON m.id = a.member_id"
            " ORDER BY name, a.member_id").fetchall()
    return [dict(row) for row in rows]