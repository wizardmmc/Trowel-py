"""从共享 Codex 状态库读取与连接 manager 无关的 thread 历史。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

_log = logging.getLogger(__name__)


class CodexThreadHistoryReader(Protocol):
    """约束 manager 池依赖的最小 Codex 历史读取能力。"""

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """列出指定目录下未被排除的用户 thread。"""

        ...

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        """读取指定 thread 的完整原生记录。"""

        ...

    async def close(self) -> None:
        """关闭历史读取器持有的资源。"""

        ...


class _HistoryManager(Protocol):
    """约束共享状态读取器复用的 manager 能力。"""

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
        use_state_db_only: bool = False,
    ) -> list[dict[str, Any]]:
        """按原生分页契约读取 thread 摘要。"""

        ...

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        """读取指定 thread 的完整原生记录。"""

        ...

    async def close(self) -> None:
        """关闭 manager 及其 transport。"""

        ...


class CodexThreadHistoryService:
    """组合共享状态库与旧默认目录，读取完整 Codex 历史。

    共享状态 manager 是多连接会话的权威来源；旧兼容 manager 只补充多连接改造前
    留在默认 ``CODEX_HOME`` 的 thread。该对象不承载活跃 session，也不根据供应商
    连接数量扩张。原生 thread ID 排除由每个 manager 在分页过程中完成，保证非用户
    thread 不占用各自的候选页大小。
    """

    def __init__(
        self,
        state_manager: _HistoryManager,
        *,
        legacy_manager: _HistoryManager | None = None,
    ) -> None:
        """保存共享状态 manager 和可选的旧目录兼容 manager。

        Args:
            state_manager: 固定连接共享 ``CODEX_SQLITE_HOME`` 的专用 manager；生命周期
                归本对象所有。
            legacy_manager: 读取旧默认 ``CODEX_HOME`` 的借用 manager；由外层池关闭。
        """

        self._state_manager = state_manager
        self._legacy_manager = legacy_manager

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """以共享状态库为主、旧默认目录为兼容源列出 thread。

        Args:
            cwd: 只读取该工作目录下的 Codex thread。
            limit: 最多返回的非排除 thread 数。
            excluded_ids: 已确认属于非用户会话的原生 thread ID。

        Returns:
            按原生更新时间倒序排列的 thread 摘要。
        """

        state_task = asyncio.create_task(
            self._state_manager.list_threads(
                cwd=cwd,
                limit=limit,
                excluded_ids=excluded_ids,
                use_state_db_only=True,
            )
        )
        legacy_task = asyncio.create_task(
            self._list_legacy_threads(
                cwd=cwd,
                limit=limit,
                excluded_ids=excluded_ids,
            )
        )
        try:
            state_rows = await state_task
        except BaseException:
            legacy_task.cancel()
            await asyncio.gather(legacy_task, return_exceptions=True)
            raise
        pages = [state_rows]
        try:
            pages.append(await legacy_task)
        except Exception as exc:  # noqa: BLE001 - 共享状态结果仍可完整服务新会话。
            _log.warning("legacy Codex history unavailable", exc_info=exc)
        return _merge_thread_pages(pages, limit=limit)

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        """读取共享状态库定位到的 thread 完整记录。

        Args:
            thread_id: Codex app-server 分配的原生 thread ID。

        Returns:
            包含 turns 的原生 thread 对象。
        """

        try:
            return await self._state_manager.read_thread(thread_id)
        except Exception as state_error:  # noqa: BLE001 - 旧 thread 可能只在默认目录。
            if self._legacy_manager is None:
                raise
            try:
                return await self._legacy_manager.read_thread(thread_id)
            except Exception:  # noqa: BLE001 - 对外保留权威共享状态源的错误。
                raise state_error

    async def close(self) -> None:
        """关闭专用 manager 及其 app-server。"""

        await self._state_manager.close()

    async def _list_legacy_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str],
    ) -> list[dict[str, Any]]:
        """从借用的旧 manager 读取兼容历史；未配置时返回空列表。

        Args:
            cwd: 只读取该工作目录下的 Codex thread。
            limit: 最多返回的非排除 thread 数。
            excluded_ids: 已确认属于非用户会话的原生 thread ID。

        Returns:
            旧默认目录中的 thread 摘要；没有兼容 manager 时为空列表。
        """

        if self._legacy_manager is None:
            return []
        return await self._legacy_manager.list_threads(
            cwd=cwd,
            limit=limit,
            excluded_ids=excluded_ids,
        )


def _merge_thread_pages(
    pages: list[list[dict[str, Any]]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """按 thread ID 去重多个已排序候选页并重新按更新时间截断。

    Args:
        pages: 共享状态页在前、兼容页在后的 thread 候选集合。
        limit: 最终最多返回的 thread 数。

    Returns:
        共享状态记录优先的去重历史页。
    """

    rows_by_id: dict[str, dict[str, Any]] = {}
    anonymous_rows: list[dict[str, Any]] = []
    for page in pages:
        for row in page:
            thread_id = row.get("id")
            if isinstance(thread_id, str) and thread_id:
                rows_by_id.setdefault(thread_id, row)
            else:
                anonymous_rows.append(row)
    rows = [*rows_by_id.values(), *anonymous_rows]
    rows.sort(key=_updated_at_key, reverse=True)
    return rows[:limit]


def _updated_at_key(row: dict[str, Any]) -> tuple[int, float | str]:
    """把原生更新时间转换为可稳定比较的同类排序键。

    Args:
        row: app-server 返回的 thread 摘要。

    Returns:
        数值时间优先按数值比较；异常字符串保留词典序兼容。
    """

    value = row.get("updatedAt")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (1, float(value))
    return (0, str(value or ""))
