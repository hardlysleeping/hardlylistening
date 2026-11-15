"""The mirror: where a descriptor is re-acquired from, and whether the bytes are the same song.

`FED-MIRROR-002`, `FED-MIRROR-003` and `FED-MIRROR-004`. `repo/federation.py` answers *which
provider result to ask for again*; this module answers the three questions that come after it:

* **002 — the original source.** Given a descriptor, decide what this node asks for: the provider
  page it names, or a search query built from the metadata, and what would prove the answer is the
  same recording (the provider's own id when the answer carries it, otherwise duration inside a
  tolerance and a title that is not a different song).
* **003 — a trusted peer.** When the source will not answer, a copy may come from a peer this node
  was configured to trust. The peer list is an allow-list typed by an operator, never a set of
  addresses learned from the wire: that is what makes a peer trusted, and it is why there is no
  DNS guard here — the destination cannot come from a peer in the first place. Redirects are
  refused rather than re-validated, because a redirect is the one way an allow-listed address
  could turn into a different one.
* **004 — the modes.** Whether bytes are fetched at all: never (`metadata`), only for a song
  somebody played (`cache`), only for a song somebody picked (`selected`), or for everything
  (`all`). The mode is one row, and an unknown value is a refusal rather than a silent default:
  a typo must not be the thing that starts mirroring an entire library.

Nothing here picks a source for the player, and nothing here is on a request path. `plan` and
`matches` are pure; `copy_from_peer` is the one function that speaks to a socket, and only to an
address an operator pinned.

The serving half is **not built**: no route hands these bytes to a peer. A peer has to be
authenticated before it can be given music, and the node identity is opaque key material with no
signature scheme yet (`docs/federation.md` §3). The contract this side would call is written down
there so the two halves cannot disagree later.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import paths
from downloaders import ytdlp
from providers import base as provider_base
from repo import federation

#: Modes, narrowest first. `metadata` is the default: a node federates descriptors and nothing else
#: until somebody says otherwise.
MODES = ("metadata", "cache", "selected", "all")
DEFAULT_MODE = "metadata"

#: Two settings rows: the mode, and the peer allow-list as JSON.
MODE_KEY = "federation_mode"
PEERS_KEY = "federation_peers"

#: The one path a peer is expected to answer. `<digest>` is the MD5 of the descriptor's canonical
#: bytes (`federation.dumps`), so both sides name the same track without another exchange.
PEER_PATH = "/federation/audio/"

#: A duration check is a check, not a vote: three seconds of encoder padding is normal, and so is
#: two per cent on a long mix.
DURATION_GRACE = 3.0
DURATION_RATIO = 0.02

#: Below this title similarity the answer is a different song, whatever the duration says.
TITLE_FLOOR = 0.5

#: One peer copy is bounded. A song is not a film; 64 MiB is far past any lossless track and well
#: short of the disk guard.
MAX_PEER_BYTES = 64 * 1024 * 1024

#: Seconds. A peer that cannot answer in this long is not a mirror, it is a stall.
PEER_TIMEOUT = 30.0


class MirrorRefused(ValueError):
    """A decision this module will not make. The message is safe to log: no URL, no credential."""


# -- 002: the original source ---------------------------------------------------------------------

def provider_for(name: str):
    """The provider object behind a descriptor's `provider`, or a refusal.

    A name is matched exactly against the registry, never by prefix: a descriptor says
    `soundcloud`, and an unknown name is a peer speaking a vocabulary this node does not have.
    """
    wanted = str(name or "").strip().lower()
    for provider in provider_base.registry():
        if provider.name.lower() == wanted:
            return provider
    raise MirrorRefused("no provider of that name is installed")


def digest(descriptor) -> str:
    """The canonical name of one acquisition: MD5 of the canonical bytes.

    The same digest names the peer request and the cache entry, so a node that has the descriptor
    can name the file it would ask for without a second round trip.
    """
    return hashlib.md5(federation.dumps(descriptor).encode("utf-8")).hexdigest()


def plan(descriptor) -> dict:
    """Where to ask again, and what would prove it is the same recording.

    Returns `{provider, how, url, query, track_id, duration, grace}`. `how` is `url` when the
    descriptor names a page its provider claims, `search` when it does not — a provider's own id is
    not a URL any downloader accepts, so it is carried for verification rather than turned into a
    request.
    """
    clean = federation.validate(descriptor)      # a descriptor from a peer is input, not truth
    provider = provider_for(clean["provider"])
    if not provider.direct_audio:
        # Apple Music names a recording; it does not serve one. Asking it for audio would fail
        # late and expensively, so it is refused here with the reason a person needs.
        raise MirrorRefused(f"{clean['provider']} is a metadata provider and cannot supply audio")

    url = clean["source_url"]
    if url and provider.patterns and not provider.identify(url):
        # The descriptor says one provider and names another provider's page. One of the two is a
        # lie, and a node that guessed which would be the one that got it wrong.
        raise MirrorRefused("the descriptor names a page its provider does not claim")
    if url:
        return {"provider": clean["provider"], "how": "url", "url": url, "query": "",
                "track_id": clean["provider_track_id"], "digest": digest(clean),
                "duration": clean["duration"],
                "grace": max(DURATION_GRACE, clean["duration"] * DURATION_RATIO)}

    query = " ".join(part for part in (clean["artist"], clean["title"]) if part).strip()
    if not query:
        raise MirrorRefused("nothing to search for: no page, no title")
    return {"provider": clean["provider"], "how": "search", "url": "", "query": query,
            "track_id": clean["provider_track_id"], "digest": digest(clean),
            "duration": clean["duration"],
            "grace": max(DURATION_GRACE, clean["duration"] * DURATION_RATIO)}


def matches(descriptor, found) -> tuple[bool, list[str]]:
    """Is this what the descriptor described? `(ok, reasons)`; the reasons name every failure.

    The provider's own id settles it when both sides have one — the same result, asked twice. When
    it does not, the checks are the ones a person would make: the length, and whether the title is
    a different song. A duration nobody recorded is not evidence either way, so it is reported as
    unverified rather than as a mismatch; refusing there would reject every row a provider listed
    without a length.
    """
    clean = federation.validate(descriptor)
    got = found or {}
    reasons: list[str] = []

    got_provider = str(got.get("provider") or "").strip().lower()
    got_id = str(got.get("provider_track_id") or "").strip()
    if got_provider and got_provider != clean["provider"]:
        reasons.append("a different provider answered")
    if got_provider == clean["provider"] and got_id and got_id == clean["provider_track_id"]:
        return True, ["the provider's own id"]

    try:
        got_duration = float(got.get("duration") or 0)
    except (TypeError, ValueError):
        got_duration = 0.0
    grace = max(DURATION_GRACE, clean["duration"] * DURATION_RATIO)
    if clean["duration"] > 0 and got_duration > 0:
        delta = abs(got_duration - clean["duration"])
        if delta > grace:
            reasons.append(f"duration differs by {delta:.0f}s (more than {grace:.0f}s)")
    else:
        reasons.append("duration unverified")

    got_title = str(got.get("title") or "").strip()
    if got_title and clean["title"]:
        if ytdlp.similarity(clean["title"], got_title) < TITLE_FLOOR:
            reasons.append("the title is a different song")

    return not [reason for reason in reasons if reason != "duration unverified"], reasons


# -- 003: a trusted peer --------------------------------------------------------------------------

def _allow_local() -> bool:
    """Loopback peers, for tests only. A production peer list is https and nothing else."""
    return os.getenv("HARDLYLISTENING_ALLOW_LOCAL_PEERS", "").lower() in ("1", "true", "yes")


def _clean_peer_url(raw: str) -> str:
    """An allow-listed peer address, or a refusal. Never a URL off the wire."""
    text = str(raw or "").strip()
    if not text:
        raise MirrorRefused("a peer needs an address")
    if len(text) > 2048:
        raise MirrorRefused("a peer address is longer than 2048 characters")
    parsed = urlsplit(text)
    if parsed.username is not None or parsed.password is not None:
        raise MirrorRefused("a peer address must not embed credentials")
    scheme = parsed.scheme.lower()
    if scheme == "https":
        pass
    elif scheme == "http" and _allow_local() and _is_loopback(parsed.hostname or ""):
        pass
    else:
        raise MirrorRefused("a peer must be an https address")
    if not parsed.hostname:
        raise MirrorRefused("a peer needs an address")
    return text.rstrip("/")


def _is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def peers(store) -> list[dict]:
    """The operator's peer allow-list: `[{base_url, node_id}]`, validated, or `[]`.

    A malformed list is not repaired into something plausible: a node that quietly dropped the
    entry it could not read would report federation as working while copying from nobody.
    """
    raw = store.setting(PEERS_KEY, "")
    if not raw.strip():
        return []
    try:
        rows = json.loads(raw)
    except ValueError:
        raise MirrorRefused("the peer list is not JSON") from None
    if not isinstance(rows, list):
        raise MirrorRefused("the peer list is not a list")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise MirrorRefused("a peer entry is not an object")
        node_id = str(row.get("node_id") or "").strip()
        if not node_id:
            raise MirrorRefused("a peer needs a pinned node id")
        out.append({"base_url": _clean_peer_url(row.get("base_url")),
                    "node_id": node_id})
    return out


def peer_request(descriptor, peers_list=None) -> dict:
    """The one request a peer copy makes, or a refusal when there is nobody to ask.

    The peer list is walked in order and the first entry answers. `node_id` is what is pinned: a
    peer that answers under a different name is a different node, and the copy is refused rather
    than accepted from an address that merely happens to be the same.
    """
    clean = federation.validate(descriptor)
    rows = peers_list if peers_list is not None else []
    if not rows:
        raise MirrorRefused("no peer is trusted to copy from")
    peer = rows[0]
    base = _clean_peer_url(peer.get("base_url"))
    node_id = str(peer.get("node_id") or "").strip()
    if not node_id:
        raise MirrorRefused("the peer has no pinned node id")
    return {"url": f"{base}{PEER_PATH}{digest(clean)}", "node_id": node_id,
            "digest": digest(clean)}


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is refused. Re-validating it would still be a second address nobody pinned."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MirrorRefused("a peer copy may not redirect")


def copy_from_peer(url: str, *, dest: Path | None = None, limit: int = MAX_PEER_BYTES,
                   timeout: float = PEER_TIMEOUT) -> dict:
    """Fetch one peer copy, bounded, and describe what arrived.

    `limit` is checked while reading, so a peer that ignores `Content-Length` still cannot fill the
    disk. The bytes are hashed as they arrive; when `dest` is given they are written to a temporary
    file beside it and renamed, so a half-copied file never lands where a completed one belongs.
    """
    clean = _clean_peer_url(url)
    request = urllib.request.Request(clean, headers={"User-Agent": "HardlyListening/1.0 (+mirror)"})
    opener = urllib.request.build_opener(_NoRedirects())
    hasher = hashlib.sha256()
    total = 0
    try:
        with opener.open(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) > limit:
                raise MirrorRefused("the peer copy is larger than the limit")
            handle = None
            temp = None
            if dest is not None:
                dest = Path(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                handle = tempfile.NamedTemporaryFile(dir=str(dest.parent), delete=False)
                temp = Path(handle.name)
            try:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise MirrorRefused("the peer copy is larger than the limit")
                    hasher.update(chunk)
                    if handle is not None:
                        handle.write(chunk)
                if handle is not None:
                    handle.flush()
                    os.fsync(handle.fileno())
                    handle.close()
                    handle = None
                    os.replace(temp, dest)
            finally:
                if handle is not None:
                    handle.close()
                    if temp is not None:
                        Path(temp).unlink(missing_ok=True)
    except MirrorRefused:
        raise
    except urllib.error.HTTPError as error:
        raise MirrorRefused(f"the peer answered {error.code}") from None
    except (urllib.error.URLError, socket.timeout, OSError):
        raise MirrorRefused("the peer could not be reached") from None
    return {"bytes": total, "sha256": hasher.hexdigest(), "path": str(dest) if dest else ""}


def cache_path(descriptor) -> Path:
    """Where a copied track belongs: one file per descriptor digest, under the federation cache."""
    return paths.FEDERATION_CACHE_DIR / digest(descriptor)


# -- 004: the modes -------------------------------------------------------------------------------

def mode(store) -> str:
    """This node's mirror mode. An unknown stored value is reported, never silently repaired."""
    value = (store.setting(MODE_KEY, DEFAULT_MODE) or "").strip().lower()
    if value not in MODES:
        raise MirrorRefused(f"unknown mirror mode: {value!r}")
    return value


