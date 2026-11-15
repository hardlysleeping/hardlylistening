"""One publish at a time, off the request thread, with the reason kept for the person who asked.

`library/funkwhale.py` does the work and owns the definition of success; this module only holds
the *job* around it. A route cannot wait on Funkwhale inside the request: an import that is
transcoded on their side is bounded by `funkwhale.ATTEMPTS × INTERVAL` (a minute by default), and
a browser or a proxy would give up long before the honest answer arrived. So the POST starts the
job and the page reads `status()` until it stops running — the same shape as the title repair.

There is deliberately **one** job, not one per member: the credential is the server's own, and a
second upload while the first is transcoding is not something this server needs to allow. A job's
state is in memory only. After a restart an in-flight publish is simply unknown — Funkwhale is
the record of what arrived, and asking again is the person's decision, not a silent retry.
"""

from __future__ import annotations

import threading
import time

from . import funkwhale

_lock = threading.RLock()
_state = {
    # Which job owns the state. A worker whose token is no longer current has been
    # replaced (its upload gave up while a new one started) and must not report onto it.
    "token": 0,
    "track_id": None,
    "name": "",
    "running": False,
    "started_at": 0.0,
    "finished": 0.0,
    "result": None,
    "error": "",
}


def status(track_id: int | None = None) -> dict:
    """The job as it stands, and nothing more than a caller already knows.

    `track_id` scopes the answer: the page asks about the song it started, so another track's
    finished job is not reported as its own. `busy` still says whether the single worker is
    occupied, which is the one fact a second attempt needs before it is refused.
    """
    with _lock:
        out = dict(_state)
    out["busy"] = bool(out["running"])
    if track_id is not None and out["track_id"] != track_id:
        return {"track_id": track_id, "name": "", "running": False, "busy": out["busy"],
                "started_at": 0.0, "finished": 0.0, "result": None, "error": ""}
    return out


def start(track_id: int, source) -> dict:
    """Queue one upload and answer immediately. The page polls `status(track_id)`."""
    with _lock:
        if _state["running"]:
            return {"started": False, "reason": "another publish is still running",
                    **status(track_id)}
        token = _state["token"] + 1
        _state.update({"token": token, "track_id": int(track_id),
                       "name": getattr(source, "name", ""), "running": True,
                       "started_at": time.time(), "finished": 0.0,
                       "result": None, "error": ""})

    def finish(**changes) -> None:
        with _lock:
            if _state["token"] != token:
                return                        # a later job owns the state now
            _state.update({"running": False, "finished": time.time(), **changes})

    def work() -> None:
        try:
            result = funkwhale.publish(source)
        except funkwhale.PublishError as error:
            finish(error=str(error))
            return
        except Exception as error:                       # noqa: BLE001
            # A bug is still an answer to the person waiting: name it rather than leave the
            # job "running" until the process restarts.
            finish(error="%s: %s" % (type(error).__name__, error))
            return
        finish(result=result)

    threading.Thread(target=work, name="funkwhale-publish", daemon=True).start()
    return {"started": True, **status(int(track_id))}