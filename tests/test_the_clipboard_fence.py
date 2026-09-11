"""
The clipboard fence: the settle waits for the token the terminal took.
"""

import pytest

from pyterm_pytest.seats import wait_for_the_clipboard


def test_a_token_that_is_there_costs_nothing():
    "The fence came, and the settle starts."

    def read():
        return b"fence-Zm9v is in the clipboard"

    assert (
        wait_for_the_clipboard(read, "fence-Zm9v", lambda: None, "the window", None)
        == 0.0
    )


def test_a_fence_that_never_comes_is_a_fault(tmp_path):
    "And the fault says the terminal never took it."

    def read():
        return b""

    with pytest.raises(RuntimeError) as reason:
        wait_for_the_clipboard(
            read, "fence-Zm9v", lambda: None, "the window", tmp_path / "log", timeout=0.1
        )

    assert "never took" in str(reason.value)


def test_a_terminal_that_ended_is_the_fault(tmp_path):
    "A dead terminal will never take the fence, so this says so at once."

    def read():
        return b""

    with pytest.raises(RuntimeError) as reason:
        wait_for_the_clipboard(read, "fence-Zm9v", lambda: 3, "the window", tmp_path / "log")

    assert "ended before" in str(reason.value)
