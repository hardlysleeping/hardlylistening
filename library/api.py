"""The HTTP adapter for a member's saved scope and folders (LIB-001).

`listening.py` routes the two paths here and does nothing else with them: this module reads the
body, keeps the refusals the endpoints already answered with, calls one domain function, and writes
the same JSON as before. The rules live beside it — `library/saves.py` (what is saved) and
`library/folders.py` (the tree) — so neither has to know what a request looks like.

The caller is already authenticated by the time it gets here (`handler.member`); the `Security`
object is passed in rather than imported so the rate limit is counted on the same instance the
server authenticated with (tests inject their own).
"""
from security import Denied

from library import folders as folder_rules
from library import saves as save_rules


def saves(handler, store, security):
    """`/api/listening/saves`: the saved-scope snapshot on GET, one save/unsave on POST."""
    uid = handler.member['id']
    if handler.command == 'GET':
        handler.json(save_rules.snapshot(store, uid))
        return
    if handler.command != 'POST':
        raise Denied(405, 'Method not allowed')
    security.limit(uid, 'listening-write', 30)
    body = handler.body()
    if not isinstance(body, dict):
        raise Denied(400, 'Object required')
    kind, identity, saved = body.get('kind'), body.get('id'), body.get('saved')
    import preferences
    virtual = kind == 'playlist' and preferences.is_virtual(identity)
    if type(saved) is not bool:
        raise Denied(400, 'kind, id and boolean saved required')
    if virtual:
        # A computed playlist saves its songs: there is no playlist row to
        # reference, and removing it removes exactly those songs again.
        track_ids = preferences.virtual_tracks(store, uid, identity)
        save_rules.save_tracks(store, uid, track_ids, saved)
        handler.json({'saved': saved, 'tracks': len(track_ids)})
        return
    if kind not in ('track', 'playlist') or type(identity) is not int or identity < 1:
        raise Denied(400, 'kind, positive integer id and boolean saved required')
    save_rules.set_saved(store, uid, kind, identity, saved)
    handler.json({'ok': True})


def folders(handler, store, security):
    """`/api/listening/folders`: the tree on GET, one action on POST."""
    uid = handler.member['id']
    if handler.command == 'GET':
        handler.json({'folders': folder_rules.listing(store, uid)})
        return
    if handler.command != 'POST':
        raise Denied(405, 'Method not allowed')
    security.limit(uid, 'listening-write', 30)
    body = handler.body()
    if not isinstance(body, dict):
        raise Denied(400, 'Object required')
    action = body.get('action')
    if action == 'create':
        folder_id = folder_rules.create(store, uid, body.get('name'), body.get('parent_id'))
    elif action in ('rename', 'delete'):
        _positive(body.get('folder_id'), 'Positive folder_id required')
        call = folder_rules.rename if action == 'rename' else folder_rules.delete
        folder_id = (call(store, uid, body['folder_id'], body.get('name')) if action == 'rename'
                     else call(store, uid, body['folder_id']))
    elif action in ('add_track', 'remove_track'):
        _positive(body.get('folder_id'))
        _positive(body.get('track_id'))
        call = folder_rules.add_track if action == 'add_track' else folder_rules.remove_track
        folder_id = call(store, uid, body['folder_id'], body['track_id'])
    else:
        raise Denied(400, 'Unknown folder action')
    handler.json({'ok': True, **({'id': folder_id} if action == 'create' else {})})


def _positive(value, message='Positive folder_id and track_id required'):
    """The ids a folder action addresses, refused exactly as before."""
    if type(value) is not int or value < 1:
        raise Denied(400, message)
    return value