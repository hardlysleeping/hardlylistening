"""Background follow checks.

Separate thread from the download worker on purpose: a slow artist page must
never stall the queue, and a queued download must not delay discovery. Each pass
looks for subscriptions whose interval has elapsed and checks at most two of them.
"""

from __future__ import annotations

import threading
import time

from discovery import subscriptions

TICK = 120          # seconds between passes; a follow's own interval decides the rest


class Scheduler(threading.Thread):
    def __init__(self, store):
        super().__init__(name="scheduler", daemon=True)
        self.store = store
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(TICK):
            try:
                due = self.store.watch_due()
            except Exception as exc:                       # noqa: BLE001
                self.store.log(f"scheduler could not read subscriptions: {exc}", "error")
                continue
            if not due:
                continue
            try:
                subscriptions.check_all(self.store, budget=2)
            except Exception as exc:                       # noqa: BLE001
                self.store.log(f"follow check failed: {exc}", "error")
            # Never so fast that the UI sees a busier app than it is.
            time.sleep(1)
