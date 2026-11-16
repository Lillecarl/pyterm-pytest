"""
The thread that decides when a deadline in an event loop is over.

A front end holds its loop's own timeout at the far bound, `MOST` times
the seconds, and this thread pulls it in once the watched threads have
had their seconds. A thread and not a task: a task needs a task group,
and a task group wraps the body's error in an ExceptionGroup, so the
front ends could not be a plain `with` like the timeouts they stand in
for.

The thread never waits on the loop. It posts the expiry and goes, so a
loop that is busy elsewhere cannot hold it, and nothing joins it.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterable

from . import Clock

#: The shortest sleep between two readings: a deadline that is nearly
#: over is not read in a tight loop.
FLOOR = 0.01


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
        self._post = post
        self._expire = expire
        self._closed = False
        self._stop = threading.Event()
        threading.Thread(target=self._run, name="fairclock", daemon=True).start()

    def _run(self) -> None:
        # The first reading is off the loop's thread: it walks /proc.
        self.clock.start()
        while not self._stop.wait(max(FLOOR, self.clock.left(self.seconds))):
            if self.clock.left(self.seconds) <= 0:
                # A RuntimeError says the loop closed: nothing is left to cancel.
                with contextlib.suppress(RuntimeError):
                    self._post(self._on_loop)
                return

    def _on_loop(self) -> None:
        if not self._closed:
            self.expired = True
            self._expire()

    def close(self) -> None:
        "On the loop's thread, as the body leaves: an expiry still in flight does nothing."
        self._closed = True
        self._stop.set()

    def __str__(self) -> str:
        return "%g s, and %.1f s more that the processes waited for a CPU" % (self.seconds, self.clock.stalled())
