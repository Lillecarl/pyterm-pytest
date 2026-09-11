"""
A client that writes a picture each time the screen changes.

The clock cannot tell a cursor that blinks slowly from one that never
blinks: a burst taken at fixed intervals samples a blink phase, and a
terminal whose blink is longer than the burst reads as one that never
blinks. The compositor knows when pixels change -- screencopy's
`copy_with_damage` holds a copy back until there is damage -- so the
honest instrument is the compositor's own: every frame this writes is
a moment the screen actually changed, whatever the clock was doing.

The bindings are the generated ones, like the keyboard holder's. Each
frame goes to `<directory>/frame.<n>.rgba` as raw bytes, rows padded
off, and `meta` carries the geometry; the seat turns them into PNGs,
because the raw reader of imagemagick wants a stride that padding
breaks and a byte order the buffer does not keep.

What a frame means, measured: the compositor damages when the
terminal submits a frame, not when a pixel differs -- a program that
rewrites the same cell every tenth of a second fills the frame budget
with pictures of nothing, and the blink between them never gets its
turn. The reader of the frames compares pixels and counts only the
pairs that differ, so the flood is not mistaken for a blink; the
fixture that draws once is what gives the blink the frames.

The client stops after `frames` frames, or when the screen has been
quiet for `QUIET` seconds: a cursor that never blinks is an answer,
and this writes the frames it has and exits.
"""

import mmap
import os
import select
import sys
import time
from pathlib import Path

from protocols.wayland import WlOutput, WlShm
from protocols.wlr_screencopy_unstable_v1.zwlr_screencopy_manager_v1 import (
    ZwlrScreencopyManagerV1,
)
from pywayland.client import Display

#: How long the screen may stay unchanged before this gives up. A
#: cursor that blinks at all blinks well inside this; five seconds of
#: nothing means nothing is coming.
QUIET = 5.0

output = None
shm = None
manager = None
frame = None

#: What the buffer event said, and the buffer to read it through.
geometry = None
buffer_object = None
pool = None
memory = None
y_inverted = False

directory = None
frames_written = 0
frames_wanted = 0
last_event = 0.0


def on_global(registry, name, interface, version):
    global output, shm, manager
    if interface == "wl_output" and output is None:
        output = registry.bind(name, WlOutput, min(version, 2))
    elif interface == "wl_shm" and shm is None:
        shm = registry.bind(name, WlShm, min(version, 1))
    elif interface == "zwlr_screencopy_manager_v1" and manager is None:
        manager = registry.bind(name, ZwlrScreencopyManagerV1, min(version, 2))


def start_a_frame():
    """
    A frame object is one copy: after its ready, the protocol says to
    destroy it, and the next capture is a new one. The buffer is ours
    and lives through them.
    """
    new_frame = manager.capture_output(0, output)
    new_frame.dispatcher["buffer"] = on_buffer
    new_frame.dispatcher["flags"] = on_flags
    new_frame.dispatcher["ready"] = on_ready
    new_frame.dispatcher["failed"] = on_failed
    return new_frame


def on_buffer(frame, format, width, height, stride):
    """
    The frame told us the shm buffer to make for it.

    Every proxy this makes is held for the life of the client: a
    collected one destroys its wayland object underneath a frame that
    is still being read from, and pywayland goes down with it.
    """
    global geometry, buffer_object, memory, pool
    if format not in (0, 1):
        raise RuntimeError("the compositor offers shm format %d" % (format,))
    if geometry is None:
        size = stride * height
        fd = os.memfd_create("pyterm-capture")
        os.ftruncate(fd, size)
        memory = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
        pool = shm.create_pool(fd, size)
        buffer_object = pool.create_buffer(0, width, height, stride, format)
        geometry = (width, height, stride)
    frame.copy_with_damage(buffer_object)


def on_flags(frame, flags):
    global y_inverted
    y_inverted = bool(flags & 1)


def on_ready(ignored_frame, hi, lo, nsec):
    "The screen changed, and the copy holds what it changed to."
    global frames_written, last_event, frame
    last_event = time.monotonic()
    width, height, stride = geometry
    name = directory / ("frame.%03d.rgba" % frames_written)
    with open(name, "wb") as writer:
        for row in range(height):
            start = row * stride
            writer.write(memory[start : start + width * 4])
    frames_written += 1
    (directory / "meta").write_text(
        "%d %d %d\n" % (width, height, 1 if y_inverted else 0)
    )
    frame.destroy()
    if frames_written < frames_wanted:
        frame = start_a_frame()


def on_failed(frame):
    raise RuntimeError("the compositor failed a screencopy frame")


def main():
    global directory, frames_wanted, last_event, frame
    directory = Path(sys.argv[1])
    frames_wanted = int(sys.argv[2])

    display = Display()
    display.connect()
    registry = display.get_registry()
    registry.dispatcher["global"] = on_global
    display.roundtrip()
    missing = [
        what
        for what, thing in (
            ("output", output),
            ("shm", shm),
            ("screencopy manager", manager),
        )
        if thing is None
    ]
    if missing:
        raise RuntimeError("the compositor offers no %s" % (missing[0],))

    frame = start_a_frame()

    fd = display.get_fd()
    last_event = time.monotonic()
    while frames_written < frames_wanted and time.monotonic() - last_event < QUIET:
        waiting, _, _ = select.select([fd], [], [], 0.5)
        if waiting:
            # The socket read is its own step, and dispatch only
            # drains what has been read: without the read the events
            # stay in the kernel and nothing happens, ever.
            display.read()
            display.dispatch(block=False)
        # The handlers queue their next copy; without the flush the
        # compositor never hears of it and waits for nothing.
        display.flush()
    display.disconnect()


# A script the seat runs as a subprocess, and a module the ceiling
# imports: the connection opens only when it is run.
if __name__ == "__main__":
    main()
