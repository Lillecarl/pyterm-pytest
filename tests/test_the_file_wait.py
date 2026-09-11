"""
The wait for a file, which holds a settle off until the fence is there.
"""

import pytest

from pyterm_pytest.seats import wait_for_the_file


def test_a_file_that_is_there_costs_nothing(tmp_path):
    path = tmp_path / "fence"
    path.touch()

    assert wait_for_the_file(path, lambda: None, "the window", tmp_path / "log") == 0.0


def test_a_file_that_never_appears_is_a_fault(tmp_path):
    "And the fault names the file, so a person knows what never came."
    path = tmp_path / "fence"

    with pytest.raises(RuntimeError) as reason:
        wait_for_the_file(
            path, lambda: None, "the window", tmp_path / "log", timeout=0.1
        )

    assert str(path) in str(reason.value)


def test_a_program_that_ended_is_the_fault_and_not_the_wait(tmp_path):
    "A dead terminal will never touch the file, so this says so at once."
    path = tmp_path / "fence"

    with pytest.raises(RuntimeError) as reason:
        wait_for_the_file(path, lambda: 3, "the window", tmp_path / "log")

    assert "ended before" in str(reason.value)