def set_mode(store, value: str) -> str:
    """Store a mode, refusing anything that is not one of the four."""
    wanted = str(value or "").strip().lower()
    if wanted not in MODES:
        raise MirrorRefused("mode must be one of: " + ", ".join(MODES))
    with store.lock:
        store.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (MODE_KEY, wanted))
        store.conn.commit()
    return wanted


def set_peers(store, rows) -> list[dict]:
    """Store the peer allow-list. Validated first, so a bad entry cannot be written at all."""
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            raise MirrorRefused("a peer entry is not an object")
        base = _clean_peer_url(row.get("base_url"))
        node_id = str(row.get("node_id") or "").strip()
        if not node_id:
            raise MirrorRefused("a peer needs a pinned node id")
        cleaned.append({"base_url": base, "node_id": node_id})
    with store.lock:
        store.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                           (PEERS_KEY, json.dumps(cleaned, sort_keys=True)))
        store.conn.commit()
    return cleaned


def should_fetch(store, *, event: str, selected: bool = False) -> bool:
    """Does the mode ask for bytes here? `event` is `import` or `play`.

    * `metadata` — never. Descriptors are the whole contribution.
    * `cache` — on play, and only then: the songs somebody actually listens to.
    * `selected` — only what was picked, whatever the event.
    * `all` — every import, so the node is a complete copy.
    """
    current = mode(store)
    if event not in ("import", "play"):
        raise MirrorRefused("event must be import or play")
    if current == "all":
        return True
    if current == "selected":
        return bool(selected)
    if current == "cache":
        return event == "play"
    return False


