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

_ACTIONABLE_STATES = frozenset({"needs_guidance", "completed", "failed"})
_ERROR_TERMINALS = frozenset({"error", "interrupted", "session_exited"})

logger = logging.getLogger(__name__)
T = TypeVar("T")


class InteractiveDelegationError(RuntimeError):
    pass


class InteractiveDelegationCleanupError(InteractiveDelegationError):
    pass


@dataclass
class _InteractiveDelegation:
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
        async with self.condition:
            self.status = status
            self.question = question
            self.error = error
            self.version += 1
            self.condition.notify_all()
            return self.version

    async def wait_actionable(self, *, after_version: int = -1) -> None:
        async with self.condition:
            await self.condition.wait_for(
                lambda: self.version > after_version
                and self.status in _ACTIONABLE_STATES
            )

    def snapshot(self) -> dict[str, Any]:
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
        if cleanup_timeout <= 0 or consumer_stop_timeout <= 0:
            raise ValueError("interactive cleanup timeouts must be positive")
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._cleanup_timeout = cleanup_timeout
        self._consumer_stop_timeout = consumer_stop_timeout
        self._records: dict[str, _InteractiveDelegation] = {}

    def _client(self) -> httpx.AsyncClient:
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
        return self._require(delegation_id).snapshot()

    def parent_session_id(self, delegation_id: str) -> str:
        return self._require(delegation_id).parent_session_id

    async def close(self, delegation_id: str) -> dict[str, Any]:
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
        record = self._records.get(delegation_id)
        if record is None:
            raise InteractiveDelegationError(
                f"unknown delegation_id: {delegation_id}; "
                "the MCP process may have restarted"
            )
        return record

    async def _close_record(self, record: _InteractiveDelegation) -> dict[str, Any]:
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
        try:
            async with self._client() as client:
                create_response = await client.post(
                    "/api/agent/sessions", json=create_body
                )
                create_response.raise_for_status()
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
            await record.transition("failed", error=repr(exc))


def _event_error(event: dict[str, Any]) -> str:
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
    try:
        response = await client.get(f"/api/agent/sessions/{session_id}")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return dict(data) if isinstance(data, dict) else fallback
    except Exception:
        logger.warning("failed to read final child binding %s", session_id, exc_info=True)
        return fallback
