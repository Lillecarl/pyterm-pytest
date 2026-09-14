"""
What `differences` and `changed_region` promise about two pictures.

Nothing judged them, and two faults stood in them for as long as the
picture farms have run. `compare -metric AE` says it counts the pixels
that differ and sums the channel error instead (Lillecarl/pymux#368),
and the box came from trimming the picture `compare` draws, which holds
all the ink and not what moved (Lillecarl/pymux#370).

Each fault has a test here that fails on the old answer.
"""

import pytest
from PIL import Image

from pyterm_pytest.seats import (
    NothingToCompare,
    changed_region,
    differences,
    the_same_drawing,
)


def picture(path, size=(20, 10), fill=(0, 0, 0), marks=()):
    "A picture of one colour, with `marks` of (box, colour) drawn on it."
    image = Image.new("RGB", size, fill)
    for box, colour in marks:
        image.paste(Image.new("RGB", (box[2], box[3]), colour), (box[0], box[1]))
    image.save(path)
    return path


def test_two_pictures_that_agree_differ_nowhere(tmp_path):
    one = picture(tmp_path / "one.png")
    two = picture(tmp_path / "two.png")

    assert differences(one, two) == 0
    assert changed_region(one, two, tmp_path / "diff.png") == (0, None)


def test_one_pixel_off_by_one_in_one_band_is_one_pixel(tmp_path):
    """
    The smallest difference there is, and the one both faults hid.

    `compare -metric AE` answered 1/765 of a pixel for it and
    `int(float(...))` made that a zero, so `_settle` called such a
    screen still. Folding the bands with `convert("L")` instead of the
    largest of the three loses it the same way: blue weighs 0.07.
    """
    one = picture(tmp_path / "one.png")
    two = picture(tmp_path / "two.png", marks=[((5, 5, 1, 1), (0, 0, 1))])

    assert differences(one, two) == 1

    count, box = changed_region(one, two, tmp_path / "diff.png")
    assert (count, box) == (1, (5, 5, 1, 1))


def test_the_box_holds_what_moved_and_not_what_is_drawn(tmp_path):
    """
    A blink is judged on this box, so ink that never moved must stay
    out of it. Lillecarl/pymux#370.
    """
    ink = [((1, 1, 12, 4), (255, 255, 255))]
    one = picture(tmp_path / "one.png", marks=ink)
    two = picture(
        tmp_path / "two.png", marks=ink + [((17, 8, 2, 2), (255, 0, 0))]
    )

    count, box = changed_region(one, two, tmp_path / "diff.png")
    assert count == 4
    assert box == (17, 8, 2, 2)


def test_two_pictures_of_different_sizes_are_compared_where_they_meet(tmp_path):
    """
    vttest asks for 132 columns and xterm widens its window; a pane
    cannot, so the two pictures of that screen are 1280 and 800 pixels
    across. The width itself is not a difference.
    """
    one = picture(tmp_path / "one.png", size=(40, 10))
    two = picture(tmp_path / "two.png", size=(20, 10))

    assert differences(one, two) == 0

    two = picture(
        tmp_path / "two.png", size=(20, 10), marks=[((3, 3, 2, 2), (0, 255, 0))]
    )
    assert differences(one, two) == 4


def test_the_picture_of_the_difference_marks_what_moved(tmp_path):
    "A person reads it, so the pixels that moved have to stand out."
    one = picture(tmp_path / "one.png", marks=[((0, 0, 20, 10), (200, 200, 200))])
    two = picture(
        tmp_path / "two.png",
        marks=[((0, 0, 20, 10), (200, 200, 200)), ((5, 5, 2, 2), (0, 0, 0))],
    )

    into = tmp_path / "diff.png"
    differences(one, two, into)

    drawn = Image.open(into).convert("RGB")
    assert drawn.getpixel((5, 5)) == (255, 0, 0)
    # And what did not move is still there to look at, only dimmer.
    assert drawn.getpixel((0, 0)) == (50, 50, 50)


#: The picture two terminals drew of one image. The same four pixels,
#: in different corners of differently sized outputs, because a
#: compositor gives one terminal the whole screen and the other centres
#: its cells in it.
DRAWING = [((0, 0, 2, 2), (255, 0, 0)), ((2, 0, 2, 2), (0, 204, 51))]


def test_one_drawing_in_two_places_is_the_same_drawing(tmp_path):
    "Where a terminal put it is not a difference. Lillecarl/pymux#262."
    moved = [
        ((box[0] + 7, box[1] + 3, box[2], box[3]), colour) for box, colour in DRAWING
    ]
    one = picture(tmp_path / "one.png", size=(20, 10), marks=DRAWING)
    two = picture(tmp_path / "two.png", size=(30, 14), marks=moved)

    count, first_box, second_box = the_same_drawing(one, two, tmp_path / "d.png")
    assert count == 0
    assert first_box == (0, 0, 4, 2)
    assert second_box == (7, 3, 11, 5)


def test_a_drawing_that_differs_is_counted_where_it_differs(tmp_path):
    one = picture(tmp_path / "one.png", size=(20, 10), marks=DRAWING)
    two = picture(
        tmp_path / "two.png",
        size=(20, 10),
        marks=DRAWING + [((3, 1, 1, 1), (255, 204, 0))],
    )

    count, _first, _second = the_same_drawing(one, two)
    assert count == 1


def test_two_drawings_of_different_sizes_are_not_compared(tmp_path):
    """
    A count would be a number with no meaning. The cell of one terminal
    has moved, or a font has, and somebody has to look.
    """
    one = picture(tmp_path / "one.png", size=(20, 10), marks=DRAWING)
    two = picture(
        tmp_path / "two.png", size=(20, 10), marks=[((0, 0, 4, 3), (255, 0, 0))]
    )

    with pytest.raises(NothingToCompare) as raised:
        the_same_drawing(one, two)
    assert "4x2" in str(raised.value) and "4x3" in str(raised.value)


def test_a_picture_that_draws_nothing_says_so(tmp_path):
    "A terminal that drew no image is not a drawing of zero pixels."
    one = picture(tmp_path / "one.png", size=(20, 10), marks=DRAWING)
    two = picture(tmp_path / "two.png", size=(20, 10))

    with pytest.raises(NothingToCompare) as raised:
        the_same_drawing(one, two)
    assert "draws nothing at all" in str(raised.value)
