"""Repo's discovery read: what the followed sources have found.

A release a followed artist put out is **catalogue**, not activity. Activity is the
`activity` table — what people did — and that is Social's. So `feed_items` is read
here, through Repo, and the web route asks Repo rather than reaching into `Store`
(REPO-012). The table keeps its name in this pass; the entry point is what moves.

This is a facade in the ARCH-002 sense: same rows, same order, same fields as
`Store.feed_list()`, so moving the call site cannot change an answer. The test pins
that rather than trusting it.
"""

from __future__ import annotations


def releases(store, limit: int = 200, decision: str = "") -> list[dict]:
    """Releases the follows have found, newest first, each with its follow's name."""
    return store.feed_list(int(limit), decision)


def release(store, item_id: int) -> dict | None:
    """One release, or None — the row a decision is about."""
    return store.feed_item(int(item_id))