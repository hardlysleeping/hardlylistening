# Temporary Party guest exception (source implementation)

`party.py` implements `/api/party/*` and the public `/party` shell. Any approved
member/admin may create up-to-24-hour parties and issue a separate reusable
bearer link per guest. This deliberately differs from permanent membership
invitations: **no member is created**, no one-time enrollment, and no app-wide
permissions are granted. First join chooses display name/built-in icon; subsequent
exchanges retain that identity and expiry. Invite/session/aux secrets are stored
hash-only; link fragment is wiped before same-Origin exchange into a separate
Secure HttpOnly party cookie. Party credentials authorize only bounded existing
music search, party queue/votes and scoped chat, never main catalog/audio/mixer/
admin/acquisition. Members control one canonical aux lease; no tight speaker sync
is promised. Server resolves chat author name/icon; client author fields are
rejected, messages capped and rendered only as text. All guest reads/writes stop
on expiry, revocation or party close. Expired metadata has no automatic purge.
See `PARTY_INTEGRATION.md` for exact routes, limits, tests and operational caveats.
No live changes or restart were performed.

# Local invitation authentication (source implementation; deployment pending)

## Configuration and deployment

Set `HARDLYLISTENING_AUTH_REQUIRED=1` and
`HARDLYLISTENING_PUBLIC_ORIGIN=https://YOUR-ACTUAL-HOST[:PORT]` in the private
service environment. Origin must be exact: no path, trailing slash, query,
fragment or userinfo. TLS terminates at the externally configured HTTPS origin;
forwarded headers never establish identity or CSRF origin. Authentication is on
by default. Explicit `AUTH_REQUIRED=0` is legacy local development only: do not
publish it. Social, personal listening, and People still require authentication.
Supabase URL/key/bootstrap-email settings are no longer used. No email service,
passwords, passkeys or external accounts are required.

Before deployment, back up SQLite, protect its directory and backups, review the
changes and test on scratch data. Restart only after authorized parent review.
No live migration, account creation, bootstrap or restart was performed during
implementation. The app must run behind HTTPS; Secure cookies intentionally do
not work on ordinary HTTP. Public pages are `/login`, `/auth/link`, robots and
health; APIs/audio/exports remain gated. Static files retain the exact allowlist.

## Explicit local administrator bootstrap / recovery

Run privately in a terminal (not a shared chat or captured service log):

```sh
cd ~/apps/hardlylistening
HARDLYLISTENING_PUBLIC_ORIGIN=https://YOUR-ACTUAL-HOST \
  python3 auth_admin.py --db ~/apps/hardlylistening/hardlylistening.db \
  --name 'Owner'
```

This creates a named administrator and prints that profile's permanent link.
It never expires and is not single-use: opening it signs the holder in, and the
session it creates is renewed on every request. One link per profile; issuing
again replaces it and `revoke` kills it. Treat it like the account itself and
send it privately. Existing Supabase member IDs and ownership/saves are preserved:
to recover an existing administrator rather than creating a duplicate, use
`--member-id EXACT_EXISTING_ID` instead of `--name`. The command requires an
existing DB path, never boots the server/worker, and never selects a public first
user. Local shell/database access is administrative authority. Use shell history
carefully; never pass the invitation token as a command-line argument.

## Enrollment and sessions

Only administrators can create members, grant/change roles and issue invitations
for new or existing approved users. Admin creation accepts only a role, not a name:
new accounts are unnamed placeholders (People shows “Not joined”). The invitee
chooses a trimmed 1–100 character display name and one built-in icon: music,
headphones, star, sun or moon. No email, remote image or password is required.
`members` upgrades additively with `display_name` and `profile_icon` (default music);
legacy IDs/email/library/saves stay untouched. Profile assignment, invite deletion
and session creation share one transaction; validation failure rolls all back.
Recovery invitations for already named users preserve both profile fields even
if profile values are submitted. Existing blank legacy names choose on enrollment.
Every approved member/admin, including Owner, has a header My profile button and
can edit only their own name/icon via authenticated same-Origin GET/POST
`/api/profile`. POST accepts exactly display_name/profile_icon: no ID or role.
Names render as text/escaped markup. Personal saves and connections remain equally
available to admins and members; the shared catalog is not partitioned.
People labels the caller “You” without destructive controls. Backend rejects
self-block/demotion and self-revoke-all even when another admin exists; normal
Sign out remains available and last-admin protection remains in place.

