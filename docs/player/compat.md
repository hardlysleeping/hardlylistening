# Compatibility: two servers, one suite

Purpose: make "the client works against a Subsonic server" a claim about servers that exist, not
about a payload someone wrote by hand. P5 of the plan.

`player/ui/src/lib/subsonic/compat.test.ts` runs one suite over every recording in
`player/ui/tools/fixtures/compat/`. A recording is what a real server answered — status line,
content type, raw body — captured by `tools/record-compat.mjs`. Nothing in the directory is typed.

## What was recorded, and when

| fixture | server | protocol | `openSubsonic` | extensions |
| --- | --- | --- | --- | --- |
| `navidrome.json` | Navidrome 0.64.2 (10114574) | 1.16.1 | `true` | 7, from `getOpenSubsonicExtensions` only |
| `supysonic.json` | Supysonic 0.8.0 | 1.12.0 | key absent | none; the endpoint answers HTTP 404, code 0 |

Both were recorded 2026-11-15 from throwaway local instances (a Navidrome container, a Supysonic
virtualenv) serving three generated MP3s: two albums, two artists, no playlists, nothing starred.
Nine endpoints each: `ping`, `getOpenSubsonicExtensions`, `getAlbumList2`, `getArtists`,
`getPlaylists`, `getStarred2`, `search3`, `getAlbum`, `getLyricsBySongId`.

## What the two servers actually disagree about

These are the differences the suite pins, and each one was found by recording rather than by
reading a specification:

| difference | Navidrome | Supysonic | what the client does |
| --- | --- | --- | --- |
| identity in `ping` | `type`, `serverVersion`, `openSubsonic` | none of the three | defaults `type` to `"subsonic"`, leaves the rest empty, never infers a name |
| protocol version | `1.16.1` | `1.12.0` | reads the envelope's own `version`, does not require 1.16.1 |
| extension list | only from `getOpenSubsonicExtensions` | endpoint missing | asks the endpoint **once** when `ping` says `openSubsonic: true` and carried no list |
| an endpoint it lacks | — | HTTP 404 with an envelope error, code 0 | code 0 is "unsupported", not "wrong request"; the sign-in stands |
| empty collections | `{"playlists":{}}`, `{"starred2":{}}` | same | an empty object is an empty list, never an error |
| album entry | counts only, no tracks | counts only, no tracks | a list entry has no `song`; only `getAlbum` does |

The first row is why the client changed in this phase: before it, `Navidrome` announced
OpenSubsonic in `ping` and the client went on believing it supported zero extensions, so
`lyrics()` returned `null` without ever asking. That is a capability bug that no hand-written
fixture would have shown, because the hand-written one included extensions in `ping` — the shape
the author expected.

## The strict transport

The suite's `FixtureTransport` **throws** for any endpoint the recording does not contain. That is
deliberate: a client that starts asking for something new fails here instead of passing against a
fixture that happens to be silent. It also mirrors `WebTransport`'s own rules — a body that is not
JSON is `NotSubsonicError`, a 401/403 is code 40 — so the responses are read the way they are read
at runtime.

`describe.each` derives the cases from the directory, and three meta-tests guard the evidence:

* at least two fixtures, and they must **disagree** about something in the table above — two
  servers answering identically would mean one of them proves nothing;
* every fixture carries `recordedAt` and `recordedFrom` (a `127.0.0.1` port), so a fixture is
  traceable to a recording rather than to an editor;
* no fixture contains `p`, `t`, a 32-hex token or the fixture password.

## Re-recording

The rig is three commands. It needs `docker` for Navidrome and a Python virtualenv for Supysonic;
neither is part of the app.

```sh
# a library: three files, enough for two artists and two albums
ffmpeg -f lavfi -i "sine=frequency=440:duration=3" -metadata title="First Song" \
  -metadata artist="Compat Artist" -metadata album="Compat Album" 01-first.mp3

# Navidrome
docker run -d --name compat-navidrome -p 127.0.0.1:4533:4533 \
  -v "$PWD/music:/music:ro,z" -v "$PWD/nd:/data:z" \
  -e ND_MUSICFOLDER=/music -e ND_DATAFOLDER=/data -e ND_SCANSCHEDULE=0 deluan/navidrome:latest
curl -X POST http://127.0.0.1:4533/auth/createAdmin \
  -H 'Content-Type: application/json' -d '{"username":"compat","password":"…"}'

# Supysonic (config: database_uri, scanner_extensions "mp3 ogg flac m4a" — spaces, not commas)
python3 -m venv venv && ./venv/bin/pip install supysonic waitress
./venv/bin/supysonic-cli folder add Music "$PWD/music"
./venv/bin/supysonic-cli folder scan
./venv/bin/supysonic-cli user add compat -p …
./venv/bin/supysonic-server -h 127.0.0.1 -p 4757 &

# then, once per server
node tools/record-compat.mjs --origin http://127.0.0.1:4533 --user compat --password … \
  --name navidrome --out tools/fixtures/compat/navidrome.json
node tools/record-compat.mjs --origin http://127.0.0.1:4757 --user compat --password … \
  --name supysonic --out tools/fixtures/compat/supysonic.json
```

Two traps worth writing down, both hit while making this:

* **SELinux.** This host is Fedora-based; a bind mount without `:z` is unreadable inside the
  container and Navidrome fails at startup with "unable to open database file".
* **`scanner_extensions` splits on whitespace.** `mp3,ogg,flac` parses as one extension called
  `mp3,ogg,flac`, so the scanner finds nothing and reports a clean scan of an empty library.

A third server was tried and is **not** a fixture: Gonic 0.23.0. It authenticates and answers
`ping` (`type: gonic`, `openSubsonic: true`, no extensions), but its scanner indexed nothing from
this library — `processing folder "/music"` followed by `+0/0 tracks` with no error — so a
recording from it would have been nine refusals and no library. It is listed here so the next
person does not spend the same hour on it.

## What this proves, and what it does not

* **It proves the client's reading of two real servers' bytes**, including the empty-object and
  missing-field shapes, and that a server which cannot page albums or has no lyrics extension is
  handled as a fact about the server.
* **It does not prove the two servers are exhaustive.** They are Go and Python, OpenSubsonic and
  plain Subsonic 1.12; Airsonic-Advanced, Ampache, LMS and others are untested. The suite is the
  thing to extend: add a JSON file and the cases follow.
* **It does not exercise the network path.** `FixtureTransport` replays stored bodies; TLS,
  redirects, timeouts, streaming and the native transport are elsewhere (`transport.test.ts`,
  `docs/player/transport.md`).
* **Recordings age.** Navidrome 0.64.2 and Supysonic 0.8.0 are the versions that answered. A later
  server that changes a shape will not break this suite — it will break a real connection first.
  Re-record before making a claim about a newer release.