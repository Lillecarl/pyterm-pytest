"""
The seats' own promises, apart from the waits they run.
"""

import os
import socket
import struct
import threading

import pytest

from pyterm_pytest import seats as the_seats
from pyterm_pytest.seats import (
    COULD_NOT_RUN,
    SEATS,
    Seat,
    TheSeatIsGone,
    XSeat,
    kiosk_configuration,
    open_the_seats,
    why_the_display_refuses,
    with_no_answer,
)


def test_every_seat_read_the_fence():
    "Both seats hold a display server whose clipboard a client can watch."

    for name, seat_class in SEATS.items():
        assert seat_class.reads_fence, "%s cannot read a fence" % (name,)


def test_the_base_cannot():
    "And the guard that refuses a token is the base's shape."

    assert Seat.reads_fence is False


class Fine(Seat):
    name = "fine"

    def trouble(self):
        return ""


class Gone(Seat):
    name = "gone"

    def trouble(self):
        return "the X server of the gone seat ended with -11"


def test_a_seat_that_can_be_drawn_on_raises_nothing():
    Fine().still_there()


def test_a_seat_that_is_gone_says_so_and_says_why():
    with pytest.raises(TheSeatIsGone) as raised:
        Gone().still_there()
    assert "ended with -11" in str(raised.value)


def test_a_run_with_no_answer_does_not_end_with_zero_or_one():
    """
    Zero is a pass and one is any error, and `pyte/nix/suite.nix` has
    to tell "could not run" from both: it keeps the first two and
    throws this one away. Lillecarl/pymux#216.
    """
    assert with_no_answer("the seat would not start") == COULD_NOT_RUN
    assert COULD_NOT_RUN not in (0, 1, 2)
    # `timeout` and the shell own 124 to 127, and a signal is 128 up.
    assert COULD_NOT_RUN < 124


class Terminal:
    def __init__(self, seat):
        self.seat = seat


def test_a_seat_that_will_not_start_is_not_a_verdict(monkeypatch):
    "The failure is the seat's, not whatever the display server said."

    class WillNot(Seat):
        def start(self, work):
            raise OSError("no such file: Xvfb")

    monkeypatch.setitem(SEATS, "x", WillNot)
    with pytest.raises(TheSeatIsGone) as raised:
        open_the_seats([Terminal("x")], work=None)
    assert "would not start" in str(raised.value)
    assert "Xvfb" in str(raised.value)


class _AnXServer:
    "As much of an Xvfb as `XSeat.start` uses, and its argv."

    argv = None

    def __init__(self, argv, pass_fds=(), stdout=None, stderr=None, **_rest):
        _AnXServer.argv = list(argv)
        stdout.close()
        os.write(pass_fds[0], b"7\n")

    def poll(self):
        return None


def test_the_x_seat_asks_its_server_not_to_reset(monkeypatch, tmp_path):
    """
    One server serves the whole run and each picture kills its
    terminal, so the client count reaches zero between every pair of
    pictures. An X server with no clients resets, which closes the
    sockets it listens on and opens them again, and a terminal that
    starts inside that window is refused: "Can't open display".

    Measured, with an Xvfb of each kind and 24 busy processes beside
    it: 67 of 300 client departures refused the client that came next,
    and 0 of 300 with `-noreset`. Lillecarl/pymux#431.
    """
    monkeypatch.setattr(the_seats.subprocess, "Popen", _AnXServer)

    seat = XSeat().start(tmp_path)

    assert "-noreset" in _AnXServer.argv
    # The number the server chose, and not one this ever assumes.
    assert seat.number == ":7"


# ----------------------------------------------------------------------
# Whether a display will serve a client.
#
# The servers here are abstract sockets and nothing else. An abstract
# name is unbound the moment its socket closes and leaves no file
# behind, so a test that ends in the middle leaves the next run a clean
# machine. That is why none of these needs an Xvfb.


def _a_display_nobody_serves():
    "A display number that no server on this machine has taken."
    for number in range(90, 120):
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.bind("\0/tmp/.X11-unix/X%d" % number)
        except OSError:
            continue
        finally:
            probe.close()
        return number
    raise AssertionError("every display from 90 to 119 is taken")


def a_server_that_answers(answer):
    "A front door that reads the setup request and answers this."
    number = _a_display_nobody_serves()
    door = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    door.bind("\0/tmp/.X11-unix/X%d" % number)
    door.listen(4)

    def serve():
        while True:
            try:
                client, _ = door.accept()
            except OSError:
                return
            with client:
                client.recv(12)
                client.sendall(answer)

    threading.Thread(target=serve, daemon=True).start()
    return ":%d" % number, door


#: What a server sends a client it will serve: protocol 11.0, and no
#: data after the header.
SERVED = struct.pack("<BxHHH", 1, 11, 0, 0)


