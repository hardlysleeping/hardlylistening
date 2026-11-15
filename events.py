"""Domain events: what happened, told to whoever listens.

Activity is Social's, so Core must not write it. A member joining or renaming themselves is a
fact about accounts — Core's — and the Feed's row about it is Social's opinion of that fact.
Core publishes the event here; the composition root subscribes Social to it at boot. That is
the whole point: `security.py` stops importing `activity`, and a Social bug can no longer
fail a sign-in.

Two properties are deliberate:

- **Publishing never raises.** An event is a notification. A subscriber that breaks must not
  break the write path that published it, so failures are swallowed here rather than wrapped
  in a `try` at every call site.
- **Publishing with nobody listening is normal.** The core runs with Social absent
  (`ARCH-007`), and then these events simply go nowhere.

Handlers are `handler(store, fields)`, called in subscription order.
"""
from __future__ import annotations

#: A member exists and has just chosen their name (a signup or a first login-link visit).
MEMBER_JOINED = "member.joined"
#: A member changed the display name the Feed shows.
MEMBER_RENAMED = "member.renamed"

_subscribers: dict[str, list] = {}


def subscribe(kind: str, handler) -> None:
    """Register one handler for one kind. Subscribing the same handler twice is one handler."""
    listeners = _subscribers.setdefault(kind, [])
    if handler not in listeners:
        listeners.append(handler)


def publish(store, kind: str, **fields) -> None:
    """Hand the event to every subscriber. Never raises, returns nothing."""
    for handler in list(_subscribers.get(kind, ())):
        try:
            handler(store, fields)
        except Exception:
            pass


def subscribers(kind: str) -> list:
    """Who would hear this kind. Read by the boundary test rather than assumed."""
    return list(_subscribers.get(kind, ()))


def clear() -> None:
    """Forget every subscriber. Tests use this; boot never does."""
    _subscribers.clear()