"""
fairclock forgives the time its processes waited for a CPU, and nothing
else: a starved wait stretches, and a hang that has the CPU still ends
on time. On every front end. Lillecarl/pyterm#1000.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
import pytest
import trio

from pyterm_pytest import fairclock
from pyterm_pytest.fairclock import Clock, Deadline, Stopwatch, pids_naming, run_delays, task_times
from pyterm_pytest.fairclock import anyio as fair_anyio
from pyterm_pytest.fairclock import asyncio as fair_asyncio
from pyterm_pytest.fairclock import subprocess as fair_subprocess
from pyterm_pytest.fairclock import trio as fair_trio

LINUX = Path("/proc/self/task").is_dir()
needs_proc = pytest.mark.skipif(not LINUX, reason="no /proc")


def test_each_thread_says_how_long_it_waited():
    if not LINUX:
        assert run_delays() == {}
        return
    waited = run_delays()
    ours = {thread.native_id for thread in threading.enumerate() if thread.native_id is not None}
    assert ours <= set(waited), waited
    assert all(seconds >= 0 for seconds in waited.values())


def test_each_thread_says_how_long_it_ran_and_waited_for_the_disk():
    "Never forgiven, only reported. The disk is zero where delay accounting is off."
    if not LINUX:
        assert task_times() == {}
        return
    times = task_times()
    ours = {thread.native_id for thread in threading.enumerate() if thread.native_id is not None}
    assert ours <= set(times), times
    assert all(ran >= 0 and io >= 0 for ran, io in times.values())

    clock = Clock().start()
    spun = time.process_time() + 0.3
    while time.process_time() < spun:
        pass
    assert clock.ran() >= 0.2
    assert clock.io_waited() >= 0


def test_an_idle_machine_ends_it_on_time():
    waiting = Deadline(0.2)
    assert not waiting.passed()
    time.sleep(0.3)
    assert waiting.passed()


def test_a_stall_moves_it_out_by_as_long(monkeypatch):
    waited = {"now": 0.0}
    monkeypatch.setattr(Clock, "stalled", lambda self: waited["now"])
    waiting = Deadline(0.2)
    time.sleep(0.3)
    waited["now"] = 0.5
    assert not waiting.passed(), "0.3 s went by, and 0.5 s of it nothing ran"
    assert "0.5 s more" in str(waiting)


def test_a_machine_that_never_runs_it_still_ends_it(monkeypatch):
    monkeypatch.setattr(Clock, "stalled", lambda self: 100.0)
    monkeypatch.setattr(fairclock, "MOST", 2)
    waiting = Deadline(0.1)
    time.sleep(0.25)
    assert waiting.passed()


def test_a_stopwatch_leaves_the_stall_out(monkeypatch):
    monkeypatch.setattr(Clock, "stalled", lambda self: 0.2)
    watch = Stopwatch()
    time.sleep(0.3)
    assert 0.05 < watch.elapsed() < 0.2, watch.elapsed()


@needs_proc
def test_a_background_process_is_found_by_its_command_line():
    word = uuid.uuid4().hex
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", word])
    try:
        # A withPackages python is a wrapper that execs the real one, and
        # Popen returns before that second exec: its cmdline reads empty
        # meanwhile. Measured in the check's sandbox, 2026-10-28.
        waiting = Deadline(10)
        while child.pid not in pids_naming(word) and not waiting.passed():
            time.sleep(0.01)
        assert child.pid in pids_naming(word)
    finally:
        child.kill()
        child.wait()


@needs_proc
def test_the_clock_watches_every_process_under_it(monkeypatch):
    "Through the kernel's children lists, and through a scan of /proc where it keeps none."
    child = subprocess.Popen([sys.executable, "-c", "import subprocess, sys; subprocess.run(['sleep', '30'])"])
    try:
        waiting = Deadline(10)
        while not (found := fairclock._under([os.getpid()])) - {os.getpid(), child.pid} and not waiting.passed():
            time.sleep(0.01)
        assert child.pid in found and len(found) >= 3, found
        monkeypatch.setattr(fairclock, "_children", lambda pid: None)
        assert fairclock._under([os.getpid()]) == found
    finally:
        child.kill()
        child.wait()


#: Each front end: how to run a coroutine, bound it, sleep in it, and
#: what it raises when the time is up.
Bound = Callable[[float, Callable[[], Awaitable[object]]], Awaitable[object]]


async def _asyncio_bound(seconds: float, body: Callable[[], Awaitable[object]]) -> object:
    async with fair_asyncio.timeout(seconds):
        return await body()


async def _anyio_bound(seconds: float, body: Callable[[], Awaitable[object]]) -> object:
    with fair_anyio.fail_after(seconds):
        return await body()


async def _trio_bound(seconds: float, body: Callable[[], Awaitable[object]]) -> object:
    with fair_trio.fail_after(seconds):
        return await body()


FRONTS = {
    "asyncio": (asyncio.run, _asyncio_bound, asyncio.sleep, TimeoutError),
    "anyio-asyncio": (lambda main: anyio.run(lambda: main), _anyio_bound, anyio.sleep, TimeoutError),
    "anyio-trio": (lambda main: anyio.run(lambda: main, backend="trio"), _anyio_bound, anyio.sleep, TimeoutError),
    "trio": (lambda main: trio.run(lambda: main), _trio_bound, trio.sleep, trio.TooSlowError),
}
fronts = pytest.mark.parametrize("front", FRONTS)


@fronts
def test_a_hang_with_the_cpu_ends_on_time(front):
    run, bound, sleep, expired = FRONTS[front]
    began = time.monotonic()
    with pytest.raises(expired, match=r"waited 0\.2 s"):
        run(bound(0.2, lambda: sleep(30)))
    assert time.monotonic() - began < 1.5


@fronts
def test_a_stall_stretches_the_wait(front, monkeypatch):
    run, bound, sleep, _expired = FRONTS[front]
    monkeypatch.setattr(Clock, "stalled", lambda self: 5.0)
    assert run(bound(0.1, lambda: _after(sleep(0.4)))) == "done"


async def _after(awaited: Awaitable[object]) -> str:
    await awaited
    return "done"


@fronts
def test_the_bodys_own_error_passes_as_it_came(front):
    run, bound, _sleep, _expired = FRONTS[front]

    async def body() -> None:
        raise TimeoutError("mine")

    with pytest.raises(TimeoutError, match=r"^mine$"):
        run(bound(5, body))


def test_an_outer_cancellation_is_not_a_timeout():
    async def main() -> str:
        with anyio.move_on_after(0.1) as outer:
            with fair_anyio.fail_after(30):
                await anyio.sleep(30)
        return "outer" if outer.cancelled_caught else "inner"

    assert anyio.run(main) == "outer"


@fronts
def test_a_thousand_short_scopes_share_one_thread(front):
    """
    A thread per scope read every thread before it: a pymux helper that
    bounds each frame paid 40 ms a scope. Lillecarl/pyterm#1000.
    """
    run, bound, sleep, _expired = FRONTS[front]

    async def scopes() -> float:
        began = time.monotonic()
        for _ in range(1000):
            await bound(10, lambda: sleep(0))
        return time.monotonic() - began

    assert run(scopes()) < 1.0
    assert sum(thread.name == "fairclock" for thread in threading.enumerate()) <= 1


def test_the_package_reaches_only_its_own_front_ends_library():
    "Each module imports the standard library, and a front end its own loop's library: it can leave pyterm whole."
    allowed = {
        "__init__": set(),
        "_watch": set(),
        "asyncio": set(),
        "subprocess": set(),
        "anyio": {"anyio"},
        "trio": {"trio"},
    }
    here = Path(fairclock.__file__).parent
    for name, extra in allowed.items():
        tree = ast.parse((here / ("%s.py" % name)).read_text())
        roots = {
            alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }
        roots |= {
            node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 0
        }
        foreign = roots - set(sys.stdlib_module_names) - extra - {"__future__"}
        assert not foreign, (name, foreign)


#: A child that needs this much CPU, then ends.
WORK = """
import os, sys, time
os.sched_setaffinity(0, {%d})
end = time.process_time() + %r
while time.process_time() < end:
    pass
