"""
`trio.fail_after` and `trio.move_on_after`, in time the watched
processes could run. A plain `with`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

import trio

from . import MOST
from ._watch import Watch

__all__ = ["fail_after", "move_on_after"]


@contextmanager
def _watched(seconds: float, pids: Iterable[int], shield: bool) -> Iterator[tuple[trio.CancelScope, Watch]]:
    with trio.move_on_after(seconds * MOST, shield=shield) as scope:
        token = trio.lowlevel.current_trio_token()
        watch = Watch(seconds, pids, token.run_sync_soon, lambda: setattr(scope, "deadline", -math.inf))
        try:
            yield scope, watch
        finally:
            watch.close()


@contextmanager
def move_on_after(seconds: float, pids: Iterable[int] = (), shield: bool = False) -> Iterator[trio.CancelScope]:
    "A cancel scope that ends once the watched processes have had `seconds` to run."
    with _watched(seconds, pids, shield) as (scope, _watch):
        yield scope


@contextmanager
def fail_after(seconds: float, pids: Iterable[int] = (), shield: bool = False) -> Iterator[trio.CancelScope]:
    "Raises trio.TooSlowError, as `trio.fail_after` does, once the watched processes have had `seconds` to run."
    with _watched(seconds, pids, shield) as (scope, watch):
        yield scope
    if scope.cancelled_caught:
        raise trio.TooSlowError("waited %s" % (watch,))
