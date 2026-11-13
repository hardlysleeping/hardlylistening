# Client SDK

Purpose: let another program — a player, a script, a test — talk to this server without
importing it. Two small modules, Python 3 standard library only, no build step and no
dependencies:

| module | answers |
| --- | --- |
| `sdk.subsonic` | a device's library, playlists, search, stars, bookmarks, stream URLs and byte ranges |
| `sdk.capabilities` | which optional domains this server advertises, and how to degrade when it will not say |

`subsonic.py` is the authority on what the surface answers, and the client is written against
it: endpoints it implements are here, and endpoints it refuses raise `NotSupported` rather than
returning an empty result that looks like success. The JSON envelope is the only form parsed
(`f=json`), because a caller that asks for JSON never needs the XML branch.

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

Tests: `python3 -m unittest tests.test_sdk` (a stub `http.server` on 127.0.0.1; no real server,
no network).