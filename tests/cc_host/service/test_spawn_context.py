from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import FinishedEvent

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
    result_ok,
)


# memory 通过 CC 原生 --append-system-prompt 注入；读取失败不能阻断 spawn。
class TestMemoryInjection:
    async def test_spawn_passes_memory_injection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trowel_py.cc_host.service as svc

        monkeypatch.setattr(
            svc, "build_memory_injection", lambda now, **kw: "INJECTION_MARKER_XYZ"
        )
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner, self_enabled=False)
        await collect(host.send("hi"))
        args = spawner.spawned[0][0]
        i = args.index("--append-system-prompt")
        assert args[i + 1] == "INJECTION_MARKER_XYZ"

    async def test_spawn_survives_injection_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(now: str, **kw) -> str:
            raise RuntimeError("memory dir gone")

        import trowel_py.cc_host.service as svc

        monkeypatch.setattr(svc, "build_memory_injection", boom)
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner, self_enabled=False)
        events = await collect(host.send("hi"))
        args = spawner.spawned[0][0]
        assert "--append-system-prompt" not in args
        assert any(isinstance(e, FinishedEvent) for e in events)

    async def test_spawn_keeps_self_when_memory_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(now: str, **kw) -> str:
            raise RuntimeError("memory dir gone")

        import trowel_py.cc_host.service as svc

        monkeypatch.setattr(svc, "build_memory_injection", boom)
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        args = spawner.spawned[0][0]
        i = args.index("--append-system-prompt")
        prompt = args[i + 1]
        assert "# 关于你（Self" in prompt
        assert "Trowel" in prompt


# MCP 调用协议不携带会话身份，只能在启动 stdio 子进程时通过环境变量注入。
class TestMcpConfig:
    async def test_spawn_args_include_mcp_config(self, tmp_path: Path) -> None:
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner, mcp_config="/tmp/mem.json")
        await collect(host.send("hi"))
        args = spawner.spawned[0][0]
        assert "--mcp-config" in args
        assert "--strict-mcp-config" in args
        assert args[args.index("--mcp-config") + 1] == "/tmp/mem.json"

    async def test_spawn_no_mcp_config_flag_when_absent(self, tmp_path: Path) -> None:
        proc = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        args = spawner.spawned[0][0]
        assert "--mcp-config" not in args

    def test_build_spawn_env_injects_identity_when_mcp_config(
        self, tmp_path: Path
    ) -> None:
        host = CCHost("sid", tmp_path, mcp_config="/tmp/mem.json", proxy_base_url=None)
        env = host._build_spawn_env()
        assert env is not None
        assert env["TROWEL_SESSION_ID"] == "sid"
        assert "MEMORY_ROOT" in env
        assert env["TROWEL_HOST_KIND"] == "cc"

    def test_build_spawn_env_native_session_id_when_resumed(
        self, tmp_path: Path
    ) -> None:
        host = CCHost(
            "sid",
            tmp_path,
            mcp_config="/tmp/mem.json",
            proxy_base_url=None,
            resume_from="cc-sid-xyz",
        )
        env = host._build_spawn_env()
        assert env is not None
        assert env["TROWEL_HOST_KIND"] == "cc"
        assert env["TROWEL_NATIVE_SESSION_ID"] == "cc-sid-xyz"
        assert env["CC_SESSION_ID"] == "cc-sid-xyz"

    def test_build_spawn_env_no_identity_without_mcp_config(
        self, tmp_path: Path
    ) -> None:
        host = CCHost("sid", tmp_path, proxy_base_url=None)
        assert host._build_spawn_env() is None
