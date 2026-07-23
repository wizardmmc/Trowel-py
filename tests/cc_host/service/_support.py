from __future__ import annotations

import asyncio
import json


class FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def write_eof(self) -> None:
        return None


class FakeProc:
    def __init__(self, lines: list[str], feed_eof: bool = True, pid: int = 1) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = FakeWriter()
        self.stdout = asyncio.StreamReader()
        for ln in lines:
            self.stdout.feed_data(ln.encode() + b"\n")
        if feed_eof:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


class FakeSpawner:
    def __init__(self, procs: list[FakeProc]) -> None:
        self._procs = list(procs)
        self.spawned: list[tuple[list[str], dict]] = []

    async def __call__(self, args: list[str], kwargs: dict) -> FakeProc:
        proc = self._procs.pop(0)
        self.spawned.append((args, kwargs))
        return proc


def line(d: dict) -> str:
    return json.dumps(d)


def init_event(sid="s-1", model="glm-5.2"):
    return {
        "type": "system",
        "subtype": "init",
        "model": model,
        "cwd": "/wd",
        "session_id": sid,
        " tools": [],
        "tools": ["Read", "Skill"],
    }


def text_delta(i, t):
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": i,
            "delta": {"type": "text_delta", "text": t},
        },
    }


def result_ok():
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": 0.03,
        "usage": {"input_tokens": 5},
        "num_turns": 1,
    }


async def collect(gen):
    return [x async for x in gen]
