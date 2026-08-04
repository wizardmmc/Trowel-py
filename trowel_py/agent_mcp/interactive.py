"""由 Agent Host 跨 MCP 进程持有的 Claude child 交互生命周期。"""

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
from trowel_py.agent_mcp.interactive_errors import (
    InteractiveDelegationCleanupError,
    InteractiveDelegationError,
)

_BACKGROUND_STATES = frozenset({"starting", "running", "unknown_requires_reconcile"})
_ACTIONABLE_STATES = frozenset({"needs_guidance", "completed", "failed"})
_ERROR_TERMINALS = frozenset({"error", "interrupted", "session_exited"})

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class _InteractiveDelegation:
    """保存 Agent Host 进程内一次交互委派的实时状态。

    Attributes:
        delegation_id: Agent Host 为本次委派生成的 ID。
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
            供父会话识别两次查询之间是否发生变化。
        condition: 用于关闭创建中委派时等待子会话 ID 的异步条件变量。
        close_lock: 防止同一子会话被并发清理的异步锁。
        consumer: 独占子会话 SSE 的后台任务；任务尚未启动时为 None。
        closing: 是否已经开始显式收敛；此时 child 的中断终态不能再次唤醒父会话。
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
    closing: bool = False

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

    async def wait_for_update(
        self,
        *,
        after_version: int,
        timeout: float,
    ) -> None:
        """等待状态版本前进或委派离开后台运行状态。

        Args:
            after_version: 调用方已经观察到的状态版本。
            timeout: 本次等待的最长秒数。
        """

        async with self.condition:
            if self.version > after_version or self.status not in _BACKGROUND_STATES:
                return
            try:
                await asyncio.wait_for(
                    self.condition.wait_for(
                        lambda: (
                            self.version > after_version
                            or self.status not in _BACKGROUND_STATES
                        )
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                pass

    def snapshot(self) -> dict[str, Any]:
        """生成供父会话查询的当前委派快照。

        Returns:
            包含父子会话标识、状态版本、待回答问题、已观察事件、子会话回答和
            清理结果的快照。
        """

        return {
            "delegation_id": self.delegation_id,
            "version": self.version,
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
    """在 Agent Host 应用进程内持有 child SSE，供后续 MCP 进程继续。"""

    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        headers: dict[str, str] | None = None,
        notifier: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        cleanup_timeout: float = 10.0,
        consumer_stop_timeout: float = 10.0,
    ) -> None:
        """配置 Agent API 地址、清理时限和应用级委派记录。

        Args:
            base_url: Trowel Agent API 的根地址。
            transport: 发起 Agent API 请求时使用的可选 httpx transport。
            headers: Agent Host 自调用时携带的可选请求头。
            notifier: 可执行状态变化后接收完整快照的异步回调；未提供时只保留查询
                接口，不自动唤醒父会话。
            cleanup_timeout: 关闭创建中的委派时等待子会话 ID 的秒数；也用于计算
                整个清理流程的超时上限。
            consumer_stop_timeout: 等待子会话事件任务自行结束的秒数；超时后取消
                该任务。
        """

        if cleanup_timeout <= 0 or consumer_stop_timeout <= 0:
            raise ValueError("interactive cleanup timeouts must be positive")
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._headers = dict(headers or {})
        self._notifier = notifier
        self._cleanup_timeout = cleanup_timeout
        self._consumer_stop_timeout = consumer_stop_timeout
        self._records: dict[str, _InteractiveDelegation] = {}
        self._closed_parents: set[str] = set()

    def _client(self) -> httpx.AsyncClient:
        """创建不设 HTTP 超时的 Agent API 客户端。"""

        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(None),
            transport=self._transport,
            headers=self._headers,
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
        if parent_session_id in self._closed_parents:
            raise InteractiveDelegationError(
                f"parent session {parent_session_id} is closing or closed"
            )
        record = _InteractiveDelegation(
            delegation_id=uuid.uuid4().hex,
            parent_session_id=parent_session_id,
        )
        self._records[record.delegation_id] = record
        record.consumer = asyncio.create_task(
            self._consume(record, task=task, create_body=create_body)
        )
        return record.snapshot()

    async def respond(
        self, delegation_id: str, answers: dict[str, str]
    ) -> dict[str, Any]:
        """把父会话答案写回等待中的 Claude Code 子会话。

        Agent API 确认写入后立即返回；写入失败时恢复原提问和 needs_guidance
        状态，供父会话重试。

        Args:
            delegation_id: Agent Host 生成的委派 ID。
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
        normalized_answers = _normalize_answers(pending_question, answers)
        await self._transition(record, "running")
        try:
            async with self._client() as client:
                response = await client.post(
                    f"/api/cc/sessions/{record.child_session_id}/answer",
                    json={"answers": normalized_answers, "cancel": False},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("success") is not True:
                    raise InteractiveDelegationError(
                        "CC rejected the elicitation answer"
                    )
        except Exception as exc:
            await self._transition(
                record,
                "needs_guidance",
                question=pending_question,
                error=str(exc),
            )
            raise
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
            delegation_id: Agent Host 生成的委派 ID。

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
            await self._transition(
                record,
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
        """尝试逐一清理 Agent Host 进程仍在管理的交互委派。"""

        for delegation_id in list(self._records):
            try:
                await self.close(delegation_id)
            except Exception:
                logger.warning(
                    "interactive delegation %s cleanup failed during Agent Host shutdown",
                    delegation_id,
                    exc_info=True,
                )

    async def close_parent(self, parent_session_id: str) -> None:
        """在父会话关闭前收敛它创建的全部交互委派。

        Args:
            parent_session_id: 即将关闭的父 Trowel 会话 ID。

        Raises:
            InteractiveDelegationCleanupError: 任一 child 无法确认已经清理。
        """

        self._closed_parents.add(parent_session_id)
        delegation_ids = [
            record.delegation_id
            for record in self._records.values()
            if record.parent_session_id == parent_session_id
        ]
        errors: list[str] = []
        for delegation_id in delegation_ids:
            try:
                await self.close(delegation_id)
            except Exception as exc:  # noqa: BLE001 - 保留所有未清理句柄后统一拒绝父关闭。
                errors.append(f"{delegation_id}: {exc}")
        if errors:
            raise InteractiveDelegationCleanupError(
                "parent delegation cleanup failed: " + "; ".join(errors)
            )

    def _require(self, delegation_id: str) -> _InteractiveDelegation:
        """读取应用级委派记录，并明确拒绝未知或已失效的句柄。"""

        record = self._records.get(delegation_id)
        if record is None:
            raise InteractiveDelegationError(f"unknown delegation_id: {delegation_id}")
        return record

    async def _transition(
        self,
        record: _InteractiveDelegation,
        status: str,
        *,
        question: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """更新委派状态，并把可执行的新版本交给父会话调度器。

        通知失败只记录日志；broker 状态和 child SSE reader 不能依赖父会话是否
        当前可恢复。

        Args:
            record: 本次状态变化所属的应用级委派记录。
            status: 新的父会话可见状态。
            question: needs_guidance 状态携带的原始提问。
            error: 状态变化附带的失败原因。
        """

        await record.transition(status, question=question, error=error)
        if (
            self._notifier is None
            or record.closing
            or status not in _ACTIONABLE_STATES
        ):
            return
        try:
            await self._notifier(record.snapshot())
        except Exception:  # noqa: BLE001 - 通知失败不能改写已经观察到的 child 状态。
            logger.warning(
                "interactive delegation %s parent notification failed",
                record.delegation_id,
                exc_info=True,
            )

    async def _close_record(self, record: _InteractiveDelegation) -> dict[str, Any]:
        """串行清理一次委派。

        创建中的子会话先等待 ID；尚未观察到终态事件时先中断再删除。
        """

        async with record.close_lock:
            record.closing = True
            if record.cleanup_status == "deleted":
                return record.snapshot()
            if not record.child_session_id and record.status == "starting":
                baseline = record.version
                await record.wait_for_update(
                    after_version=baseline,
                    timeout=self._cleanup_timeout,
                )
                if not record.child_session_id and record.status == "starting":
                    error = (
                        "child creation did not resolve; no binding id is available "
                        "for cleanup"
                    )
                    record.cleanup_status = "preserved"
                    record.cleanup_error = error
                    await self._transition(
                        record,
                        "unknown_requires_reconcile",
                        error=error,
                    )
                    raise InteractiveDelegationCleanupError(error)
            if (
                not record.child_session_id
                and record.status == "unknown_requires_reconcile"
            ):
                raise InteractiveDelegationCleanupError(
                    record.cleanup_error
                    or "child creation did not resolve; no binding id is available "
                    "for cleanup"
                )
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
                        await self._transition(
                            record,
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
                        await self._transition(
                            record, "cleanup_pending", error=str(exc)
                        )
                        return record.snapshot()
            record.cleanup_status = "deleted"
            record.cleanup_error = None
            await self._transition(record, "closed")
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
            asyncio.wait_for(operation(), timeout=timeout or self._cleanup_timeout)
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
            record: 本次委派共享的 Agent Host 应用级状态。
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
                await self._transition(record, "running")

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
                        elif event_type == "text" and isinstance(event_payload, dict):
                            text = event_payload.get("text")
                            if isinstance(text, str):
                                record.answer_chunks.append(text)
                        elif event_type == "elicit_request" and isinstance(
                            event_payload, dict
                        ):
                            await self._transition(
                                record,
                                "needs_guidance", question=dict(event_payload)
                            )
                        elif event_type == "finished":
                            record.terminal_event = event_type
                            break
                        elif event_type in _ERROR_TERMINALS:
                            record.terminal_event = event_type
                            await self._transition(
                                record, "failed", error=_event_error(event)
                            )
                            return
                    else:
                        await self._transition(
                            record,
                            "failed",
                            error="delegated stream ended without terminal event",
                        )
                        return

                record.child_binding = await _read_child_binding(
                    client,
                    record.child_session_id,
                    record.child_binding,
                )
                await self._transition(record, "completed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = (
                str(exc) if isinstance(exc, InteractiveDelegationError) else repr(exc)
            )
            await self._transition(record, "failed", error=error)


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


def _normalize_answers(
    pending_question: dict[str, Any],
    answers: dict[str, str],
) -> dict[str, str]:
    """把唯一标题或完整问题形式的答案键统一成完整问题。

    Args:
        pending_question: 当前 elicit_request 事件的 payload。
        answers: 父会话按唯一标题或完整问题提供的回答。

    Returns:
        以完整问题为键、完整覆盖本轮问题的回答。

    Raises:
        InteractiveDelegationError: 问题结构无效，或回答缺失、重复、未知或有歧义。
    """

    raw_questions = pending_question.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise InteractiveDelegationError("pending guidance has no questions")

    questions: list[str] = []
    headers: dict[str, list[str]] = {}
    for raw in raw_questions:
        if not isinstance(raw, dict):
            raise InteractiveDelegationError("pending guidance has an invalid question")
        question = raw.get("question")
        if not isinstance(question, str) or not question:
            raise InteractiveDelegationError("pending guidance has an invalid question")
        if question in questions:
            raise InteractiveDelegationError(
                f"duplicate pending guidance question: {question}"
            )
        questions.append(question)
        header = raw.get("header")
        if isinstance(header, str) and header:
            headers.setdefault(header, []).append(question)

    normalized: dict[str, str] = {}
    for key, value in answers.items():
        if key in questions:
            question = key
        else:
            matches = headers.get(key, [])
            if not matches:
                raise InteractiveDelegationError(f"unknown guidance answer key: {key}")
            if len(matches) != 1:
                raise InteractiveDelegationError(
                    f"ambiguous guidance answer header: {key}"
                )
            question = matches[0]
        if question in normalized:
            raise InteractiveDelegationError(
                f"duplicate guidance answer for question: {question}"
            )
        normalized[question] = value

    missing = [question for question in questions if question not in normalized]
    if missing:
        raise InteractiveDelegationError(
            "missing guidance answer for question(s): " + ", ".join(missing)
        )
    return normalized


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
        logger.warning(
            "failed to read final child binding %s", session_id, exc_info=True
        )
        return fallback