def select(store, track_id: int, on: bool = True) -> dict:
    """Mark (or unmark) one track for `selected` mode.

    Selection is Repo state on the track row, not a member's list: a mirror is about which songs
    this *node* keeps, and every member sees the same answer.
    """
    with store.lock:
        changed = store.conn.execute("UPDATE tracks SET mirror_selected=? WHERE id=?",
                                     (1 if on else 0, int(track_id))).rowcount
        store.conn.commit()
    if not changed:
        raise MirrorRefused("no such track")
    return {"id": int(track_id), "selected": bool(on)}


def is_selected(store, track_id: int) -> bool:
    with store.lock:
        row = store.conn.execute("SELECT mirror_selected FROM tracks WHERE id=?",
                                 (int(track_id),)).fetchone()
    return bool(row and row["mirror_selected"])


def status(store) -> dict:
    """What the mirror is set to, for a read-only route and for the next report.

    The node id is reported only if it was already generated. A status read must not be the thing
    that mints key material: an install that never federates keeps an empty `identity/`, which is
    the rule `repo/federation.py` states.
    """
    try:
        current = mode(store)
    except MirrorRefused:
        current = "invalid"
    try:
        trusted = len(peers(store))
    except MirrorRefused:
        trusted = -1
    with store.lock:
        picked = store.conn.execute(
            "SELECT count(*) FROM tracks WHERE mirror_selected=1").fetchone()[0]
    name = paths.IDENTITY_DIR / federation.NODE_ID_FILE
    return {"mode": current, "modes": list(MODES), "peers": trusted, "selected": picked,
            "node_id": name.read_text(encoding="utf-8").strip() if name.is_file() else ""}