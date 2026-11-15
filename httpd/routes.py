"""The POST route table: method, path pattern, action method name.

One row per action, and the action name is a method on the composed handler. The table is
data so a route can be added without touching `do_POST`; `boundary()` keeps the policy that
says who may reach it.
"""
from __future__ import annotations

ROUTES = [
    ("POST", r"^/api/add$", "add"),
    ("POST", r"^/api/pause$", "pause"),
    ("POST", r"^/api/cooldown/clear$", "clear_cooldown"),
    ("POST", r"^/api/failed/retry$", "retry_failed"),
    ("POST", r"^/api/queue/clear-completed$", "clear_completed"),
    ("POST", r"^/api/queue/auto-accept$", "queue_auto"),
    ("POST", r"^/api/settings$", "settings"),
    ("POST", r"^/api/import$", "import_paths"),
    ("POST", r"^/api/import/upload$", "import_upload"),
    ("POST", r"^/api/artist/art$", "artist_art"),
    ("POST", r"^/api/library/move$", "library_move"),
    ("POST", r"^/api/track/(\d+)/retry$", "track_retry"),
    ("POST", r"^/api/track/(\d+)/skip$", "track_skip"),
    ("POST", r"^/api/track/(\d+)/source$", "track_source"),
    ("POST", r"^/api/track/(\d+)/unpick$", "track_unpick"),
    ("POST", r"^/api/track/(\d+)/move$", "track_move"),
    ("POST", r"^/api/track/(\d+)/delete$", "track_delete"),
    ("POST", r"^/api/track/(\d+)/restore$", "track_restore"),
    ("POST", r"^/api/trash/empty$", "trash_empty"),
    ("POST", r"^/api/replacements/collect$", "replacements_collect"),
    ("POST", r"^/api/trash/(\d+)/restore$", "trash_restore"),
    ("POST", r"^/api/playlist/(\d+)/remove$", "playlist_remove"),
    ("POST", r"^/api/release/(\d+)/remove$", "release_remove"),
    ("POST", r"^/api/playlist/(\d+)/requeue$", "playlist_requeue"),
    ("POST", r"^/api/playlist/(\d+)/verify$", "playlist_verify"),
    ("POST", r"^/api/library/verify$", "library_verify"),
    ("POST", r"^/api/library/grab$", "library_grab"),
    ("POST", r"^/api/library/tempo$", "library_tempo"),
    ("POST", r"^/api/sonic/analyse$", "sonic_analyse"),
    ("POST", r"^/api/library/titles$", "library_titles"),
    ("POST", r"^/api/track/(\d+)/bpm$", "track_bpm"),
    ("POST", r"^/api/track/(\d+)/publish$", "track_publish"),
    ("POST", r"^/api/activity/listen$", "activity_listen"),
    ("POST", r"^/api/members$", "members_update"),
    ("POST", r"^/api/subscriptions$", "subscription_add"),
    ("POST", r"^/api/subscriptions/(\d+)/check$", "subscription_check"),
    ("POST", r"^/api/subscriptions/(\d+)/toggle$", "subscription_toggle"),
    ("POST", r"^/api/subscriptions/(\d+)/remove$", "subscription_remove"),
    ("POST", r"^/api/subscriptions/(\d+)/unsubscribe$", "subscription_leave"),
    ("POST", r"^/api/feed/refresh-all$", "feed_refresh_all"),
    ("POST", r"^/api/feed/(\d+)/queue$", "feed_queue"),
    ("POST", r"^/api/track/queue$", "track_queue"),
    ("POST", r"^/api/feed/(\d+)/ignore$", "feed_ignore"),
    ("POST", r"^/api/feed/(\d+)/seen$", "feed_seen"),
]
