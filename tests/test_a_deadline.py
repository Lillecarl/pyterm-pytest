"""A deadline forgives the time its processes waited for a CPU, and nothing else."""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from pyterm_pytest import deadline as module
from pyterm_pytest.deadline import Deadline, pids_naming, queued


def test_a_process_says_how_long_it_waited():
    if queued() is None:
        pytest.skip("this kernel keeps no schedstat")
    assert queued() >= 0.0


def test_an_idle_machine_ends_it_on_time():
    waiting = Deadline(0.2)
    assert not waiting.passed()
    time.sleep(0.3)
    assert waiting.passed()


def test_a_stall_moves_it_out_by_as_long(monkeypatch):
    waited = {"self": 0.0}
    monkeypatch.setattr(module, "queued", lambda pid="self": waited["self"])
    waiting = Deadline(0.2)
    time.sleep(0.3)
    waited["self"] = 0.5
    assert not waiting.passed(), "0.3 s went by, and 0.5 s of it nothing ran"
    assert "0.5 s more" in str(waiting)


def test_a_machine_that_never_runs_it_still_ends_it(monkeypatch):
    waited = {"self": 0.0}
    monkeypatch.setattr(module, "queued", lambda pid="self": waited["self"])
    monkeypatch.setattr(module, "MOST", 2)
    waiting = Deadline(0.1)
    waited["self"] = 100.0
    time.sleep(0.25)
    assert waiting.passed()


@pytest.mark.skipif(not Path("/proc/self").exists(), reason="no /proc")
def test_a_background_process_is_found_by_its_command_line():
    word = uuid.uuid4().hex
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", word])
    try:
        assert child.pid in pids_naming(word)
    finally:
        child.kill()
        child.wait()
