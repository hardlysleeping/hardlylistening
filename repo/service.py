"""Repo: the catalogue of everything, independent of anyone's personal library.

`ARCH-002`. A facade, not a move. Every function here answers the same question the module it
delegates to already answers, with the same arguments and the same return value — the difference is
that callers stop reaching into `db.Store`, `discovery.subscriptions` and `discovery.tracklist`
directly, so Repo has exactly one entry point and `Library` can be trusted not to depend on it.

Two rules this module exists to make checkable:

* **Repo never imports Library or Social.** "What exists" does not need "what I saved" or "what
  people said about it". If a future Repo feature needs either, it belongs elsewhere.
* **Nothing here mutates a member's personal state.** Following an artist is Repo state (the row
  is the artist); which member may call that follow theirs is a field derived from the row, not a
  personal write.

Behaviour is deliberately unchanged (`ARCH-002`), and the routes still call their own code
(`ARCH-006` is the card that makes them delegate). `tests/test_repo_service.py` pins the facade to
the functions underneath so that delegation later cannot quietly change an answer.
"""
from __future__ import annotations

from discovery import subscriptions as _subscriptions
from discovery import tracklist as _tracklist
from discovery.artist_profile import profile as _artist_profile


# -- the snapshot ---------------------------------------------------------------------------------

def snapshot(store) -> dict:
    """Everything the UI polls for, in one read.

    Delegates to `Store.state()`, which owns the queries: this is the same dict, decorated by the
    adapter that called it. The facade exists so a caller can say "the repository snapshot"
    without knowing that the query set lives on the store object.
    """
    return store.state()


# -- artists and their releases -------------------------------------------------------------------

def artist(store, name: str) -> dict:
    """One artist page: what the library knows, and whether it is followed."""
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    return _artist_profile(store, name)


def releases(store, watch_id: int, offset: int = 0, limit: int = _tracklist.PAGE) -> dict:
    """The releases behind one follow, one page at a time."""
    return _tracklist.catalogue(store, int(watch_id), offset=offset, limit=limit)


def tracks(url: str) -> list[dict]:
    """The songs inside one album. A user-initiated read; never part of a poll."""
    return _tracklist.tracks(url)


# -- subscriptions --------------------------------------------------------------------------------

def subscriptions(store, member_id: str = "") -> list[dict]:
    """Followed artists, each marked with whether this member may call it theirs.

    A follow made before ownership existed has no owner. It belongs to nobody until somebody asks
    for it again — inheriting it is what made every artist look subscribed as soon as one was.
    With no member id (sign-in off, or an anonymous read) every row is theirs to see.
    """
    uid = str(member_id or "")
    rows = store.watches()
    for row in rows:
        owner = str(row.get("member_id") or "")
        row["mine"] = True if not uid else bool(owner and owner == uid)
    return rows


def watch(store, watch_id: int) -> dict:
    """One follow, or a refusal — the same ValueError every adapter turns into a 400."""
    row = store.watch_get(int(watch_id))
    if not row:
        raise ValueError("no such subscription")
    return row


def subscribe(store, url: str = "", *, name: str = "", provider: str = "",
              member_id: str = "") -> dict:
    """Follow by link, or resolve the link from the artist's name.

    `discovery.subscriptions.subscribe` refuses an empty link with "paste an artist link"; the
    name path is how an artist page subscribes an artist whose source link the library does not
    have.
    """
    if (url or "").strip():
        return _subscriptions.subscribe(store, url, member_id=member_id)
    return _subscriptions.subscribe_by_name(store, (name or "").strip(), provider or "",
                                            member_id=member_id)


def check(store, watch_row: dict) -> dict:
    """Read the provider once for this follow. Only a click turns a result into a download."""
    return _subscriptions.check(store, watch_row)


def check_all(store, *, budget: int = 2) -> list[dict]:
    """The scheduled sweep: every enabled follow, within its own budget."""
    return _subscriptions.check_all(store, budget=budget)


def set_enabled(store, watch_id: int, enabled: bool) -> dict:
    """Pause or resume a follow. Tracking state, not personal state."""
    store.watch_mark(int(watch_id), enabled=bool(enabled))
    return {"id": int(watch_id), "enabled": bool(enabled)}


def remove(store, watch_id: int) -> dict:
    """Stop tracking an artist for everybody on this server."""
    store.watch_remove(int(watch_id))
    return {"removed": int(watch_id)}


def release_seat(store, watch_id: int, member_id: str) -> dict:
    """Stop calling an artist mine without stopping the tracking.

    The row stays and the artist stays on the feed for everybody else, so the seat goes back to
    the "+" and can be picked up again.
    """
    watch(store, watch_id)
    if not store.watch_release(int(watch_id), str(member_id or "")):
        raise ValueError("that is not one of your subscriptions")
    return {"unsubscribed": int(watch_id)}