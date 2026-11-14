"""A Repo object's discussion: comments, votes, and the counts a grid needs.

Split out of `social/discussion.py` (SOCIAL-001). Likes are `social/likes.py`; the two share this
file's `schema`, `validate_target` and `summary`, which is why they are here rather than duplicated.
`social.service` is the entry point; `social.discussion` still re-exports everything for the old
imports, and no request object appears in this file (`ARCH-005`, `ARCH-006`).
"""
import hashlib
import json
import re
import unicodedata
from security import Denied

MAX_TEXT = 2000
PAGE_SIZE = 30


def album_id(track):
    # A normalized artist + album tuple, never an artist-only display header.
    # Provider-independent grouping for imports and cross-provider copies alike.
    def norm(value):
        return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())
    artist, album = norm(track['artist_name']), norm(track['album'])
    if not artist or not album:
        return None
    return hashlib.sha256(json.dumps([artist, album], ensure_ascii=False).encode()).hexdigest()


def annotate(tracks):
    for track in tracks:
        track['album_id'] = album_id(track)
    return tracks


def schema(store):
    store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS social_comments (
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target TEXT NOT NULL,
          member_id TEXT NOT NULL, body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 2000),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
        CREATE INDEX IF NOT EXISTS social_comment_target ON social_comments(kind,target,id);
        CREATE TABLE IF NOT EXISTS social_comment_likes (
          comment_id INTEGER NOT NULL REFERENCES social_comments(id) ON DELETE CASCADE,
          member_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(comment_id, member_id));
        CREATE INDEX IF NOT EXISTS social_comment_like_comment ON social_comment_likes(comment_id);
        CREATE TABLE IF NOT EXISTS social_votes (
          kind TEXT NOT NULL, target TEXT NOT NULL, member_id TEXT NOT NULL,
          value INTEGER NOT NULL CHECK(value IN (-1,1)), PRIMARY KEY(kind,target,member_id));
    ''')


def validate_target(store, kind, target):
    if kind in ('track', 'playlist'):
        if not re.fullmatch(r'[1-9][0-9]{0,18}', target):
            raise Denied(400, 'Invalid target')
        table = 'tracks' if kind == 'track' else 'playlists'
        exists = store.conn.execute(f'SELECT 1 FROM {table} WHERE id=?', (int(target),)).fetchone()
    elif kind == 'album' and re.fullmatch(r'[a-f0-9]{64}', target):
        exists = any(album_id(dict(t)) == target for t in store.conn.execute('SELECT artist_name,album FROM tracks'))
    else:
        raise Denied(400, 'Invalid target')
    if not exists:
        raise Denied(404, 'Target not found')


def summary(store, kind, target, member):
    row = store.conn.execute('''SELECT COALESCE(SUM(value),0) score,
        COALESCE(SUM(value=1),0) up, COALESCE(SUM(value=-1),0) down
        FROM social_votes WHERE kind=? AND target=?''', (kind, target)).fetchone()
    own = store.conn.execute('SELECT value FROM social_votes WHERE kind=? AND target=? AND member_id=?', (kind,target,member['id'])).fetchone()
    count = store.conn.execute('SELECT COUNT(*) FROM social_comments WHERE kind=? AND target=?', (kind,target)).fetchone()[0]
    # Votes are never anonymous: name the voters so the score has faces behind it.
    voters = [dict(r) for r in store.conn.execute(
        '''SELECT v.member_id AS id, v.value AS value,
                  m.display_name AS name, m.profile_icon AS icon
             FROM social_votes v LEFT JOIN members m ON m.id=v.member_id
            WHERE v.kind=? AND v.target=? ORDER BY v.rowid DESC LIMIT 24''',
        (kind, target)).fetchall()]
    for voter in voters:
        voter['name'] = (voter['name'] or '').strip() or 'Member'
        voter['icon'] = voter['icon'] or 'music'
    return dict(score=row['score'], up=row['up'], down=row['down'], vote=own[0] if own else 0,
                comments=count, voters=voters)


def batch(store, kind, targets, member, latest: int = 0):
    """Counts for many targets at once, so a grid does not fetch per card.

    Unknown targets simply come back as zeroes; the caller already has a
    validated list to render and a summary cannot mutate anything.
    """
    if kind not in ('track', 'playlist', 'album'):
        raise Denied(400, 'Invalid kind')
    pattern = r'[a-f0-9]{64}' if kind == 'album' else r'[1-9][0-9]{0,18}'
    wanted = []
    for target in targets:
        target = str(target)
        if re.fullmatch(pattern, target) and target not in wanted:
            wanted.append(target)
        if len(wanted) >= 300:
            break
    if not wanted:
        return {}
    marks = ','.join('?' * len(wanted))
    empty = dict(score=0, up=0, down=0, vote=0, comments=0)
    out = {target: dict(empty) for target in wanted}
    with store.lock:
        schema(store)
        for row in store.conn.execute(
                'SELECT target, COALESCE(SUM(value),0) score, COALESCE(SUM(value=1),0) up,'
                ' COALESCE(SUM(value=-1),0) down FROM social_votes WHERE kind=? AND target IN (%s)'
                ' GROUP BY target' % marks, (kind, *wanted)):
            out[row['target']].update(score=row['score'], up=row['up'], down=row['down'])
        for row in store.conn.execute(
                'SELECT target, COUNT(*) n FROM social_comments WHERE kind=? AND target IN (%s)'
                ' GROUP BY target' % marks, (kind, *wanted)):
            out[row['target']]['comments'] = row['n']
        for row in store.conn.execute(
                'SELECT target, value FROM social_votes WHERE kind=? AND member_id=? AND target IN (%s)'
                % marks, (kind, member['id'], *wanted)):
            out[row['target']]['vote'] = row['value']
        # A card can show the newest few comments as floating bubbles without a
        # second request per album. Text is returned as data; the DOM sets it
        # with textContent, never as markup.
        if latest > 0:
            kept = {target: 0 for target in wanted}
            for row in store.conn.execute(
                    'SELECT target, body, member_id FROM social_comments WHERE kind=? AND target IN (%s)'
                    ' ORDER BY id DESC' % marks, (kind, *wanted)):
                target = row['target']
                if kept[target] >= min(latest, 5):
                    continue
                kept[target] += 1
                out[target].setdefault('recent', []).append(
                    {'body': row['body'], 'member': row['member_id']})
    return out


def comments(store, kind, target, member, after: int = 0) -> dict:
    """One page of comments with their likes, plus the target's own summary."""
    with store.lock:
        validate_target(store, kind, target)
        schema(store)
        rows = [dict(r) for r in store.conn.execute('''SELECT c.id,c.member_id,c.body,c.created_at,c.updated_at,
            m.display_name,m.profile_icon
            FROM social_comments c LEFT JOIN members m ON m.id=c.member_id
            WHERE c.kind=? AND c.target=? AND c.id>? ORDER BY c.id LIMIT ?''',
            (kind, target, after, PAGE_SIZE + 1))]
        more = len(rows) > PAGE_SIZE
        rows = rows[:PAGE_SIZE]
        liked = set()
        if rows:
            ids = [r['id'] for r in rows]
            marks = ','.join('?' for _ in ids)
            for like_row in store.conn.execute(
                    f'SELECT comment_id FROM social_comment_likes WHERE member_id=?'
                    f' AND comment_id IN ({marks})', (member['id'], *ids)):
                liked.add(like_row['comment_id'])
            counts = {r['comment_id']: r['n'] for r in store.conn.execute(
                f'SELECT comment_id, COUNT(*) n FROM social_comment_likes'
                f' WHERE comment_id IN ({marks}) GROUP BY comment_id', ids)}
        else:
            counts = {}
        for row in rows:
            row['likes'] = counts.get(row['id'], 0)
            row['liked'] = row['id'] in liked
            row['can_edit'] = member['role'] == 'admin' or row['member_id'] == member['id']
            # Comments carry who wrote them, so the name opens their profile instead of
            # printing a raw member id.
            row['name'] = (row.pop('display_name') or '').strip() or 'Member'
            row['icon'] = row.pop('profile_icon') or 'music'
        return dict(summary(store, kind, target, member), items=rows,
                    next_after=rows[-1]['id'] if more else None)


def vote(store, kind, target, member, value) -> dict:
    """Set, switch or clear one member's vote. Returns the target's new summary."""
    if type(value) is not int or value not in (-1, 0, 1):
        raise Denied(400, 'Vote must be -1, 0 or 1')
    with store.lock:
        validate_target(store, kind, target)
        schema(store)
        with store.conn:
            if value == 0:
                store.conn.execute('DELETE FROM social_votes WHERE kind=? AND target=? AND member_id=?',
                                   (kind, target, member['id']))
            else:
                store.conn.execute('''INSERT INTO social_votes VALUES(?,?,?,?)
                    ON CONFLICT(kind,target,member_id) DO UPDATE SET value=excluded.value''',
                    (kind, target, member['id'], value))
        return summary(store, kind, target, member)


def comment(store, kind, target, member, *, body=None, comment_id=None, delete=False) -> dict:
    """Add, edit or delete a comment. Only the author or an administrator may moderate."""
    with store.lock:
        validate_target(store, kind, target)
        schema(store)
        if comment_id:
            row = store.conn.execute('SELECT member_id FROM social_comments WHERE id=? AND kind=? AND target=?',
                                     (int(comment_id), kind, target)).fetchone()
            if not row:
                raise Denied(404, 'Comment not found')
            if row[0] != member['id'] and member['role'] != 'admin':
                raise Denied(403, 'Only author or administrator may moderate')
        with store.conn:
            if comment_id and delete is True:
                store.conn.execute('DELETE FROM social_comments WHERE id=?', (int(comment_id),))
            else:
                text = body
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT or '\x00' in text:
                    raise Denied(400, 'Comment must contain 1–2000 text characters')
                if comment_id:
                    store.conn.execute('UPDATE social_comments SET body=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                                       (text.strip(), int(comment_id)))
                else:
                    store.conn.execute('INSERT INTO social_comments(kind,target,member_id,body) VALUES(?,?,?,?)',
                                       (kind, target, member['id'], text.strip()))
        return summary(store, kind, target, member)
