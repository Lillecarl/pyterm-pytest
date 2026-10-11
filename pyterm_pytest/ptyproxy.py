"""
The test's half of ptyproxy: run an application on a pty, and serve
what it writes to every terminal that shows it. Lillecarl/pymux#590.

    async with Proxy.serving(path) as proxy:
        # Each terminal runs `ptyproxy <path>`, from `ptyproxy_view`.
        async with proxy.application(["yazi"], viewers=2) as app:
            app.write(b"q")
            await app.ended.wait()
        await proxy.inject(RESET)
        await proxy.drained()

The proxy outlives an application, so a terminal stays up for a whole
session and shows one application after another. Four things travel,
and each has its own call:

- what the application writes goes to every viewer, unchanged, however
  many attach; and in this process to `Application.parsed`, a pyte
  screen a scenario reads, and to every other `listen`er;
- `inject` writes to the viewers only, which is how a reset or a fence
  reaches the terminals and never the application;
- `Application.write` gives the application keys;
- the pty takes the size the viewers report.

**The application starts once every viewer it waits for is attached and
all of them report one size.** A viewer that joins late misses the first
bytes, and a terminal that reports a size before it takes its final one
is a terminal the application draws for wrongly. Lillecarl/pymux#584.

**Queries are answered here, when `answer` says how.** Then both of two
terminals showing one application get the same answer at the same time,
and what the terminals themselves send is dropped. Without `answer`, the
viewers' input reaches the application, so one terminal can answer for
real -- which is how a list of answers is recorded in the first place.
"""

from __future__ import annotations

import contextlib
import os
import re
from codecs import getincrementaldecoder
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import anyio.abc
from ptyhost.backends.posix import PosixBackend
from ptyhost.spawn import Spawn
from pyte import Screen, Stream

from .ptyproxy_view import COUNT, ENVIRONMENT, INPUT, ROWS_COLUMNS, SIZE, TAKEN, Frames

__all__ = ["RESET", "Application", "Parsed", "Proxy", "answers_from"]

#: What every terminal takes as "forget everything": RIS, then the
#: scrollback, which RIS leaves alone in xterm.
RESET = b"\x1bc\x1b[3J"

#: How long a wait on the viewers may take before it is a fault.
WAIT = 10.0

#: A finished query, in the 7-bit forms only: a C1 byte such as 0x9b is
#: also a continuation byte of UTF-8 text.
_SEQUENCE = re.compile(
    rb"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    rb"|\x1b[P\]_^X][^\x07\x1b]*(?:\x07|\x1b\\)"
    # An escape of its own, such as ESC 7 or ESC ( B: it asks nothing,
    # and the stream is between sequences after it.
    rb"|\x1b[\x20-\x2f]*[\x30-\x4f\x51-\x57\x59\x5a\x5c\x60-\x7e]"
)

#: An unfinished sequence held across reads is at most this long.
_HELD = 4096

Answer = Callable[[bytes], bytes | None]


@contextlib.contextmanager
def _one_error():
    """
    Raise the one error of a task group as itself.

    A task group wraps what the block of `with` raised in an exception
    group, so a caller's `except RuntimeError` would miss the error its
    own block raised: every picture driver catches that to put its logs
    beside the reason.
    """
    try:
        yield
    except BaseExceptionGroup as group:
        if len(group.exceptions) == 1:
            raise group.exceptions[0] from None
        raise


def answers_from(table: Mapping[bytes, bytes]) -> Answer:
    "An answer read out of a table of query and answer bytes."
    return lambda query: table.get(query)


class _Queries:
    "The finished control sequences of a stream, wherever its reads cut them."

    def __init__(self) -> None:
        self.held = b""

    def feed(self, data: bytes) -> list[bytes]:
        data = self.held + data
        found = [match.group() for match in _SEQUENCE.finditer(data)]
        last = data.rfind(b"\x1b")
        ended = max((match.end() for match in _SEQUENCE.finditer(data)), default=0)
        self.held = data[last:][:_HELD] if last >= ended else b""
        return found


@dataclass
class _Viewer:
    stream: anyio.abc.ByteStream
    #: What the terminal gave its viewer, for an application to start in.
    environment: dict[str, str] | None = None
    size: tuple[int, int] | None = None
    sent: int = 0
    taken: int = 0
    #: Everything the terminal sent while an application ran, kept
    #: whether it reached the application or not: how far into the
    #: application's output it came, and the bytes. Two terminals'
    #: lists side by side say how each answers the same queries.
    replies: list[tuple[int, bytes]] = field(default_factory=list)


