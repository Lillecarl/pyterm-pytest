"""
`subprocess.run`, with a timeout of time the processes could run.

The child is under this process, so it is watched; `pids` adds what it
talks to, such as a server that went into the background.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from typing import Any

from . import SAMPLE, Clock
from ._watch import FLOOR

__all__ = ["FairTimeoutExpired", "run"]


class FairTimeoutExpired(subprocess.TimeoutExpired):
    "`TimeoutExpired`, and the clock it was read from, so a failure says where the time went."

    def __init__(self, *args: Any, clock: Clock, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.wall = clock.elapsed()
        self.most = clock.stalled()
        self.together = clock.waited()
        self.ran = clock.ran()
        self.io = clock.io_waited()

    def __str__(self) -> str:
        return (
            "%s: %.1f s of wall time, %.1f s of it forgiven as waits for a CPU (every thread's: %.1f s;"
            " %.1f s on a CPU and %.1f s waiting for block I/O, not forgiven)"
            % (super().__str__(), self.wall, self.most, self.together, self.ran, self.io)
        )


def run(
    args: Sequence[str],
    seconds: float,
    pids: Iterable[int] = (),
    *,
    input: bytes | None = None,
    check: bool = False,
    capture_output: bool = False,
    **popen: Any,
) -> subprocess.CompletedProcess[bytes]:
    """
    Run `args` to its end, and kill it once `seconds` of fair time have
    gone by: `subprocess.TimeoutExpired` then, as `subprocess.run` raises.
    """
    if capture_output:
        popen.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    clock = Clock(pids).start()
    with subprocess.Popen(args, stdin=subprocess.PIPE if input is not None else None, **popen) as process:
        while True:
            try:
                # In steps of `SAMPLE`, so the clock reads the children that come and go.
                stdout, stderr = process.communicate(input, timeout=max(FLOOR, min(SAMPLE, clock.left(seconds))))
                break
            except subprocess.TimeoutExpired:
                # `communicate` takes a second call after a timeout, and
                # sends what is left of the input.
                if clock.left(seconds) > 0:
                    continue
                process.kill()
                stdout, stderr = process.communicate()
                raise FairTimeoutExpired(args, seconds, output=stdout, stderr=stderr, clock=clock) from None
    done = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if check:
        done.check_returncode()
    return done
