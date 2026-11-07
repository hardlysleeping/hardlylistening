"""One shared mixer room.

The mixer audio still runs in each browser (Web Audio is local by definition),
but every control, deck and queue is one server-side state that all browsers in
the room read and write. A control being dragged is *claimed* by its member for a
moment so everyone can see who is moving it; claims expire on their own, so a
closed laptop never locks a fader.

Wire format is deliberately small: a POST carries only what changed, and the SSE
stream carries only what changed as well. Full snapshots are for joining and for
the rare case where a subscriber fell behind.
"""
import hashlib
import queue
import threading
import time

CLAIM_TTL = 2.5          # seconds a control stays visibly "held" after a touch
MEMBER_TTL = 25.0        # presence drops when no stream and no action for this long
MAX_QUEUE = 12           # same cap the decks use for preload queues

# key -> (min, max, step, default)
CONTROLS = {}
for _side in ("A", "B"):
    CONTROLS.update({
        f"{_side}.low": (-20, 12, 0.5, 0.0),
        f"{_side}.mid": (-20, 12, 0.5, 0.0),
        f"{_side}.high": (-20, 12, 0.5, 0.0),
        f"{_side}.filter": (-100, 100, 1, 0.0),
        f"{_side}.reverb": (0, 100, 1, 0.0),
        f"{_side}.delay": (0, 100, 1, 0.0),
        f"{_side}.pitch": (-8, 8, 0.1, 0.0),
        f"{_side}.volume": (0, 100, 1, 70.0),
    })
CONTROLS.update({"cross": (0, 100, 1, 50.0), "master": (0, 100, 1, 80.0)})

PALETTE = ["#58d3a8", "#7aa2ff", "#ff9f5c", "#e879f9",
           "#ffd166", "#4dd4d4", "#ff7a8a", "#a3e635"]


def colour_for(identity):
    """A stable colour per member id, so people look the same every session."""
    digest = hashlib.sha256(str(identity).encode()).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def clamp(key, value):
    if key not in CONTROLS:
        return None
    low, high, step, _ = CONTROLS[key]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:                    # NaN
        return None
    number = max(low, min(high, number))
    if step:
        number = round(number / step) * step
    return round(number, 3)


