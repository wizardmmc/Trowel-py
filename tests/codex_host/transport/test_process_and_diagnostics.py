from __future__ import annotations

import asyncio

import pytest

from trowel_py.codex_host import AppServerClient
from trowel_py.codex_host.errors import VersionMismatchError
from trowel_py.codex_host.version import CodexVersion
from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.transport.support import (
    _build,
    _handshake,
    _initialize_response,
)


async def test_default_expected_version_enforces_lock() -> None:
    async def reader() -> CodexVersion:
        return CodexVersion("codex-cli 0.999.0", (0, 999, 0))

    fake = FakeAppServer(_handshake())
    client = AppServerClient(version_reader=reader, spawner=fake.spawner())
    with pytest.raises(VersionMismatchError):
        await client.start()


async def test_env_overrides_merge_with_parent_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")

    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()

    client, fake = _build(behavior(), env={"HTTPS_PROXY": "http://127.0.0.1:7897"})
    await client.start()
    assert fake.last_spawn_kwargs is not None
    child_env = fake.last_spawn_kwargs["env"]
    assert child_env["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert child_env["PATH"] == "/usr/local/bin:/usr/bin"
    await client.close()


async def test_bad_json_line_is_logged_not_fatal(caplog) -> None:
    async def behavior():
        msg = yield Step.recv()
        yield Step.send_raw("{not valid json")
        yield _initialize_response(msg["id"])
        yield Step.recv()

    client, _fake = _build(behavior())
    with caplog.at_level("WARNING", logger="trowel_py.codex_host.transport"):
        await client.start()
    assert any("valid JSON" in r.message for r in caplog.records)
    assert client.initialize_result is not None
    await client.close()


async def test_unknown_response_id_is_ignored(caplog) -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        yield Step.send({"id": "never-asked", "result": {}})
        yield Step.recv()

    client, _fake = _build(behavior())
    with caplog.at_level("WARNING", logger="trowel_py.codex_host.transport"):
        await client.start()
        await asyncio.sleep(0.05)
    assert any(
        "unknown" in r.message or "duplicate" in r.message for r in caplog.records
    )
    await client.close()


async def test_duplicate_response_id_ignored_after_first() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield _initialize_response(msg["id"])
        yield Step.recv()
        req = yield Step.recv()
        yield Step.send({"id": req["id"], "result": {"v": 1}})
        yield Step.send({"id": req["id"], "result": {"v": 2}})
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()
    result = await client.request("once/only", {})
    assert result == {"v": 1}
    await client.close()


async def test_stderr_lines_captured_and_redacted() -> None:
    async def behavior():
        msg = yield Step.recv()
        yield Step.stderr("WARN schema cache stale")
        yield Step.stderr("token=sk-1234567890abcdef leaked")
        yield _initialize_response(msg["id"])
        yield Step.recv()

    client, _fake = _build(behavior())
    await client.start()
    await asyncio.sleep(0.05)
    tail = client.stderr_tail
    assert "schema cache stale" in tail
    assert "sk-1234567890abcdef" not in tail
    assert "sk-***" in tail
    await client.close()
