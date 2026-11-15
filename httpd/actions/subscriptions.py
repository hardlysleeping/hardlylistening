"""Subscriptions, the release feed, and queueing a track from it.

Follows are per-member: `subscriptions_with_ownership` is the one place a row's `mine` flag is
decided, and it claims an unowned legacy row for the member who asks rather than handing it to
the administrator. The feed reads through `repo.discovery`, never through a private copy of
the table.

`want_in_library` is the bridge from a followed playlist to the queue; it queues, it never
downloads inline.
"""

from __future__ import annotations

from ..runtime import boot


class SubscriptionActions:
    def subscriptions_with_ownership(self, store) -> list[dict]:
        """Subscriptions, each marked with whether this member may call it theirs.

        The rule (an unowned follow belongs to nobody; an anonymous reader sees all of
        them) lives in Repo, so the endpoint and the feed cannot drift apart.
        """
        from repo import api as repo_api
        return repo_api.subscriptions_for(self, store)

    def do_subscription_add(self) -> None:
        body = self.body()
        store, _ = boot()
        from repo import service as repo_service
        member_id = str((getattr(self, "member", None) or {}).get("id") or "")
        # From a link, or — from an artist page that has no known link — by name, with
        # the subscribable source resolved from the library.
        self.json(repo_service.subscribe(
            store, str(body.get("url") or "").strip(),
            name=str(body.get("name") or "").strip(),
            provider=str(body.get("provider") or ""), member_id=member_id))

    def do_subscription_check(self, watch_id: int) -> None:
        store, _ = boot()
        from repo import service as repo_service
        self.json(repo_service.check(store, repo_service.watch(store, watch_id)))

    def do_subscription_toggle(self, watch_id: int) -> None:
        store, _ = boot()
        from repo import service as repo_service
        watch = repo_service.watch(store, watch_id)
        self.json(repo_service.set_enabled(store, watch_id, not bool(watch["enabled"])))

    def do_subscription_remove(self, watch_id: int) -> None:
        store, _ = boot()
        from repo import service as repo_service
        self.json(repo_service.remove(store, watch_id))

    def do_subscription_leave(self, watch_id: int) -> None:
        """Stop calling this artist one of mine — without stopping the tracking.

        The row stays, the artist stays on the feed for everybody else, and the
        seat goes back to the "+" so it can be picked up again.
        """
        store, _ = boot()
        from repo import service as repo_service
        member_id = str((getattr(self, "member", None) or {}).get("id") or "")
        self.json(repo_service.release_seat(store, watch_id, member_id))

    def do_feed_refresh_all(self) -> None:
        """Manual sweep: every enabled follow, once, in order.

        Sequential on purpose — one provider request at a time is what keeps a
        big list from getting rate-limited — so a long list means a long wait,
        with the button spinning until the last artist answers.
        """
        store, _ = boot()
        from repo import service as repo_service
        done = []
        for watch in repo_service.subscriptions(store):
            if not watch["enabled"]:
                continue
            try:
                done.append(repo_service.check(store, watch))
            except Exception as exc:                    # noqa: BLE001
                done.append({"artist": watch["artist_name"], "error": str(exc)})
        self.json({"checked": done})

    def do_feed_queue(self, item_id: int) -> None:
        """The only place a feed item becomes work — a click, never a timer."""
        store, queue = boot()
        from repo import acquisition as repo_acquisition
        from repo import discovery as repo_discovery
        item = repo_discovery.release(store, item_id)
        if not item:
            raise ValueError("no such feed item")
        if item["decision"] == "queued":
            # Already queued by someone — pressing Download again still claims it
            # for this person's library.
            self.want_in_library(item["playlist_id"])
            self.json({"ok": True, "already": True, "playlist_id": item["playlist_id"]})
            return
        try:
            result = repo_acquisition.ingest(queue, item["source_url"])
        except Exception as exc:                       # noqa: BLE001
            store.feed_mark(item_id, outcome="failed")
            raise ValueError(str(exc))
        store.feed_mark(item_id, decision="queued",
                        outcome="running" if result.get("queued") else "complete",
                        playlist_id=result.get("playlist_id"))
        self.want_in_library(result.get("playlist_id"))
        self.json({"ok": True, **result})

    def do_track_queue(self) -> None:
        """Download one song from an opened album, not the whole release.

        The album stays in the Feed afterwards: only the download button on the
        release row takes it out of the list.
        """
        body = self.body()
        url = str(body.get("url") or "").strip()
        if not url:
            raise ValueError("nothing to download")
        _, queue = boot()
        from repo import acquisition as repo_acquisition
        result = repo_acquisition.ingest(queue, url)
        self.want_in_library(result.get("playlist_id"))
        self.json({"ok": True, **result})

    def want_in_library(self, playlist_id) -> None:
        """A download from the Feed joins the library of whoever pressed it, once
        the file is there — not before, or the library lists songs that cannot play."""
        member = getattr(self, "member", None)
        if not member or not playlist_id:
            return
        import listening
        try:
            listening.want_playlist(boot()[0], member["id"], int(playlist_id))
        except Exception:                              # noqa: BLE001
            # A library row is a convenience; a failure here must not fail the
            # download the person just asked for.
            pass

    def do_feed_ignore(self, item_id: int) -> None:
        store, _ = boot()
        store.feed_mark(item_id, decision="ignored")
        self.json({"ok": True})

    def do_feed_seen(self, item_id: int) -> None:
        store, _ = boot()
        from repo import discovery as repo_discovery
        item = repo_discovery.release(store, item_id)
        if item and item["decision"] == "new":
            store.feed_mark(item_id, decision="seen")
        self.json({"ok": True})
