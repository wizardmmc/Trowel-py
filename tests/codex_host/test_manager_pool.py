"""验证 Codex manager pool 的连接隔离与串行预热。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from trowel_py.codex_host.pool import CodexManagerPool
from trowel_py.configuration.models import (
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
)
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration


class _FakeClient:
    """记录预热启动与关闭次数。"""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start(self) -> None:
        """记录预热开始。"""

        self.events.append("prewarm-start")

    async def close(self) -> None:
        """记录预热关闭。"""

        self.events.append("prewarm-close")


class _FakeManager:
    """提供 pool 测试需要的最小 manager 协议。"""

    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        thread_rows: list[dict[str, object]] | None = None,
        fail_history: bool = False,
    ) -> None:
        """保存 manager 名称、事件账本和可选历史失败行为。"""

        self.name = name
        self.events = events
        self.sessions: dict[str, object] = {}
        self.thread_rows = thread_rows or []
        self.fail_history = fail_history

    def register(self, session: object) -> None:
        """登记测试会话。"""

        self.sessions[session.session_id] = session

    def unregister(self, session_id: str) -> object | None:
        """注销测试会话。"""

        return self.sessions.pop(session_id, None)

    def get_session(self, session_id: str) -> object | None:
        """读取测试会话。"""

        return self.sessions.get(session_id)

    async def send(self, session: object, text: str, **_kwargs: object) -> str:
        """记录所属 manager 并返回测试 turn ID。"""

        self.events.append(f"send:{self.name}:{session.session_id}:{text}")
        return "turn"

    async def get_goal(self, session: object) -> dict[str, object]:
        """记录 Goal 被路由到的 manager。"""

        self.events.append(f"goal:{self.name}:{session.session_id}")
        return {"manager": self.name}

    async def list_models(self) -> list[dict[str, object]]:
        """返回可识别 manager 归属的模型目录。"""

        self.events.append(f"models:{self.name}")
        return [{"id": f"model-{self.name}"}]

    async def list_skills(self, *, cwd: str) -> dict[str, object]:
        """返回可识别 manager 和工作目录的技能目录。"""

        self.events.append(f"skills:{self.name}:{cwd}")
        return {"skills": [{"name": self.name}], "errors": []}

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str],
    ) -> list[dict[str, object]]:
        """返回测试历史，或模拟单个连接 app-server 故障。"""

        del cwd
        if self.fail_history:
            raise RuntimeError(f"{self.name} history unavailable")
        return [row for row in self.thread_rows if row.get("id") not in excluded_ids][
            :limit
        ]

    async def close(self) -> None:
        """记录 manager 关闭。"""

        self.events.append(f"close:{self.name}")


class _FakeHistoryReader:
    """提供不依赖连接 manager 的共享 Codex 历史。"""

    def __init__(
        self,
        events: list[str],
        rows: list[dict[str, object]],
        *,
        fail_close: bool = False,
    ) -> None:
        """保存事件账本、共享历史和可选关闭失败。"""

        self.events = events
        self.rows = rows
        self.fail_close = fail_close

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str],
    ) -> list[dict[str, object]]:
        """记录查询并在截断前排除非用户 thread。"""

        self.events.append(f"history:{cwd}")
        return [row for row in self.rows if row.get("id") not in excluded_ids][
            :limit
        ]

    async def close(self) -> None:
        """记录历史读取器已随池收敛。"""

        self.events.append("history-close")
        if self.fail_close:
            raise RuntimeError("history close failed")

    async def read_thread(self, thread_id: str) -> dict[str, object]:
        """按 ID 返回共享状态中的 thread。"""

        for row in self.rows:
            if row.get("id") == thread_id:
                return row
        raise RuntimeError(f"unknown thread {thread_id}")


class _SlowCloseManager(_FakeManager):
    """在测试放行前停住关闭过程，用于复现释放与新会话并发。"""

    def __init__(self, name: str, events: list[str]) -> None:
        """保存关闭开始和继续执行所需的同步事件。"""

        super().__init__(name, events)
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close(self) -> None:
        """通知测试关闭已经开始，等待测试显式放行。"""

        self.close_started.set()
        await self.allow_close.wait()
        await super().close()


class _RetryCloseManager(_FakeManager):
    """第一次关闭失败、第二次成功，用于验证维护隔离与显式恢复。"""

    def __init__(self, name: str, events: list[str]) -> None:
        """保存尚未发生的单次关闭失败。"""

        super().__init__(name, events)
        self.failures_remaining = 1

    async def close(self) -> None:
        """首次抛错但不假定进程已退出，之后按正常关闭记录。"""

        if self.failures_remaining:
            self.failures_remaining -= 1
            self.events.append(f"close-failed:{self.name}")
            raise RuntimeError("simulated close failure")
        await super().close()


def _launch(
    connection_id: str, identity_version: int = 1
) -> RuntimeLaunchConfiguration:
    """构造不含真实凭据的 pool identity。"""

    return RuntimeLaunchConfiguration(
        connection_id=connection_id,
        connection_version=1,
        connection_identity_version=identity_version,
        connection_name=connection_id,
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        model="deepseek-v4-flash",
        effort="high",
        capability_version="test",
        base_url=f"https://{connection_id}.example/v1",
        login_directory=None,
        proxy_url=None,
        claude_role_models={},
        codex_catalog=(),
        api_key="secret",
    )


@pytest.mark.asyncio
async def test_pool_routes_sessions_to_connection_manager_after_single_prewarm(
    tmp_path: Path,
) -> None:
    """两个连接必须进入两个 manager，而共享状态预热只运行一次。"""

    events: list[str] = []
    legacy = _FakeManager("legacy", events)
    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=legacy,
        manager_factory=lambda launch: _FakeManager(launch.connection_id, events),
        prewarm_client_factory=lambda: _FakeClient(events),
    )
    first = SimpleNamespace(session_id="first")
    second = SimpleNamespace(session_id="second")
    pool.register(first, launch=_launch("alpha"))
    pool.register(second, launch=_launch("beta"))

    first_skills, _ = await asyncio.gather(
        pool.list_skills(first, cwd="/workspace/alpha"),
        pool.send(second, "b"),
    )

    assert pool.manager_count == 2
    assert first_skills == {"skills": [{"name": "alpha"}], "errors": []}
    assert events[:2] == ["prewarm-start", "prewarm-close"]
    assert "skills:alpha:/workspace/alpha" in events
    assert "send:beta:second:b" in events


@pytest.mark.asyncio
async def test_pool_reads_each_connection_model_catalog_from_its_own_manager(
    tmp_path: Path,
) -> None:
    """两个 provider 的模型目录必须分别来自各自的 app-server。"""

    events: list[str] = []
    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=_FakeManager("legacy", events),
        manager_factory=lambda launch: _FakeManager(launch.connection_id, events),
        prewarm_client_factory=lambda: _FakeClient(events),
    )

    alpha, beta = await asyncio.gather(
        pool.list_models_for_launch(_launch("alpha")),
        pool.list_models_for_launch(_launch("beta")),
    )

    assert alpha == [{"id": "model-alpha"}]
    assert beta == [{"id": "model-beta"}]
    assert "models:legacy" not in events


@pytest.mark.asyncio
async def test_pool_reads_shared_history_before_any_connection_manager_exists(
    tmp_path: Path,
) -> None:
    """冷启动历史不能依赖本次进程是否已经创建连接 manager。"""

    events: list[str] = []
    created_connections: list[str] = []
    target = {"id": "cold-thread", "updatedAt": "2026-08-08T04:18:30Z"}

    def manager_factory(launch: RuntimeLaunchConfiguration) -> _FakeManager:
        """记录任何违反连接 manager 懒启动边界的创建。"""

        created_connections.append(launch.connection_id)
        return _FakeManager(launch.connection_id, events)

    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=_FakeManager("legacy", events),
        manager_factory=manager_factory,
        prewarm_client_factory=lambda: _FakeClient(events),
        history_reader=_FakeHistoryReader(events, [target]),
    )

    rows = await pool.list_threads(cwd="/workspace", limit=20)

    assert rows == [target]
    assert created_connections == []
    assert "history:/workspace" in events
    assert events[:2] == ["prewarm-start", "prewarm-close"]


@pytest.mark.asyncio
async def test_pool_routes_goal_independently_from_shared_history(
    tmp_path: Path,
) -> None:
    """活跃会话路由和共享历史来源不能反向依赖彼此。"""

    events: list[str] = []
    legacy = _FakeManager("legacy", events)

    def manager_factory(launch: RuntimeLaunchConfiguration) -> _FakeManager:
        """让 beta 保留原有失败配置，证明 Goal 路由与历史读取相互独立。"""

        return _FakeManager(
            launch.connection_id,
            events,
            thread_rows=[{"id": "shared", "updatedAt": "2026-02-01"}],
            fail_history=launch.connection_id == "beta",
        )

    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=legacy,
        manager_factory=manager_factory,
        prewarm_client_factory=lambda: _FakeClient(events),
        history_reader=_FakeHistoryReader(
            events,
            [{"id": "shared", "updatedAt": "2026-02-01"}],
        ),
    )
    first = SimpleNamespace(session_id="first")
    second = SimpleNamespace(session_id="second")
    pool.register(first, launch=_launch("alpha"))
    pool.register(second, launch=_launch("beta"))

    goal = await pool.get_goal(first)
    rows = await pool.list_threads(cwd=str(tmp_path), limit=20)

    assert goal == {"manager": "alpha"}
    assert rows == [{"id": "shared", "updatedAt": "2026-02-01"}]
    assert "goal:alpha:first" in events


@pytest.mark.asyncio
async def test_pool_close_still_closes_all_managers_when_history_close_fails(
    tmp_path: Path,
) -> None:
    """历史读取器关闭失败不能跳过兼容和供应商 manager 的收敛。"""

    events: list[str] = []
    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=_FakeManager("legacy", events),
        manager_factory=lambda launch: _FakeManager(launch.connection_id, events),
        prewarm_client_factory=lambda: _FakeClient(events),
        history_reader=_FakeHistoryReader(events, [], fail_close=True),
    )
    pool.register(SimpleNamespace(session_id="active"), launch=_launch("alpha"))

    with pytest.raises(ExceptionGroup, match="manager pool close failed"):
        await pool.close()

    assert "history-close" in events
    assert "close:legacy" in events
    assert "close:alpha" in events


def test_pool_key_changes_when_connection_identity_changes() -> None:
    """同一连接重绑后必须创建新的 manager identity。"""

    assert _launch("alpha", 1).pool_key != _launch("alpha", 2).pool_key


@pytest.mark.asyncio
async def test_release_launch_only_closes_an_unreferenced_manager(tmp_path: Path) -> None:
    """删除供应商不能关闭仍被冻结会话使用的 manager。"""

    events: list[str] = []
    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=_FakeManager("legacy", events),
        manager_factory=lambda launch: _FakeManager(launch.connection_id, events),
        prewarm_client_factory=lambda: _FakeClient(events),
    )
    launch = _launch("alpha")
    session = SimpleNamespace(session_id="session")
    pool.register(session, launch=launch)

    assert await pool.release_launch(launch) is False
    pool.unregister(session.session_id)
    assert await pool.release_launch(launch) is True
    assert pool.manager_count == 0
    assert "close:alpha" in events


@pytest.mark.asyncio
async def test_release_launch_rejects_new_session_until_close_finishes(
    tmp_path: Path,
) -> None:
    """manager 正在关闭时不能接入新会话，避免把会话绑到已关闭进程。"""

    events: list[str] = []
    manager = _SlowCloseManager("alpha", events)
    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=_FakeManager("legacy", events),
        manager_factory=lambda _launch: manager,
        prewarm_client_factory=lambda: _FakeClient(events),
    )
    launch = _launch("alpha")
    original = SimpleNamespace(session_id="original")
    pool.register(original, launch=launch)
    pool.unregister(original.session_id)

    release = asyncio.create_task(pool.release_launch(launch))
    await manager.close_started.wait()

    with pytest.raises(RuntimeError, match="being released"):
        pool.register(SimpleNamespace(session_id="late"), launch=launch)

    manager.allow_close.set()
    assert await release is True
    assert pool.manager_count == 0


@pytest.mark.asyncio
async def test_connection_maintenance_covers_every_frozen_identity(
    tmp_path: Path,
) -> None:
    """当前连接更新后，旧 identity 的活动会话仍必须阻止配置删除或覆盖。"""

    events: list[str] = []
    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=_FakeManager("legacy", events),
        manager_factory=lambda launch: _FakeManager(
            f"{launch.connection_id}-v{launch.connection_identity_version}",
            events,
        ),
        prewarm_client_factory=lambda: _FakeClient(events),
    )
    old_launch = _launch("alpha", 1)
    current_launch = _launch("alpha", 2)
    old_session = SimpleNamespace(session_id="old-session")
    pool.register(old_session, launch=old_launch)
    await pool.list_models_for_launch(current_launch)

    assert await pool.begin_connection_maintenance("alpha") is False
    assert pool.manager_count == 2

    pool.unregister(old_session.session_id)
    assert await pool.begin_connection_maintenance("alpha") is True
    assert pool.manager_count == 0
    with pytest.raises(RuntimeError, match="being released"):
        pool.register(SimpleNamespace(session_id="late"), launch=current_launch)
    pool.end_connection_maintenance("alpha")

    pool.register(SimpleNamespace(session_id="fresh"), launch=current_launch)
    assert pool.manager_count == 1


@pytest.mark.asyncio
async def test_failed_maintenance_close_keeps_manager_quarantined_for_retry(
    tmp_path: Path,
) -> None:
    """关闭结果未知时不能忘掉旧 manager 或解除连接级创建门禁。"""

    events: list[str] = []
    manager = _RetryCloseManager("alpha", events)
    pool = CodexManagerPool(
        shared_state_root=tmp_path,
        legacy_manager=_FakeManager("legacy", events),
        manager_factory=lambda _launch: manager,
        prewarm_client_factory=lambda: _FakeClient(events),
    )
    launch = _launch("alpha")
    await pool.list_models_for_launch(launch)

    with pytest.raises(ExceptionGroup, match="maintenance close failed"):
        await pool.begin_connection_maintenance("alpha")

    assert pool.manager_count == 1
    with pytest.raises(RuntimeError, match="being released"):
        pool.register(SimpleNamespace(session_id="blocked"), launch=launch)

    assert await pool.begin_connection_maintenance("alpha") is True
    assert pool.manager_count == 0
    pool.end_connection_maintenance("alpha")
    pool.register(SimpleNamespace(session_id="fresh"), launch=launch)
    assert pool.manager_count == 1
