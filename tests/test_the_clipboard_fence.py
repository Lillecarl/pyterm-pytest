"""
The clipboard fence: the settle waits for the token the terminal took.
"""

import base64
import re
import subprocess

import pytest

from pyterm_pytest.seats import fence, wait_for_the_clipboard


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


def _program(tmp_path, lines):
    "Run shell lines, with what they write as the terminal's clipboard."
    written = tmp_path / "written"
    process = subprocess.Popen(["sh", "-c", lines], stdout=written.open("wb"))

    def read():
        # The terminal decodes the escape; this reads the escape itself.
        return b"".join(
            base64.b64decode(one)
            for one in re.findall(rb"\x1b]52;c;([A-Za-z0-9+/=]*)\x07", written.read_bytes())
        )

    return process, read


def test_the_program_writes_until_the_seat_took_it(tmp_path):
    "The handshake ends both sides, and leaves nothing for the next run."
    process, read = _program(tmp_path, fence(tmp_path, "fence-Zm9v"))

    wait_for_the_clipboard(read, "fence-Zm9v", process.poll, "the window", None, work=tmp_path)

    assert process.wait(timeout=5) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["written"]


def test_a_program_that_writes_once_is_a_fault(tmp_path):
    "It never says it stopped, so a stray fence could land in the settle."
    lines = "printf '\\033]52;c;%s\\007'; exec sleep 5\n" % base64.b64encode(b"fence-Zm9v").decode()
    process, read = _program(tmp_path, lines)

    try:
        with pytest.raises(RuntimeError) as reason:
            wait_for_the_clipboard(
                read, "fence-Zm9v", process.poll, "the window", tmp_path / "log", timeout=0.5, work=tmp_path
            )
    finally:
        process.kill()
        process.wait()

    assert "never came" in str(reason.value)
