# Client SDK

Purpose: let another program — a player, a script, a test — talk to this server without
importing it. Four small modules, Python 3 standard library only, no build step and no
dependencies:

| module | answers |
| --- | --- |
| `sdk.subsonic` | a device's library, playlists, search, stars, bookmarks, stream URLs and byte ranges |
| `sdk.capabilities` | which optional domains this server advertises, and how to degrade when it will not say |
| `sdk.social` | the Activity feed, comments, votes, likes and boosts — or a refusal naming the flag that is missing |
| `sdk.repo` | the Repository: the snapshot, a followed artist's releases and tracks, an artist page, subscriptions — or a refusal naming the domain that is missing |

`subsonic.py` is the authority on what the surface answers, and the client is written against
it: endpoints it implements are here, and endpoints it refuses raise `NotSupported` rather than
returning an empty result that looks like success. The JSON envelope is the only form parsed
(`f=json`), because a caller that asks for JSON never needs the XML branch. Social follows the
same rule with its own authorities: `api/v1.py` for the three versioned reads, `social/api.py`
and `server.py` for the writes they never versioned. Repository does the same with `api/v1.py`
for all five of its reads, and `repo/service.py` for the rows.

## Using it

```python
from sdk import Credentials, SubsonicClient, probe

caps = probe('http://10.0.0.20:8460')          # never raises; caps.degraded says if it could not read
client = SubsonicClient('http://10.0.0.20:8460',
                        Credentials.from_device(device))   # {username, password} from /api/listening/devices
client.ping()
album = client.album(client.artist(client.artists()[0].id).albums[0].id)   # .songs are typed Song rows
client.star([album.songs[0].id])                            # repeated ids go out as repeated params
client.create_bookmark(album.songs[0].id, 42_000, 'chorus')
for chunk in client.iter_range(album.songs[0].id, offset=1 << 20):
    ...                                                     # resumes from the byte it stopped at

if caps.features.social.available:                          # hide the controls, don't probe them
    ...                                                     # nothing is offered that the document omits
```

Credentials come in the two forms this server accepts — a device's username/password pair, or
the MD5 token pair `md5(password + salt)` built from it. The client stores neither: they live in
the `Credentials` value the caller passes in and are never written anywhere.

`client.stream_url(song_id, max_bitrate=128, format='opus')` builds a URL a player can open
directly, including the re-encode request; `read_range` returns bytes from one request and
`iter_range` yields them, resuming with a second `Range` header when a transfer drops.

## Social

`/api/v1/social/*` is the read surface. `activity(limit)` returns the feed as it arrives — one
`Boost` per live 24-hour slot, then `DayCard`s, each with per-group `counts` and the entries
inside `groups` (`listen`, `add`, `playlist`, `other`). `comments(kind, target, after)` returns
one page: the target's `summary`, the `comments` with their likes and `can_edit`, and
`next_after`. `summary(kind, target)` and `summaries(kind, targets)` answer the same counts for
one target or for a whole grid in one request.

The writes were never versioned, so they go where `social/api.py` puts them: `comment`,
`edit_comment`, `delete_comment`, `vote` and `like` build `/api/social/<kind>/<target>/…`, while
`boost` posts to `/api/boost`. Every write answers the target's summary, so nothing is re-read
afterwards.

```python
from sdk import Session, SocialClient, probe

caps = probe('http://10.0.0.20:8460')
client = SocialClient('http://10.0.0.20:8460', caps, Session.from_cookie(session_value))

for item in client.activity(limit=40):
    if item.kind == 'boost':
        print(item.member.name, 'boosted', item.track.title)   # expires_at is the server's
    else:
        print(item.member.name, item.day, item.counts)         # groups hold the entries

page = client.comments('track', '7')          # page.summary, page.comments, page.next_after
client.vote('track', '7', 1)                  # 0 retracts the vote
client.comment('track', '7', 'plain text')    # the 1–2000 character rule is the server's
client.like('track', '7', page.comments[0].id)   # toggles; there is no unlike route
```

`kind` is `track`, `playlist` or `album`; an album's target is the 64-character key
`social.album_id` produces. Neither is validated here — the server answers `400` or `404`, and
`sdk.social.NotFound` is how that arrives.

A write needs the member's browser session: the `__Host-hl_session` cookie value, plus the
`Origin` header `security.csrf` compares against `Host`. Both travel in headers, never in a URL;
nothing is stored, and no message the module raises carries either. Between them, the refusals say
what happened: `Unavailable` when the capability document does not advertise the flag,
`SignInRequired` for a missing or expired session, `RateLimited` with `retry_after` for a spent
bucket, `Denied`/`NotFound` for anything the server answered with `{"error": "..."}`, and
`ClientError` when the answer was not an answer at all.

Boosts are the one thing the document has no flag for: `server.py` advertises the domain's
`comments`, `votes`, `likes` and `activity`, and no `boosts`, so a boost is gated on
`social.available` rather than on a flag invented in the client.

`SocialClient` takes a `Capabilities` value or the raw document; given an empty one it refuses
every call, because a client that guesses "surely it has that" is what `sdk.capabilities` exists
to prevent.

## Repository

`/api/v1/repo/*` is the server's Repository — the shared catalogue, not anyone's library.
`api/v1.py` registers five reads and nothing else, and that is the whole of this module:

