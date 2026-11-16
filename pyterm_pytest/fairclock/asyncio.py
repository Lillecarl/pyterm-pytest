"""
`asyncio.timeout`, in time the watched processes could run: an
`async with` that raises TimeoutError as asyncio's own does.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from . import MOST
from ._watch import Watch

__all__ = ["timeout"]


@asynccontextmanager
async def timeout(seconds: float, pids: Iterable[int] = ()) -> AsyncIterator[asyncio.Timeout]:
    "Raises TimeoutError once the watched processes have had `seconds` to run."
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout_at(loop.time() + seconds * MOST) as held:
            # Rescheduled to now, asyncio's own timeout fires and raises.
            watch = Watch(seconds, pids, loop.call_soon_threadsafe, lambda: held.reschedule(loop.time()))
            try:
                yield held
            finally:
                watch.close()
    except TimeoutError as timed_out:
        # Only this timeout's own: a TimeoutError the body raised passes as it came.
        if not held.expired():
            raise
        raise TimeoutError("waited %s" % (watch,)) from timed_out
