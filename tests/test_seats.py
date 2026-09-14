"""
The seats' own promises, apart from the waits they run.
"""

import pytest

from pyterm_pytest.seats import (
    COULD_NOT_RUN,
    SEATS,
    Seat,
    TheSeatIsGone,
    open_the_seats,
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