def refusal(reason):
    "What a server sends a client it will not serve, and why."
    padded = reason + b"\0" * (-len(reason) % 4)
    return (
        struct.pack("<BBHHH", 0, len(reason), 11, 0, len(padded) // 4) + padded
    )


def test_a_display_nothing_answers_says_so():
    assert "nothing answers display" in why_the_display_refuses(
        ":%d" % _a_display_nobody_serves()
    )


def test_a_display_that_serves_a_client_is_no_trouble():
    "Or every red run would read as a seat that is gone, and none would count."
    number, door = a_server_that_answers(SERVED)
    try:
        assert why_the_display_refuses(number) == ""
    finally:
        door.close()


def test_a_display_that_refuses_gives_the_reason_the_server_gave():
    """
    The server's own words are the only thing that says why, and
    nothing else in a run holds them. "Maximum number of clients
    reached" is one of them. Lillecarl/pymux#431.
    """
    number, door = a_server_that_answers(
        refusal(b"Maximum number of clients reached")
    )
    try:
        said = why_the_display_refuses(number)
    finally:
        door.close()
    assert "Maximum number of clients reached" in said


class _Running:
    "An Xvfb that has not ended."

    returncode = None

    def poll(self):
        return None


def test_a_server_that_runs_and_cannot_be_reached_is_a_seat_that_is_gone(tmp_path):
    """
    The trouble of this seat was the exit code of the process alone. A
    display that refuses every client while its server runs read as
    healthy, so the terminal's "Can't open display" became a verdict on
    a picture and a red run of it stayed in the store.
    Lillecarl/pymux#431.
    """
    seat = XSeat()
    seat._process = _Running()
    seat._log = tmp_path / "xvfb.log"
    seat.number = ":%d" % _a_display_nobody_serves()

    with pytest.raises(TheSeatIsGone) as raised:
        seat.still_there()
    assert "nothing answers display" in str(raised.value)


def test_a_server_that_runs_and_serves_is_not(tmp_path):
    seat = XSeat()
    seat._process = _Running()
    seat._log = tmp_path / "xvfb.log"
    seat.number, door = a_server_that_answers(SERVED)
    try:
        seat.still_there()
    finally:
        door.close()


def test_the_wayland_seat_starts_no_x_server():
    """
    wlroots opens an X display as soon as the compositor starts, before
    it has a client for it, so every picture took a display number and
    gave it back. The X seat's server is on the other side of that
    search. Nothing on this seat speaks X: foot and kitty are Wayland
    only and `_run` passes an empty DISPLAY. Lillecarl/pymux#432.
    """
    assert "xwayland disable" in kiosk_configuration("/tmp/run.sh")


class _Ended:
    "A terminal that is not there any more."

    returncode = 1

    def poll(self):
        return 1


def test_a_seat_that_is_gone_is_not_a_runtime_error():
    """
    Every driver wraps a picture in `except RuntimeError` to put the
    logs of the room beside the reason. A seat that is gone caught
    there comes out as a verdict on the picture, which is the one thing
    it is not. Lillecarl/pymux#431.
    """
    assert not issubclass(TheSeatIsGone, RuntimeError)


def test_a_terminal_that_never_opened_the_display_fails_the_seat(tmp_path):
    """
    A terminal that could not open the display drew nothing, so there
    is no picture to judge. The window a refusal opens is short --
    0.305s at its worst, measured -- so the display answers again by
    the time anything asks it, and what the terminal said is what
    stays. Lillecarl/pymux#431.
    """
    seat = XSeat()
    seat.number = ":0"
    log = tmp_path / "bare.log"
    log.write_bytes(b"xterm: Xt error: Can't open display: :0\n")

    with pytest.raises(TheSeatIsGone) as raised:
        seat._wait_for_a_new_window("XTerm", set(), _Ended(), log)
    assert "never reached display :0" in str(raised.value)


def test_a_window_that_never_appears_asks_the_display(monkeypatch, tmp_path):
    """
    `xdotool search` answers with an empty list whether the display
    refused it or the window is not there yet, so a seat that went away
    during a picture read as a window that was slow.
    """
    monkeypatch.setattr(the_seats, "APPEAR_TIMEOUT", 0.0)
    seat = XSeat()
    seat._process = _Running()
    seat._log = tmp_path / "xvfb.log"
    seat.number = ":%d" % _a_display_nobody_serves()

    with pytest.raises(TheSeatIsGone):
        seat._wait_for_a_new_window(
            "XTerm", set(), _Running(), tmp_path / "bare.log"
        )


def test_a_terminal_that_died_of_something_else_is_still_a_verdict(tmp_path):
    "A terminal that reached the display and then died is the run's answer."
    seat = XSeat()
    seat.number = ":0"
    log = tmp_path / "bare.log"
    log.write_bytes(b"xterm: cannot load font 'nonesuch'\n")

    with pytest.raises(RuntimeError) as raised:
        seat._wait_for_a_new_window("XTerm", set(), _Ended(), log)
    assert "ended before it drew anything" in str(raised.value)


def test_one_seat_is_opened_for_the_terminals_that_share_it(monkeypatch):
    started = []

    class Counting(Seat):
        def start(self, work):
            started.append(work)
            return self

    monkeypatch.setitem(SEATS, "x", Counting)
    seats = open_the_seats([Terminal("x"), Terminal("x")], work="here")
    assert list(seats) == ["x"]
    assert started == ["here"]
