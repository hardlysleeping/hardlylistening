# Session follow-on requests

- Member-specific Library memberships and nested folders reference the one canonical Repository; no duplicate audio. Existing saves/snapshot playlists are already per-member. Folder API/UI source is deployed and isolated tests pass; authenticated full-page smoke was unavailable without signing in.
- One audio-file import, 20 MB max; folders/zips/batches/non-audio rejected. Implemented, deployed and covered by isolated rules/API tests, including an actual valid 20 MiB WAV imported into a temporary library root. 28 MiB request cap permits base64 overhead.
- Move library endpoint enforces admin + CSRF server-side; isolated member-403/admin-route tests pass; deployed; no real move.
- Daily boost: one rolling 24-hour slot/member. Swapping songs keeps the original expiry; same song does not refresh it. Active boosts prepend the Activity feed; only the existing song context menu triggers the API. Source and tests pass; deployment pending.
- Profile pins for songs/playlists/albums; likes show counts and compact named-voter popup; same popup for up/downvotes.

See `BACKLOG.md` / `BOARD.md` for status and acceptance detail.
