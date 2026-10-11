"""
A seat keeps one terminal per kind for the run, and resets it between
runs. The terminal here is a pty with the viewer on its far side and a
pyte screen on its near one, and a picture is that screen as pixels.
Lillecarl/pymux#591.
"""

import base64
import os
import re
import subprocess
import threading

import pytest
from PIL import Image
from ptyhost.backends.posix_utils import set_terminal_size

from pyterm_pytest import seats as the_seats
from pyterm_pytest.ptyproxy import Parsed
from pyterm_pytest.seats import Seat, Shown

_FENCE = re.compile(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)\x07")


class Terminal:
    def __init__(self, name="fake", environment=None):
        self.name = name
        self.environment = environment or {}

    def argv(self, command):
        return ["sh", "-c", command]


class FakeSeat(Seat):
    """
    A seat whose terminals are ptys. `ignores_ris` makes a terminal that
    keeps its screen through a reset, the fault the check is for.
    """

    name = "fake"
    reads_fence = True

    def __init__(self, ignores_ris=False):
        super().__init__()
        self.ignores_ris = ignores_ris
        self.opened = 0
        self.received = b""

    def clipboard(self):
        found = _FENCE.findall(self.received)
        return base64.b64decode(found[-1]) if found else b""

    def _open(self, terminal, command, work, log, stack):
        self.opened += 1
        master, slave = os.openpty()
        set_terminal_size(master, 4, 10)
        viewer = subprocess.Popen(
            terminal.argv(command),
            stdin=slave,
            stdout=slave,
            stderr=open(log, "wb"),
            start_new_session=True,
            env={**os.environ, **terminal.environment},
        )
        os.close(slave)
        screen = Parsed(4, 10)
        lock = threading.Lock()

        def read():
            while True:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return
                if not data:
                    return
                with lock:
                    self.received += data
                    if self.ignores_ris:
                        data = data.replace(b"\x1bc", b"")
                    screen.feed(data)

        threading.Thread(target=read, daemon=True).start()

        def take(path):
            with lock:
                cells = "".join(row.ljust(10)[:10] for row in screen.rows()).encode("latin-1", "replace")
            Image.frombytes("L", (10, 4), cells).save(path)

        def close():
            viewer.kill()
            viewer.wait()
            os.close(master)

        stack.callback(close)
        return Shown(log=log, take=take, roots=[], gone=viewer.poll)


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setattr(the_seats, "SETTLE_TIMEOUT", 2.0)
    made = []

    def make(**options):
        made.append(FakeSeat(**options))
        return made[-1]

    yield make
    for one in made:
        one.stop()


def shown_by(seat, terminal, command, work):
    "What the application wrote, as the terminal shows it, and how it ended."
    pictured = work / "pictured.png"

    def director(take_one, ended):
        for _ in range(500):
            if ended() is not None:
                break
            threading.Event().wait(0.01)
        take_one(pictured)
        return ended()

    # The wait gives the viewer time to show the last of it.
    held = "(%s); code=$?; sleep 0.3; exit $code" % command
    code = seat.running(terminal, held, work, work / "run.log", director)
    return Image.open(pictured).tobytes().decode().rstrip(), code


def test_two_runs_share_one_terminal(seat, tmp_path):
    kept = seat()
    first, code = shown_by(kept, Terminal(), "printf first; exit 3", tmp_path)
    assert (first, code) == ("first", 3)
    second, _ = shown_by(kept, Terminal(), "printf second", tmp_path)
    assert second == "second"
    assert kept.opened == 1


def test_a_run_starts_where_the_terminal_would_start_it(seat, tmp_path):
    kept = seat()
    said, _ = shown_by(kept, Terminal(environment={"TERM": "fake-term"}), 'printf "$TERM"', tmp_path)
    assert said == "fake-term"


def test_a_terminal_whose_reset_leaves_the_screen_is_let_go(seat, tmp_path):
    kept = seat(ignores_ris=True)
    shown_by(kept, Terminal(), "printf first", tmp_path)
    assert kept.opened == 1
    second, _ = shown_by(kept, Terminal(), "printf second", tmp_path)
    assert second == "second"
    assert kept.opened == 2


def test_another_command_line_is_another_terminal(seat, tmp_path):
    kept = seat()

    class Other(Terminal):
        def argv(self, command):
            return ["sh", "-c", "OTHER=1 " + command]

    shown_by(kept, Terminal(), "printf one", tmp_path)
    shown_by(kept, Other(), "printf two", tmp_path)
    assert kept.opened == 2

