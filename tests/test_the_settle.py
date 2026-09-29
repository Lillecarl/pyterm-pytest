"""
What a settle waits for.

Two pictures in a row that are the same used to be the whole of it.
They are not evidence: a terminal that is behind and starved draws
nothing between them either, and one run photographed a pane that was
still catching up. So the tree that draws is read as well, and a
picture is kept only when both say the drawing has finished.
Lillecarl/pymux#435.
"""

import pytest
from PIL import Image

from pyterm_pytest import seats as the_seats
from pyterm_pytest.seats import IDLE_TICKS, _settle, the_tree_is_quiet


@pytest.fixture(autouse=True)
def a_short_settle(monkeypatch):
    "Three rounds rather than the gate's fifteen seconds of them."
    monkeypatch.setattr(the_seats, "SETTLE_TIMEOUT", 1.2)


def draws(colours):
    """
    A `take_one` that writes these colours in turn, the last for ever.

    One colour is a screen that has stopped changing. Two are a screen
    that has not.
    """
    drawn = []

    def take_one(where):
        colour = colours[min(len(drawn), len(colours) - 1)]
        Image.new("RGB", (4, 4), colour).save(where)
        drawn.append(colour)

    return take_one


def a_tree(runnable=0, ticks_a_round=0):
    "A `how_busy` that answers the same thing every round."
    used = [0]

    def how_busy():
        used[0] += ticks_a_round
        return runnable, used[0]

    return how_busy


def room_of(tmp_path):
    """
    The two directories a settle uses, made.

    They are not one directory: the harness keeps its scratch pictures
    in the working directory and the kept ones in the picture's room,
    and a settle that could not settle copies from the first into the
    second.
    """
    work, room = tmp_path / "work", tmp_path / "room"
    work.mkdir()
    room.mkdir()
    return work, room


def settle(tmp_path, take_one, how_busy):
    "Run a settle with the fakes, and give back what it raised."
    work, room = room_of(tmp_path)
    _settle(
        work,
        room / "bare.png",
        take_one,
        lambda: None,
        "the picture of foot",
        room / "bare.log",
        how_busy=how_busy,
    )
    return room


# ----------------------------------------------------------------------
# The reading on its own.


def test_a_tree_that_did_nothing_is_quiet():
    assert the_tree_is_quiet((0, 100), (0, 100))


def test_a_tree_inside_the_measured_idle_is_quiet():
    "Measured: a finished tree used up to two ticks a round, never more."
    assert the_tree_is_quiet((0, 100), (0, 100 + IDLE_TICKS))


def test_a_tree_that_burned_cpu_is_not_quiet():
    "A tree that draws uses about forty ticks in the same interval."
    assert not the_tree_is_quiet((0, 100), (0, 140))


def test_a_tree_with_a_task_waiting_for_a_turn_is_not_quiet():
    """
    The half that pixels and CPU both miss. A starved process burns
    almost nothing and has not finished: every task of it that still
    has work is runnable, waiting for a turn.
    """
    assert not the_tree_is_quiet((0, 100), (2, 100))


def test_a_seat_that_cannot_read_its_tree_is_quiet():
    "Or it would never settle at all."
    assert the_tree_is_quiet(None, None)


# ----------------------------------------------------------------------
# The settle.


def test_a_still_screen_with_a_quiet_tree_settles(tmp_path):
    room = settle(tmp_path, draws([(0, 0, 0)]), a_tree())

    assert (room / "bare.png").exists()


def test_a_still_screen_whose_tree_is_still_drawing_does_not_settle(tmp_path):
    """
    The fault this exists for: two equal pictures of a pane that has
    not finished read exactly like two of a pane that has.
    """
    with pytest.raises(RuntimeError) as reason:
        settle(tmp_path, draws([(0, 0, 0)]), a_tree(ticks_a_round=40))

    assert "never went idle" in str(reason.value)


def test_a_still_screen_whose_tree_waits_for_a_turn_does_not_settle(tmp_path):
    with pytest.raises(RuntimeError) as reason:
        settle(tmp_path, draws([(0, 0, 0)]), a_tree(runnable=3))

    assert "3 tasks runnable" in str(reason.value)


def test_a_seat_that_cannot_read_its_tree_settles_on_the_pixels(tmp_path):
    "The X seat before this, and any seat whose tree cannot be found."
    room = settle(tmp_path, draws([(0, 0, 0)]), None)

    assert (room / "bare.png").exists()


def test_the_message_says_which_half_did_not_hold(tmp_path):
    """
    A settle waits for two things. One message for both would leave the
    next flake as hard to read as this one was.
    """
    with pytest.raises(RuntimeError) as reason:
        settle(
            tmp_path,
            draws([(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]),
            a_tree(ticks_a_round=40),
        )

    said = str(reason.value)
    assert "kept changing" in said
    assert "never went idle" in said


def test_a_screen_that_keeps_changing_says_only_that(tmp_path):
    with pytest.raises(RuntimeError) as reason:
        settle(
            tmp_path,
            draws([(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]),
            a_tree(),
        )

    said = str(reason.value)
    assert "kept changing" in said
    assert "never went idle" not in said


def test_the_settle_writes_what_it_read(tmp_path):
    "A settle that went wrong leaves the readings that say why."
    room = settle(tmp_path, draws([(0, 0, 0)]), a_tree())

    written = (room / "settle-bare.log").read_text().splitlines()
    assert written[0].split() == ["#", "seconds", "changed", "runnable", "ticks"]
    # The first reading is taken before there is anything to compare.
    assert written[1].split()[1:] == ["-", "0", "-"]
    assert written[2].split()[1:] == ["0", "0", "0"]
