"""The composed handler: every mixin, in the order the request travels.

Nothing is implemented here. The mixins are the HTTP layer split by concern; the order below is
the MRO the methods rely on (`super()` in the plumbing reaches `BaseHTTPRequestHandler` last),
and the two class attributes are what the protocol reports.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from .actions.library import LibraryActions
from .actions.members import MemberActions
from .actions.queue import QueueActions
from .actions.subscriptions import SubscriptionActions
from .actions.trash import TrashActions
from .base import Plumbing
from .gate import Gate
from .get import Pages
from .media import Media
from .session import BrowserSession


class Handler(
    Plumbing,            # setup, replies, headers, POST dispatch
    Gate,                # who may reach the route at all
    Pages,               # the GET chain
    Media,               # bytes: streams, artwork, static
    BrowserSession,      # cookies, OAuth, signup
    LibraryActions,
    QueueActions,
    SubscriptionActions,
    TrashActions,
    MemberActions,
    BaseHTTPRequestHandler,
):
    """One HTTP handler: the mixins above, in the order a request passes through them."""

    server_version = "HardlyListening"
    protocol_version = "HTTP/1.1"
