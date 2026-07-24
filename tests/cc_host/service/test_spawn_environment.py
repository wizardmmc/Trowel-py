from __future__ import annotations

from pathlib import Path

import pytest

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
    result_ok,
)
from trowel_py.cc_host.service import CCHost


@pytest.fixture(autouse=True)
def _isolated_memory_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )


async def test_spawn_args_include_mcp_config(tmp_path: Path) -> None:
    process = FakeProc([line(init_event()), line(result_ok())])
    spawner = FakeSpawner([process])
    config_path = tmp_path / "memory-mcp.json"
    host = CCHost(
        "session-id",
        tmp_path,
        spawner=spawner,
        mcp_config=str(config_path),
    )

    await collect(host.send("hi"))

    args = spawner.spawned[0][0]
    index = args.index("--mcp-config")
    assert args[index : index + 3] == [
        "--mcp-config",
        str(config_path),
        "--strict-mcp-config",
    ]


def test_build_spawn_env_injects_identity_when_mcp_config(tmp_path: Path) -> None:
    host = CCHost(
        "session-id",
        tmp_path,
        mcp_config=str(tmp_path / "memory-mcp.json"),
        proxy_base_url=None,
    )

    env = host._build_spawn_env()

    assert env is not None
    assert env["TROWEL_SESSION_ID"] == "session-id"
    assert env["TROWEL_HOST_KIND"] == "cc"
    assert env["MEMORY_ROOT"] == str(tmp_path)
    assert "TROWEL_NATIVE_SESSION_ID" not in env
    assert "CC_SESSION_ID" not in env


def test_build_spawn_env_native_session_id_when_resumed(tmp_path: Path) -> None:
    host = CCHost(
        "session-id",
        tmp_path,
        mcp_config=str(tmp_path / "memory-mcp.json"),
        proxy_base_url=None,
        resume_from="native-session-id",
    )

    env = host._build_spawn_env()

    assert env is not None
    assert env["TROWEL_SESSION_ID"] == "session-id"
    assert env["TROWEL_HOST_KIND"] == "cc"
    assert env["TROWEL_NATIVE_SESSION_ID"] == "native-session-id"
    assert env["CC_SESSION_ID"] == "native-session-id"
    assert env["MEMORY_ROOT"] == str(tmp_path)


def test_build_spawn_env_no_identity_without_mcp_config(tmp_path: Path) -> None:
    host = CCHost("session-id", tmp_path, proxy_base_url=None)

    assert host._build_spawn_env() is None
