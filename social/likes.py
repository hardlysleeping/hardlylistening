"""Likes on a comment.

Split out of `social/discussion.py` (SOCIAL-001) so a like is a named thing rather than a fifth
paragraph in the comment file. It reuses the comments module's schema, target validation and
summary: a like changes a comment's count and nothing else.
"""
from security import Denied

from social.comments import schema, summary, validate_target


def like(store, kind, target, member, comment_id) -> dict:
    """Toggle one member's like on a comment. A like needs a comment that exists."""
    with store.lock:
        validate_target(store, kind, target)
        schema(store)
        if not store.conn.execute('SELECT id FROM social_comments WHERE id=? AND kind=? AND target=?',
                                  (int(comment_id), kind, target)).fetchone():
            raise Denied(404, 'Comment not found')
        with store.conn:
            if store.conn.execute('SELECT 1 FROM social_comment_likes WHERE comment_id=? AND member_id=?',
                                  (int(comment_id), member['id'])).fetchone():
                store.conn.execute('DELETE FROM social_comment_likes WHERE comment_id=? AND member_id=?',
                                   (int(comment_id), member['id']))
            else:
                store.conn.execute('INSERT INTO social_comment_likes(comment_id, member_id) VALUES(?,?)',
                                   (int(comment_id), member['id']))
        return summary(store, kind, target, member)
