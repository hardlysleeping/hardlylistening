"""The acquisition descriptor: the one acquisition artefact that may cross a boundary.

`FED-MIRROR-001`. A peer that mirrors this node's catalogue does not need the library, a member's
state or the queue. It needs to know *which provider result to ask for again* — the provider, the
provider's own track id, and the metadata re-acquisition needs to recognise the same recording
(`FED-MIRROR-002`). That is a descriptor, and nothing else about acquisition should ever leave.

Three rules, because the descriptor is untrusted in both directions:

* **An allow-list, not a scrubber.** Only `FIELDS` are carried. Any other input key refuses to
  build, including keys that are merely uninteresting: a scrubber that silently drops is one edit
  away from dropping the wrong thing, and "it was in the input and is not in the output" is not
  something a caller can see.
* **Credential and path material refuses to build.** Cookies, headers, tokens, passwords, API
  keys, sessions and file paths are recognised by field name (`_forbidden`). The refusal names the
  field and never the value, so the refusal itself can be logged safely.
* **A URL is http(s) or nothing.** `source_url` is the one field that could describe a file rather
  than a page, so it is checked; that is what keeps a descriptor from pointing at somebody's disk.

`dumps` is canonical — sorted keys, no whitespace, no NaN, UTF-8 — so two nodes that agree on the
descriptor agree on its bytes. `parse` re-checks everything, because a descriptor off the wire is
input, not truth. Nothing here writes to disk and nothing here reads a credential.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping

#: Bumped when the carried fields or their meaning change; a reader refuses what it does not know.
VERSION = 1

#: Carried, in this order. `version` is written first so a reader can refuse before trusting a body.
FIELDS = ("version", "provider", "provider_track_id", "title", "artist", "album", "duration",
          "source_url")

#: Provider metadata that is safe but not needed to re-acquire. Accepted and left behind, so a
#: `TrackRef` can be handed over whole without the descriptor growing fields no mirror uses.
IGNORED = ("artwork", "isrc", "track_number", "release_id")

#: The names a provider result already uses for the same thing. A caller should not have to
#: translate its own result into the descriptor's vocabulary to be allowed to hand it over.
ALIASES = {
    "engine": "provider",
    "source_id": "provider_track_id",
    "url": "source_url",
    "webpage_url": "source_url",
    "uploader": "artist",
}

#: Longest free text a descriptor will carry. A peer sending a megabyte of title is not metadata.
MAX_TEXT = 300

#: Matched as whole tokens, never as substrings: "author" must not read as "auth". Everything is
#: lowercased and split on non-alphanumerics first, so `api_key`, `apiKey` and `Api-Key` agree.
SECRET_TOKENS = frozenset({
    "auth", "authorization", "bearer", "cookie", "cookiefile", "cookies", "credential",
    "credentials", "header", "headers", "key", "token", "password", "passwd", "passphrase",
    "secret", "session", "signature", "sig", "apikey", "nonce",
})
PATH_TOKENS = frozenset({
    "file", "filename", "filepath", "dir", "directory", "path", "localfile", "cwd", "outtmpl",
    "root",
})


class UnsafeDescriptor(ValueError):
    """An input carrying credential or filesystem material, or a field off the allow-list.

    `fields` names what was refused. Values are deliberately absent: this exception is meant to be
    readable in a log, and the whole point is that it cannot leak what it refused.
    """

    def __init__(self, fields, why: str):
        self.fields = tuple(fields)
        self.why = why
        super().__init__(f"{why}: {', '.join(self.fields)}")


def _verdict(name: str) -> str:
    """What a field name is: `carried`, `ignored`, `forbidden` or `unknown`.

    Decided by the name alone. A title is allowed to be called "Password"; a field is not.
    """
    key = str(name or "").strip().lower()
    if key in FIELDS or key in ALIASES:
        return "carried"
    if key in IGNORED:
        return "ignored"
    tokens = {token for token in re.split(r"[^a-z0-9]+", key) if token}
    if tokens & SECRET_TOKENS or tokens & PATH_TOKENS:
        return "forbidden"
    return "unknown"


def _refuse(results: dict) -> None:
    forbidden = results.get("forbidden") or []
    unknown = results.get("unknown") or []
    if forbidden:
        raise UnsafeDescriptor(forbidden, "refusing credential or path material")
    if unknown:
        raise UnsafeDescriptor(unknown, "field not in the acquisition descriptor allow-list")


def _input(result) -> dict:
    """The result as descriptor fields only, with aliases resolved.

    A mapping is checked key by key, because it may have come from a JSON blob. An object (a
    `providers.base.TrackRef`) is *read* by name instead: its fields are known to the code that
    made it, and enumerating a live object's attributes to police them would be a worse rule than
    never reading them.
    """
    if result is None:
        raise ValueError("no provider result to describe")
    if isinstance(result, Mapping):
        raw = dict(result)
    else:
        raw = {}
        for name in (*FIELDS, *IGNORED, *ALIASES):
            if hasattr(result, name):
                value = getattr(result, name)
                if value not in (None, ""):
                    raw[name] = value

    resolved: dict = {}
    refused = {"forbidden": [], "unknown": []}
    for name, value in raw.items():
        verdict = _verdict(name)
        if verdict in refused:
            refused[verdict].append(str(name))
            continue
        if verdict == "ignored":
            continue
        resolved[ALIASES.get(str(name), str(name))] = value
    _refuse(refused)
    return resolved


def validate(payload) -> dict:
    """The canonical descriptor, or a refusal. Every field is re-derived, never trusted.

    Used by `dumps` and `parse` as well as `build`, so a descriptor that crossed the wire and one
    that was just built pass through exactly the same gate.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("a descriptor is a JSON object")

    refused = {"forbidden": [], "unknown": []}
    for name in payload:
        verdict = _verdict(name)
        if verdict == "forbidden":
            refused["forbidden"].append(str(name))
        elif verdict != "carried" or str(name) in ALIASES:
            # A wire descriptor speaks the canonical names; an alias is the caller's shorthand.
            refused["unknown"].append(str(name))
    _refuse(refused)

    version = payload.get("version")
    if type(version) is not int or version != VERSION:
        raise ValueError(f"unsupported descriptor version: {version!r}")

    text = {}
    for name in ("provider", "provider_track_id", "title", "artist", "album"):
        value = str(payload.get(name) or "").strip()
        if len(value) > MAX_TEXT:
            raise ValueError(f"{name} is longer than {MAX_TEXT} characters")
        text[name] = value
    for name in ("provider", "provider_track_id", "title"):
        if not text[name]:
            raise ValueError(f"{name} is required")

    try:
        duration = float(payload.get("duration") or 0)
    except (TypeError, ValueError):
        raise ValueError("duration is not a number") from None
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("duration must be a finite number of seconds")

    source_url = str(payload.get("source_url") or "").strip()
    if source_url and not re.match(r"^https?://", source_url, re.I):
        raise ValueError("source_url must be an http(s) link")

    return {
        "version": VERSION,
        "provider": text["provider"],
        "provider_track_id": text["provider_track_id"],
        "title": text["title"],
        "artist": text["artist"],
        "album": text["album"],
        "duration": duration,
        "source_url": source_url,
    }


def build(result) -> dict:
    """A descriptor from one provider result — or a refusal, never a partial descriptor."""
    payload = _input(result)
    payload.setdefault("version", VERSION)
    return validate(payload)


def dumps(descriptor) -> str:
    """The canonical encoding: the same descriptor is always the same bytes."""
    return json.dumps(validate(descriptor), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=True)


def parse(text) -> dict:
    """A descriptor from another node: decoded, checked and returned canonically."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"not a descriptor: {error}") from None
    return validate(payload)