"""同一 MCP 进程内的 Claude child 交互生命周期。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx

from trowel_py.agent_mcp.http_errors import agent_api_error_detail

_ACTIONABLE_STATES = frozenset({"needs_guidance", "completed", "failed"})
_ERROR_TERMINALS = frozenset({"error", "interrupted", "session_exited"})

logger = logging.getLogger(__name__)
T = TypeVar("T")


class InteractiveDelegationError(RuntimeError):
    """表示交互委派的输入无效、操作失败或句柄无效。"""

    pass


class InteractiveDelegationCleanupError(InteractiveDelegationError):
    """表示无法确认子会话已经清理。"""

    pass


@dataclass
class _InteractiveDelegation:
    """保存当前 MCP 进程内一次交互委派的实时状态。

    Attributes:
        delegation_id: 当前 MCP 进程为本次委派生成的 ID。
        parent_session_id: 发起委派的 Trowel 会话 ID。
        child_session_id: 子会话的 Trowel 会话 ID；创建完成前为空字符串。
        child_binding: 子会话绑定信息；创建响应提供初值，session_started 事件补充
            原生会话 ID 与模型，收到 finished 后尝试通过查询刷新。
        status: 父会话可见的委派状态，可取 starting、running、needs_guidance、
            completed、failed、cleanup_pending、unknown_requires_reconcile 或
            closed。
        question: needs_guidance 状态下 elicit_request 事件的 payload；其他状态
            为 None。
        answer_chunks: 从子会话 text 事件中依次收集的回答片段。
        event_counts: 按事件类型统计的子会话事件数量。
        terminal_event: 已观察到的子会话终态事件类型；尚未收到终态事件时为 None。
        cleanup_status: 子会话的清理进度；pending 表示尚未删除，preserved 表示
            保留委派记录及已有绑定信息等待重试或核对，deleted 表示已经删除。
        cleanup_error: 最近一次清理失败的原因；没有失败时为 None。
        error: 父会话可见的委派失败原因；没有失败时为 None。
        version: 每次 transition() 更新 status、question 和 error 时递增的版本号，
            供状态长轮询判断是否返回。
        condition: 用于唤醒状态长轮询调用的异步条件变量。
        close_lock: 防止同一子会话被并发清理的异步锁。
        consumer: 独占子会话 SSE 的后台任务；任务尚未启动时为 None。
    """

    delegation_id: str
    parent_session_id: str
    child_session_id: str = ""
    child_binding: dict[str, Any] = field(default_factory=dict)
    status: str = "starting"
    question: dict[str, Any] | None = None
    answer_chunks: list[str] = field(default_factory=list)
    event_counts: Counter[str] = field(default_factory=Counter)
    terminal_event: str | None = None
    cleanup_status: str = "pending"
    cleanup_error: str | None = None
    error: str | None = None
    version: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    consumer: asyncio.Task[None] | None = None

    async def transition(
        self,
        status: str,
        *,
        question: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> int:
        """更新父会话可见的状态，递增版本并唤醒等待者。

        Args:
            status: 委派接下来对父会话显示的状态。
            question: 子会话等待回答的提问；其余状态传入 None。
            error: 本次状态变化附带的失败原因；没有失败时为 None。

        Returns:
            更新后的状态版本号。
        """

        async with self.condition:
            self.status = status
            self.question = question
            self.error = error
            self.version += 1
            self.condition.notify_all()
            return self.version

    async def wait_actionable(self, *, after_version: int = -1) -> None:
        """等待版本前进且委派进入父会话需要处理的状态。

        Args:
            after_version: 已观察的状态版本；只有更高版本才返回。
        """

        async with self.condition:
            await self.condition.wait_for(
                lambda: self.version > after_version
                and self.status in _ACTIONABLE_STATES
            )

    def snapshot(self) -> dict[str, Any]:
        """生成供父会话查询的当前委派快照。

        Returns:
            包含父子会话标识、状态版本、待回答问题、已观察事件、子会话回答和
            清理结果的快照。
        """

        return {
            "delegation_id": self.delegation_id,
            "parent": {"trowel_session_id": self.parent_session_id},
            "child": {
                "trowel_session_id": self.child_session_id,
                "runtime": "claude_code",
                "native_session_id": self.child_binding.get("native_session_id"),
                "model": self.child_binding.get("model"),
            },
            "status": self.status,
            "needs_guidance": self.question,
            "observed": {
                "terminal_event": self.terminal_event,
                "event_counts": dict(self.event_counts),
                "changed_paths": None,
                "validation": None,
            },
            "reported": {"answer": "".join(self.answer_chunks)},
            "cleanup": {
                "status": self.cleanup_status,
                "error": self.cleanup_error,
            },
            "error": self.error,
        }


class InteractiveBroker:
    """在一个 stdio MCP 进程内持有 child SSE，供后续 tool call 继续。"""

    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        cleanup_timeout: float = 10.0,
        consumer_stop_timeout: float = 10.0,
    ) -> None:
        """配置 Agent API 地址、清理时限和进程内委派记录。

        Args:
            base_url: Trowel Agent API 的根地址。
            transport: 发起 Agent API 请求时使用的可选 httpx transport。
            cleanup_timeout: 关闭创建中的委派时等待子会话 ID 的秒数；也用于计算
                整个清理流程的超时上限。
            consumer_stop_timeout: 等待子会话事件任务自行结束的秒数；超时后取消
                该任务。
        """

        if cleanup_timeout <= 0 or consumer_stop_timeout <= 0:
            raise ValueError("interactive cleanup timeouts must be positive")
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._cleanup_timeout = cleanup_timeout
        self._consumer_stop_timeout = consumer_stop_timeout
        self._records: dict[str, _InteractiveDelegation] = {}

    def _client(self) -> httpx.AsyncClient:
        """创建不设 HTTP 超时的 Agent API 客户端。"""

        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(None),
            transport=self._transport,
        )

    async def start(
        self,
        *,
        parent_session_id: str,
        task: str,
        create_body: dict[str, Any],
    ) -> dict[str, Any]:
        """启动负责创建并读取子会话的后台任务，立即返回可继续查询的委派快照。

        Args:
            parent_session_id: 发起委派的 Trowel 会话 ID。
            task: 交给子会话执行的初始任务。
            create_body: 创建 Claude Code 子会话时发送给 Agent API 的请求体；
                runtime 必须为 "claude_code"。

        Returns:
            包含委派 ID 和初始状态的快照。
        """

        if not task.strip():
            raise ValueError("task must not be empty")
        if create_body.get("runtime") != "claude_code":
            raise ValueError("interactive delegation only supports claude_code")
        record = _InteractiveDelegation(
            delegation_id=uuid.uuid4().hex,
            parent_session_id=parent_session_id,
        )
        self._records[record.delegation_id] = record
        record.consumer = asyncio.create_task(
            self._consume(record, task=task, create_body=create_body)
        )
        try:
            await record.wait_actionable()
        except asyncio.CancelledError:
            await self.close(record.delegation_id)
            raise
        return record.snapshot()

    async def respond(
        self, delegation_id: str, answers: dict[str, str]
    ) -> dict[str, Any]:
        """把父会话答案写回等待中的 Claude Code 子会话。

        Agent API 确认写入后立即返回；写入失败时恢复原提问和 needs_guidance
        状态，供父会话重试。

        Args:
            delegation_id: 当前 MCP 进程生成的委派 ID。
            answers: 每个完整问题或唯一标题对应的答案；必须覆盖全部待答问题。

        Returns:
            Agent API 确认写入答案后的当前委派快照。
        """

        record = self._require(delegation_id)
        if record.status != "needs_guidance" or record.question is None:
            raise InteractiveDelegationError(
                f"delegation {delegation_id} is not waiting for guidance"
            )
        pending_question = record.question
        baseline = await record.transition("running")
        try:
            async with self._client() as client:
                response = await client.post(
                    f"/api/cc/sessions/{record.child_session_id}/answer",
                    json={"answers": answers, "cancel": False},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("success") is not True:
                    raise InteractiveDelegationError(
                        "CC rejected the elicitation answer"
                    )
        except Exception as exc:
            await record.transition(
                "needs_guidance",
                question=pending_question,
                error=str(exc),
            )
            raise
        await record.wait_actionable(after_version=baseline)
        return record.snapshot()

    def status(self, delegation_id: str) -> dict[str, Any]:
        """返回一次交互委派的当前快照。"""

        return self._require(delegation_id).snapshot()

    def parent_session_id(self, delegation_id: str) -> str:
        """读取委派句柄所属的父会话 ID。"""

        return self._require(delegation_id).parent_session_id

    async def close(self, delegation_id: str) -> dict[str, Any]:
        """关闭委派，并清理对应的子会话。

        尚未观察到子会话终态事件时先中断再删除。删除失败时保留记录并返回
        cleanup_pending 快照；创建结果不明、中断失败或整体超时时保留记录并抛出
        异常。

        Args:
            delegation_id: 当前 MCP 进程生成的委派 ID。

        Returns:
            已关闭或等待重试清理的委派快照。

        Raises:
            InteractiveDelegationError: 委派 ID 未知或已经失效。
            InteractiveDelegationCleanupError: 无法确认子会话已经清理。
        """

        record = self._require(delegation_id)
        try:
            return await self._finish_cleanup(
                lambda: self._close_record(record),
                timeout=(2 * self._cleanup_timeout) + self._consumer_stop_timeout,
            )
        except TimeoutError as exc:
            record.cleanup_status = "preserved"
            record.cleanup_error = repr(exc)
            await record.transition(
                "unknown_requires_reconcile",
                error=(
                    "cleanup timed out; child binding may remain: "
                    f"{record.child_session_id}"
                ),
            )
            raise InteractiveDelegationCleanupError(
                f"cleanup timed out; binding {record.child_session_id} was preserved"
            ) from exc

    async def shutdown(self) -> None:
        """尝试逐一清理当前 MCP 进程仍在管理的交互委派。"""

        for delegation_id in list(self._records):
            try:
                await self.close(delegation_id)
            except Exception:
                logger.warning(
                    "interactive delegation %s cleanup failed during MCP shutdown",
                    delegation_id,
                    exc_info=True,
                )

    def _require(self, delegation_id: str) -> _InteractiveDelegation:
        """读取进程内委派记录，并明确拒绝未知或已失效的句柄。"""

        record = self._records.get(delegation_id)
        if record is None:
            raise InteractiveDelegationError(
                f"unknown delegation_id: {delegation_id}; "
                "the MCP process may have restarted"
            )
        return record

    async def _close_record(self, record: _InteractiveDelegation) -> dict[str, Any]:
        """串行清理一次委派。

        创建中的子会话先等待 ID；尚未观察到终态事件时先中断再删除。
        """

        async with record.close_lock:
            if record.cleanup_status == "deleted":
                return record.snapshot()
            async with self._client() as client:
                if record.child_session_id and record.terminal_event is None:
                    try:
                        response = await client.post(
                            f"/api/agent/sessions/{record.child_session_id}/interrupt"
                        )
                        response.raise_for_status()
                    except Exception as exc:
                        record.cleanup_status = "preserved"
                        record.cleanup_error = repr(exc)
                        await record.transition(
                            "unknown_requires_reconcile",
                            error=(
                                "interrupt failed; child binding was preserved: "
                                f"{exc!r}"
                            ),
                        )
                        raise InteractiveDelegationCleanupError(
                            f"interrupt failed; binding {record.child_session_id} "
                            "was preserved"
                        ) from exc
                await self._stop_consumer(record)
                if record.child_session_id:
                    try:
                        response = await client.delete(
                            f"/api/agent/sessions/{record.child_session_id}"
                        )
                        response.raise_for_status()
                    except Exception as exc:
                        record.cleanup_status = "preserved"
                        record.cleanup_error = repr(exc)
                        await record.transition("cleanup_pending", error=str(exc))
                        return record.snapshot()
            record.cleanup_status = "deleted"
            record.cleanup_error = None
            await record.transition("closed")
            result = record.snapshot()
            self._records.pop(record.delegation_id, None)
            return result

    async def _stop_consumer(self, record: _InteractiveDelegation) -> None:
        """等待独占事件流的后台任务结束，超时后取消它。"""

        consumer = record.consumer
        if consumer is None or consumer.done():
            return
        try:
            await asyncio.wait_for(consumer, timeout=self._consumer_stop_timeout)
        except TimeoutError:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass

    async def _finish_cleanup(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        timeout: float | None = None,
    ) -> T:
        """即使调用方任务收到取消，也持续等待清理操作完成或超时。

        Args:
            operation: 尚未启动的异步清理操作。
            timeout: 清理操作的最长等待秒数；为 None 或 0 时使用默认清理时限。

        Returns:
            清理操作的返回值。
        """

        cleanup = asyncio.create_task(
            asyncio.wait_for(
                operation(), timeout=timeout or self._cleanup_timeout
            )
        )
        while True:
            try:
                return await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                if cleanup.done():
                    return cleanup.result()
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()

    async def _consume(
        self,
        record: _InteractiveDelegation,
        *,
        task: str,
        create_body: dict[str, Any],
    ) -> None:
        """创建子会话并独占读取事件流，用提问和终态更新委派记录。

        Args:
            record: 本次委派共享的进程内状态。
            task: 交给子会话执行的初始任务。
            create_body: 创建 Claude Code 子会话时发送给 Agent API 的请求体。
        """

        try:
            async with self._client() as client:
                create_response = await client.post(
                    "/api/agent/sessions", json=create_body
                )
                create_error = agent_api_error_detail(create_response)
                if create_error is not None:
                    raise InteractiveDelegationError(create_error)
                payload = create_response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict) or not isinstance(
                    data.get("session_id"), str
                ):
                    raise InteractiveDelegationError(
                        "create session response has no data.session_id"
                    )
                record.child_binding = dict(data)
                record.child_session_id = data["session_id"]
                await record.transition("running")

                async with client.stream(
                    "POST",
                    f"/api/agent/sessions/{record.child_session_id}/messages",
                    json={"text": task},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line.removeprefix("data:").strip()
                        if not raw:
                            continue
                        event = json.loads(raw)
                        if not isinstance(event, dict):
                            raise InteractiveDelegationError(
                                "SSE data is not an event object"
                            )
                        event_type = str(event.get("type", ""))
                        record.event_counts[event_type] += 1
                        event_payload = event.get("payload")
                        if event_type == "session_started" and isinstance(
                            event_payload, dict
                        ):
                            native = event_payload.get("cc_session_id")
                            if isinstance(native, str):
                                record.child_binding["native_session_id"] = native
                            model = event_payload.get("model")
                            if isinstance(model, str) and model:
                                record.child_binding["model"] = model
                        elif event_type == "text" and isinstance(
                            event_payload, dict
                        ):
                            text = event_payload.get("text")
                            if isinstance(text, str):
                                record.answer_chunks.append(text)
                        elif event_type == "elicit_request" and isinstance(
                            event_payload, dict
                        ):
                            await record.transition(
                                "needs_guidance", question=dict(event_payload)
                            )
                        elif event_type == "finished":
                            record.terminal_event = event_type
                            break
                        elif event_type in _ERROR_TERMINALS:
                            record.terminal_event = event_type
                            await record.transition(
                                "failed", error=_event_error(event)
                            )
                            return
                    else:
                        await record.transition(
                            "failed",
                            error="delegated stream ended without terminal event",
                        )
                        return

                record.child_binding = await _read_child_binding(
                    client,
                    record.child_session_id,
                    record.child_binding,
                )
                await record.transition("completed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, InteractiveDelegationError)
                else repr(exc)
            )
            await record.transition("failed", error=error)


def _event_error(event: dict[str, Any]) -> str:
    """从子会话错误终态中提取供父会话显示的原因。"""

    payload = event.get("payload")
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
        if payload.get("error"):
            return str(payload["error"])
    return f"delegated session ended with {event.get('type', 'unknown')}"


async def _read_child_binding(
    client: httpx.AsyncClient,
    session_id: str,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """读取子会话的最终绑定信息，读取失败时保留已有信息。

    Args:
        client: 用于读取子会话的 Agent API 客户端。
        session_id: 子会话的 Trowel 会话 ID。
        fallback: 查询前已经取得的绑定信息。

    Returns:
        最新绑定信息；请求或响应无效时返回 fallback。
    """

    try:
        response = await client.get(f"/api/agent/sessions/{session_id}")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return dict(data) if isinstance(data, dict) else fallback
    except Exception:
        logger.warning("failed to read final child binding %s", session_id, exc_info=True)
        return fallback
