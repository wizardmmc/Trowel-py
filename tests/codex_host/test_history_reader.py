"""验证 Codex 共享状态历史读取器的窄职责与委托边界。"""

from __future__ import annotations

from typing import Any

import pytest

from trowel_py.codex_host.history_reader import CodexThreadHistoryService


class _FakeHistoryManager:
    """记录历史读取器传给 manager 的查询语义。"""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        fail_list: bool = False,
        fail_read: bool = False,
    ) -> None:
        """初始化候选记录、失败开关、调用账本和关闭状态。"""

        self.rows = rows or []
        self.fail_list = fail_list
        self.fail_read = fail_read
        self.list_calls: list[dict[str, Any]] = []
        self.read_ids: list[str] = []
        self.closed = False

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
        use_state_db_only: bool = False,
    ) -> list[dict[str, Any]]:
        """记录查询参数并模拟分页前排除。"""

        self.list_calls.append(
            {
                "cwd": cwd,
                "limit": limit,
                "excluded_ids": excluded_ids,
                "use_state_db_only": use_state_db_only,
            }
        )
        if self.fail_list:
            raise RuntimeError("history source unavailable")
        return [row for row in self.rows if row["id"] not in excluded_ids][:limit]

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        """记录读取 ID 并返回可识别结果。"""

        self.read_ids.append(thread_id)
        if self.fail_read:
            raise RuntimeError("thread unavailable")
        return {"id": thread_id, "turns": []}

    async def close(self) -> None:
        """记录资源关闭。"""

        self.closed = True


@pytest.mark.asyncio
async def test_reader_uses_shared_state_and_preserves_non_user_exclusions() -> None:
    """历史读取器必须固定使用状态库，同时透传非用户 ID 和页大小。"""

    manager = _FakeHistoryManager(
        [{"id": "delegate", "updatedAt": 30}, {"id": "user", "updatedAt": 20}]
    )
    legacy = _FakeHistoryManager(
        [{"id": "legacy", "updatedAt": 10}, {"id": "user", "updatedAt": 5}]
    )
    reader = CodexThreadHistoryService(manager, legacy_manager=legacy)

    rows = await reader.list_threads(
        cwd="/workspace",
        limit=2,
        excluded_ids=frozenset({"delegate"}),
    )

    assert rows == [
        {"id": "user", "updatedAt": 20},
        {"id": "legacy", "updatedAt": 10},
    ]
    assert manager.list_calls == [
        {
            "cwd": "/workspace",
            "limit": 2,
            "excluded_ids": frozenset({"delegate"}),
            "use_state_db_only": True,
        }
    ]
    assert legacy.list_calls == [
        {
            "cwd": "/workspace",
            "limit": 2,
            "excluded_ids": frozenset({"delegate"}),
            "use_state_db_only": False,
        }
    ]


@pytest.mark.asyncio
async def test_reader_delegates_full_thread_read_and_close() -> None:
    """完整历史读取和生命周期收敛继续复用同一个专用 manager。"""

    manager = _FakeHistoryManager()
    reader = CodexThreadHistoryService(manager)

    thread = await reader.read_thread("thread-1")
    await reader.close()

    assert thread == {"id": "thread-1", "turns": []}
    assert manager.read_ids == ["thread-1"]
    assert manager.closed is True


@pytest.mark.asyncio
async def test_reader_keeps_shared_history_when_legacy_source_fails() -> None:
    """旧目录读取失败不能遮住共享状态库中的多连接会话。"""

    manager = _FakeHistoryManager([{"id": "user", "updatedAt": 20}])
    legacy = _FakeHistoryManager(fail_list=True)
    reader = CodexThreadHistoryService(manager, legacy_manager=legacy)

    rows = await reader.list_threads(cwd="/workspace", limit=20)

    assert rows == [{"id": "user", "updatedAt": 20}]


@pytest.mark.asyncio
async def test_reader_does_not_hide_shared_state_failure_with_legacy_rows() -> None:
    """权威共享状态源故障必须向上抛出，不能返回看似成功的残缺历史。"""

    manager = _FakeHistoryManager(fail_list=True)
    legacy = _FakeHistoryManager([{"id": "legacy", "updatedAt": 10}])
    reader = CodexThreadHistoryService(manager, legacy_manager=legacy)

    with pytest.raises(RuntimeError, match="history source unavailable"):
        await reader.list_threads(cwd="/workspace", limit=20)


@pytest.mark.asyncio
async def test_reader_falls_back_to_legacy_for_pre_pool_thread() -> None:
    """共享状态库没有旧 thread 时，完整回放继续使用默认目录。"""

    manager = _FakeHistoryManager(fail_read=True)
    legacy = _FakeHistoryManager()
    reader = CodexThreadHistoryService(manager, legacy_manager=legacy)

    thread = await reader.read_thread("legacy-thread")

    assert thread == {"id": "legacy-thread", "turns": []}
    assert manager.read_ids == ["legacy-thread"]
    assert legacy.read_ids == ["legacy-thread"]