| call | route | answers |
| --- | --- | --- |
| `snapshot()` | `GET /api/v1/repo/snapshot` | `Snapshot`: follows (`Subscription` rows), the queue's `stats`, `current`, `feed_new` |
| `releases(watch, offset=0, limit=60)` | `GET /api/v1/repo/releases?watch=&offset=&limit=` | `ReleasePage`: `releases`, a page of `tracks`, `track_total`, `offset`, `page`, `fetched` |
| `tracks(url)` | `GET /api/v1/repo/tracks?url=` | the songs inside one album page, as `Track` rows |
| `artist(name)` | `GET /api/v1/repo/artist?name=` | `ArtistPage`: `providers`, `subscribed`, `watches`, `feed`, `tracks`, `downloaded` |
| `subscriptions()` | `GET /api/v1/repo/subscriptions` | every followed artist, each with the server's `mine` |

```python
from sdk import RepoClient, Session, probe

caps = probe('http://10.0.0.20:8460')
repo = RepoClient('http://10.0.0.20:8460', caps, Session.from_cookie(session_value))

follows = repo.subscriptions()            # row.mine is the server's answer, not this client's
page = repo.releases(follows[0].id, offset=0, limit=60)
songs = repo.tracks(page.releases[0].source_url)
```

Nothing here decides anything. Which releases exist, whether a follow is yours, what a
subscription entitles a member to and whether a source page is readable are all
`repo/service.py`'s answers, carried through unchanged; `limit` is the server's to clamp
(1–200), and an unknown `watch` is its `400`.

**This is the module the player must never import.** The player is a streaming client, and
acquisition code in a store build is what PLAYER-007 prevents, so the rule is asserted rather
than promised: `tests/test_player_store_safe.py` reads the five routes out of `api/v1.py`, then
requires that no assembled file — and no file reachable from the player's entry documents —
names one of them or this module, and that the build refuses `sdk/repo.py` outright. That proves
what the player serves and loads; it cannot prove a player script would never build a route by
string concatenation or fetch a `.js` at runtime, and an edit to `assemble.mjs` is outside it.
Nothing under `sdk/` may be imported by `web/` or `player/shell/` for the same reason.

Against this server every call refuses, and that is the honest answer rather than a bug. The
capability document `server.py` publishes names `accounts`, `library`, `playback`, `party`,
`social` and `discovery` (`search`, `sonic_analysis`) — and **no `repo` domain**, no acquisition
flag anywhere. Acquisition is reachable over HTTP on those five routes, but it is not in the
contract, so `Unavailable` says so, quoting `discovery`'s own flags so a caller can see that one
domain was not quietly translated into another. A server that adds a `repo` section needs no
change here; a client does not invent the section, and no flag inside it is guessed at.

The refusal classes stay in the module, like Social's: `sdk.repo.Unavailable` (`needed='repo'`),
`Denied`/`NotFound`/`SignInRequired`/`RateLimited` for an `{"error": "..."}` answer, and
`ClientError` when the answer was not an answer at all. `Session` comes from `sdk.session` — its
own module since the second client needed it, because a cookie type that lives inside the first
module to use it makes the second one import a surface it does not speak. `sdk.social.Session`
and `sdk.repo.Session` are that same object, not copies of it, and a test asserts the identity
rather than the equality. In hosted mode the reads send it in a header, never in a URL and never
in a message.

## What it deliberately does not do

- **No acquisition of its own.** `sdk.repo` reads `/api/v1/repo/*`; it never searches a
  provider, queues a download, decides what is downloadable or writes a follow. Following,
  checking and pausing an artist are Repo's writes, unversioned and deliberately not called here.
  The capability document names no `repo` domain, so `caps.features.repo.available` is `False`
  and every Repo call refuses until a server says otherwise.
- **No federation.** This server does not advertise federation yet — there is no flag in
  `/api/v1/capabilities` — so the client reports `federation = False` and does not invent a
  cross-instance feature from the app's plans.
- **No credential storage.** No keychain, no config file, no cache: a caller owns its secrets.
- **No XML.** Only the JSON envelope is parsed.
- **No playlist writes on this server.** `create_playlist`, `update_playlist` and
  `delete_playlist` are sent like the spec says, and `subsonic.py`'s `Unsupported endpoint`
  becomes `NotSupported`. The read path (`playlists`, `playlist`) is real.
- **No silent degradation of a claimed feature.** When `probe` cannot read the document it
  returns `degraded=True` with the OpenSubsonic core only — `subsonic.enabled` True, no
  extensions, no transcode targets, every optional domain unavailable.
- **No Social of its own.** `sdk.social` is an HTTP client: it does not build the feed, filter
  members, recompute a score or decide visibility. The day cards, the named voters, `can_edit`
  and the target validation are all the server's answers, carried through unchanged.
- **No member profiles, no play reporting.** `/api/member/<id>` and `/api/activity/listen`
  belong to the cards that own them; this module does not call them.
- **No unversioned discussion reads.** `GET /api/social/<kind>/<target>` answers the same page as
  the versioned route, so only the versioned one is called.
- **No Repository writes.** `subscribe`, `check`, `set_enabled`, `remove` and `release_seat` are
  Repo's, and unversioned; this module reads. It also does not re-declare the queue's own fields
  inside `snapshot().stats`/`current`, because that would be a second definition of acquisition.
- **No unlike and no DELETE.** `like` toggles on the route `social/api.py` matches, and a comment
  is removed by posting `{"delete": true}` to its edit route — the shapes the adapter defines,
  not the ones a REST client would expect.

Tests: `python3 -m unittest tests.test_sdk`, `tests.test_sdk_social` and `tests.test_sdk_repo`
(a stub `http.server` on 127.0.0.1 each; no real server, no network), and
`python3 -m unittest tests.test_player_store_safe` for the player's independence.