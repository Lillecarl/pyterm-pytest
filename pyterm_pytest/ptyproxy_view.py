"""
The viewer half of ptyproxy: what runs inside a terminal and shows it
the application a test runs. Lillecarl/pymux#590.

    ptyproxy SOCKET [--no-input]

It connects to the socket the test serves and writes what comes down it
to the terminal, byte for byte. It tells the test its size, and again on
each SIGWINCH, and after each write it says how many bytes the terminal
has taken. With input on, what the terminal sends -- keys, and answers
to the application's queries -- goes back up. With it off, the viewer
still reads the terminal's input and drops it. First of all it sends
the environment the terminal gave it.

**Raw mode on both ends, input off or not.** Output processing turns a
`\\n` into `\\r\\n`, so the terminal would not get the application's
bytes; echo would draw a terminal's answer to a query on its own screen.

Standard library only: every terminal that shows a test pays this
import. The wire is defined here, so the server reads the same one.

Down, server to viewer: the stream, raw. Up, viewer to server: frames,
each a kind byte and a four byte length, then the payload.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import select
import signal
import socket
import struct
import termios
import tty

__all__ = ["ENVIRONMENT", "INPUT", "SIZE", "TAKEN", "Frames", "frame", "main", "view"]

#: Rows and columns, as two unsigned shorts.
SIZE = b"S"
#: What the terminal sent.
INPUT = b"I"
#: How many bytes the terminal has taken so far, written and drained, as
#: an unsigned long long.
TAKEN = b"T"
#: The environment the terminal gave the viewer, once, first: `NAME=value`
#: pairs, each ended by a NUL. The terminal sets TERM, COLORTERM and its
#: own names there, and an application started anywhere else would not
#: see them.
ENVIRONMENT = b"E"

HEADER = struct.Struct(">cI")
ROWS_COLUMNS = struct.Struct(">HH")
COUNT = struct.Struct(">Q")


def frame(kind: bytes, payload: bytes) -> bytes:
    return HEADER.pack(kind, len(payload)) + payload


class Frames:
    "Frames out of a stream, wherever its reads cut it."

    def __init__(self) -> None:
        self.buffer = b""

    def feed(self, data: bytes) -> list[tuple[bytes, bytes]]:
        self.buffer += data
        found = []
        while len(self.buffer) >= HEADER.size:
            kind, length = HEADER.unpack_from(self.buffer)
            end = HEADER.size + length
            if len(self.buffer) < end:
                break
            found.append((kind, self.buffer[HEADER.size : end]))
            self.buffer = self.buffer[end:]
        return found


def _size_frame(fd: int) -> bytes:
    size = os.get_terminal_size(fd)
    return frame(SIZE, ROWS_COLUMNS.pack(size.lines, size.columns))


@contextlib.contextmanager
def _raw(*fds: int):
    "Raw mode on each descriptor that is a terminal, and back after."
    saved = []
    try:
        for fd in fds:
            if os.isatty(fd):
                saved.append((fd, termios.tcgetattr(fd)))
                tty.setraw(fd)
        yield
    finally:
        for fd, attributes in reversed(saved):
            termios.tcsetattr(fd, termios.TCSAFLUSH, attributes)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def view(path: str, input_fd: int = 0, output_fd: int = 1, take_input: bool = True) -> int:
    "Show the stream at `path` on `output_fd` until the server closes it."
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(path)
    woken, wake = os.pipe()
    previous = signal.signal(signal.SIGWINCH, lambda *_: os.write(wake, b"w"))
    taken = 0
    try:
        with _raw(input_fd, output_fd):
            connection.sendall(frame(ENVIRONMENT, b"".join(b"%s=%s\0" % pair for pair in os.environb.items())))
            connection.sendall(_size_frame(output_fd))
            watched = [connection, woken, input_fd]
            while True:
                ready = select.select(watched, [], [])[0]
                if woken in ready:
                    os.read(woken, 64)
                    connection.sendall(_size_frame(output_fd))
                if input_fd in ready:
                    try:
                        sent = os.read(input_fd, 4096)
                    except OSError:  # A pty with no other side says EIO.
                        sent = b""
                    if not sent:
                        watched.remove(input_fd)
                    elif take_input:
                        connection.sendall(frame(INPUT, sent))
                if connection in ready:
                    data = connection.recv(65536)
                    if not data:
                        return 0
                    _write_all(output_fd, data)
                    if os.isatty(output_fd):
                        termios.tcdrain(output_fd)
                    taken += len(data)
                    connection.sendall(frame(TAKEN, COUNT.pack(taken)))
    finally:
        signal.signal(signal.SIGWINCH, previous)
        connection.close()
        os.close(woken)
        os.close(wake)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ptyproxy", description=__doc__.split("\n\n")[0])
    parser.add_argument("socket", help="the socket the test serves")
    parser.add_argument("--no-input", action="store_true", help="drop what the terminal sends")
    arguments = parser.parse_args(argv)
    return view(arguments.socket, take_input=not arguments.no_input)


if __name__ == "__main__":
    raise SystemExit(main())
