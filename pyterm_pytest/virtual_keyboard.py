"""
A client that holds a virtual keyboard on the seat, beside the compositor.

A headless seat has no input devices, and wlroots gives a seat
keyboard capabilities only through a device: without one, no client
is ever focused. foot accepts an OSC 52 only from a focused terminal
(osc.c checks the focus before it reads its own osc52 option, and a
keyboard reaches a client only as a wl_keyboard enter event), so the
fence a fixture ends in never lands while the seat is keyboardless.

The bindings are the generated ones: the derivation that runs
pywayland's scanner over the core wayland.xml and wlroots' virtual
keyboard XML puts a `protocols` package on this script's path. The
keyboard lives as long as this process does, which is as long as the
compositor's picture.

Lillecarl/pymux#281.
"""

from protocols.wayland import WlSeat
from protocols.virtual_keyboard_unstable_v1.zwp_virtual_keyboard_manager_v1 import (
    ZwpVirtualKeyboardManagerV1,
)
from pywayland.client import Display

seat = None
manager = None


def on_global(registry, name, interface, version):
    global seat, manager
    if interface == "wl_seat" and seat is None:
        seat = registry.bind(name, WlSeat, min(version, 1))
    elif interface == "zwp_virtual_keyboard_manager_v1":
        manager = registry.bind(name, ZwpVirtualKeyboardManagerV1, 1)


def main():
    display = Display()
    display.connect()
    registry = display.get_registry()
    registry.dispatcher["global"] = on_global
    display.roundtrip()
    if seat is None or manager is None:
        raise RuntimeError(
            "the compositor offers no %s"
            % ("seat" if seat is None else "keyboard manager")
        )
    keyboard = manager.create_virtual_keyboard(seat)
    display.roundtrip()
    print("keyboard %s is on the seat" % (keyboard,), flush=True)
    while True:
        display.dispatch(block=True)


# A script the seat runs as a subprocess, and a module the ceiling
# imports on every run: the connection opens only when it is run.
if __name__ == "__main__":
    main()