class Room:
    def __init__(self):
        self.lock = threading.RLock()
        self.rev = 0
        self.autodj = None
        self.values = {key: spec[3] for key, spec in CONTROLS.items()}
        self.claims = {}                    # control key -> {id, name, icon, colour, ts}
        self.members = {}                   # member id -> {id, name, icon, colour, seen}
        self.decks = {side: {"track_id": None, "playing": False, "pos": 0.0,
                             "at": 0.0, "rate": 1.0} for side in ("A", "B")}
        self.queues = {"A": [], "B": []}
        self._subscribers = {}              # id -> queue.Queue
        self._behind = set()                # subscribers that need a full snapshot
        self._last_ids = set()              # who was online at the last broadcast
        self._tokens = 0

    # -- presence ---------------------------------------------------------
    def _identity(self, member):
        return {
            "id": str(member["id"]),
            "name": (member.get("display_name") or "Guest")[:40],
            "icon": member.get("profile_icon") or "",
            "colour": colour_for(member["id"]),
        }

    def touch(self, member):
        """Record that this member is here and return the presence entry."""
        with self.lock:
            entry = self._identity(member)
            known = self.members.get(entry["id"], {})
            entry["seen"] = time.time()
            # Keep a shown name stable unless the member renamed themselves.
            entry["icon"] = entry["icon"] or known.get("icon", "")
            # Two people must never share a colour: take a free one on arrival and
            # keep it for as long as they are here.
            used = {m["colour"] for m in self.online() if m["id"] != entry["id"]}
            if known.get("colour") and known["colour"] not in used:
                entry["colour"] = known["colour"]
            else:
                free = [c for c in PALETTE if c not in used]
                entry["colour"] = free[0] if free else entry["colour"]
            self.members[entry["id"]] = entry
            return entry

    def online(self):
        cutoff = time.time() - MEMBER_TTL
        return [m for m in self.members.values() if m["seen"] >= cutoff]

    # -- pub/sub ----------------------------------------------------------
    def subscribe(self):
        with self.lock:
            # A counter, not id(object()): CPython reuses ids, which silently gave
            # two subscribers the same slot.
            self._tokens += 1
            token = self._tokens
            self._subscribers[token] = queue.Queue(maxsize=256)
            return token, self.snapshot()

    def unsubscribe(self, token):
        with self.lock:
            self._subscribers.pop(token, None)
            self._behind.discard(token)

    def _broadcast(self, patch):
        with self.lock:
            self.rev += 1
            patch["rev"] = self.rev
            # Anyone arriving or timing out changes the roster, so it rides along
            # with whatever patch is being sent rather than needing its own tick.
            online = {m["id"]: m for m in self.online()}
            if set(online) != self._last_ids:
                self._last_ids = set(online)
                patch["members"] = list(online.values())
            for token, box in list(self._subscribers.items()):
                try:
                    box.put_nowait(patch)
                except queue.Full:
                    # A slow reader gets one full snapshot instead of a gap.
                    self._behind.add(token)

    def next_event(self, token, timeout=15.0):
        """Blocking read for one subscriber: a patch, a full state, or None."""
        box = self._subscribers.get(token)
        if box is None:
            return None
        if token in self._behind:
            self._behind.discard(token)
            while not box.empty():
                try:
                    box.get_nowait()
                except queue.Empty:
                    break
            return {"type": "state", **self.snapshot()}
        try:
            patch = box.get(timeout=timeout)
        except queue.Empty:
            return {}
        return {"type": "patch", **patch}

    # -- state ------------------------------------------------------------
    def snapshot(self):
        with self.lock:
            self._expire()
            return {
                "autodj": dict(self.autodj) if self.autodj else None,
                "rev": self.rev,
                "values": dict(self.values),
                "claims": dict(self.claims),
                "members": self.online(),
                "decks": {side: dict(deck) for side, deck in self.decks.items()},
                "queues": {side: list(ids) for side, ids in self.queues.items()},
            }

    def _expire(self):
        cutoff = time.time() - CLAIM_TTL
        gone = [key for key, claim in self.claims.items() if claim["ts"] < cutoff]
        for key in gone:
            del self.claims[key]
        return gone

    # -- mutations --------------------------------------------------------
    def auto_claim(self, member, token, enabled=True):
        """One expiring controller per tab, including tabs of the same member."""
        token = str(token or "")[:80]
        if not token:
            return False
        with self.lock:
            now = time.time()
            owner = self.autodj
            mine = owner and owner["id"] == str(member["id"]) and owner["token"] == token
            if owner and owner["until"] > now and not mine:
                return False
            self.autodj = {"id": str(member["id"]), "token": token, "until": now + 6} if enabled else None
            self._broadcast({"autodj": self.autodj})
            return True

    def set_values(self, member, values):
        """Apply control values and hold those controls for this member."""
        who = self._identity(member)
        now = time.time()
        changed, claims = {}, {}
        with self.lock:
            for key, raw in (values or {}).items():
                if key not in CONTROLS:
                    continue
                value = clamp(key, raw)
                if value is None:
                    continue
                if self.values.get(key) != value:
                    self.values[key] = value
                    changed[key] = value
                claim = {"id": who["id"], "name": who["name"], "icon": who["icon"],
                         "colour": who["colour"], "ts": now}
                self.claims[key] = claim
                claims[key] = claim
            # touch() keeps the member's assigned colour; overwriting the entry here
            # would put the hash colour back and give two people the same one.
            self.touch(member)
            if not changed and not claims:
                return {}
            patch = {"values": changed, "claims": claims}
        self._broadcast(patch)
        return patch

    def claim(self, member, keys):
        who = self._identity(member)
        now = time.time()
        held = {}
        with self.lock:
            for key in keys or []:
                if key in CONTROLS:
                    self.claims[key] = {"id": who["id"], "name": who["name"],
                                        "icon": who["icon"], "colour": who["colour"], "ts": now}
                    held[key] = self.claims[key]
        if held:
            self._broadcast({"claims": held})
        return held

    def release(self, member, keys):
        who = self._identity(member)
        freed = {}
        with self.lock:
            for key in keys or []:
                claim = self.claims.get(key)
                if claim and claim["id"] == who["id"]:
                    del self.claims[key]
                    freed[key] = None
        if freed:
            self._broadcast({"claims": freed})
        return freed

    def set_deck(self, member, side, payload):
        side = "B" if side == "B" else "A"
        deck = self.decks[side]
        with self.lock:
            if "track_id" in payload:
                try:
                    deck["track_id"] = int(payload["track_id"]) if payload["track_id"] not in (None, "") else None
                except (TypeError, ValueError):
                    pass
                deck["pos"] = 0.0
                deck["at"] = time.time()
            if "playing" in payload:
                playing = bool(payload["playing"])
                # Freeze the clock at the moment of the change.
                deck["pos"] = self.position(side)
                deck["at"] = time.time()
                deck["playing"] = playing
            if "position" in payload and deck["track_id"] is not None:
                try:
                    deck["pos"] = max(0.0, float(payload["position"]))
                    deck["at"] = time.time()
                except (TypeError, ValueError):
                    pass
            if "rate" in payload:
                try:
                    deck["rate"] = max(0.5, min(2.0, float(payload["rate"])))
                    deck["at"] = time.time()
                except (TypeError, ValueError):
                    pass
            self.touch(member)
            state = dict(deck, at=deck["at"])
        self._broadcast({"decks": {side: state}, "members": self.online()})
        return state

    def position(self, side):
        """Where the deck should be right now, from its anchor."""
        deck = self.decks[side]
        if not deck["playing"]:
            return deck["pos"]
        return deck["pos"] + max(0.0, time.time() - deck["at"]) * deck["rate"]

    def set_queue(self, member, side, payload):
        side = "B" if side == "B" else "A"
        action = payload.get("action") or "set"
        ids = payload.get("ids")
        with self.lock:
            current = list(self.queues[side])
            if action == "set" and isinstance(ids, list):
                current = [int(i) for i in ids if str(i).isdigit()][:MAX_QUEUE]
            elif action == "add" and isinstance(ids, list):
                for value in ids:
                    try:
                        track_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if track_id not in current and len(current) < MAX_QUEUE:
                        current.append(track_id)
            elif action == "remove" and isinstance(ids, list):
                drop = {str(i) for i in ids}
                current = [i for i in current if str(i) not in drop]
            elif action == "clear":
                current = []
            self.queues[side] = current
            self.touch(member)
            state = list(current)
        self._broadcast({"queues": {side: state}})
        return state


