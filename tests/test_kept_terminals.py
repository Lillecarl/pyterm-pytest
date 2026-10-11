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
from pyterm_pytest.ptyproxy import Parsed, answers_from
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
        # Each terminal answers with its own number, so a test can say whose answer it got.
        attributes = b"\x1b[?%dc" % self.opened
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
                    if b"\x1b[c" in data:
                        os.write(master, attributes)
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


def twins_of(seat, command, work, answer=None, **options):
    "What each twin shows: the bare one, and the one through a stand-in for the program under test."
    paths = (work / "bare.png", work / "through.png")
    logs = (work / "bare.log", work / "through.log")

    def director(twins):
        twins.until_it_ends()
        twins.photograph(paths)
        twins.record(work)

    seat.twins(Terminal(), lambda viewer: "OTHER=1 exec " + viewer, command, work, logs, director, answer, **options)
    return [Image.open(path).tobytes().decode().rstrip() for path in paths]


def test_twins_show_one_run_in_two_kept_terminals(seat, tmp_path):
    kept = seat()
    ran = tmp_path / "ran"
    assert twins_of(kept, "echo >> %s; printf twins" % ran, tmp_path) == ["twins", "twins"]
    assert ran.read_text() == "\n"
    assert twins_of(kept, "printf again", tmp_path) == ["again", "again"]
    assert kept.opened == 2


def asked(work):
    """
    An application that asks for the device attributes, and keeps what
    comes back. A copy started through the stand-in has OTHER set, so
    two copies apart keep theirs apart.
    """
    return "stty -echo -icanon min 0 time 5; printf '\\033[c'; cat > %s-$OTHER" % (work / "answered")


def test_neither_twin_answers_the_application(seat, tmp_path):
    twins_of(seat(), asked(tmp_path), tmp_path)
    assert (tmp_path / "answered-").read_bytes() == b""


def test_a_director_drives_the_twins_while_the_application_runs(seat, tmp_path):
    paths = (tmp_path / "bare.png", tmp_path / "through.png")

    def director(twins):
        twins.shows("name?")
        twins.press("ada\r")
        twins.shows("ada")
        twins.photograph(paths)
        return "".join(twins.rows()).rstrip()

    asks = "stty -echo; printf 'name? '; read name; printf 'hello %s' \"$name\"; sleep 30"
    seen = seat().twins(
        Terminal(), lambda viewer: "exec " + viewer, asks, tmp_path, (tmp_path / "b.log", tmp_path / "t.log"), director
    )
    assert seen == "name? hello ada"
    assert [Image.open(path).tobytes().decode().rstrip() for path in paths] == ["name? hello ada"] * 2


def test_twins_apart_each_hear_their_own_terminal(seat, tmp_path):
    twins_of(seat(), asked(tmp_path), tmp_path, apart=True)
    assert (tmp_path / "answered-").read_bytes() == b"\x1b[?1c"
    assert (tmp_path / "answered-1").read_bytes() == b"\x1b[?2c"


def test_twins_apart_run_the_application_once_each(seat, tmp_path):
    ran = tmp_path / "ran"
    assert twins_of(seat(), "echo >> %s; printf apart" % ran, tmp_path, apart=True) == ["apart", "apart"]
    assert ran.read_text() == "\n\n"


@pytest.mark.parametrize("apart", [False, True])
def test_each_terminals_replies_are_recorded(seat, tmp_path, apart):
    "Shared, the proxy drops both answers; they are kept anyway, to be compared."
    twins_of(seat(), asked(tmp_path), tmp_path, apart=apart)
    assert "b'\\x1b[?1c'" in (tmp_path / "bare-replies.txt").read_text()
    assert "b'\\x1b[?2c'" in (tmp_path / "through-replies.txt").read_text()
    assert (tmp_path / "through-output.bin").exists() == apart


def test_the_proxy_answers_for_both_twins(seat, tmp_path):
    twins_of(seat(), asked(tmp_path), tmp_path, answers_from({b"\x1b[c": b"\x1b[?62c"}))
    assert (tmp_path / "answered-").read_bytes() == b"\x1b[?62c"

