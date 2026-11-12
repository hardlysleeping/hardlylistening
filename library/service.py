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
SCOPE = '''SELECT DISTINCT t.* FROM tracks t WHERE t.id IN
  (SELECT track_id FROM listening_tracks WHERE member_id=? UNION
   SELECT track_id FROM listening_playlist_tracks WHERE member_id=? UNION
   SELECT track_id FROM listening_folder_tracks WHERE member_id=? UNION
   SELECT track_id FROM listening_favorites WHERE member_id=?)'''


def catalog(store, uid):
    """Every track in the member's scope, in album order."""
    import preferences
    preferences.schema(store)
    with store.lock:
        rows = store.conn.execute(SCOPE + ' ORDER BY t.artist_name,t.album,t.track_number,t.id',
                                 (uid, uid, uid, uid)).fetchall()
    return [dict(r) for r in rows]


def in_scope(store, uid, track_id) -> bool:
    """One id through the same union, so a caller never has to fetch the whole catalogue."""
    with store.lock:
        row = store.conn.execute('SELECT 1 FROM (' + SCOPE + ') WHERE id=? LIMIT 1',
                                 (uid, uid, uid, uid, int(track_id))).fetchone()
    return row is not None


def starred(store, uid) -> set:
    with store.lock:
        return {r[0] for r in store.conn.execute(
            'SELECT track_id FROM listening_favorites WHERE member_id=?', (uid,))}


def ratings(store, uid) -> dict:
    with store.lock:
        return {r[0]: r[1] for r in store.conn.execute(
            'SELECT track_id, rating FROM listening_ratings WHERE member_id=?', (uid,))}


def bookmarks(store, uid) -> dict:
    with store.lock:
        return {r[0]: r[1] for r in store.conn.execute(
            'SELECT track_id, position_ms FROM listening_bookmarks WHERE member_id=?', (uid,))}


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


def favorites(store, uid) -> list:
    """Favourited track rows, newest id last — the order the Favourites playlist uses."""
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