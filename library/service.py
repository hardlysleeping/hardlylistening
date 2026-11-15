"""A member's own library: what they chose to keep, and how the player sees it.

Personal state only. Acquisition, discovery and social signal belong to other domains
(`docs/server-domains.md`); this module owns the queries over the personal tables so the web API
and the Subsonic surface read the same thing instead of each keeping its own copy of the SQL.

Scope is deliberate: a member sees tracks they saved, tracks inside their own playlists, tracks in
their own folders, and their favourites. Nothing here can return a track outside that union, and
nothing here can see another member's rows.
"""
from security import Denied

# The union that defines "my library". Order matters for display, not for membership.
SCOPE = '''SELECT DISTINCT t.* FROM tracks t WHERE t.deleted=0 AND t.id IN
  (SELECT track_id FROM listening_tracks WHERE member_id=? UNION
   SELECT track_id FROM listening_playlist_tracks WHERE member_id=? UNION
   SELECT track_id FROM listening_folder_tracks WHERE member_id=? UNION
   SELECT track_id FROM listening_favorites WHERE member_id=?)'''


def schema(store):
    """Create the member-state tables this service touches, if a quiet database lacks them.

    `CREATE TABLE IF NOT EXISTS` throughout, so this is the same statement the original
    owner ran — not a migration. Favourites and album likes come from `preferences`, which
    already owns those two definitions.
    """
    import preferences
    preferences.schema(store)
    with store.lock:
        store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS listening_ratings (
          member_id TEXT NOT NULL, track_id INTEGER NOT NULL, rating INTEGER NOT NULL,
          PRIMARY KEY (member_id, track_id));
        CREATE TABLE IF NOT EXISTS listening_plays (
          member_id TEXT NOT NULL, track_id INTEGER NOT NULL, plays INTEGER NOT NULL DEFAULT 0,
          last_played REAL NOT NULL DEFAULT 0, PRIMARY KEY (member_id, track_id));
        CREATE TABLE IF NOT EXISTS listening_bookmarks (
          member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position_ms INTEGER NOT NULL DEFAULT 0, comment TEXT NOT NULL DEFAULT '',
          created REAL NOT NULL, updated REAL NOT NULL,
          PRIMARY KEY (member_id, track_id));
        ''')
        store.conn.commit()


def catalog(store, uid):
    """Every track in the member's scope, in album order."""
    import preferences
    preferences.schema(store)
    with store.lock:
        rows = store.conn.execute(SCOPE + ' ORDER BY t.artist_name,t.album,t.track_number,t.id',
                                 (uid, uid, uid, uid)).fetchall()
    return [dict(r) for r in rows]


def albums(store, uid) -> list:
    """The member's albums, grouped from the same scope the song list uses.

    A projection of `catalog`, not another query definition: the union lives in `SCOPE`, so an
    album can never contain a song the song list would not return.
    """
    schema(store)
    with store.lock:
        rows = store.conn.execute(
            'SELECT artist_name AS artist, album, COUNT(*) AS song_count,'
            ' COALESCE(SUM(duration),0) AS duration, MAX(added) AS added'
            ' FROM (' + SCOPE + ') GROUP BY artist_name, album'
            ' ORDER BY artist_name, album', (uid, uid, uid, uid)).fetchall()
    return [dict(r) for r in rows]


def artists(store, uid) -> list:
    """The member's artists, with the album count the artist wall sorts by."""
    schema(store)
    with store.lock:
        rows = store.conn.execute(
            'SELECT artist_name AS name, COUNT(*) AS song_count,'
            ' COUNT(DISTINCT album) AS album_count, COALESCE(SUM(duration),0) AS duration'
            ' FROM (' + SCOPE + ') GROUP BY artist_name'
            ' ORDER BY artist_name', (uid, uid, uid, uid)).fetchall()
    return [dict(r) for r in rows]


def in_scope(store, uid, track_id) -> bool:
    """One id through the same union, so a caller never has to fetch the whole catalogue."""
    with store.lock:
        row = store.conn.execute('SELECT 1 FROM (' + SCOPE + ') WHERE id=? LIMIT 1',
                                 (uid, uid, uid, uid, int(track_id))).fetchone()
    return row is not None


def starred(store, uid) -> set:
    schema(store)
    with store.lock:
        return {r[0] for r in store.conn.execute(
            'SELECT track_id FROM listening_favorites WHERE member_id=?', (uid,))}


def ratings(store, uid) -> dict:
    schema(store)
    with store.lock:
        return {r[0]: r[1] for r in store.conn.execute(
            'SELECT track_id, rating FROM listening_ratings WHERE member_id=?', (uid,))}


def plays(store, uid) -> dict:
    """{track id: (play count, last played)} — the scrobble ledger, per member."""
    schema(store)
    with store.lock:
        return {r[0]: (r[1], r[2]) for r in store.conn.execute(
            'SELECT track_id, plays, last_played FROM listening_plays WHERE member_id=?', (uid,))}


def album_added(store) -> dict:
    """{(artist, album): newest added time} — the album wall's sort key.

    Deleted songs do not age an album: a tombstone is not a reason for its shelf to
    jump to the top.
    """
    with store.lock:
        return {(r[0] or '', r[1] or ''): r[2] for r in store.conn.execute(
            'SELECT artist_name, album, MAX(added) FROM tracks WHERE deleted=0'
            ' GROUP BY artist_name, album')}


def bookmarks(store, uid) -> dict:
    schema(store)
    with store.lock:
        return {r[0]: r[1] for r in store.conn.execute(
            'SELECT track_id, position_ms FROM listening_bookmarks WHERE member_id=?', (uid,))}


def bookmark_rows(store, uid) -> list:
    """Every bookmark this member holds, newest change first, comment included."""
    schema(store)
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            'SELECT track_id,position_ms,comment,created,updated FROM listening_bookmarks'
            ' WHERE member_id=? ORDER BY updated DESC,track_id', (uid,))]


# -- writes ------------------------------------------------------------------
# The same rows the reads above answer, so a toggle and a listing cannot disagree about
# what a member starred or where they stopped listening.

def star(store, uid, track_id: int, on: bool) -> None:
    schema(store)
    with store.lock:
        if on:
            store.conn.execute('INSERT OR IGNORE INTO listening_favorites VALUES(?,?)',
                               (uid, int(track_id)))
        else:
            store.conn.execute('DELETE FROM listening_favorites WHERE member_id=? AND track_id=?',
                               (uid, int(track_id)))
        store.conn.commit()


def like_album(store, uid, album_key: str, on: bool) -> None:
    schema(store)
    with store.lock:
        if on:
            store.conn.execute('INSERT OR IGNORE INTO listening_album_likes VALUES(?,?)',
                               (uid, album_key))
        else:
            store.conn.execute('DELETE FROM listening_album_likes WHERE member_id=? AND album_key=?',
                               (uid, album_key))
        store.conn.commit()


def rate(store, uid, track_id: int, rating: int) -> None:
    """0 clears the rating; 1–5 stores it."""
    rating = max(0, min(int(rating), 5))
    schema(store)
    with store.lock:
        if rating:
            store.conn.execute('INSERT OR REPLACE INTO listening_ratings VALUES(?,?,?)',
                               (uid, int(track_id), rating))
        else:
            store.conn.execute('DELETE FROM listening_ratings WHERE member_id=? AND track_id=?',
                               (uid, int(track_id)))
        store.conn.commit()


def record_play(store, uid, track_id: int, when: float | None = None) -> None:
    """One scrobble: the count goes up and the track becomes the member's last played."""
    import time
    when = time.time() if when is None else float(when)
    schema(store)
    with store.lock:
        store.conn.execute(
            'INSERT INTO listening_plays (member_id,track_id,plays,last_played) VALUES (?,?,1,?)'
            ' ON CONFLICT(member_id,track_id) DO UPDATE SET plays=plays+1,'
            ' last_played=excluded.last_played', (uid, int(track_id), when))
        store.conn.commit()


def save_bookmark(store, uid, track_id: int, position_ms: int, comment: str = '',
                  when: float | None = None) -> None:
    import time
    when = time.time() if when is None else float(when)
    schema(store)
    with store.lock:
        exists = store.conn.execute(
            'SELECT 1 FROM listening_bookmarks WHERE member_id=? AND track_id=?',
            (uid, int(track_id))).fetchone()
        if exists:
            store.conn.execute(
                'UPDATE listening_bookmarks SET position_ms=?,comment=?,updated=?'
                ' WHERE member_id=? AND track_id=?',
                (int(position_ms), comment, when, uid, int(track_id)))
        else:
            store.conn.execute(
                'INSERT INTO listening_bookmarks(member_id,track_id,position_ms,comment,created,updated)'
                ' VALUES(?,?,?,?,?,?)',
                (uid, int(track_id), int(position_ms), comment, when, when))
        store.conn.commit()


def remove_bookmark(store, uid, track_id: int) -> None:
    schema(store)
    with store.lock:
        store.conn.execute('DELETE FROM listening_bookmarks WHERE member_id=? AND track_id=?',
                           (uid, int(track_id)))
        store.conn.commit()


def playlists(store, uid) -> list:
    """The member's saved playlists, favourites included when it has been saved."""
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            'SELECT p.id,p.title FROM playlists p JOIN listening_playlists s ON p.id=s.playlist_id '
            'WHERE s.member_id=? ORDER BY p.id', (uid,))]


def playlist_tracks(store, uid) -> list:
    """Membership of the member's playlists, as positions over canonical track ids."""
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            'SELECT playlist_id,track_id,position FROM listening_playlist_tracks '
            'WHERE member_id=? ORDER BY position,track_id', (uid,))]


def playlist(store, uid, playlist_id) -> dict | None:
    """One playlist the member saved, with its tracks in listening order.

    `None` means "not saved by this member", which is the same answer as "does not exist":
    a playlist somebody else saved is not theirs to read here.
    """
    with store.lock:
        row = store.conn.execute(
            'SELECT p.id,p.title FROM playlists p JOIN listening_playlists s ON p.id=s.playlist_id'
            ' WHERE s.member_id=? AND p.id=?', (uid, int(playlist_id))).fetchone()
        if not row:
            return None
        tracks = [dict(r) for r in store.conn.execute(
            'SELECT t.* FROM listening_playlist_tracks l JOIN tracks t ON t.id=l.track_id'
            ' WHERE l.member_id=? AND l.playlist_id=? AND t.deleted=0'
            ' ORDER BY l.position,t.id', (uid, int(playlist_id))).fetchall()]
    return {'id': row['id'], 'title': row['title'], 'tracks': tracks}


def favorites(store, uid) -> list:
    """Favourited track rows, newest id last — the order the Favourites playlist uses."""
    schema(store)
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            'SELECT t.* FROM tracks t JOIN listening_favorites f ON t.id=f.track_id '
            'WHERE f.member_id=? ORDER BY t.id', (uid,))]


def devices(store, uid) -> list:
    """The member's Subsonic devices. No secret ever leaves this call."""
    with store.lock:
        return [dict(r) for r in store.conn.execute(
            'SELECT id,label,revoked,created FROM listening_devices WHERE member_id=? '
            'ORDER BY created,id', (uid,))]