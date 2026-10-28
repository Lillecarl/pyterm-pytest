"""
A deadline that counts the time the processes it waits on could run.

On a loaded build machine a process that is ready to run can wait
seconds for a CPU, and a wall-clock deadline counts those seconds
against it: a test fails for the machine's load and not for a fault.
Linux keeps, per thread, the nanoseconds it sat on a run queue
(`/proc/<pid>/schedstat`, the second field). A `Deadline` moves out by
the most any process it watches sat there since it began, so a stall
costs nothing, and a hang that has the CPU still ends on time.

Where nobody keeps that number (macOS) it is a wall-clock deadline.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path

__all__ = ["Deadline", "pids_naming", "queued"]

#: However loaded the machine, a deadline ends by this many times its
#: seconds. A process the machine never runs is a hang as well.
MOST = 10


def queued(pid: int | str = "self") -> float | None:
    "The seconds the main thread of `pid` has waited for a CPU, or None where that is not known."
    try:
        return int(Path("/proc/%s/schedstat" % (pid,)).read_text().split()[1]) / 1e9
    except OSError, IndexError, ValueError:
        return None


def pids_naming(word: str) -> list[int]:
    """
    The processes whose command line holds `word`: a socket path finds a
    pymux server that went into the background, which no parent holds.
    """
    found = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(word.encode() in argument for argument in argv) and int(entry.name) != os.getpid():
            found.append(int(entry.name))
    return found


class Deadline:
    """
    `seconds` of time this process and `pids` could run.

    `passed()` says whether it is over; `str()` says how long that was,
    for a failure message.
    """

    def __init__(self, seconds: float, pids: Iterable[int] = ()) -> None:
        self.seconds = seconds
        self._began = time.monotonic()
        self._first: dict[int | str, float] = {}
        self._last: dict[int | str, float] = {}
        for pid in ("self", *pids):
            waited = queued(pid)
            if waited is not None:
                self._first[pid] = self._last[pid] = waited

    def stalled(self) -> float:
        "The most any watched process has waited for a CPU since this began."
        most = 0.0
        for pid, first in self._first.items():
            waited = queued(pid)
            # A process that has ended keeps what it last said.
            if waited is not None:
                self._last[pid] = waited
            most = max(most, self._last[pid] - first)
        return most

    def passed(self) -> bool:
        elapsed = time.monotonic() - self._began
        return elapsed > min(self.seconds + self.stalled(), self.seconds * MOST)

    def __str__(self) -> str:
        return "%.1f s, and %.1f s more that the processes waited for a CPU" % (self.seconds, self.stalled())
