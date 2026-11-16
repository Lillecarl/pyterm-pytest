"""
`anyio.fail_after` and `anyio.move_on_after`, in time the watched
processes could run. A plain `with`, on either of anyio's backends.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

import anyio
import anyio.lowlevel

from . import MOST
from ._watch import Watch

__all__ = ["fail_after", "move_on_after"]


def _poster() -> Callable[[Callable[[], None]], None]:
    "A way into this loop from another thread that does not wait: anyio's own `from_thread` does."
    native = anyio.lowlevel.current_token().native_token
    run_soon = getattr(native, "call_soon_threadsafe", None) or native.run_sync_soon  # asyncio's loop, trio's token
    return run_soon


@contextmanager
def _watched(seconds: float, pids: Iterable[int], shield: bool) -> Iterator[tuple[anyio.CancelScope, Watch]]:
    # The scope's own deadline is the far bound; the watch pulls it to
    # the past, which is the deadline passing as anyio sees it.
    with anyio.move_on_after(seconds * MOST, shield=shield) as scope:
        watch = Watch(seconds, pids, _poster(), lambda: setattr(scope, "deadline", -math.inf))
        try:
            yield scope, watch
        finally:
            watch.close()


@contextmanager
def move_on_after(seconds: float, pids: Iterable[int] = (), shield: bool = False) -> Iterator[anyio.CancelScope]:
    "A cancel scope that ends once the watched processes have had `seconds` to run."
    with _watched(seconds, pids, shield) as (scope, _watch):
        yield scope


@contextmanager
def fail_after(seconds: float, pids: Iterable[int] = (), shield: bool = False) -> Iterator[anyio.CancelScope]:
    """
    Raises TimeoutError, as `anyio.fail_after` does, once the watched
    processes have had `seconds` to run. A cancellation from outside
    passes through as it came.
    """
    with _watched(seconds, pids, shield) as (scope, watch):
        yield scope
    if scope.cancelled_caught and scope.deadline <= anyio.current_time():
        raise TimeoutError("waited %s" % (watch,))
