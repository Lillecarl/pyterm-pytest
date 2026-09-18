"""
The seats: a display server of its own, and how to take a picture on it.

Moved here out of `pymux/tests/take_picture.py`, so that every
suite that photographs a real terminal borrows the same two. An
`XSeat` runs one Xvfb per run and finds windows with xdotool; a
`WaylandSeat` runs one headless `sway` per picture, with a client
of ours holding a virtual keyboard on the seat, and takes the output
with `grim`. The settle loop asks pixels and not the clock; the fixed
waits that are left are the ones a pixel question cannot answer, and
Lillecarl/pymux#276 is the pass that narrows them further.
"""

import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops


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

#: What a suite exits with to say "this is not my answer".
#:
#: A suite that ran and disliked what it saw is an answer, and nix
#: keeps it: the log is the evidence, which is why a check is two
#: derivations at all. **A suite whose display server never came up is
#: not**, and a cached one of those cannot be cleared -- `--rebuild`
#: re-runs the suite and then throws the new output away, because it
#: compares rather than replaces, so the only way back to a green gate
#: is deleting the output and its referrers from the store by hand.
#: That cost two sessions an hour each.
#:
#: `pyte/nix/suite.nix` fails the run derivation on this code alone,
#: so nix keeps nothing and the next build tries again. It passes the
#: number in, so the two cannot drift; the literal is for a run
#: started outside nix. Lillecarl/pymux#216.
COULD_NOT_RUN = int(os.environ.get("PYTERM_COULD_NOT_RUN") or 97)


class TheSeatIsGone(Exception):
    """
    The display server this run draws on is not there any more.

    Every picture after it fails the same way, so a run that meets one
    stops rather than working through the rest of its fixtures to
    report the same thing each time.

    **Not a `RuntimeError`.** Every driver wraps a picture in `except
    RuntimeError` to put the logs of the room beside the reason. A seat
    that is gone caught there comes out as a verdict on the picture,
    which is the one thing it is not. Lillecarl/pymux#431.
    """


def open_the_seats(terminals, work):
    """
    One seat for each kind of display server these terminals need.

    A seat that will not start is not a verdict on anything, so the
    failure is `TheSeatIsGone` and not whatever the display server
    said. Lillecarl/pymux#216.
    """
    seats = {}
    for terminal in terminals:
        if terminal.seat in seats:
            continue
        try:
            seats[terminal.seat] = SEATS[terminal.seat]().start(work)
        except Exception as reason:
            for seat in seats.values():
                seat.stop()
            raise TheSeatIsGone(
                "the %s seat would not start: %s" % (terminal.seat, reason)
            ) from reason
    return seats


