"""Social's entry point: Activity, boosts, comments, likes and profiles.

`ARCH-005`. A facade, not a rewrite. The functions below are the whole of Social from outside:
who did what (`feed`, `profile`, `recent`), the 24-hour boost slot, and discussion about a Repo
object (summary, batch, comments, votes, likes). Each one delegates to the module that already owns
the query — `social.activity`, `social.boosts`, `social.comments`, `social.likes` and `profiles` —
with the same arguments and the same return value.

What this buys is a boundary that can be checked. `docs/server-domains.md` forbids Social -> Library
and Playback -> Social; once callers reach Social through here, an import of `social.service` is the
only way in, and the adapter (`social.discussion.dispatch`) is not something a feature has to know
about.

Nothing here is called by a route yet: `ARCH-006` is the card that makes adapters delegate, and it
is a different change from naming the owner. `tests/test_social_service.py` pins every function to
the one underneath so that delegation cannot quietly change an answer.
"""
from __future__ import annotations

import importlib

import profiles as _profiles
import social.activity as _activity
import social.boosts as _boosts
import social.likes as _likes

# `social/__init__.py` re-exports the *function* `comments`, and that package attribute shadows the
# submodule of the same name: `import social.comments as _comments` binds the function. Ask the
# loader for the module instead of trusting the attribute.
_comments = importlib.import_module('social.comments')


# -- the Activity log -----------------------------------------------------------------------------

def subscribe_events() -> None:
    """Attach Activity to the Core events that belong in the Feed.

    Called once by the composition root. Core publishes `member.joined` and `member.renamed`
    (`events.py`) and never learns what the Feed does with them; the mapping from Core's
    vocabulary to Activity's kinds lives here.
    """
    import events as _events
    _events.subscribe(_events.MEMBER_JOINED, _record_join)
    _events.subscribe(_events.MEMBER_RENAMED, _record_name)


def _record_join(store, fields) -> None:
    _activity.record(store, _activity.KIND_JOIN, member_id=fields.get('member_id', ''),
                     detail=fields.get('detail', ''))


def _record_name(store, fields) -> None:
    _activity.record(store, _activity.KIND_NAME, member_id=fields.get('member_id', ''),
                     detail=fields.get('detail', ''))


def record(store, kind: str, member_id: str = "", track_id: int = 0, playlist_id: int = 0,
           detail: str = "", commit: bool = True) -> None:
    """Append one event. Silent by contract: the feed must never break a write path."""
    return _activity.record(store, kind, member_id=member_id, track_id=track_id,
                            playlist_id=playlist_id, detail=detail, commit=commit)


def feed(store, limit: int = 80, now: float | None = None) -> list[dict]:
    """Active boosts first, then one collapsed card per member per day."""
    return _activity.feed(store, limit, now)


def listen(store, member: dict | None, track_id: int) -> None:
    """A play reported by the player. Deduped on write, stacked at read time."""
    return _activity.listen(store, member, track_id)


# -- boosts ---------------------------------------------------------------------------------------

def boost(store, member_id: str, track_id: int, now: float | None = None) -> dict:
    """Set this member's 24-hour slot; switching songs never extends its expiry."""
    return _boosts.boost(store, member_id, track_id, now)


def current_boost(store, member_id: str, now: float | None = None) -> dict | None:
    return _boosts.current_boost(store, member_id, now)


def active_boosts(store, limit: int = 80, now: float | None = None) -> list[dict]:
    return _boosts.active_boosts(store, limit, now)


# -- discussion -----------------------------------------------------------------------------------

def annotate(tracks):
    """Give each track the provider-independent album id the discussion targets."""
    return _comments.annotate(tracks)


def album_id(track):
    return _comments.album_id(track)


def validate_target(store, kind, target) -> None:
    """Refuse an unknown or forged target before any table is touched."""
    return _comments.validate_target(store, kind, target)


def summary(store, kind, target, member) -> dict:
    """The score, the counts and the named voters for one target."""
    return _comments.summary(store, kind, target, member)


def batch(store, kind, targets, member, latest: int = 0) -> dict:
    """Counts for many targets at once, so a grid does not fetch per card."""
    return _comments.batch(store, kind, targets, member, latest=latest)


def comments(store, kind, target, member, after: int = 0) -> dict:
    """One page of comments with their likes, plus the target's summary."""
    return _comments.comments(store, kind, target, member, after)


def vote(store, kind, target, member, value) -> dict:
    """Set, switch or clear one member's vote. Returns the target's new summary."""
    return _comments.vote(store, kind, target, member, value)


def like(store, kind, target, member, comment_id) -> dict:
    """Toggle one member's like on a comment."""
    return _likes.like(store, kind, target, member, comment_id)


def comment(store, kind, target, member, *, body=None, comment_id=None, delete=False) -> dict:
    """Add, edit or delete a comment; author or administrator only for the last two."""
    return _comments.comment(store, kind, target, member, body=body,
                               comment_id=comment_id, delete=delete)


# -- profiles -------------------------------------------------------------------------------------

def profile(store, member_id: str, limit: int = 40) -> dict | None:
    """One member's public page: history, adds, playlists, votes and saves."""
    return _profiles.profile(store, member_id, limit)


def recent(store, limit: int = 20) -> list[dict]:
    """The newest public events across all members."""
    return _profiles.recent(store, limit)