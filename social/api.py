"""The HTTP adapter for Social. Parse, call one domain function, answer.

Split out of `discussion.py` so the domain side (comments, votes, likes, summaries) can be imported
by anything without dragging in request parsing, `parse_qs` or a handler object. `ARCH-006`: this is
what `server.py` calls for `/api/social/*`; nothing else in the server needs to know the shape of
those URLs.
"""
import re
from urllib.parse import parse_qs, urlparse

from security import Denied
from social.discussion import batch, comment, comments, like, vote


def dispatch(handler, store):
    """The HTTP adapter: parse, call one domain function, answer. No logic lives here."""
    path = urlparse(handler.path).path
    if path == '/api/social/summary':
        if handler.command != 'GET':
            raise Denied(405, 'Read-only endpoint')
        query = parse_qs(urlparse(handler.path).query)
        kind = (query.get('kind') or [''])[0]
        targets = (query.get('targets') or [''])[0].split(',')
        try:
            latest = int((query.get('latest') or ['0'])[0])
        except ValueError:
            raise Denied(400, 'Invalid latest count')
        handler.json({'items': batch(store, kind, targets, handler.member, latest=latest)})
        return
    match = re.fullmatch(r'/api/social/(track|playlist|album)/([^/]+)(?:/(comments|vote|like)(?:/([1-9][0-9]{0,18}))?)?', path)
    if not match:
        raise Denied(404, 'Unknown social endpoint')
    kind, target, action, comment_id = match.groups()
    member = handler.member
    if handler.command == 'GET':
        if action or comment_id:
            raise Denied(404, 'Unknown social endpoint')
        query = parse_qs(urlparse(handler.path).query)
        try:
            after = int(query.get('after', ['0'])[0])
            if after < 0 or after > 9223372036854775807:
                raise ValueError()
        except ValueError:
            raise Denied(400, 'Invalid pagination cursor')
        handler.json(comments(store, kind, target, member, after))
        return
    data = handler.body()
    if not isinstance(data, dict):
        raise Denied(400, 'Expected JSON object')
    if action == 'vote' and not comment_id:
        handler.json(vote(store, kind, target, member, data.get('value')))
    elif action == 'like':
        if not comment_id:
            raise Denied(400, 'Comment id required')
        handler.json(like(store, kind, target, member, int(comment_id)))
    elif action == 'comments':
        handler.json(comment(store, kind, target, member, body=data.get('body'),
                             comment_id=int(comment_id) if comment_id else None,
                             delete=data.get('delete') is True))
    else:
        raise Denied(404, 'Unknown social endpoint')
