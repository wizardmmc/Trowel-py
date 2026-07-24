"""用进程内管道模拟 ``codex app-server``，稳定覆盖协议与进程故障边界。

测试以异步生成器产出 ``Step``；runner 按顺序驱动 stdin、stdout、stderr、EOF 和
returncode，无需网络、Codex 认证或真实子进程。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Awaitable, Callable

JsonObject = dict[str, Any]


class _FakeStdin:
    """只实现 Transport 会使用的 ``StreamWriter`` 写入侧接口。"""

    def __init__(self) -> None:
        self._reader = asyncio.StreamReader()
        self._closing = False

    def write(self, data: bytes) -> None:
        self._reader.feed_data(data)

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True
        self._reader.feed_eof()

    def write_eof(self) -> None:
        self.close()

    async def drain(self) -> None:
        return None

    async def readline(self) -> bytes:
        return await self._reader.readline()


@dataclass
class Step:
    """一条 app-server 脚本动作；``recv`` 的结果会送回生成器的 ``yield``。"""

    kind: str
    payload: Any = None

    @classmethod
    def send(cls, message: JsonObject) -> "Step":
        return cls("send", message)

    @classmethod
    def send_raw(cls, text: str) -> "Step":
        return cls("send_raw", text)

    @classmethod
    def stderr(cls, text: str) -> "Step":
        return cls("stderr", text)

    @classmethod
    def recv(cls) -> "Step":
        return cls("recv")

    @classmethod
    def exit(cls, code: int = 0) -> "Step":
        return cls("exit", code)

    @classmethod
    def hold(cls, seconds: float) -> "Step":
        return cls("hold", seconds)


Behavior = AsyncGenerator[Step, JsonObject | None]


class FakeAppServer:
    """按异步生成器脚本驱动进程内 app-server。"""

    def __init__(self, behavior: Behavior) -> None:
        self._behavior = behavior
        self._process: FakeProcess | None = None
        self._task: asyncio.Task[None] | None = None
        self.received: list[JsonObject] = []
        self.raw_received: list[str] = []
        self.last_spawn_args: list[str] | None = None
        self.last_spawn_kwargs: dict[str, Any] | None = None

    def spawner(
        self,
    ) -> Callable[[list[str], dict[str, Any]], Awaitable["FakeProcess"]]:
        """返回与 ``AppServerClient`` 注入点兼容的异步进程工厂。"""

        async def _spawn(args: list[str], kwargs: dict[str, Any]) -> FakeProcess:
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
        """通过 ``asend`` 把 ``recv`` 结果送回 ``yield``，供脚本复用原生请求 ID。"""

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
        except Exception:  # noqa: BLE001
            self._finish(1)
            raise

    async def _run_step(self, step: Step) -> JsonObject | None:
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
        """同时关闭两个输出管道并写入 returncode，模拟子进程退出。"""

        proc = self._process
        if proc is None:
            return
        proc.stdout.feed_eof()
        proc.stderr.feed_eof()
        proc._set_returncode(code)

    async def join(self) -> None:
        if self._task is not None:
            await self._task


class FakeProcess:
    """只实现 Transport 会使用的 ``asyncio.subprocess.Process`` 接口。"""

    def __init__(self, *, stdout_limit: int = 2**16) -> None:
        self._stdin = _FakeStdin()
        # stdout 必须保留生产 spawner 的 limit 语义，才能覆盖超长 JSONL 消息。
        self.stdout = asyncio.StreamReader(limit=stdout_limit)
        self.stderr = asyncio.StreamReader()
        self._exit = asyncio.Event()
        self._signaled = False
        self.returncode: int | None = None

    @property
    def stdin(self) -> _FakeStdin:
        return self._stdin

    def terminate(self) -> None:
        """模拟 SIGTERM；进程未退出时把 returncode 设为 -15。"""

        self._signaled = True
        if not self._exit.is_set():
            self._set_returncode(-15)

    def kill(self) -> None:
        """模拟 SIGKILL；进程未退出时把 returncode 设为 -9。"""

        self._signaled = True
        if not self._exit.is_set():
            self._set_returncode(-9)

    def _set_returncode(self, code: int) -> None:
        """只写入一次 returncode，并释放所有 ``wait`` 等待者。"""

        if self._exit.is_set():
            return
        self.returncode = code
        self._exit.set()

    async def wait(self) -> int:
        await self._exit.wait()
        assert self.returncode is not None
        return self.returncode