ROOM = Room()
_LAST_SWEEP = [0.0]
_SWEEP_THREAD = [None]


def _sweep_forever():
    while True:
        time.sleep(0.5)
        try:
            sweep(force=True)
        except Exception:              # a broken sweep must never kill the room
            pass


def ensure_sweeper():
    """Started on the first subscriber; a daemon, so the server still exits."""
    if _SWEEP_THREAD[0] is None:
        import threading as _threading
        _SWEEP_THREAD[0] = _threading.Thread(target=_sweep_forever, name="mixer-sweep", daemon=True)
        _SWEEP_THREAD[0].start()


def sweep(force=False):
    """Drop stale claims and presence; call from a request or the stream loop."""
    now = time.time()
    if not force and now - _LAST_SWEEP[0] < 1.0:
        return
    _LAST_SWEEP[0] = now
    with ROOM.lock:
        expired = ROOM._expire()
        before = set(ROOM.members)
        after = {m["id"] for m in ROOM.online()}
    if expired:
        ROOM._broadcast({"claims": {key: None for key in expired}})
    if before - after:
        ROOM._broadcast({"members": ROOM.online()})


# --------------------------------------------------------------------- HTTP
def _identity_from_query(handler):
    """Local (auth-off) mode has no session, so a tab identifies itself."""
    from urllib.parse import parse_qs, urlparse
    query = parse_qs(urlparse(handler.path).query)
    cid = (query.get("cid") or ["tab"])[0][:40] or "tab"
    name = (query.get("name") or ["Guest"])[0][:40] or "Guest"
    return {"id": "local:" + cid, "display_name": name, "profile_icon": "", "role": "member"}


def dispatch(handler, member, path):
    """Route /api/mixer and /api/mixer/stream. Never raises past Denied."""
    from security import Denied
    if path == "/api/mixer/stream":
        if handler.command != "GET":
            raise Denied(405, "Method not allowed")
        stream(handler, member)
        return
    if path == "/api/mixer":
        if handler.command == "GET":
            handler.json({**ROOM.snapshot(), "you": str(member["id"])})
            return
        payload = handler.body()
        op = payload.get("op")
        ROOM.touch(member)
        if op == "autodj":
            if not ROOM.auto_claim(member, payload.get("token"), payload.get("enabled") is True):
                raise Denied(409, "Auto DJ is controlled by another tab")
        elif op == "values":
            ROOM.set_values(member, payload.get("values"))
        elif op in ("claim", "touch"):
            ROOM.claim(member, payload.get("keys"))
        elif op == "release":
            ROOM.release(member, payload.get("keys"))
        elif op == "deck":
            ROOM.set_deck(member, payload.get("side"), payload)
        elif op == "queue":
            ROOM.set_queue(member, payload.get("side"), payload)
        elif op == "ping":
            pass
        else:
            raise Denied(400, "Unknown mixer operation")
        handler.json({"ok": True, "rev": ROOM.rev})
        return
    raise Denied(404, "Not found")


def stream(handler, member):
    """Server-sent events: one patch per change, so a fader feels live."""
    import json
    ROOM.touch(member)
    token, snapshot = ROOM.subscribe()
    ensure_sweeper()
    snapshot["you"] = str(member["id"])
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Accel-Buffering", "no")   # a proxy must not buffer this
        handler.send_header("Connection", "close")
        handler.close_connection = True
        handler.end_headers()
        try:
            handler.connection.settimeout(None)
        except OSError:
            pass

        def send(event):
            body = json.dumps(event, separators=(",", ":"), default=str)
            handler.wfile.write(f"data: {body}\n\n".encode())
            handler.wfile.flush()

        handler.wfile.write(b"retry: 1500\n\n")
        handler.wfile.flush()
        send({"type": "state", **snapshot})
        while True:
            event = ROOM.next_event(token, timeout=15.0)
            if event is None:
                break                       # unsubscribed elsewhere
            if not event:                   # keep-alive tick
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()
            else:
                send(event)
            sweep()
    except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
        pass
    finally:
        ROOM.unsubscribe(token)
