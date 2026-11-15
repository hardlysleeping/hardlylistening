# Extensions

Opt-in and disabled by default. Installing one grants nothing; an administrator enables it by name,
and that is the decision that matters. **This is not a sandbox** — read that section before
installing anything.

Two files make the host:

| file | what it is |
| --- | --- |
| `extensions.py` | the grant: manifest validation, the closed capability set, install → enable → revoke → remove. Runs nothing. |
| `extension_host.py` | the loader: one extension per run in a child process, every call mediated, every call written down. |
| `extension_shim.py` | the child half. Imports nothing from this application. |

## Where the code lives

    <DATA_DIR>/extensions/<id>/manifest.json
    <DATA_DIR>/extensions/<id>/<entry>          (usually main.py)

`EXTENSIONS_DIR` follows the same rule as every other runtime path (`paths.py`): under
`HARDLYLISTENING_DATA_DIR` when one is declared, otherwise `<app>/extensions`. In a checkout nothing
creates it at boot — an administrator does.

Installation is **from disk**, not from a request body. There is deliberately no upload route: this
runs as the server's own user, and accepting code over HTTP is a bigger decision than this host
makes. Copy a directory in, then:

```sh
curl -b 'session' -H 'Origin: https://music.example' -H 'Content-Type: application/json' \
     -d '{"id":"example.shows"}' https://music.example/api/extensions/install
```

`GET /api/extensions` answers what is installed **and** what is sitting on disk with a manifest — the
second half is how "I put it there and nothing happened" stops being a mystery. A broken manifest is
reported with its own error rather than hidden.

Then `enable`, and a run:

```sh
curl -b 'session' -H 'Origin: …' -X POST https://music.example/api/extensions/example.shows/enable
curl -b 'session' -H 'Origin: …' -X POST https://music.example/api/extensions/example.shows/run
curl -b 'session' https://music.example/api/extensions/example.shows/calls
```

There is no web UI for any of this yet; the API is the surface.

## The manifest

```json
{
  "schema": 1,
  "id": "example.shows",
  "name": "Example shows",
  "version": "1.0.0",
  "description": "Upcoming shows on the artist page",
  "entry": "main.py",
  "capabilities": ["library.read", "network"],
  "hosts": ["api.example.org", "*.example.org"]
}
```

`id` is 2–64 characters of `a-z`, `0-9`, `.`, `-`, `_` and must match its directory. `version` is
semantic. `entry` must stay inside the extension's own directory (a symlink out is refused at
install). `hosts` are bare hostnames or `*.` wildcards and require the `network` capability.

An unknown capability **fails the whole manifest** instead of being dropped, so a typo cannot
quietly shrink what the author thought they were asking for.

| capability | offered today | what it means |
| --- | --- | --- |
| `library.read` | yes | the triggering member's own library, paged |
| `social.read` | yes | the Activity feed, capped |
| `network` | yes | one GET to a declared host |
| `library.write` | **no** — named refusal | create or change the member's saves, playlists, folders |
| `social.write` | **no** — named refusal | post activity, comments, votes as the member |
| `background` | **no** — named refusal | scheduled work while the server is up |
| `acquisition` | never | reserved: a separate per-member permission (`acquisitions.py`), refused at install time |

A refusal is a sentence the extension receives: `library.write is not offered by this host yet`. It
is not a silent no-op.

## The entry file

Define `run(extension)` or `main(extension)`. Its return value is the run's output and must be
JSON-serialisable and at most 64 KiB. A module with neither function is allowed — importing it is
the program — and reports no output.

```python
def main(extension):
    page = extension.library(limit=50)
    artists = sorted({track["artist_name"] for track in page["tracks"]})
    return {"count": page["total"], "artists": artists}
```

Anything the extension prints goes to **stderr** (kept, bounded, beside the run); stdout is the
protocol. An extension that prints cannot corrupt its own channel.

### What `extension` offers

| call | returns |
| --- | --- |
| `extension.library(offset=0, limit=200)` | `{"tracks": [...], "total": n, "offset": o, "limit": l}`, `limit` capped at 1000 |
| `extension.social(limit=50)` | `{"items": [...]}`, `limit` capped at 200 |
| `extension.fetch(url)` | `{"status", "headers", "body"}` — one GET, https, declared host, no redirects |
| `extension.call(method, **params)` | the raw form of the three above |

A track is exactly ten fields: `id`, `artist_name`, `album`, `title`, `track_number`, `duration`,
`isrc`, `bpm`, `added`, `art_hash`. **`file` and `source_url` are never included**: the first is a
path on this server, the second is the provider's own URL, and those are how a credential would
leave. A read grant is not a filesystem grant.

A refused call raises `RuntimeError` with the reason, so an extension can catch it and report
something useful instead of dying.

## Limits

| what | value |
| --- | --- |
| one run, wall clock (including interpreter start and import) | 20 s, then the child is killed and the run is `timeout` |
| output | 64 KiB |
| one response body | 2 MiB |
| audit retention | newest 200 runs and 500 calls per extension |
| run rate | 10 per minute per administrator |

`network` also refuses: a host that was not declared, anything but `https` (unless the loopback
test hatch `HARDLYLISTENING_EXTENSION_ALLOW_LOCAL=1` is set), a URL carrying credentials, and every
redirect — a redirect is a second request to a host nobody declared.

The audit stores `scheme://host/path` for a network call, never the query string, because a token in
a query must not become a log line.

## Isolation, stated honestly

The child runs as the **same user** with the **same filesystem**. What the process boundary actually
buys:

* `python3 -I -B` keeps this application off `sys.path` and ignores `PYTHONPATH`, so `import server`,
  `import db` or `import security` fails inside an extension by construction. The environment holds
  only `PATH`, `HOME` and a locale — no database path, no key material.
* The grant is read on **every call**, not cached, so revoking takes effect on the next call and no
  new run can start.
* Every call and run is a row. "What did it ask for" is a query.

What it does **not** buy: containment. An extension can read the filesystem, open its own sockets
and read any file this user can. There is no seccomp, no container, no capability drop. Trusting an
extension is trusting the code, and the only safe posture is to read it first.

One more consequence worth knowing: the child puts its own directory first on `sys.path`, so an
extension file named `json.py` shadows the standard library **for that extension**. That is the
extension's own problem, not the server's.

## Protocol

For debugging a stubborn extension, the wire is one JSON object per line:

    child → host   {"call": 1, "method": "library.read", "params": {}}
    host  → child  {"result": 1, "value": {…}}   |   {"error": 1, "message": "…"}
    child → host   {"done": true, "output": …}   |   {"fatal": "traceback"}

The host never trusts these lines: a message over 1 MiB, a line that is not JSON, or an object that
is not a message ends the run.

## Statuses

A run is `finished`, `failed` (the extension raised, or its output was not JSON), `crashed` (the
process exited without finishing) or `timeout`. `GET /api/extensions/<id>/calls` returns the runs
and the calls, newest first, with the last 4 KiB of stderr kept for each run.