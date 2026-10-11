"""
ptyproxy with no display: each terminal is a pty with the viewer on its
far side, and the test reads what reached the near side.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field

import anyio
import pytest
from ptyhost.backends.posix_utils import set_terminal_size

from pyterm_pytest.ptyproxy import RESET, Proxy, answers_from


@dataclass
class Terminal:
    "The near side of a pty whose far side runs the viewer."

    master: int
    viewer: subprocess.Popen
    received: bytearray = field(default_factory=bytearray)

    async def until(self, wanted: bytes, seconds: float = 5.0) -> None:
        with anyio.fail_after(seconds):
            while wanted not in self.received:
                await anyio.sleep(0.01)

    def resize(self, rows: int, columns: int) -> None:
        set_terminal_size(self.master, rows, columns)
        self.viewer.send_signal(signal.SIGWINCH)


@contextlib.asynccontextmanager
async def terminal(path, rows=24, columns=80, no_input=False):
    master, slave = os.openpty()
    set_terminal_size(master, rows, columns)
    command = [sys.executable, "-m", "pyterm_pytest.ptyproxy_view", str(path)]
    if no_input:
        command.append("--no-input")
    viewer = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    os.set_blocking(master, False)
    shown = Terminal(master, viewer)

    async def read():
        while True:
            await anyio.wait_readable(master)
            try:
                shown.received += os.read(master, 65536)
            except OSError:
                return

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(read)
            yield shown
            group.cancel_scope.cancel()
    finally:
        viewer.kill()
        viewer.wait()
        os.close(master)


def python(source: str) -> list[str]:
    "An application, in Python, on a raw pty: what it writes is what it wrote."
    return [sys.executable, "-c", "import tty; tty.setraw(0)\n" + source]


@pytest.fixture
async def proxy(tmp_path):
    async with Proxy.serving(tmp_path / "proxy.sock") as served:
        yield served


async def test_the_terminal_gets_the_bytes_the_application_wrote(proxy):
    "Not UTF-8, an eight bit control, a character cut in two, a bare newline."
    written = python(
        "import os, time\n"
        "os.write(1, b'one\\ntwo\\xff\\x9b' + 'é'.encode()[:1])\n"
        "time.sleep(0.1)\n"
        "os.write(1, 'é'.encode()[1:] + b'end')\n"
        "time.sleep(5)\n"
    )
    async with terminal(proxy.path) as shown, proxy.application(written) as app:
        await shown.until(b"end")
        assert bytes(app.output) == b"one\ntwo\xff\x9b" + "é".encode() + b"end"
        assert bytes(shown.received) == bytes(app.output)


async def test_every_terminal_and_the_parsed_screen_get_the_stream(proxy):
    written = python("import os, time\nos.write(1, b'\\x1b[2;3Hhello\\x1b[1mbold')\ntime.sleep(5)\n")
    async with contextlib.AsyncExitStack() as stack:
        shown = [await stack.enter_async_context(terminal(proxy.path, 5, 20)) for _ in range(3)]
        app = await stack.enter_async_context(proxy.application(written, viewers=3))
        heard = bytearray()
        await app.shows("hellobold")
        app.listen(heard.extend)
        for one in shown:
            await one.until(b"bold")
            assert bytes(one.received) == bytes(app.output)
        assert bytes(heard) == bytes(app.output)
        assert app.parsed.rows()[1] == "  hellobold" + " " * 9


async def test_a_wait_for_text_that_never_comes_says_what_is_there(proxy):
    async with (
        terminal(proxy.path, 3, 10),
        proxy.application(python("print('elsewhere')\nimport time; time.sleep(5)")) as app,
    ):
        with pytest.raises(TimeoutError, match="elsewhere"):
            await app.shows("wanted", seconds=1)


async def test_the_application_waits_for_every_terminal(proxy):
    async with terminal(proxy.path):
        with pytest.raises(TimeoutError, match="did not attach"):
            async with proxy.application(["true"], viewers=2, wait=1):
                pass


async def test_the_terminals_must_agree_on_a_size(proxy):
    async with terminal(proxy.path, 24, 80), terminal(proxy.path, 30, 80):
        with pytest.raises(TimeoutError, match="one size"):
            async with proxy.application(["true"], viewers=2, wait=1):
                pass


SIZES = python(
    "import os, signal, time\n"
    "say = lambda *_: os.write(1, b'size %d %d;' % os.get_terminal_size(0)[::-1])\n"
    "signal.signal(signal.SIGWINCH, say)\n"
    "say()\n"
    "time.sleep(5)\n"
)


async def test_the_application_takes_the_size_of_its_terminals(proxy):
    async with terminal(proxy.path, 40, 100) as shown, proxy.application(SIZES):
        await shown.until(b"size 40 100;")
        shown.resize(30, 90)
        await shown.until(b"size 30 90;")


ECHO = python("import os\nos.write(1, b'\\x1b[cready;')\nwhile True:\n    os.write(1, b'got %r;' % os.read(0, 100))\n")


async def test_a_query_is_answered_from_the_list(proxy):
    answer = answers_from({b"\x1b[c": b"\x1b[?62c"})
    async with terminal(proxy.path) as shown, proxy.application(ECHO, answer=answer):
        await shown.until(b"got b'\\x1b[?62c';")


async def test_with_a_list_the_terminal_says_nothing_to_the_application(proxy):
    answer = answers_from({})
    async with (
        terminal(proxy.path) as shown,
        proxy.application(ECHO, answer=answer) as app,
    ):
        await shown.until(b"ready;")
        os.write(shown.master, b"typed")
        app.write(b"from the test")
        await shown.until(b"got b'from the test';")
        assert b"typed" not in app.output


async def test_without_a_list_the_terminal_answers(proxy):
    async with terminal(proxy.path) as shown, proxy.application(ECHO):
        await shown.until(b"ready;")
        os.write(shown.master, b"typed")
        await shown.until(b"got b'typed';")


async def test_a_terminal_without_input_says_nothing(proxy):
    async with (
        terminal(proxy.path, no_input=True) as shown,
        proxy.application(ECHO) as app,
    ):
        await shown.until(b"ready;")
        os.write(shown.master, b"typed")
        app.write(b"from the test")
        await shown.until(b"got b'from the test';")
        assert b"typed" not in app.output


async def test_an_injection_reaches_the_terminals_and_not_the_application(proxy):
    async with terminal(proxy.path) as one, terminal(proxy.path) as two:
        async with proxy.application(ECHO, viewers=2, answer=answers_from({})) as app:
            await one.until(b"ready;")
            await proxy.inject(RESET)
            await proxy.drained()
            # Drained is in the pty; the reader of this side may not have it yet.
            for shown in (one, two):
                await shown.until(RESET)
                assert shown.received.endswith(RESET)
            assert RESET not in app.output


async def test_an_application_may_start_where_the_terminal_would_start_it(proxy, monkeypatch):
    monkeypatch.setenv("TERM", "a-terminal-of-its-own")
    async with terminal(proxy.path) as shown:
        monkeypatch.setenv("TERM", "the-test's")
        command = ["sh", "-c", 'printf "TERM=%s;" "$TERM"; exit 3']
        async with proxy.application(command, as_the_terminal=True) as app:
            await app.ended.wait()
            assert app.backend.returncode == 3
        await shown.until(b"TERM=a-terminal-of-its-own;")


async def test_a_terminal_outlives_an_application(proxy):
    async with terminal(proxy.path) as shown:
        for word in (b"first", b"second"):
            async with proxy.application(["printf", word.decode()]) as app:
                await app.ended.wait()
            await shown.until(word)
