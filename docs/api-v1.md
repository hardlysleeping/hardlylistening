# `/api/v1` — the stable JSON surface

`/api/*` is the original surface: it grew route by route, its shapes are loose, and its names
invert (`/api/library` is the shared pool, `/api/listening` is a member's own rows). It stays
exactly as it is, for the web UI and every client that already speaks it.

`/api/v1` is the promise a new client can build against. Every route parses its arguments, calls
one function on a domain service and answers the envelope below. There is no SQL in the adapter
and no rule that exists only there — if an answer changes, it changes in the service.

`docs/server-domains.md` is the authority for which domain owns each route.

## The envelope

```json
{"api": "v1", "resource": "library.songs", "data": [...], "count": 3}
```

* `api` — always `"v1"`.
* `resource` — a stable name for what `data` is, so a client can route on it.
* `data` — the service's own answer, unmodified apart from what `Handler.json` already hides from
  non-admin members (`file` becomes `"available"`). A single object or `null` when the question
  has one answer, a list when it has many.
* `count` — present only when `data` is a list.

Errors keep the server's existing shape: `{"error": "..."}` with the HTTP status (`400`, `401`,
`403`, `404`, `405`, `429`, `503`). An unknown `/api/v1/*` path is `404`; a known path with the
wrong method is `405` and names the method that would have answered.

## Authentication

The same rule the unversioned routes beside it follow:

* **Shared reads** (`repo/*`, `social/activity`) — a session when accounts are on; no session
  when `HARDLYLISTENING_AUTH_REQUIRED` is off (local mode). `handler.member` stays `None`, so
  ownership fields say "nobody" rather than being invented.
* **Personal reads and writes** (`library/*`, `playback/*`, `social/comments`, `social/summary`) —
  a member in every mode, exactly like `/api/listening/*` and `/api/social/*`. A local install
  has no account and therefore no personal row: the request is refused rather than answered with
  an empty library.

`POST /api/v1/playback/state` goes through `SECURITY.authenticate`, so it carries the same
Origin/CSRF requirement as every other write.

`/api/v1/capabilities` is unchanged and public; it is answered in `server.py` before this
dispatcher so a client can ask what the server does before it holds a session.

## Routes

| route | answers | delegates to |
| --- | --- | --- |
| `GET /api/v1/repo/snapshot` | the whole poll snapshot, with `subscriptions` decorated by ownership | `repo.service.snapshot`, `repo.service.subscriptions` |
| `GET /api/v1/repo/releases?watch=&offset=&limit=` | one followed artist's releases and a page of songs (`limit` 1–200) | `repo.service.releases` |
| `GET /api/v1/repo/tracks?url=` | the songs inside one album | `repo.service.tracks` |
| `GET /api/v1/repo/artist?name=` | one artist page: what the library knows, whether it is followed | `repo.service.artist` |
| `GET /api/v1/repo/subscriptions` | every followed artist, each marked `mine` | `repo.service.subscriptions` |
| `GET /api/v1/library/songs` | the member's scope, in album order | `library.service.catalog` |
| `GET /api/v1/library/albums` | the same scope grouped by artist + album (`song_count`, `duration`, `added`) | `library.service.albums` |
| `GET /api/v1/library/artists` | the same scope grouped by artist (`album_count`, `song_count`, `duration`) | `library.service.artists` |
| `GET /api/v1/library/playlists` | the playlists this member saved | `library.service.playlists` |
| `GET /api/v1/library/playlist/<id>` | one saved playlist and its ordered tracks; `404` if the member never saved it | `library.service.playlist` |
| `GET /api/v1/library/favorites` | favourited track rows | `library.service.favorites` |
| `GET /api/v1/library/bookmarks` | every bookmark, newest change first, comment included | `library.service.bookmark_rows` |
| `GET /api/v1/playback/state` | where the member left off: track, queue, position, `updated`; `null` if never saved | `nowplaying.get` |
| `POST /api/v1/playback/state` | saves that state (same body the unversioned route takes) and answers the cleaned copy | `nowplaying.put` |
| `GET /api/v1/playback/queue` | `track_ids`, `current`, `index`, `position`, `updated`; `null` if never saved | `nowplaying.get` |
| `GET /api/v1/playback/now` | `track_id`, `position`, `updated`; `null` if never saved | `nowplaying.get` |
| `GET /api/v1/playback/stream/<id>` | `track_id`, `url` (`/api/track/<id>/audio`), `available` | `library.playback.available` |
| `GET /api/v1/social/activity?limit=` | the Activity feed (limit 1–200) | `social.service.feed` |
| `GET /api/v1/social/comments?kind=&target=&after=` | one page of comments with their likes and the target's summary | `social.service.comments` |
| `GET /api/v1/social/summary?kind=&target=` | score, counts and named voters for one target | `social.service.summary` |
| `GET /api/v1/social/summary?kind=&targets=a,b,c` | the same counts for many targets at once | `social.service.batch` |

`kind` is the Repo object under discussion: `track`, `playlist` or `album` — the values
`social.discussion` validates. `target` is that object's id (an album target is the 64-character
album key `social.album_id` produces).

## What v1 does not cover yet

Deliberately, because the answering service function does not exist or the card did not ask:

* **Writes** to Library (star, rate, save, bookmark) and Social (comment, vote, like, boost).
  The services have them; the version contract for them does not exist yet.
* **Member profiles** (`social.service.profile`) and **search** (`search.find`) — the unversioned
  `/api/member/<id>`, `/api/search` and `/api/social/*` routes still own them.
* **Acquisition** (`/api/add`, the queue, imports) — Repo's queue is not part of the player-facing
  v1 surface.
* **Byte streaming** — `playback/stream/<id>` answers the URL; the bytes still come from
  `/api/track/<id>/audio`, which is where Range handling already lives.

`tests/test_api_v1.py` pins every route's envelope and its delegation, the 404/405 behaviour, and
the auth rule. It also refuses a table query inside `api/v1.py`, because a route that starts
reading rows has become a second implementation.