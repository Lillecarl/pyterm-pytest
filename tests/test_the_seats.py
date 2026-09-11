"""
The seats' own promises, apart from the waits they run.
"""

from pyterm_pytest.seats import SEATS, Seat


def test_every_seat_read_the_fence():
    "Both seats hold a display server whose clipboard a client can watch."

    for name, seat_class in SEATS.items():
        assert seat_class.reads_the_fence, "%s cannot read a fence" % (name,)


def test_the_base_cannot():
    "And the guard that refuses a token is the base's shape."

    assert Seat.reads_the_fence is False
