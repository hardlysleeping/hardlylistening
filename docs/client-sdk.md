# Client SDK

Purpose: let another program — a player, a script, a test — talk to this server without
importing it. Three small modules, Python 3 standard library only, no build step and no
dependencies:

| module | answers |
| --- | --- |
| `sdk.subsonic` | a device's library, playlists, search, stars, bookmarks, stream URLs and byte ranges |
| `sdk.capabilities` | which optional domains this server advertises, and how to degrade when it will not say |
| `sdk.social` | the Activity feed, comments, votes, likes and boosts — or a refusal naming the flag that is missing |

`subsonic.py` is the authority on what the surface answers, and the client is written against
it: endpoints it implements are here, and endpoints it refuses raise `NotSupported` rather than
returning an empty result that looks like success. The JSON envelope is the only form parsed
(`f=json`), because a caller that asks for JSON never needs the XML branch. Social follows the
same rule with its own authorities: `api/v1.py` for the three versioned reads, `social/api.py`
and `server.py` for the writes they never versioned.

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

## What it deliberately does not do

- **No Repo acquisition.** Searching a provider and queueing a download is Repo's, not the
  protocol's, and a Subsonic client must keep working against a server that has no queue. The
  capability document does not advertise a `repo` domain, so `caps.features.repo.available` is
  `False` and a caller offers no acquisition.
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
- **No unlike and no DELETE.** `like` toggles on the route `social/api.py` matches, and a comment
  is removed by posting `{"delete": true}` to its edit route — the shapes the adapter defines,
  not the ones a REST client would expect.

Tests: `python3 -m unittest tests.test_sdk` and `python3 -m unittest tests.test_sdk_social` (a stub
`http.server` on 127.0.0.1 each; no real server, no network).