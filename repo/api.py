"""The HTTP-shaped helpers for Repo (REPO-014).

Two things live here because they need a request: which member is asking, and how a Repo answer is
decorated for them. The rule itself stays in `repo/service.py` — this module only reads
`handler.member` and hands the id over, so the endpoint, the feed and any future client cannot
drift apart on who a follow belongs to.
"""
from __future__ import annotations

from repo import service


def subscriptions_for(handler, store) -> list[dict]:
    """The subscriptions, each marked with whether *this* caller may call it theirs."""
    member = getattr(handler, "member", None) or {}
    return service.subscriptions(store, str(member.get("id") or ""))