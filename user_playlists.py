"""`/api/playlists`: the HTTP edge for member-owned shared playlists (LIB-001).

The domain — validation, ownership, the one transaction, the ordered membership — lives in
`library/playlists.py`. This module keeps what needs a request: the route shape, the accepted
body keys, the rate limit, and the Activity event, which is written *after* the transaction so a
Feed write can never undo an edit. Nothing here downloads or saves audio.
"""
import re
from urllib.parse import urlparse

from security import Denied
from library import playlists as library_playlists

MAX_TRACKS = library_playlists.MAX_TRACKS
ids = library_playlists.ids
title = library_playlists.title
listing = library_playlists.listing


def dispatch(handler, store, security):
    member = security.authenticate(handler, store)
    handler.member = member
    path = urlparse(handler.path).path
    match = re.fullmatch(r'/api/playlists(?:/([1-9][0-9]*))?', path)
    if not match:
        raise Denied(404, 'Not found')
    pid = int(match[1]) if match[1] else None
    if pid is not None and pid > 9223372036854775807:
        raise Denied(404, 'Not found')
    if handler.command == 'GET':
        with store.lock:
            rows = listing(store, member['id'], pid)
        if pid is not None and not rows:
            raise Denied(404, 'No such playlist')
        handler.json({'playlists': rows} if pid is None else {'playlist': rows[0]})
        return
    if handler.command != 'POST':
        raise Denied(405, 'Method not allowed')
    security.limit(member['id'], 'playlist-write', 60)
    body = handler.body()
    if not isinstance(body, dict) or set(body) - {'title', 'track_ids', 'expected_track_ids'}:
        raise Denied(400, 'Only title, track_ids and expected_track_ids are accepted')
    name = title(body['title']) if 'title' in body else None
    tracks = ids(body['track_ids']) if 'track_ids' in body else None
    expected = ids(body['expected_track_ids']) if 'expected_track_ids' in body else None
    if pid is None and (name is None or expected is not None):
        raise Denied(400, 'New playlists require title; expected_track_ids is only for edits')
    if pid is not None and (not body or (tracks is not None and expected is None)):
        raise Denied(400, 'Membership edits require expected_track_ids from the last read')
    result = library_playlists.write(store, member['id'], pid, name, tracks, expected)
    # The Feed shows playlist news; written after the transaction so it can never
    # undo an edit.
    import activity
    activity.record(store,
                    activity.KIND_PLAYLIST_NEW if result['created'] else activity.KIND_PLAYLIST_EDIT,
                    member_id=member['id'], playlist_id=result['playlist']['id'],
                    detail=name or result['playlist'].get('title') or '')
    handler.json({'playlist': result['playlist']})