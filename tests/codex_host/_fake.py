"""In-process fake of ``codex app-server`` for transport unit tests.

Why in-process (not a real subprocess): the production spawner path
(:func:`asyncio.create_subprocess_exec`) is stdlib and not under test here.
What we *do* need to exercise is the transport's behaviour against the
app-server wire format — EOF, non-zero exit, late responses, server requests,
bad JSON. An in-process fake that speaks the same StreamReader/StreamWriter
interface as ``asyncio.subprocess.Process`` is fully deterministic, needs no
network or Codex auth, and runs in milliseconds.

A test describes the fake's behaviour as an async generator that yields
:class:`Step` values. The runner feeds/drains the pipes accordingly, so each
test reads top-to-bottom as a script of what the server does.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

JsonObject = dict[str, Any]


class _FakeStdin:
    """StreamWriter-side of stdin: transport writes bytes, the driver reads."""

    def __init__(self) -> None:
        """Create the internal queue the driver drains."""

        self._reader = asyncio.StreamReader()
        self._closing = False

    def write(self, data: bytes) -> None:
        """Accept bytes from the transport and feed them to the driver."""

        self._reader.feed_data(data)

    def is_closing(self) -> bool:
        """Return True after :meth:`close`."""

        return self._closing

    def close(self) -> None:
        """Signal EOF to the driver and mark the pipe closed."""

        self._closing = True
        self._reader.feed_eof()

    def write_eof(self) -> None:
        """Mirror :meth:`asyncio.StreamWriter.write_eof`."""

        self.close()

    async def drain(self) -> None:
        """No-op backpressure sink — writes are synchronous."""

        return None

    async def readline(self) -> bytes:
        """Read one newline-terminated line the transport wrote."""

        return await self._reader.readline()


@dataclass
class Step:
    """One scripted action the fake server performs.

    Pick the kind via the factory classmethods; do not build the dataclass by
    hand (keeps the call sites readable).
    """

    kind: str
    payload: Any = None

    @classmethod
    def send(cls, message: JsonObject) -> "Step":
        """Feed one parsed message to stdout (the transport reads it)."""

        return cls("send", message)

    @classmethod
    def send_raw(cls, text: str) -> "Step":
        """Feed a raw line (use to emit malformed JSON on purpose)."""

        return cls("send_raw", text)

    @classmethod
    def stderr(cls, text: str) -> "Step":
        """Feed one stderr line."""

        return cls("stderr", text)

    @classmethod
    def recv(cls) -> "Step":
        """Block until the transport writes one stdin line, return it parsed."""

        return cls("recv")

    @classmethod
    def exit(cls, code: int = 0) -> "Step":
        """Close stdout/stderr and set ``returncode``."""

        return cls("exit", code)

    @classmethod
    def hold(cls, seconds: float) -> "Step":
        """Sleep — used to simulate a server that ignores close (kill path)."""

        return cls("hold", seconds)


Behavior = AsyncIterator[Step]


class FakeAppServer:
    """Drives a scripted in-process app-server process.

    Usage in a test::

        async def behavior():
            msg = yield Step.recv()
            assert msg["method"] == "initialize"
            yield Step.send({"id": msg["id"], "result": {...}})
            ...

        fake = FakeAppServer(behavior())
        client = AppServerClient(spawner=fake.spawner, ...)
        await client.start()
    """

    def __init__(self, behavior: Behavior) -> None:
        """Store the behavior generator; pipes are created lazily on spawn."""

        self._behavior = behavior
        self._process: FakeProcess | None = None
        self._task: asyncio.Task[None] | None = None
        self.received: list[JsonObject] = []
        self.raw_received: list[str] = []
        self.last_spawn_args: list[str] | None = None
        self.last_spawn_kwargs: dict[str, Any] | None = None

    def spawner(self) -> Callable[[list[str], dict[str, Any]], Awaitable["FakeProcess"]]:
        """Return an async spawner compatible with ``AppServerClient``."""

        async def _spawn(args: list[str], kwargs: dict[str, Any]) -> FakeProcess:
            """Create the fake process and kick off the behavior driver."""

            assert "app-server" in args, f"fake expected app-server argv, got {args}"
            self.last_spawn_args = list(args)
            self.last_spawn_kwargs = dict(kwargs)
            stdout_limit = kwargs.get("limit", 2**16)
            assert isinstance(stdout_limit, int)
            self._process = FakeProcess(stdout_limit=stdout_limit)
            self._task = asyncio.create_task(self._drive(), name="fake-app-server")
            return self._process

        return _spawn

    async def _drive(self) -> None:
        """Execute each Step the behavior yields, in order.

        Uses ``asend`` so a ``recv`` step surfaces the parsed message back to
        the generator as the value of its ``yield`` expression — that lets a
        behavior echo the client's request id when building a response.
        """

        send_value: Any = None
        try:
            while True:
                step = await self._behavior.asend(send_value)
                send_value = await self._run_step(step)
        except StopAsyncIteration:
            self._finish(0)
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — surface test-script bugs loudly
            self._finish(1)
            raise

    async def _run_step(self, step: Step) -> JsonObject | None:
        """Apply one step; return the parsed message for recv, else None."""

        proc = self._process
        assert proc is not None
        if step.kind == "send":
            line = (json.dumps(step.payload, ensure_ascii=False) + "\n").encode("utf-8")
            proc.stdout.feed_data(line)
            return None
        if step.kind == "send_raw":
            proc.stdout.feed_data((step.payload + "\n").encode("utf-8"))
            return None
        if step.kind == "stderr":
            proc.stderr.feed_data((step.payload + "\n").encode("utf-8"))
            return None
        if step.kind == "recv":
            line = await proc._stdin.readline()
            if not line:
                return None
            text = line.decode("utf-8").strip()
            self.raw_received.append(text)
            try:
                parsed = json.loads(text)
                self.received.append(parsed)
                return parsed
            except json.JSONDecodeError:
                return None
        if step.kind == "hold":
            await asyncio.sleep(step.payload)
            return None
        if step.kind == "exit":
            self._finish(step.payload)
            return None
        raise AssertionError(f"unknown step kind {step.kind!r}")

    def _finish(self, code: int) -> None:
        """Feed EOF on both pipes and stamp the returncode."""

        proc = self._process
        if proc is None:
            return
        proc.stdout.feed_eof()
        proc.stderr.feed_eof()
        proc._set_returncode(code)

    async def join(self) -> None:
        """Wait for the driver task to finish."""

        if self._task is not None:
            await self._task


class FakeProcess:
    """Subset of ``asyncio.subprocess.Process`` the transport touches."""

    def __init__(self, *, stdout_limit: int = 2**16) -> None:
        """Create fresh stdin/stdout/stderr pipes.

        Args:
            stdout_limit: Maximum buffered stdout line size, matching the
                ``limit`` accepted by :func:`asyncio.create_subprocess_exec`.
        """

        self._stdin = _FakeStdin()
        self.stdout = asyncio.StreamReader(limit=stdout_limit)
        self.stderr = asyncio.StreamReader()
        self._exit = asyncio.Event()
        self._signaled = False
        self.returncode: int | None = None

    @property
    def stdin(self) -> _FakeStdin:
        """The stdin the transport writes to."""

        return self._stdin

    def terminate(self) -> None:
        """SIGTERM — set returncode=15 if the process has not exited yet."""

        self._signaled = True
        if not self._exit.is_set():
            self._set_returncode(-15)

    def kill(self) -> None:
        """SIGKILL — set returncode=-9 if the process has not exited yet."""

        self._signaled = True
        if not self._exit.is_set():
            self._set_returncode(-9)

    def _set_returncode(self, code: int) -> None:
        """Stamp the returncode and release anyone awaiting :meth:`wait`."""

        if self._exit.is_set():
            return
        self.returncode = code
        self._exit.set()

    async def wait(self) -> int:
        """Block until the fake process has a returncode."""

        await self._exit.wait()
        assert self.returncode is not None
        return self.returncode
