"""
`subprocess.run`, with a timeout of time the processes could run.

The child is under this process, so it is watched; `pids` adds what it
talks to, such as a server that went into the background.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from typing import Any

from . import Clock
from ._watch import FLOOR

__all__ = ["run"]


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
                stdout, stderr = process.communicate(input, timeout=max(FLOOR, clock.left(seconds)))
                break
            except subprocess.TimeoutExpired:
                # `communicate` takes a second call after a timeout, and
                # sends what is left of the input.
                if clock.left(seconds) > 0:
                    continue
                process.kill()
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(args, seconds, output=stdout, stderr=stderr) from None
    done = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if check:
        done.check_returncode()
    return done