"""
SPIN = "import os\nos.sched_setaffinity(0, {%d})\nwhile True:\n    pass\n"


@contextlib.contextmanager
def _crowded(cpu: int, spinners: int):
    "Processes that hold one CPU busy."
    crowd = [subprocess.Popen([sys.executable, "-c", SPIN % (cpu,)]) for _ in range(spinners)]
    try:
        yield
    finally:
        for one in crowd:
            one.kill()
            one.wait()


async def _ended(child: subprocess.Popen, sleep: Callable[[float], Awaitable[object]]) -> str:
    while child.poll() is None:
        await sleep(0.01)
    return "done"


@needs_proc
@fronts
def test_a_starved_child_still_meets_its_deadline(front):
    """
    A child pinned to one CPU beside four spinners runs a fifth of the
    time. It needs 0.15 s of CPU against a deadline of 0.5 s, so a wall
    clock gives up on it and fairclock does not: a deadline in fair time
    passes once the child has had about that much CPU.
    """
    run, bound, sleep, _expired = FRONTS[front]
    cpu = min(os.sched_getaffinity(0))
    with _crowded(cpu, spinners=4):
        child = subprocess.Popen([sys.executable, "-c", WORK % (cpu, 0.15)])
        try:
            assert run(bound(0.5, lambda: _ended(child, sleep))) == "done"
        finally:
            child.kill()
            child.wait()

        # The same wait on the wall clock, to prove the crowd starves it.
        child = subprocess.Popen([sys.executable, "-c", WORK % (cpu, 0.15)])
        try:
            with pytest.raises(TimeoutError):
                anyio.run(_wall, child)
        finally:
            child.kill()
            child.wait()


async def _wall(child: subprocess.Popen) -> None:
    with anyio.fail_after(0.5):
        await _ended(child, anyio.sleep)


def test_a_command_says_what_subprocess_run_would():
    done = fair_subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read()); sys.exit(3)"],
        5,
        input=b"echoed",
        capture_output=True,
    )
    assert (done.returncode, done.stdout.strip()) == (3, b"echoed")
    with pytest.raises(subprocess.CalledProcessError):
        fair_subprocess.run([sys.executable, "-c", "raise SystemExit(3)"], 5, check=True)


def test_a_command_that_hangs_is_killed_on_time():
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired, match=r"of wall time, .* forgiven as waits for a CPU") as expired:
        fair_subprocess.run([sys.executable, "-c", "import time; time.sleep(30)"], 0.3, capture_output=True)
    assert time.monotonic() - began < 2
    assert expired.value.wall >= 0.3


def test_processes_that_wait_in_turn_add_up_and_threads_side_by_side_do_not(monkeypatch):
    "A server and the check it runs wait one after the other; one process's threads wait together."
    readings = iter([{}, {(10, 10): 2.0, (10, 11): 3.0, (20, 20): 4.0}])
    monkeypatch.setattr(Clock, "_read", lambda self: next(readings))
    clock = Clock().start()
    assert clock.stalled() == 3.0 + 4.0


@needs_proc
def test_a_process_that_came_and_went_before_the_deadline_still_counts():
    "An upgrade's `check-snapshot` starts and ends inside the wait. Lillecarl/pyterm#1000."
    cpu = min(os.sched_getaffinity(0))
    clock = Clock().start()
    with _crowded(cpu, spinners=4):
        child = subprocess.Popen([sys.executable, "-c", WORK % (cpu, 0.15)])
        while child.poll() is None:
            clock.left(60)
            time.sleep(0.05)
    # Every child has gone, so only what the clock sampled can say this.
    assert clock.stalled() > 0.1


@needs_proc
def test_a_starved_command_still_ends():
    "As the starved child above, through `run`: 0.15 s of CPU at a fifth of one CPU, against 0.5 s."
    cpu = min(os.sched_getaffinity(0))
    with _crowded(cpu, spinners=4):
        assert fair_subprocess.run([sys.executable, "-c", WORK % (cpu, 0.15)], 0.5).returncode == 0