class Parsed:
    """
    The application's stream, parsed into a pyte screen in this process:
    what a scenario reads to know where the application is.

    It answers nobody. Bytes that are not UTF-8 draw the replacement
    character, as a terminal draws them.
    """

    def __init__(self, rows: int, columns: int) -> None:
        self.screen = Screen(rows, columns, write_process_input=lambda data: None)
        self._stream = Stream(self.screen)
        self._decoder = getincrementaldecoder("utf-8")("replace")

    def feed(self, data: bytes) -> None:
        self._stream.feed(self._decoder.decode(data))

    def rows(self) -> list[str]:
        "What the screen shows, one string per row."
        screen = self.screen
        return ["".join(screen.page.data_buffer[y][x].char for x in range(screen.columns)) for y in range(screen.lines)]


@dataclass
class Application:
    """
    One application on a pty, shown by every viewer of its proxy.

    Every byte it writes goes three ways: to each viewer, to `output`,
    and to each listener in this process. `parsed` is the first
    listener, a pyte screen; `listen` adds another.
    """

    proxy: Proxy
    backend: PosixBackend
    answer: Answer | None
    parsed: Parsed
    output: bytearray = field(default_factory=bytearray)
    ended: anyio.Event = field(default_factory=anyio.Event)
    listeners: list[Callable[[bytes], None]] = field(default_factory=list)
    _changed: anyio.Event = field(default_factory=anyio.Event)
    _queries: _Queries = field(default_factory=_Queries)

    def __post_init__(self) -> None:
        self.listeners.append(self.parsed.feed)

    def write(self, data: bytes) -> None:
        "Give the application `data`, as keys a person typed."
        self.backend.write_bytes(data)

    def listen(self, listener: Callable[[bytes], None]) -> None:
        "Give `listener` everything written so far, then each byte after."
        listener(bytes(self.output))
        self.listeners.append(listener)

    async def shows(self, text: str, seconds: float = WAIT) -> None:
        "Until a row of the parsed screen holds `text`."
        try:
            with anyio.fail_after(seconds):
                while not any(text in row for row in self.parsed.rows()):
                    await self._changed.wait()
        except TimeoutError:
            rows = "\n".join(self.parsed.rows())
            raise TimeoutError("%r never showed, after %.0f s:\n%s" % (text, seconds, rows)) from None

    def _resize(self, rows: int, columns: int) -> None:
        self.backend.set_size(columns, rows)
        self.parsed.screen.resize(rows, columns)

    async def _read(self) -> None:
        data = self.backend.read_bytes(65536)
        if data:
            self.output += data
            for listener in self.listeners:
                listener(data)
            async with self.proxy._order:
                await self.proxy._send(data)
                queries = self._queries.feed(data)
                if not self._queries.held:
                    await self.proxy._flush()
            if self.answer is not None:
                for query in queries:
                    reply = self.answer(query)
                    if reply is not None:
                        self.write(reply)
        if self.backend.closed:
            self.ended.set()
        self._changed.set()
        self._changed = anyio.Event()


