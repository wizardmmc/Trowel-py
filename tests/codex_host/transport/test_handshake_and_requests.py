from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest

from trowel_py.codex_host.errors import (
    ProtocolViolationError,
    TransportClosedError,
    VersionMismatchError,
)
from trowel_py.codex_host.transport import AppServerClient
from trowel_py.codex_host.version import CodexVersion
from tests.codex_host._fake import Step
from tests.codex_host.transport.support import (
    _build,
    _handshake,
    _initialize_response,
)


async def test_handshake_sends_initialize_then_initialized() -> None:
    async def behavior():
        msg = yield Step.recv()
        assert msg["method"] == "initialize"
        assert msg["params"]["clientInfo"]["name"] == "trowel_codex_host"
        yield _initialize_response(msg["id"])
        msg = yield Step.recv()
        assert msg["method"] == "initialized"
        yield Step.recv()

    client, fake = _build(behavior())
    await client.start()
    # initialized 是 notification，fake 要到下一次事件循环才会记录。
    await asyncio.sleep(0.01)
    sent = [m["method"] for m in fake.received]
    assert sent == ["initialize", "initialized"]
    assert client.initialize_result is not None
    assert client.initialize_result["platformOs"] == "macos"
    await client.close()


async def test_handshake_rejects_unsupported_version() -> None:
    async def reader():
        return CodexVersion("codex-cli 0.200.0", (0, 200, 0))

    async def behavior():
        yield Step.recv()

    client, _fake = _build(behavior(), version_reader=reader)
    with pytest.raises(VersionMismatchError):
        await client.start()
    assert client._process is None
    assert client._reader_task is None


async def test_handshake_override_allows_unsupported_version(caplog) -> None:
    async def reader():
        return CodexVersion("codex-cli 0.200.0", (0, 200, 0))

    client, _fake = _build(
        _handshake(), version_reader=reader, allow_version_override=True
    )
    with caplog.at_level("WARNING", logger="trowel_py.codex_host.version"):
        await client.start()
    assert any(
        "0.200.0" in r.message and "0.144.0" in r.message for r in caplog.records
    )
    await client.close()


async def test_normal_request_returns_result() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "result": {"thread": {"id": "t-1"}}})
        yield Step.recv()

    client, fake = _build(behavior())
    await client.start()
    result = await client.request("thread/start", {"cwd": "/tmp"})
    assert result == {"thread": {"id": "t-1"}}
    assert fake.received[-1]["method"] == "thread/start"
    await client.close()


async def test_concurrent_100_out_of_order_responses() -> None:
    count = 100

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        pairs: list[tuple[object, int]] = []
        for _ in range(count):
            req = yield Step.recv()
            pairs.append((req["id"], int(req["params"]["i"])))
        random.shuffle(pairs)
        for request_id, i in pairs:
            yield Step.send({"id": request_id, "result": {"echo": i}})
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()

    async def call(i: int) -> int:
        return (await client.request("noop", {"i": i}))["echo"]

    results = await asyncio.gather(*(call(i) for i in range(count)))
    for i in range(count):
        assert results[i] == i
    assert len(client._state.pending) == 0
    await client.close()


async def test_large_single_line_response_exceeding_default_limit_is_received() -> None:
    padding = "x" * 80_000

    async def behavior():
        msg = yield Step.recv()
        response = _initialize_response(msg["id"])
        response.payload["result"]["padding"] = padding
        yield response
        yield Step.recv()

    client, _fake = _build(behavior())
    result = await client.start()
    assert result["padding"] == padding
    await client.close()


async def test_error_response_raises_protocol_violation() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        msg = yield Step.recv()
        yield Step.send({"id": msg["id"], "error": {"code": -32601, "message": "nope"}})
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()
    with pytest.raises(ProtocolViolationError):
        await client.request("bogus/method", {})
    await client.close()


async def test_request_timeout_cleans_pending() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.recv()
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()
    with pytest.raises(asyncio.TimeoutError):
        await client.request("never/responds", {}, timeout=0.05)
    assert len(client._state.pending) == 0
    await client.close()


async def test_send_failure_after_fail_all_retrieves_pending_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TrackingFuture(asyncio.Future[dict[str, Any]]):
        exception_observed = False

        def exception(self) -> BaseException | None:
            self.exception_observed = True
            return super().exception()

    pending = TrackingFuture()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "create_future", lambda: pending)
    client = AppServerClient()
    closed_error = TransportClosedError("reader failed during send")
    send_error = OSError("stdin drain failed")

    async def fail_during_send(_message: dict[str, Any]) -> None:
        await client._fail_all(closed_error)
        raise send_error

    monkeypatch.setattr(client, "_send", fail_during_send)

    with pytest.raises(OSError) as raised:
        await client.request("thread/start", {})

    exception_observed = pending.exception_observed
    pending.exception()
    assert raised.value is send_error
    assert exception_observed is True
    assert client._state.pending == {}


async def test_send_failure_preserves_error_when_pending_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = asyncio.get_running_loop().create_future()
    pending.cancel()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "create_future", lambda: pending)
    client = AppServerClient()
    send_error = OSError("stdin drain failed")

    async def fail_during_send(_message: dict[str, Any]) -> None:
        raise send_error

    monkeypatch.setattr(client, "_send", fail_during_send)

    with pytest.raises(OSError) as raised:
        await client.request("thread/start", {})

    assert raised.value is send_error
    assert pending.cancelled() is True
    assert client._state.pending == {}
