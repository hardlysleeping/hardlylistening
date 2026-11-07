# Party mode source integration

Implemented in `party.py`, `web/party.html`, `web/party.css`, `web/party.js`.
`server.py` now dispatches `/api/party` and `/api/party/*` before the legacy local
bypass, serves the exact public Party shell/assets before boot, and suppresses
Party access logs (including query strings). Existing `/api/playlists` dispatch
is retained. The public shell contains no personal data; all music/chat access is
authenticated. APIs/assets use no-store; no service-worker Party caching added.

## Parent integration remaining

Add a **Party** tab/link in `web/index.html` pointing to `/party` (prefer a new tab
if preserving ongoing main-player playback). No edits to index.html or dj.html
were made by this implementation. No further server wiring is needed.
Review source before authorized restart; nothing deployed/restarted and no live
SQLite or canonical media was touched. Schema is additive/lazy on Party requests.

## API contract

All JSON POSTs require exact configured HTTPS Origin. Existing approved membership
always uses the existing independent `__Host-hl_session` cookie; there is no local
anonymous member fallback. Party guest credentials never authenticate app APIs.

- `GET /api/party`: member/admin only, up to 100 active `{parties:[{id,title,expires}]}`.
- `POST /api/party {title,hours?}`: any approved member/admin; title 1–100 chars,
  `0 < hours <= 24` (default 24); returns `{id}`.
- `POST /api/party/:id/invite {}`: any approved member/admin (not admin-only);
  returns `{guest_id,expires,link:'/party#invite=SECRET'}`. One new identity/link
  per call, up to 200 guests per party. Copy/share privately. Existing member
  member links are permanent per profile (revocable, re-issuable); Party guest links are the only 24-hour exception.
- `POST /api/party/exchange {invite,display_name?,profile_icon?}`: same-Origin,
  IP-rate-limited exchange. First join requires name 1–100 chars and built-in
  music/headphones/star/sun/moon icon. Rejoins reuse identity/profile/votes; submitted
  alternate profiles do not overwrite it. Returns `{party_id,expires}` and distinct
  `__Host-hl_party` Secure/HttpOnly/SameSite=Strict cookie. No permanent user created.
  Link is reusable until original guest/party expiry or revocation, never consumed
  once. Session expiry is min(original guest expiry, party expiry), never extended.
  Raw link/session/host secrets are never persisted, only SHA256 hashes. At most
  20 simultaneously valid cookie sessions per guest (oldest replaced on later joins).
- `GET /api/party/:id`: party-scoped guest or member; `{id,title,expires,
  host_active,current,queue,icons,viewer}`. Queue/current metadata exposes no
  paths, source URLs, artwork/audio links, settings, emails or credentials.
- `GET /api/party/:id/search?q=...`: 1–100-char search, up to 200 candidate rows
  checked and 30 playable canonical-root track metadata results; existing music
  only, no network acquisition. Literal SQL wildcard escaping.
- `POST /api/party/:id/queue {track_id}`: existing DB positive track ID and
  existing regular file inside canonical root required; no duplicate active
  recording, waiting queue maximum 200. Never edits/deletes canonical tracks.
- `POST /api/party/:id/skip {expected?}`: any party identity (guest or member).
  One skip per identity per current entry; `expected` is the entry the caller
  believes is playing (a stale value reports the truth instead of resurrecting
  it). Skips are applied when they are **strictly more than half** of the people
  present, so a single person in the room skips instantly and two people need
  both clicks. Presence is anyone who fetched `GET /api/party/:id` in the last
  45 s (POLL every 3 s), which means a closed tab stops counting — including its
  skip. Meeting the threshold retires the current entry and promotes the
  top-ranked waiting one atomically, the same transition as `control.next`, and
  the aux host's next poll switches its audio. `GET /api/party/:id` therefore
  also returns `participants`, `skip:{count,mine,needed}` and
  `next:{entry_id,id,title,artist_name,duration,score}` ("next up").
- `POST /api/party/:id/vote {entry_id,value}`: value exactly -1, 0 (retract), or 1.
  One upsert per stable guest/member identity and queue entry; change/retract
  supported. Full waiting queue sorts by aggregate SUM(value) descending, then
  entry ID ascending (stable arrival ties). Current entry cannot be voted/reordered.
- `GET /api/party/:id/chat?after=0`: ascending cursor page, up to 100 messages,
  `{messages:[{id,display_name,profile_icon,body,created}],cursor,has_more}`.
- `POST /api/party/:id/chat {body}`: rejects every extra field including client
  author/name/icon. Server resolves approved member profile or unexpired guest
  name/icon; body trimmed 1–1000 chars, 20 posts/minute/identity. Retains latest 500
  messages per party, no public author identifiers/emails. Page appends text nodes,
  preserves textarea/focus/drafts during polling, incremental cursor, accessible log.
- `POST /api/party/:id/control {op,...}`: member/admin only:
  - `claim`: 30-second exclusive aux lease, returns `{host_token}` (in memory only).
  - `heartbeat {host_token}`: same approved member renews lease; page every 10s.
  - `next {host_token,expected}`: expected current entry ID or null; atomic
    compare-and-swap retires current and promotes highest-ranked waiting entry.
  - `release {host_token}`: releases aux lease.
  - `revoke {guest_id}`: party-scoped revocation, invalidates link and all sessions.
  - `close`: ends party immediately and denies guest reads/writes/exchange.

## Playback and limitations

Only an approved member aux host can stream through existing `/api/track/:id/audio`.
Guest UI never requests audio; aux requires explicit Become aux host/Play gesture.
This is a single canonical control lease, not DRM: members retain their normal
independent playback rights, and browsers cannot guarantee stopping audio at the
exact instant a suspended tab/network loses a lease. UI stops on lease failure.
Multiple-speaker tight sync is not implemented or promised. Hidden guest/member
foreground state/chat polling pauses; aux heartbeat continues while hosting.

Guest/session/room/queue/chat metadata persists in SQLite across restart, but
expired rows are inaccessible, not automatically garbage-collected. Chat keeps a
500-message rolling bound per room; ended-room cleanup/retention purge is future
work. There is no guest acquisition, mixer, admin, main catalog or audio grant.
AUTH_REQUIRED=0 is still unsafe for public hosting: it deliberately exposes old
anonymous APIs to clients without any cookie; Party dispatch itself always checks
membership/guest auth, and presenting a party cookie cannot activate that bypass.

## Verification

`python3 -m unittest discover -s tests -p test_party.py -v`

`NODE_PATH=<dir with jsdom>/node_modules node tests/test_party_ui.cjs`

Dedicated HTTP fixtures use temporary SQLite, fake unplayed media bytes, patched
boot and no workers. Test current order, stable ties, repeat/retract votes,
concurrent rejoin/same-guest voting, actual DB reconnect, host exclusivity,
CSRF/rates, original expiry, revoked/expired/cross-party denials, strict app API
boundary, member/admin invitation permission, chat spoof rejection/icons/order/
paging/retention, public exact static and log redaction. DOM fixtures check
text-only chat/name/track rendering, cursor polling, draft/focus preservation,
body-only submit, fragment wipe and no autoplay. They do not establish real
HTTPS-cookie enrollment, codec playback or synchronized speakers.