class Proxy:
    "A socket that terminals attach to, and the application they show."

    def __init__(self, path: Path) -> None:
        self.path = path
        self.viewers: list[_Viewer] = []
        #: The application the viewers show now, if one runs.
        self.running: Application | None = None
        self._changed = anyio.Event()
        #: Held for each send, so an injection lands between two of the
        #: application's sends and never inside one.
        self._order = anyio.Lock()
        #: Injections waiting for the application's stream to leave a
        #: control sequence, with the viewers each is for.
        self._waiting: list[tuple[bytes, list[_Viewer] | None]] = []

    @classmethod
    @contextlib.asynccontextmanager
    async def serving(cls, path: Path) -> AsyncIterator[Proxy]:
        "Serve at `path` for as long as the block runs."
        proxy = cls(path)
        listener = await anyio.create_unix_listener(path)
        try:
            with _one_error():
                async with anyio.create_task_group() as group:
                    group.start_soon(listener.serve, proxy._attend, group)
                    yield proxy
                    group.cancel_scope.cancel()
        finally:
            await listener.aclose()
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)

    def _change(self) -> None:
        self._changed.set()
        self._changed = anyio.Event()

    async def until(self, holds: Callable[[], bool], what: str, seconds: float = WAIT) -> None:
        "Until `holds()` is true after some change, or fail saying `what`."
        try:
            with anyio.fail_after(seconds):
                while not holds():
                    await self._changed.wait()
        except TimeoutError:
            raise TimeoutError("%s, after %.0f s: %s" % (what, seconds, self.describe())) from None

    def describe(self) -> str:
        return (
            ", ".join(
                "viewer %d at %s, %d of %d bytes taken" % (number, viewer.size, viewer.taken, viewer.sent)
                for number, viewer in enumerate(self.viewers)
            )
            or "no viewer"
        )

    async def _attend(self, stream: anyio.abc.ByteStream) -> None:
        viewer = _Viewer(stream)
        self.viewers.append(viewer)
        frames = Frames()
        try:
            async for data in stream:
                for kind, payload in frames.feed(data):
                    self._take(viewer, kind, payload)
        except anyio.BrokenResourceError, anyio.EndOfStream:
            pass
        finally:
            self.viewers.remove(viewer)
            self._change()

    def _take(self, viewer: _Viewer, kind: bytes, payload: bytes) -> None:
        if kind == SIZE:
            viewer.size = ROWS_COLUMNS.unpack(payload)
            application = self.running
            if application is not None and self._one_size() == viewer.size:
                application._resize(*viewer.size)
        elif kind == TAKEN:
            (viewer.taken,) = COUNT.unpack(payload)
        elif kind == ENVIRONMENT:
            pairs = (os.fsdecode(pair).partition("=") for pair in payload.split(b"\0") if pair)
            viewer.environment = {name: value for name, _, value in pairs}
        elif kind == INPUT:
            application = self.running
            if application is not None:
                viewer.replies.append((len(application.output), payload))
            if application is not None and application.answer is None:
                application.write(payload)
        self._change()

    def _one_size(self) -> tuple[int, int] | None:
        "The size every viewer reports, when they agree on one."
        sizes = {viewer.size for viewer in self.viewers}
        return sizes.pop() if len(sizes) == 1 else None

    async def _send(self, data: bytes, viewers: list[_Viewer] | None = None) -> None:
        for viewer in list(self.viewers if viewers is None else viewers):
            viewer.sent += len(data)
            with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
                await viewer.stream.send(data)

    async def inject(self, data: bytes, viewers: list[_Viewer] | None = None) -> None:
        """
        Write `data` to the viewers only; the application never sees it.
        `viewers` picks some of them: a fence one terminal takes says
        nothing about another that shares its clipboard.

        **It never lands inside a control sequence.** While what the
        application sent last stops inside one, `data` waits for the
        send that ends it. Measured: a fence inside yazi's sixel made
        foot draw the sixel as text.
        """
        async with self._order:
            running = self.running
            if running is not None and running._queries.held:
                self._waiting.append((data, viewers))
            else:
                await self._send(data, viewers)

    async def _flush(self) -> None:
        waiting, self._waiting = self._waiting, []
        for data, viewers in waiting:
            await self._send(data, viewers)

    async def let_go(self) -> None:
        "End the stream to every viewer, which ends each viewer."
        for viewer in list(self.viewers):
            with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
                await viewer.stream.send_eof()

    async def drained(self, seconds: float = WAIT) -> None:
        "Until each viewer's terminal has taken every byte sent to it so far."
        owed = [(viewer, viewer.sent) for viewer in self.viewers]
        await self.until(
            lambda: all(viewer.taken >= sent for viewer, sent in owed),
            "a terminal did not take what it was sent",
            seconds,
        )

    @contextlib.asynccontextmanager
    async def application(
        self,
        command: list[str],
        *,
        viewers: int = 1,
        environment: Mapping[str, str] | None = None,
        as_the_terminal: bool = False,
        directory: str | None = None,
        answer: Answer | None = None,
        size: tuple[int, int] | None = None,
        wait: float = WAIT,
    ) -> AsyncIterator[Application]:
        """
        Run `command` once `viewers` terminals are attached and agree on
        a size, `size` when it is given, as rows and columns.

        The application starts from `environment` alone, not from the
        test's: a variable that pytest happens to carry is not one the
        application should see. With none, it gets `PATH` and nothing
        else to go on. `as_the_terminal` starts it in the environment the
        first terminal gave its viewer instead, with `environment` laid
        over that. It ends with the block.
        """
        assert self.running is None, "one application at a time"

        def agreed() -> bool:
            one = self._one_size()
            return len(self.viewers) >= viewers and one is not None and (size is None or one == tuple(size))

        await self.until(agreed, "the terminals did not attach at one size", wait)
        rows, columns = self._one_size()
        if as_the_terminal:
            environment = {**(self.viewers[0].environment or {}), **(environment or {})}
        elif environment is None:
            environment = {"PATH": os.environ.get("PATH", "")}
        spawn = Spawn(list(command), dict(environment), directory)
        backend = PosixBackend(lambda: spawn)
        backend.set_size(columns, rows)
        application = Application(self, backend, answer, Parsed(rows, columns))
        backend.add_input_ready_callback(application._read)
        self.running = application
        try:
            with _one_error():
                async with anyio.create_task_group() as group:
                    await backend.start(group)
                    yield application
                    group.cancel_scope.cancel()
        finally:
            self.running = None
            backend.close()
            async with self._order:
                await self._flush()