An invite is a bearer enrollment credential, not a public signup URL. 256-bit
random secrets are stored only as SHA256 digests in `browser_invites` with target
member and expiry. Link secrets live in `#invite=...`, not query strings. The link
page immediately wipes the fragment and POSTs to `/api/auth/session`. SQLite
`BEGIN IMMEDIATE` plus `DELETE ... RETURNING` and session insertion consume once
atomically, including concurrent processes. Invalid/expired/used links cannot
create sessions. Exchange is rate-limited and requires the actual configured
Origin. Invite role is not embedded: current approved membership is checked.

Browser sessions are independent 256-bit random opaque credentials, SHA256 only
in SQLite `browser_sessions`, persistent across restart, expiring after 30 days.
Cookie is `__Host-hl_session; Path=/; Secure; HttpOnly; SameSite=Strict`, with
Max-Age matching server expiry. No browser storage or JWT. Every request rechecks
local member role; blocked/pending users lose access immediately. Blocking also
revokes browser sessions and outstanding invites. Last administrator demotion is
refused. Logout deletes the current persisted session and expires the cookie.
People has per-user revoke-all browser sessions/invites. Existing issued invites
remain valid until used, expired, blocked or explicitly revoked; issuing another
does not silently revoke previously shared links.

The Settings People creation result displays a copyable link only in that result,
never the member list. Dismiss/close/cancel clears it, late responses cannot
redisplay it after close. This is sensitive data: share privately. App logs
suppress auth/People/rest paths; reverse proxies must suppress/redact auth and
`/rest` query logs independently. Never put invite secrets into URL queries.

## Listening devices: password and Subsonic token auth

A device secret is generated server-side (`secrets.token_urlsafe(32)`) and shown once.
Two credentials are stored for it, because Subsonic clients authenticate in two ways:

- `digest` — SHA-256 of the secret, used to verify the `p` / `enc:p` password form. It is
  not reversible, so a database leak does not hand over the usable password directly.
- `token_secret` — the secret in Subsonic's `enc:` hex form, used only to verify MD5 token
  auth (`t = md5(secret + s)`). This is reversible by construction: that hash cannot be
  computed from a SHA-256, and the Subsonic protocol has no other token scheme. It exists
  so a token-only player can connect at all. Rows created before this column existed have
  `token_secret` NULL and stay password-only; recreate the device to enable tokens.
  Revoking the device deletes its usable secret from the authentication path.

Neither value is ever written to a response, a log line or an export. A wrong password, a
wrong token, a revoked device and an unknown device all answer the same `Invalid device
credentials` (Subsonic code 40), so the reply does not confirm which devices exist.

## Shared pool versus personal listening

There is ONE canonical community pool, library and feed visible to approved
members. Auth does not filter the main frontend library/feed by personal saves.
Acquisitions add canonical tracks to that same pool. Personal saves/playlists
reference those recordings; only personal listening/OpenSubsonic access is scoped
to explicit saves and snapshot playlist entries. Unsaving never deletes shared
recordings. Invite enrollment grants no new acquisition capability: existing
legacy web acquisition behavior remains unchanged; independent extension
acquisition authorization is future work, not implicitly implemented here.

Device OpenSubsonic credentials remain separate hash-only secrets in listening
tables, with their own revoke controls, saved-record scope and membership checks.
A device credential cannot authenticate browser routes and browser sessions
cannot authenticate `/rest`. Revoking browser sessions is not device revocation;
block a person to deny both immediately. No whole-library device admin bypass.
Device API currently supports `u`/`p`, XML/JSON, original bytes and Range; MD5
`t`/`s`, scrobbling, artwork fetching/transcoding, real-client compatibility and
companion apps are not claimed.

## Surface and limits

- `POST /api/auth/session {invite,display_name?,profile_icon?}`: public but Origin/rate/expiry checked. A member link is permanent and reusable; a party guest link keeps its own 24-hour bound. Signing in again never changes an existing profile name/icon.
- `POST /api/auth/logout`: same-Origin, invalidates current cookie.
- `GET /api/members`, `POST /api/members {id,role}`: admin only.
- `POST /api/members/create {role}`: admin only, creates user + link.
- `POST /api/members/invite {id}`: admin only, link for existing approved user.
- `POST /api/members/revoke {id}`: admin only, all browser sessions and invites.
- Email OTP, OAuth callback and remote bearer verification are retired.

Central request/export/social/enrollment rate limits and 8-MiB framing limits
remain. Mutating authenticated requests require exact configured HTTPS Origin.
Revocation stops future requests, not already-running streams or offline copies.
No background expiry purge is implemented; expired rows cannot authenticate.
SQLite/backups and local terminal output remain sensitive. No public exposure,
real-browser HTTPS enrollment or external client validation has been performed.
