"""
Deadlines and stopwatches that count only the time a process could run.

On a loaded machine a process that is ready to run can wait seconds for
a CPU, and a wall clock counts those seconds against it: a test fails
for the machine's load and not for a fault. Linux keeps, per thread,
the nanoseconds it sat on a run queue (`/proc/<pid>/task/<tid>/schedstat`,
the second field). A `Clock` watches this process, every process under
it, and the pids it is given, and answers the most any one of their
threads waited since it began. A deadline moves out by that much, and a
stopwatch leaves it out.

Where nobody keeps that number (macOS) nothing has waited, and every
deadline is a wall-clock one.

The standard library only, so this package can leave pyterm whole. The
front ends for an event loop are `fairclock.asyncio`, `fairclock.anyio`
and `fairclock.trio`; each imports its own library and no other.
`fairclock.subprocess.run` is `subprocess.run` with a fair timeout.
Lillecarl/pyterm#1000.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path

__all__ = ["MOST", "Clock", "Deadline", "Stopwatch", "pids_naming", "run_delays"]

#: However loaded the machine, a deadline ends by this many times its
#: seconds. A process the machine never runs is a hang as well.
MOST = 10

PROC = Path("/proc")


def run_delays(pid: int | str = "self") -> dict[int, float]:
    "The seconds each thread of `pid` has waited for a CPU, by thread id; empty where that is not known."
    found = {}
    try:
        tasks = list((PROC / str(pid) / "task").iterdir())
    except OSError:
        return found
    for task in tasks:
        try:
            found[int(task.name)] = int((task / "schedstat").read_text().split()[1]) / 1e9
        except OSError, IndexError, ValueError:
            continue
    return found


def _parents() -> dict[int, int]:
    "Each process's parent, by pid."
    found = {}
    for entry in PROC.glob("[0-9]*"):
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        # The name in parentheses may hold spaces and parentheses itself.
        found[int(entry.name)] = int(stat[stat.rindex(")") + 2 :].split()[1])
    return found


def _children(pid: int) -> list[int] | None:
    "The children of `pid`, from each of its threads; None where the kernel keeps no such list."
    found: list[int] = []
    try:
        tasks = list((PROC / str(pid) / "task").iterdir())
    except OSError:
        return found
    for task in tasks:
        try:
            found.extend(int(child) for child in (task / "children").read_text().split())
        except FileNotFoundError:
            if not (task / "stat").exists():
                continue
            return None
        except OSError:
            continue
    return found


def _under(roots: Iterable[int]) -> set[int]:
    """
    The roots and every process under them. The kernel's children lists
    walk only the tree; without them (CONFIG_PROC_CHILDREN off) every
    process is read, which costs 20 ms at a thousand processes.
    """
    found, todo = set(), list(roots)
    by_parent: dict[int, list[int]] | None = None
    while todo:
        pid = todo.pop()
        if pid in found:
            continue
        found.add(pid)
        children = _children(pid) if by_parent is None else None
        if children is None:
            if by_parent is None:
                by_parent = {}
                for child, parent in _parents().items():
                    by_parent.setdefault(parent, []).append(child)
            children = by_parent.get(pid, [])
        todo.extend(children)
    return found


def pids_naming(word: str) -> list[int]:
    """
    The processes whose command line holds `word`: a socket path finds a
    server that went into the background, which no parent holds.
    """
    found = []
    for entry in PROC.glob("[0-9]*"):
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(word.encode() in argument for argument in argv) and int(entry.name) != os.getpid():
            found.append(int(entry.name))
    return found


class Clock:
    """
    How long the watched threads waited for a CPU since `start()`: the
    threads of this process, of `pids`, and of every process under
    either.

    A thread that began after `start()` counts from nothing. A thread
    that ended keeps what it last said.
    """

    def __init__(self, pids: Iterable[int] = ()) -> None:
        self._roots = [os.getpid(), *pids]
        self._first: dict[tuple[int, int], float] = {}
        self._last: dict[tuple[int, int], float] = {}
        self.began = time.monotonic()

    def _read(self) -> dict[tuple[int, int], float]:
        if not PROC.is_dir():
            return {}
        return {(pid, tid): waited for pid in _under(self._roots) for tid, waited in run_delays(pid).items()}

    def start(self) -> Clock:
        "Take the first reading. The time counts from the clock's making, which a late start does not move."
        self._first = self._read()
        self._last = dict(self._first)
        return self

    def stalled(self) -> float:
        "The most any watched thread has waited for a CPU since `start()`."
        self._last.update(self._read())
        return max((waited - self._first.get(key, 0.0) for key, waited in self._last.items()), default=0.0)

    def elapsed(self) -> float:
        return time.monotonic() - self.began

    def left(self, seconds: float) -> float:
        """
        What is left of `seconds` of time the watched threads could run,
        `MOST` times `seconds` at most. Before `seconds` have gone by it
        answers without reading /proc: a stall only adds time, so the
        plain remainder is a floor, and a wait that ends in time pays
        for no reading.
        """
        elapsed = self.elapsed()
        if elapsed < seconds:
            return seconds - elapsed
        return min(seconds + self.stalled(), seconds * MOST) - elapsed


class Deadline:
    """
    `seconds` of time this process and `pids` could run, for a loop that
    polls: `passed()` says whether it is over, and `str()` says how long
    that was, for a failure message.
    """

    def __init__(self, seconds: float, pids: Iterable[int] = ()) -> None:
        self.seconds = seconds
        self.clock = Clock(pids).start()

    def passed(self) -> bool:
        return self.clock.left(self.seconds) <= 0

    def __str__(self) -> str:
        return "%.1f s, and %.1f s more that the processes waited for a CPU" % (self.seconds, self.clock.stalled())


class Stopwatch:
    """
    Time gone by, less the most any watched thread waited for a CPU: what
    a budget measures, with the machine's load left out.
    """

    def __init__(self, pids: Iterable[int] = ()) -> None:
        self.clock = Clock(pids).start()

    def elapsed(self) -> float:
        return max(0.0, self.clock.elapsed() - self.clock.stalled())