def with_no_answer(reason):
    """
    Say that this run has no answer, and give the code that says so.

    A `main` returns this, and `pyte/nix/suite.nix` fails the run
    derivation on it, so nix keeps nothing and the next build tries
    again. Lillecarl/pymux#216.
    """
    print("")
    print("This run has no answer. %s" % (reason,), flush=True)
    print("Nothing is kept, so the next build runs it again.", flush=True)
    return COULD_NOT_RUN


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

    def still_there(self):
        "Raise `TheSeatIsGone` when this seat cannot be drawn on."
        trouble = self.trouble()
        if trouble:
            raise TheSeatIsGone(trouble)

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
    #: back through the display server it runs. Both seats here can:
    #: the X one with xclip, the wayland one with wl-paste over sway's
    #: data-control device, which needs neither a popup nor a
    #: keyboard. What the wayland reader needed measured out as three
    #: walls -- cage offers no clipboard protocol at all,
    #: wl-clipboard's core fallback wants a keyboard, and foot refuses
    #: an unfocused write -- and a virtual keyboard held beside sway
    #: is what got past the last of them. Lillecarl/pymux#281.
    reads_fence = False

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
        self,
        terminal,
        command,
        work,
        path,
        log_path,
        frames=1,
        not_before=0.0,
        judge=None,
    ):
        """
        Run one command in one terminal and leave its picture at `path`.

        `frames` above one takes a burst instead of waiting for the
        screen to settle, and gives back the list of pictures.

        `not_before` holds the settle off, for a run whose keys have
        not been pressed yet: a number of seconds, the file a relay
        touches, or the token a fixture's fence put in the clipboard.
        `_settle` says why.

        `judge` reads the state of the program once that wait is over
        and raises when it is not the state the keys asked for.
        `_settle` says why a picture needs one.
        """
        if isinstance(not_before, str) and not self.reads_fence:
            raise RuntimeError("the %s seat has no reader for the fence" % self.name)
        if judge is not None and not isinstance(not_before, (str, Path)):
            # A judge reads the state after the wait, so a wait with no
            # evidence in it would have it read the state from before
            # the keys and pass.
            raise RuntimeError("a judge needs a fence to read the state after")

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
                judge,
            ),
        )

    def burst_frames(self, terminal, command, work, path, log_path, frames):
        """
        `frames` pictures taken at fixed intervals.

        The clock is all a seat that cannot hear the screen has: a
        burst at fixed intervals samples whatever the screen was doing
        at those moments, and a blink longer than the burst reads as no
        blink at all. A seat that hears damage measures instead.
        """
        return self.picture_of(
            terminal, command, work, path, log_path, frames=frames
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


def _settle(
    work,
    path,
    take_one,
    ended,
    what,
    log_path,
    not_before=0.0,
    clipboard=None,
    judge=None,
):
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

    `judge` reads the state of the program once that wait is over.
    **The fence proves the program finished the keys, never that a key
    arrived.** A key that reached nothing leaves a program that is
    finished with an empty list, and the fence comes back through a
    pane that is alive either way: one run of a two pane fixture drew
    one pane and stayed green, because a picture of the wrong state is
    still a picture. So the state is asked for and compared, and a
    wrong one is a red run with no picture kept.
    Lillecarl/pymux#353.
    """
    if isinstance(not_before, str):
        not_before = wait_for_the_clipboard(
            clipboard, not_before, ended, what, log_path
        )
    elif isinstance(not_before, Path):
        not_before = wait_for_the_file(not_before, ended, what, log_path)

    if judge is not None:
        try:
            judge()
        except Exception as reason:
            raise RuntimeError("%s: %s\n%s" % (what, reason, _tail(log_path)))

    previous = work / "settle.png"
    #: The older of the last pair that differed, and where they did.
    #: `previous` cannot be either: the loop copies the new picture
    #: over it, so the two kept frames were the same image every time
    #: and "what moved" showed nothing. Lillecarl/pymux#362.
    differed = work / "settle-differed.png"
    difference = work / "settle-difference.png"

    started = time.time()
    deadline = started + SETTLE_TIMEOUT + not_before
    take_one(previous)
    box = None
    while time.time() < deadline:
        time.sleep(0.4)
        gone = ended()
        if gone is not None:
            raise RuntimeError(
                "%s ended while it was drawing (exit %s)\n%s"
                % (what, gone, _tail(log_path))
            )
        take_one(path)
        count, box = changed_region(previous, path, difference)
        if count == 0 and time.time() - started >= not_before:
            return
        shutil.copy(previous, differed)
        shutil.copy(path, previous)

    # What would not settle, left where a person reads the logs: the
    # last two frames that kept differing, and the pixels between
    # them. The box is the whole of "what moved" -- one cell says a
    # cursor, and the width of the screen says a redraw.
    if log_path is not None:
        room = Path(log_path).parent
        for one, name in (
            (differed, "settle-previous.png"),
            (Path(path), "settle-last.png"),
            (difference, "settle-difference.png"),
        ):
            if one.exists():
                shutil.copy(one, room / name)
    raise RuntimeError(
        "%s never settled: %s\n%s"
        % (
            what,
            "%dx%d at %d,%d kept changing" % (box[2], box[3], box[0], box[1])
            if box
            else "nothing to compare",
            _tail(log_path),
        )
    )


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


#: What a terminal says when it never reached the display server:
#: "xterm: Xt error: Can't open display: :0". The words around it
#: belong to the toolkit, and "open display" is what they share.
THE_DISPLAY_WOULD_NOT_OPEN = re.compile(rb"open display", re.IGNORECASE)

#: How long a client may take to reach a display. Nothing asks until
#: something has already gone wrong, so this is a bound and not a
#: budget.
DISPLAY_TIMEOUT = 5.0

#: The first twelve bytes a client sends an X server: little endian,
#: protocol 11.0, and no authorisation. The server answers 1 for a
#: client it will serve, and 0 with a reason for one it refuses.
_THE_SETUP_REQUEST = struct.pack("<BBHHHH2x", ord("l"), 0, 11, 0, 0, 0)


def _read_exactly(sock, count):
    "Exactly that many bytes, or fewer when the server stops talking."
    got = b""
    while len(got) < count:
        piece = sock.recv(count - len(got))
        if not piece:
            break
        got += piece
    return got


def _a_socket_to_the_display(number):
    """
    A socket to this display, or nothing and why there is nothing.

    The abstract name first and the file after it, which is the order a
    real client tries. A server on Linux binds both, and one in a
    sandbox that could not make `/tmp/.X11-unix` has only the abstract
    one.
    """
    where = "/tmp/.X11-unix/X%s" % number.lstrip(":").split(".")[0]
    refused = "there is no display to try"
    for address in ("\0" + where, where):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DISPLAY_TIMEOUT)
        try:
            sock.connect(address)
            return sock, ""
        except OSError as reason:
            sock.close()
            refused = "%s: %s" % (address.replace("\0", "@", 1), reason)
    return None, refused


def why_the_display_refuses(number):
    """
    Why the X server at this display will not serve a client, or
    nothing when it will.

    **A server that runs is not a display a client can reach.** The
    trouble of the X seat was the exit code of the Xvfb process alone,
    so a server that was alive and unreachable read as healthy: the
    terminal's "Can't open display" became a verdict on a picture, the
    check went red, and a red run of a check stays in the store until
    somebody deletes it by hand. Lillecarl/pymux#431.

    A refusal carries the server's own words, and "Maximum number of
    clients reached" is one of them. Nothing else in a run holds them,
    so this speaks the first twelve bytes of the protocol to read the
    answer back.
    """
    sock, refused = _a_socket_to_the_display(number)
    if sock is None:
        return "nothing answers display %s (%s)" % (number, refused)

    try:
        sock.sendall(_THE_SETUP_REQUEST)
        head = _read_exactly(sock, 8)
        if len(head) < 8:
            return "display %s answers a client and then drops it" % (number,)
        if head[0] == 1:
            return ""
        rest = _read_exactly(sock, 4 * struct.unpack_from("<H", head, 6)[0])
    except OSError as reason:
        return "display %s dropped the client: %s" % (number, reason)
    finally:
        sock.close()

    # A refusal says how long its reason is. A demand for authorisation
    # says nothing, and the whole of what follows is the reason.
    said = rest[: head[1]] if head[0] == 0 else rest
    return "display %s refuses a client: %s" % (
        number,
        said.decode("ascii", "replace").strip() or "and gives no reason",
    )


class XSeat(Seat):
    "An X server of its own, with nothing else on it."

    name = "x"

    def __init__(self):
        self.number = None
        self._process = None
        self._log = None

    def trouble(self) -> str:
        "Whether the one server this seat runs will serve a client."
        if self._process is None:
            return ""

        if self._process.poll() is not None:
            return "the X server of the %s seat ended with %s\n%s" % (
                self.name,
                self._process.returncode,
                _tail(self._log),
            )

        refused = why_the_display_refuses(self.number)
        if refused:
            return "the X server of the %s seat is running, and %s\n%s" % (
                self.name,
                refused,
                _tail(self._log),
            )
        return ""

    def start(self, work):
        read_fd, write_fd = os.pipe()
        self._log = work / "xvfb.log"
        self._process = subprocess.Popen(
            # **`-noreset`, or the seat goes away between two pictures.**
            # One server serves the whole run, and each picture is a
            # terminal that starts and is killed. So the client count
            # reaches zero between every pair of pictures, and an X
            # server with no clients left resets: it closes the sockets
            # it listens on and opens them again. A terminal that starts
            # inside that window is refused, and says "Can't open
            # display".
            #
            # Measured, with an Xvfb of each kind and 24 busy processes
            # beside it: 67 of 300 client departures refused the client
            # that came next, the worst of them for 0.305s, and with
            # this flag 0 of 300. Lillecarl/pymux#431.
            ["Xvfb", "-noreset", "-displayfd", str(write_fd), "-screen", "0", SCREEN],
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

    def _the_terminal_never_got_in(self, log_path):
        """
        Stop the run when the terminal says it never reached the display.

        A terminal that could not open the display drew nothing, so
        there is no picture and no verdict to give. The seat is what
        failed: the run ends with no answer, nix keeps nothing, and the
        next build tries again.

        **The terminal is asked and not the display.** The window a
        refusal opens is short -- 0.305s at its worst, measured -- so a
        display that turned this terminal away answers again by the time
        anything asks it. What the terminal said does not change.
        Lillecarl/pymux#431.
        """
        said = Path(log_path).read_bytes() if Path(log_path).exists() else b""
        if THE_DISPLAY_WOULD_NOT_OPEN.search(said):
            raise TheSeatIsGone(
                "the terminal of the %s seat never reached display %s\n%s"
                % (self.name, self.number, _tail(log_path))
            )

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
                self._the_terminal_never_got_in(log_path)
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

    reads_fence = True

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
    A compositor, one for each picture, arranged to be a kiosk.

    `sway` manages windows, and a configuration of three lines makes
    it do what `cage` did: no borders, one output, and the terminal
    as the only thing on it. The whole output is the terminal, so
    `grim` takes the output and there is no geometry to crop. It
    stays a kiosk the same way the fence reader arrived: sway offers
    the clipboard protocol that cage never did, and a client of ours
    holds a virtual keyboard on the seat, which is what gives the
    terminal the focus its OSC 52 write demands.

    It renders with pixman. A build sandbox has no graphics card, and a
    software renderer draws the same pixels on every machine, which is
    what a comparison of pictures needs.

    Each picture gets a runtime directory of its own, so the socket
    that appears in it is its own. `sway` does not end when the
    application it runs ends, the way `cage` does, so the wrapper
    script it execs records the terminal's exit code, and `ended`
    reads it: to the harness, whatever draws is still gone at the
    same moment.
    """

    name = "wayland"
    reads_fence = True

    def __init__(self):
        self._runs = 0
        self._where = None

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

    def clipboard(self) -> bytes:
        """
        What the compositor's clipboard holds right now.

        `sway` offers a client the data-control device, and
        `wl-paste` reads through it without holding a surface and
        without a keyboard, which is the whole reason the reader is
        cheap here: the keyboard is only foot's gate, and wl-paste
        needs none.
        """
        room, display = self._where
        answer = subprocess.run(
            ["wl-paste", "-t", "text/plain"],
            capture_output=True,
            timeout=5,
            env={
                **os.environ,
                "XDG_RUNTIME_DIR": str(room),
                "WAYLAND_DISPLAY": display,
            },
        )
        return answer.stdout

    def _wait_for_the_keyboard(self, room):
        """
        Wait until the holder has put its keyboard on the seat.

        foot writes the fence only when it is focused, and the focus
        arrives as a keyboard enter event that the compositor sends
        only once the seat has a keyboard device. The holder prints
        when it has one, and a run without that line is a run whose
        fence will never come.
        """
        log = room / "holder.log"
        deadline = time.time() + APPEAR_TIMEOUT
        while time.time() < deadline:
            if log.exists() and "keyboard" in log.read_text():
                return
            time.sleep(0.2)
        raise RuntimeError(
            "the keyboard never reached the seat\n%s" % _tail(log)
        )

    @staticmethod
    def _generated_protocols():
        "Where the generated bindings of the seat's clients live."
        protocols = os.environ.get("PYTERM_WAYLAND_PROTOCOLS")
        if protocols is None:
            raise RuntimeError(
                "PYTERM_WAYLAND_PROTOCOLS is not set: the wayland seat's "
                "clients have no generated bindings to run with"
            )
        return protocols

    def running(self, terminal, command, work, log_path, director):
        def body(room, display, ended):
            return director(
                lambda where: self._take(room, display, where), ended
            )

        return self._run(terminal, command, work, log_path, body)

    def burst_frames(self, terminal, command, work, path, log_path, frames):
        """
        `frames` pictures taken when the screen changes, not when the
        clock says so.

        The default is a burst at fixed intervals, which is the only
        thing a seat that cannot hear the screen can do. This seat
        hears it: screencopy's copy-with-damage holds a copy back until
        pixels change, so every frame is a moment something moved, and
        a cursor that blinks slowly is no longer mistaken for one that
        never blinks.
        """

        def body(room, display, ended):
            return self._frames_on_damage(room, display, path, frames)

        return self._run(terminal, command, work, log_path, body)

    def _frames_on_damage(self, room, display, path, frames):
        # Beside the pictures, not in the compositor's room: a run
        # that leaves its logs behind leaves them where the reader of
        # the run looks. One per side: both sides of a comparison use
        # this room, and their frames must not mix.
        capture = path.parent / ("capture-%s" % path.stem)
        capture.mkdir()
        log = open(path.parent / "capture.log", "wb")
        client = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).parent / "capture_on_damage.py"),
                str(capture),
                str(frames),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "XDG_RUNTIME_DIR": str(room),
                "WAYLAND_DISPLAY": display,
                "PYTHONPATH": self._generated_protocols(),
            },
        )
        try:
            client.wait(timeout=frames * 5 + 20)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "the capture did not end\n%s" % _tail(path.parent / "capture.log")
            ) from None
        finally:
            if client.poll() is None:
                _end(client)
            log.close()

        raws = sorted(capture.glob("frame.*.rgba"))
        if not raws:
            raise RuntimeError(
                "the screen never changed\n%s"
                % _tail(path.parent / "capture.log")
            )
        meta = (capture / "meta").read_text().split()
        width, height, inverted = int(meta[0]), int(meta[1]), meta[2] == "1"
        shots = []
        for number, raw in enumerate(raws):
            shot = path.with_name("%s.%d.png" % (path.stem, number))
            # The settings that come before the file change how the
            # raw reader reads it: alpha off first makes imagemagick
            # read three bytes a pixel and the file half makes no
            # sense. The reader is told nothing but the geometry.
            convert = [
                "magick",
                "-size",
                "%dx%d" % (width, height),
                "-depth",
                "8",
                "bgra:%s" % raw,
            ]
            if inverted:
                convert.append("-flip")
            convert += ["-alpha", "off", str(shot)]
            answer = subprocess.run(
                convert, capture_output=True, timeout=60
            )
            if answer.returncode:
                raise RuntimeError(
                    "the picture did not convert: %s"
                    % answer.stderr.decode().strip()
                )
            shots.append(shot)
        return shots

    def _run(self, terminal, command, work, log_path, body):
        room = self._room(work)
        protocols = self._generated_protocols()
        wrapper = room / "run.sh"
        wrapper.write_text(
            "%s\necho $? > %s\n"
            % (shlex.join(terminal.argv(command)), room / "the-code")
        )
        config = room / "sway.conf"
        config.write_text(
            "default_border none\n"
            "output HEADLESS-1 resolution 1024x768\n"
            "exec /bin/sh %s\n" % (wrapper,)
        )

        log = open(log_path, "wb")
        process = subprocess.Popen(
            ["sway", "-c", str(config)],
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
        holder = None
        try:
            display = self._wait_for_the_socket(room, process, log_path)
            self._where = (room, display)
            holder = subprocess.Popen(
                [sys.executable, str(Path(__file__).parent / "virtual_keyboard.py")],
                stdout=(room / "holder.log").open("wb"),
                stderr=subprocess.STDOUT,
                env={
                    **os.environ,
                    "XDG_RUNTIME_DIR": str(room),
                    "WAYLAND_DISPLAY": display,
                    "PYTHONPATH": protocols,
                },
            )
            self._wait_for_the_keyboard(room)
            code = room / "the-code"

            def ended():
                gone = process.poll()
                if gone is not None:
                    return gone
                if code.exists():
                    return int(code.read_text() or 0)
                return None

            return body(room, display, ended)
        finally:
            if holder is not None:
                _end(holder)
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


def _the_difference(first, second):
    """
    The first picture, and where the second differs from it.

    The two are cropped to the rectangle they share. **They are not
    always the same size.** vttest asks for 132 columns and xterm
    answers by making its window wider; a pane has the cells the user
    gave it and cannot, so one picture of that screen is 1280 pixels
    across and the other is 800. The shared rectangle is the
    comparison, and the width itself is not a difference.
    """
    one = Image.open(first).convert("RGB")
    two = Image.open(second).convert("RGB")
    if one.size != two.size:
        shared = (0, 0, min(one.width, two.width), min(one.height, two.height))
        one, two = one.crop(shared), two.crop(shared)
    return one, ImageChops.difference(one, two)


def _what_moved(difference):
    """
    One band, holding the largest of the three differences of a pixel.

    **Not `convert("L")`**, which weighs the bands the way an eye sees
    them: a pixel that differs in blue alone weighs 0.07 of that
    difference and rounds away to nothing. Weighing a difference
    instead of counting it is what Lillecarl/pymux#368 was.
    """
    red, green, blue = difference.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def _draw_what_moved(one, moved, into):
    "The first picture faded, with every pixel that moved in red."
    faded = Image.eval(one, lambda value: value // 4)
    marked = Image.new("RGB", one.size, (255, 0, 0))
    Image.composite(marked, faded, moved.point(bool, mode="1")).save(into)


def _measured(first, second, into):
    "How many pixels moved, and the band that says which."
    one, difference = _the_difference(first, second)
    moved = _what_moved(difference)
    if into is not None:
        _draw_what_moved(one, moved, into)
    # Every value above zero is a pixel that differs in some band.
    return sum(moved.histogram()[1:]), moved


def differences(first, second, into=None):
    """
    How many pixels differ between two pictures.

    **Counted, and not weighed.** `compare -metric AE` stood here and
    its own documentation calls it a count of pixels. It is not: in
    ImageMagick 7.1.2 it sums the channel error of the whole picture,
    normalised, so `rgb(255,0,0)` and `rgb(0,204,51)` against black both
    answer 255/765 of the pixels, and `rgb(1,0,0)` answers 1/765 of
    them. Every pixel of all three differs. A small difference over few
    pixels therefore counted as none at all, and `_settle` calls a
    screen still on that answer. Lillecarl/pymux#368.
    """
    count, _moved = _measured(first, second, into)
    return count


def changed_region(first, second, into):
    """
    The box that holds every pixel that differs, as (x, y, w, h).

    The count comes with it: (0, None) is two pictures that agree.
    The box is what says whether two changes are the same change --
    a cursor that blinks changes one cell, twice, and every other
    movement moves something else.

    **The box is of what moved and not of what is drawn.** It was
    `magick <into> -format %@`, the trim of the picture `compare`
    wrote, which is a faded copy of the first picture with the
    differences marked on it -- so the trim held all the ink and the
    same box came back whatever had moved. Lillecarl/pymux#370.
    """
    count, moved = _measured(first, second, into)
    if not count:
        return 0, None
    left, top, right, bottom = moved.getbbox()
    return count, (left, top, right - left, bottom - top)


class NothingToCompare(RuntimeError):
    "Two drawings that cannot be lined up, and why."


def _drawn_part(path):
    "What a picture draws, cut out of the background it draws it on."
    picture = Image.open(path).convert("RGB")
    box = picture.getbbox()
    if box is None:
        raise NothingToCompare("%s draws nothing at all" % (path,))
    return picture.crop(box), box


def the_same_drawing(first, second, into=None):
    """
    How many pixels differ between what two pictures draw, wherever
    each of them drew it.

    **This is the comparison that may cross two terminals**, and it is
    the only one. Two terminals draw their own glyphs from their own
    font stacks, so comparing text between them says nothing. An image
    has no glyphs: the pixels are the program's, and a terminal that
    draws them somewhere else on its output is still drawing the same
    picture. So each drawing is cut out of its background first and the
    two cuts are compared.

    `getbbox` finds the cut, so the background has to be black, which
    is what the picture harness asks every terminal for.

    Raises `NothingToCompare` when the two drawings are not the same
    size. Then there is nothing to line up and a count would be a
    number with no meaning -- the cell of one terminal has moved, or a
    font has, and somebody has to look.
    """
    one, first_box = _drawn_part(first)
    two, second_box = _drawn_part(second)
    if one.size != two.size:
        raise NothingToCompare(
            "%s draws %dx%d and %s draws %dx%d, so the two do not line up"
            % (first, one.width, one.height, second, two.width, two.height)
        )

    moved = _what_moved(ImageChops.difference(one, two))
    if into is not None:
        _draw_what_moved(one, moved, into)
    return sum(moved.histogram()[1:]), first_box, second_box


def fully_overlaps(first, second):
    "Whether one of two boxes holds the other entirely."
    fx, fy, fw, fh = first
    sx, sy, sw, sh = second
    width = min(fx + fw, sx + sw) - max(fx, sx)
    height = min(fy + fh, sy + sh) - max(fy, sy)
    if width <= 0 or height <= 0:
        return False
    return width * height == min(fw * fh, sw * sh)
