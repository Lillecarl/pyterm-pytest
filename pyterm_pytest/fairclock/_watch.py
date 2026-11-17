"""
The thread that decides when a deadline in an event loop is over.

A front end holds its loop's own timeout at the far bound, `MOST` times
the seconds, and this thread pulls it in once the watched threads have
had their seconds. A thread and not a task: a task needs a task group,
and a task group wraps the body's error in an ExceptionGroup, so the
front ends could not be a plain `with` like the timeouts they stand in
for.

**One thread for every watch in the process, not one each.** A reading
walks every thread under this process, so a thread per watch made each
new watch read the ones before it: a helper that bounds every frame
held thousands open, at 40 ms a scope. Lillecarl/pyterm#1000.

**A watch's clock starts late**, after `START` of its seconds, so a
scope that ends sooner reads nothing. What its processes waited before
that is not forgiven, which errs strict.

The thread never waits on a loop. It posts the expiry and goes, so a
loop that is busy elsewhere cannot hold it, and nothing joins it.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable, Iterable

from . import SAMPLE, Clock

#: The shortest sleep between two readings: a deadline that is nearly
#: over is not read in a tight loop.
FLOOR = 0.01

#: The part of its seconds a watch waits before its first reading.
START = 0.5


class Watch:
    """
    Calls `expire` on the loop's thread, through `post`, once `seconds`
    of time the watched threads could run have gone by; never once
    `close()` has run there.
    """

    def __init__(
        self,
        seconds: float,
        pids: Iterable[int],
        post: Callable[[Callable[[], None]], None],
        expire: Callable[[], None],
    ) -> None:
        self.seconds = seconds
        self.clock = Clock(pids)
        self.expired = False
        self.started = False
        self.starts_at = self.clock.began + min(SAMPLE, seconds * START)
        self._post = post
        self._expire = expire
        self._closed = False
        _SAMPLER.add(self)

    def due(self, now: float) -> float | None:
        "On the sampler's thread: seconds until this watch is next read, or None once it has expired."
        if not self.started:
            if now < self.starts_at:
                return self.starts_at - now
            self.clock.start()
            self.started = True
        left = self.clock.left(self.seconds)
        if left > 0:
            return max(FLOOR, left)
        # A RuntimeError says the loop closed: nothing is left to cancel.
        with contextlib.suppress(RuntimeError):
            self._post(self._on_loop)
        return None

    def _on_loop(self) -> None:
        if not self._closed:
            self.expired = True
            self._expire()

    def close(self) -> None:
        "On the loop's thread, as the body leaves: an expiry still in flight does nothing."
        self._closed = True
        _SAMPLER.remove(self)

    def __str__(self) -> str:
        stalled = self.clock.stalled() if self.started else 0.0
        return "%g s, and %.1f s more that the processes waited for a CPU" % (self.seconds, stalled)


class _Sampler:
    "The one thread that reads every open watch, and ends when none is open."

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._watches: set[Watch] = set()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        # When the thread next wakes on its own; a watch due sooner wakes it.
        self._until = 0.0

    def add(self, watch: Watch) -> None:
        with self._lock:
            self._watches.add(watch)
            if self._thread is None or not self._thread.is_alive():
                self._until = watch.starts_at
                self._thread = threading.Thread(target=self._run, name="fairclock", daemon=True)
                self._thread.start()
                return
            if watch.starts_at < self._until:
                self._wake.set()

    def remove(self, watch: Watch) -> None:
        with self._lock:
            self._watches.discard(watch)

    def _run(self) -> None:
        while True:
            with self._lock:
                watches = list(self._watches)
                if not watches:
                    self._thread = None
                    return
                # Until it sleeps again, every new watch wakes it.
                self._until = math.inf
                self._wake.clear()
            now = time.monotonic()
            sleep = SAMPLE
            for watch in watches:
                due = watch.due(now)
                if due is None:
                    self.remove(watch)
                else:
                    sleep = min(sleep, due)
            with self._lock:
                self._until = time.monotonic() + sleep
            self._wake.wait(sleep)


_SAMPLER = _Sampler()
