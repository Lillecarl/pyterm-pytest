"""
The seats: a display server of its own, and how to take a picture on it.

Moved here out of `pymux/tests/take_a_picture.py`, so that every
suite that photographs a real terminal borrows the same two. An
`XSeat` runs one Xvfb per run and finds windows with xdotool; a
`WaylandSeat` runs one `cage` per picture and takes the output with
`grim`. The settle loop asks pixels and not the clock; the fixed
waits that are left are the ones a pixel question cannot answer, and
Lillecarl/pymux#276 is the pass that narrows them further.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path


#: The screen of the X server. It only has to be larger than the
#: window; nothing is placed against its edges.
SCREEN = "1280x800x24"

#: How long to wait for a window to appear, for the fence a relay
#: touches when the keys are done, and for what it draws to stop
#: changing.
APPEAR_TIMEOUT = 20.0
SETTLE_TIMEOUT = 15.0

#: A blink is about half a second on and half a second off. Eight
#: pictures a quarter of a second apart cover two cycles and catch each
#: phase more than once.
BLINK_FRAMES = 8
BLINK_GAP = 0.25

#: How long to let a blink fixture draw before the first picture.
#: `_settle` cannot be used for one: a screen with a blinking cursor on
#: it never settles, and that is the whole point of the measurement.
BLINK_START = 2.0


# ----------------------------------------------------------------------
# The seats: a display server, and how to take a picture on it.


def _tail(path, lines=40):
    "The end of a log file, for a message that has to say why."
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return "(no log)"
    return "--- %s ---\n%s" % (path, "\n".join(text.splitlines()[-lines:]))


class Seat:
    """
    A display server, and how to take a picture of a terminal on it.

    The two seats differ in more than the protocol.

    The X seat runs one server for the whole run, and every terminal
    opens a window on it. So a picture is of one window among several,
    and the seat has to find the right one.

    The Wayland seat runs a compositor for each picture, and that
    compositor gives its one window the whole output. So a picture is
    of the output, and there is nothing to find. That is the shape the
    harness wants, and it is what a kiosk compositor is for.
    """

    name = ""

    def start(self, work):
        "Open the seat. Returns itself."
        return self

    def stop(self):
        "Close it."

    def trouble(self) -> str:
        """
        Why this seat cannot be drawn on, or nothing when it can.

        A display server that dies in the middle of a run takes every
        picture after it, and the terminal is the one that complains:
        "xterm: Xt error: Can't open display: :0". That names the
        display and not the reason, and the server's own log is in the
        working directory rather than the room, so the message a person
        reads holds nothing that says what happened.

        Lillecarl/pymux#216.
        """
        return ""

    def running(self, terminal, command, work, log_path, director):
        """
        Run one command in one terminal, and let `director` photograph it.

        The director is called with `take_one`, which writes a picture
        of the terminal to the path it is given, and `ended`, which
        gives back the exit code once whatever draws has gone and
        `None` while it is still there. What it returns is what this
        returns.

        Every way of taking a picture goes through here: one still
        picture, a burst for a blink, and a long run that stops at each
        screen of a program. Only the two ways of opening a terminal
        differ, and a seat holds one of those.
        """
        raise NotImplementedError

    #: Whether this seat can read a fence. A fence is an OSC 52 in
    #: the clipboard of the outermost terminal, and the seat reads it
    #: back through the display server it runs. The wayland seat
    #: cannot read one yet, measured: cage offers no clipboard
    #: protocol to a client, so wl-clipboard falls back to the core
    #: data device and wants a keyboard the headless seat has none
    #: of, and foot refuses an unfocused write even with its osc52
    #: option on. The reader needs a virtual keyboard held beside the
    #: compositor, and cage patched to offer the protocol.
    #: Lillecarl/pymux#281.
    reads_the_fence = False

    def clipboard(self) -> bytes:
        """
        What the display server's clipboard holds right now.

        The fence of a picture is an OSC 52, the clipboard escape: the
        outermost terminal has acted on a fixture's bytes exactly when
        it put the fence's token in the clipboard, and the terminal is
        the thing the picture is of. Only a fence asks, so this never
        runs while a picture waits on anything else.
        """
        raise NotImplementedError

    def picture_of(
        self, terminal, command, work, path, log_path, frames=1, not_before=0.0
    ):
        """
        Run one command in one terminal and leave its picture at `path`.

        `frames` above one takes a burst instead of waiting for the
        screen to settle, and gives back the list of pictures.

        `not_before` holds the settle off, for a run whose keys have
        not been pressed yet: a number of seconds, the file a relay
        touches, or the token a fixture's fence put in the clipboard.
        `_settle` says why.
        """
        if isinstance(not_before, str) and not self.reads_the_fence:
            raise RuntimeError("the %s seat has no reader for the fence" % self.name)

        what = "%s of %s" % (self.subject, terminal.name)
        if frames > 1:
            return self.running(
                terminal,
                command,
                work,
                log_path,
                lambda take_one, ended: _burst(path, take_one, ended, what, log_path),
            )
        return self.running(
            terminal,
            command,
            work,
            log_path,
            lambda take_one, ended: _settle(
                work,
                path,
                take_one,
                ended,
                what,
                log_path,
                not_before,
                self.clipboard if isinstance(not_before, str) else None,
            ),
        )

    #: How a message names what this seat photographs. The X seat takes
    #: a picture of one window among several; the Wayland seat takes the
    #: whole output, because the compositor holds one window.
    subject = "the picture"


def wait_for_the_file(path, ended, what, log_path, timeout=APPEAR_TIMEOUT):
    """
    Wait for a file to appear, and give back what is left of the wait
    once it is here, which is nothing: the fence has done the waiting.

    The relay of a driving harness touches its fence when the program
    has done with the keys, and the file is the evidence that the
    frame on the screen is the one the keys asked for. A settle that
    starts before them keeps the screen from before them and calls it
    finished. Its absence after this long is a run that photographs
    nothing, and this says so. Lillecarl/pymux#275.
    """
    deadline = time.time() + timeout
    while not path.exists():
        gone = ended()
        if gone is not None:
            raise RuntimeError(
                "%s ended before %s appeared (exit %s)\n%s"
                % (what, path, gone, _tail(log_path))
            )
        if time.time() >= deadline:
            raise RuntimeError(
                "waited %gs for %s, and it never came\n%s"
                % (timeout, path, _tail(log_path))
            )
        time.sleep(0.2)
    return 0.0


def wait_for_the_clipboard(
    read_the_clipboard, token, ended, what, log_path, timeout=APPEAR_TIMEOUT
):
    """
    Wait for the display server's clipboard to hold the token.

    The clipboard only changes when the outermost terminal has acted
    on the bytes, and the terminal is the thing the picture is of: a
    fence the terminal never took is a run that photographs nothing,
    and this says so. Lillecarl/pymux#281.
    """
    deadline = time.time() + timeout
    while token.encode() not in read_the_clipboard():
        gone = ended()
        if gone is not None:
            raise RuntimeError(
                "%s ended before the fence came (exit %s)\n%s"
                % (what, gone, _tail(log_path))
            )
        if time.time() >= deadline:
            raise RuntimeError(
                "waited %gs for the fence, and the terminal never took it\n%s"
                % (timeout, _tail(log_path))
            )
        time.sleep(0.1)
    return 0.0


def _settle(work, path, take_one, ended, what, log_path, not_before=0.0, clipboard=None):
    """
    Take pictures until two in a row are the same, and keep the last.

    A fixed wait would be a race on a slow machine and a delay on a
    fast one. `ended` gives back the exit code when whatever draws has
    gone, and `None` while it is still there.

    `not_before` holds the settle off, for a run whose keys have not
    been pressed yet: a number of seconds, the file a relay touches
    when they are done, or the token a fixture's fence put in the
    clipboard of the display server. A screen that is waiting for a
    key is perfectly still, so two pictures of it are the same and
    this would keep the screen from before the keys and call it
    settled. Nothing that only writes bytes needs it.
    Lillecarl/pymux#161, Lillecarl/pymux#275, Lillecarl/pymux#281.
    """
    if isinstance(not_before, str):
        not_before = wait_for_the_clipboard(
            clipboard, not_before, ended, what, log_path
        )
    elif isinstance(not_before, Path):
        not_before = wait_for_the_file(not_before, ended, what, log_path)

    previous = work / "settle.png"
    started = time.time()
    deadline = started + SETTLE_TIMEOUT + not_before
    take_one(previous)
    while time.time() < deadline:
        time.sleep(0.4)
        gone = ended()
        if gone is not None:
            raise RuntimeError(
                "%s ended while it was drawing (exit %s)\n%s"
                % (what, gone, _tail(log_path))
            )
        take_one(path)
        if differences(previous, path) == 0 and time.time() - started >= not_before:
            return
        shutil.copy(path, previous)
    # What would not settle, left where a person reads the logs: the
    # last two frames that kept differing, so "never settled" says
    # what moved and not only that it moved.
    if log_path is not None:
        room = Path(log_path).parent
        shutil.copy(previous, room / "settle-previous.png")
        shutil.copy(path, room / "settle-last.png")
    raise RuntimeError("%s never settled\n%s" % (what, _tail(log_path)))


def _burst(path, take_one, ended, what, log_path):
    """
    Take `BLINK_FRAMES` pictures, `BLINK_GAP` apart, and keep them all.

    `_settle` waits for two pictures in a row to be the same. That is
    the wrong instrument for a cursor: one that blinks never settles,
    and one that has stopped blinking settles at once. So this takes a
    fixed burst and the caller compares them.

    The wait before the first one is fixed, because the fixture draws
    a few bytes and there is nothing to settle on.
    """
    time.sleep(BLINK_START)
    shots = []
    for number in range(BLINK_FRAMES):
        if number:
            time.sleep(BLINK_GAP)
        gone = ended()
        if gone is not None:
            raise RuntimeError(
                "%s ended while it was drawing (exit %s)\n%s"
                % (what, gone, _tail(log_path))
            )
        shot = path.with_name("%s.%d.png" % (path.stem, number))
        take_one(shot)
        shots.append(shot)
    return shots


class XSeat(Seat):
    "An X server of its own, with nothing else on it."

    name = "x"

    def __init__(self):
        self.number = None
        self._process = None
        self._log = None

    def trouble(self) -> str:
        "Whether the one server this seat runs is still there."
        if self._process is None or self._process.poll() is None:
            return ""

        return "the X server of the %s seat ended with %s\n%s" % (
            self.name,
            self._process.returncode,
            _tail(self._log),
        )

    def start(self, work):
        read_fd, write_fd = os.pipe()
        self._log = work / "xvfb.log"
        self._process = subprocess.Popen(
            ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", SCREEN],
            pass_fds=(write_fd,),
            stdout=open(self._log, "wb"),
            stderr=subprocess.STDOUT,
        )
        os.close(write_fd)

        with os.fdopen(read_fd, "rb") as reader:
            deadline = time.time() + APPEAR_TIMEOUT
            number = b""
            while b"\n" not in number and time.time() < deadline:
                piece = reader.read(1)
                if not piece:
                    break
                number += piece
        if not number.strip():
            raise RuntimeError("Xvfb never said which display it took")

        self.number = ":%s" % number.strip().decode()
        return self

    def stop(self):
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def _windows(self, window_class):
        "Every window of this class that is on the display now."
        found = subprocess.run(
            ["xdotool", "search", "--class", window_class],
            capture_output=True,
            timeout=30,
            env={**os.environ, "DISPLAY": self.number},
        )
        return set(found.stdout.split())

    def _wait_for_a_new_window(self, window_class, already, process, log_path):
        """
        Wait for a window of this class that was not there before.

        The identifier of a window that has gone is still an
        identifier, and taking a picture of one waits for a window that
        never draws again. So a run never reuses the one before it.
        """
        deadline = time.time() + APPEAR_TIMEOUT
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "the terminal ended before it drew anything (exit %s)\n%s"
                    % (process.returncode, _tail(log_path))
                )
            new = self._windows(window_class) - already
            if new:
                return sorted(new)[-1].decode()
            time.sleep(0.2)
        raise RuntimeError("no %s window appeared on %s" % (window_class, self.number))

    def _take(self, window, path):
        subprocess.run(
            ["import", "-display", self.number, "-window", window, str(path)],
            check=True,
            capture_output=True,
            timeout=30,
        )

    def clipboard(self) -> bytes:
        try:
            answer = subprocess.run(
                [
                    "xclip",
                    "-o",
                    "-selection",
                    "clipboard",
                    "-display",
                    self.number,
                ],
                capture_output=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return b""
        return answer.stdout

    reads_the_fence = True

    subject = "the window"

    def running(self, terminal, command, work, log_path, director):
        already = self._windows(terminal.window_class)

        log = open(log_path, "wb")
        process = subprocess.Popen(
            terminal.argv(command),
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                **terminal.environment,
                "DISPLAY": self.number,
            },
        )
        try:
            window = self._wait_for_a_new_window(
                terminal.window_class, already, process, log_path
            )
            return director(lambda where: self._take(window, where), process.poll)
        finally:
            _end(process)
            log.close()


class WaylandSeat(Seat):
    """
    A kiosk compositor, one for each picture.

    `cage` runs a single application and gives it the whole output,
    with no decoration of any kind. That is what this harness asks a
    display server for, so there is no window to find and no geometry
    to crop: `grim` takes the output, and the output is the terminal.

    It renders with pixman. A build sandbox has no graphics card, and a
    software renderer draws the same pixels on every machine, which is
    what a comparison of pictures needs.

    The compositor lives for one picture, because `cage` ends when the
    application it runs ends. Each one gets a runtime directory of its
    own, so the socket that appears in it is its own.
    """

    name = "wayland"

    def __init__(self):
        self._runs = 0

    def _room(self, work):
        self._runs += 1
        room = work / ("wayland-%d" % self._runs)
        room.mkdir(parents=True, exist_ok=True)
        return room

    @staticmethod
    def _wait_for_the_socket(room, process, log_path):
        "The name of the display that the compositor put in this room."
        deadline = time.time() + APPEAR_TIMEOUT
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "the compositor ended before it drew anything "
                    "(exit %s)\n%s" % (process.returncode, _tail(log_path))
                )
            sockets = sorted(room.glob("wayland-*"))
            sockets = [s for s in sockets if not s.name.endswith(".lock")]
            if sockets:
                return sockets[0].name
            time.sleep(0.2)
        raise RuntimeError(
            "the compositor never opened a display\n%s" % _tail(log_path)
        )

    @staticmethod
    def _take(room, display, path):
        subprocess.run(
            ["grim", str(path)],
            check=True,
            capture_output=True,
            timeout=30,
            env={
                **os.environ,
                "XDG_RUNTIME_DIR": str(room),
                "WAYLAND_DISPLAY": display,
            },
        )

    subject = "the output"

    def running(self, terminal, command, work, log_path, director):
        room = self._room(work)

        log = open(log_path, "wb")
        process = subprocess.Popen(
            ["cage", "--"] + terminal.argv(command),
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                **terminal.environment,
                "XDG_RUNTIME_DIR": str(room),
                # No graphics card in a build sandbox, and no input
                # devices either.
                "WLR_BACKENDS": "headless",
                "WLR_RENDERER": "pixman",
                "WLR_LIBINPUT_NO_DEVICES": "1",
                "LIBSEAT_BACKEND": "noop",
                # A terminal that speaks both must not reach the X
                # server that the other seat is running.
                "DISPLAY": "",
            },
        )
        try:
            display = self._wait_for_the_socket(room, process, log_path)
            return director(
                lambda where: self._take(room, display, where), process.poll
            )
        finally:
            _end(process)
            log.close()


def _end(process):
    "Stop a process, and do not wait for ever."
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


SEATS = {
    "x": XSeat,
    "wayland": WaylandSeat,
}


def differences(first, second, into=None):
    """
    How many pixels differ between two pictures.

    `compare` writes the count on stderr and exits non zero when there
    is one, so the count is what this reads and not the exit code.
    """
    command = ["compare", "-metric", "AE", str(first), str(second)]
    command.append(str(into) if into is not None else "null:")
    answer = subprocess.run(command, capture_output=True, timeout=60)
    text = answer.stderr.decode().strip().split()
    if not text:
        raise RuntimeError("compare said nothing: %r" % answer.stderr)
    try:
        return int(float(text[0]))
    except ValueError:
        raise RuntimeError("compare said %r" % answer.stderr.decode())
