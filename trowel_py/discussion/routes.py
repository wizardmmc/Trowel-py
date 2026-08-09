"""提供研讨创建、轮次命令、查询和共同发布 SSE。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute

from trowel_py.discussion.errors import DiscussionError
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.schemas import (
    AddDiscussionMessageRequest,
    AnswerParticipantQuestionRequest,
    ContinueDiscussionRequest,
    CreateDiscussionHandoffRequest,
    CreateDiscussionRequest,
    MarkDiscussionResultRequest,
    StopDiscussionRequest,
    VersionedCommand,
)
from trowel_py.discussion.service import DiscussionService
from trowel_py.discussion.timeline import DiscussionTimelineService

_HEARTBEAT_SECONDS = 15.0
_logger = logging.getLogger(__name__)


@runtime_checkable
class DiscussionEventReader(Protocol):
    """声明 discussion SSE 读取持久快照和事件所需的窄接口。"""

    def get(self, discussion_id: str) -> dict[str, Any]:
        """返回当前公开 discussion 快照。"""
        ...

    def list_events(
        self, discussion_id: str, *, after_sequence: int
    ) -> tuple[dict[str, Any], ...]:
        """返回指定 sequence 之后的持久事件。"""
        ...


class DiscussionRoute(APIRoute):
    """把请求校验和领域错误统一转换成稳定 envelope。"""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """包装 FastAPI handler 并阻止错误回显研讨正文。

        Returns:
            带请求校验与领域错误映射的 handler。
        """

        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            """执行 handler，并只返回稳定错误码和脱敏消息。"""

            try:
                return await original_handler(request)
            except RequestValidationError:
                return _error(
                    DiscussionError(
                        "DISCUSSION_INVALID_REQUEST",
                        "研讨请求字段无效",
                        status_code=422,
                    )
                )
            except DiscussionError as exc:
                return _error(exc)
            except Exception as exc:  # noqa: BLE001 - 公开边界必须屏蔽文件路径等内部细节。
                correlation_id = uuid.uuid4().hex
                _logger.error(
                    "discussion request failed correlation_id=%s exception_type=%s",
                    correlation_id,
                    type(exc).__name__,
                )
                return _internal_error(correlation_id)

        return safe_handler


router = APIRouter(route_class=DiscussionRoute, tags=["discussions"])


def get_discussion_service(request: Request) -> DiscussionService:
    """从应用 lifespan 取得已装配的 discussion service。

    Args:
        request: 当前 HTTP 请求。

    Returns:
        应用唯一 discussion service。

    Raises:
        DiscussionError: 应用尚未完成 discussion 初始化。
    """

    service = getattr(request.app.state, "discussion_service", None)
    if not isinstance(service, DiscussionService):
        raise DiscussionError(
            "DISCUSSION_UNAVAILABLE",
            "研讨服务尚未初始化",
            status_code=503,
        )
    return service


def get_discussion_events(request: Request) -> DiscussionEventBus:
    """从应用 lifespan 取得 discussion 状态唤醒总线。

    Args:
        request: 当前 HTTP 请求。

    Returns:
        应用唯一事件总线。

    Raises:
        DiscussionError: 应用尚未完成 discussion 初始化。
    """

    events = getattr(request.app.state, "discussion_events", None)
    if not isinstance(events, DiscussionEventBus):
        raise DiscussionError(
            "DISCUSSION_UNAVAILABLE",
            "研讨服务尚未初始化",
            status_code=503,
        )
    return events


def get_discussion_coordinator(request: Request) -> DiscussionCoordinator:
    """从应用 lifespan 取得唯一 participant 协调器。"""

    coordinator = getattr(request.app.state, "discussion_coordinator", None)
    if not isinstance(coordinator, DiscussionCoordinator):
        raise DiscussionError(
            "DISCUSSION_UNAVAILABLE",
            "研讨服务尚未初始化",
            status_code=503,
        )
    return coordinator


def get_discussion_timeline(request: Request) -> DiscussionTimelineService:
    """从应用 lifespan 取得只读 participant 历史服务。"""

    timeline = getattr(request.app.state, "discussion_timeline", None)
    if not isinstance(timeline, DiscussionTimelineService):
        raise DiscussionError(
            "DISCUSSION_UNAVAILABLE",
            "研讨服务尚未初始化",
            status_code=503,
        )
    return timeline


def get_discussion_event_reader(request: Request) -> DiscussionEventReader:
    """取得只供 SSE 重放使用的窄读取接口，并允许等价测试替身。"""

    reader = getattr(request.app.state, "discussion_service", None)
    if not isinstance(reader, DiscussionEventReader):
        raise DiscussionError(
            "DISCUSSION_UNAVAILABLE",
            "研讨服务尚未初始化",
            status_code=503,
        )
    return reader


def _success(data: Any) -> dict[str, Any]:
    """构造全局一致的成功 envelope。

    Args:
        data: 公开 DTO 或命令结果。

    Returns:
        success/data/error 三字段响应。
    """

    return {"success": True, "data": data, "error": None}


def _error(exc: DiscussionError) -> JSONResponse:
    """把 discussion 领域错误转换成不含正文的 HTTP envelope。

    Args:
        exc: 已带稳定代码和状态码的领域错误。

    Returns:
        对应状态码的 JSONResponse。
    """

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "data": None,
            "error": {"code": exc.code, "message": exc.message},
        },
    )


def _internal_error(correlation_id: str) -> JSONResponse:
    """构造不含异常正文和 artifact 身份的固定内部错误。

    Args:
        correlation_id: 只用于关联安全日志的随机标识。

    Returns:
        脱敏 500 envelope。
    """

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": "DISCUSSION_INTERNAL_ERROR",
                "message": f"研讨数据暂时无法读取（关联标识 {correlation_id}）",
            },
        },
    )


@router.post("")
async def create_discussion(
    body: CreateDiscussionRequest,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """创建并冻结参与者，但由显式 start 命令开始第一轮。"""

    return _success(await service.create(body))


@router.get("")
async def list_discussions(
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """返回最近更新且尚未删除的研讨。"""

    return _success(service.list())


@router.get("/{discussion_id}")
async def get_discussion(
    discussion_id: str,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """返回一场研讨的当前公开快照。

    Args:
        discussion_id: 要查询的研讨 ID。
        service: discussion 命令与查询服务。
    """

    return _success(service.get(discussion_id))


@router.get("/{discussion_id}/transcript")
async def get_discussion_transcript(
    discussion_id: str,
    service: DiscussionService = Depends(get_discussion_service),
) -> Response:
    """通过只读 HTTP 接口返回完整的已公开研讨记录。"""

    return Response(
        service.get_transcript(discussion_id),
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{discussion_id}/attempts/{attempt_id}/events")
async def get_discussion_attempt_events(
    discussion_id: str,
    attempt_id: str,
    timeline: DiscussionTimelineService = Depends(get_discussion_timeline),
) -> dict[str, Any]:
    """从 participant 原生记录恢复一个 attempt 的单轮轨迹。"""

    return _success(await timeline.get_attempt(discussion_id, attempt_id))


@router.post("/{discussion_id}/start")
async def start_discussion(
    discussion_id: str,
    body: VersionedCommand,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """创建第一轮并让后台协调器开始运行。"""

    return _success(service.start(discussion_id, body))


@router.post("/{discussion_id}/messages")
async def add_discussion_message(
    discussion_id: str,
    body: AddDiscussionMessageRequest,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """在轮次间向全体或指定 participant 补充用户原话。"""

    return _success(service.add_message(discussion_id, body))


@router.post("/{discussion_id}/marks")
async def mark_discussion_result(
    discussion_id: str,
    body: MarkDiscussionResultRequest,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """把一个已公开参与者结果加入或移出 Agent 交接现场。"""

    return _success(service.mark_result(discussion_id, body))


@router.post("/{discussion_id}/participant-questions/answer")
async def answer_participant_question(
    discussion_id: str,
    body: AnswerParticipantQuestionRequest,
    coordinator: DiscussionCoordinator = Depends(get_discussion_coordinator),
) -> dict[str, Any]:
    """只把用户答案交给发问的当前 attempt。"""

    answered = await coordinator.answer_elicitation(
        discussion_id=discussion_id,
        participant_id=body.participant_id,
        attempt_id=body.attempt_id,
        request_id=body.request_id,
        answers=body.answers,
    )
    return _success({"answered": answered})


@router.post("/{discussion_id}/handoffs")
async def handoff_discussion_to_agent(
    discussion_id: str,
    body: CreateDiscussionHandoffRequest,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """创建普通 Agent，把研讨现场作为背景并发送用户首条指令。"""

    return _success(await service.handoff(discussion_id, body))


@router.post("/{discussion_id}/continue")
async def continue_discussion(
    discussion_id: str,
    body: ContinueDiscussionRequest,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """在共同公开边界切换逐轮或自动批次并创建下一普通轮。"""

    return _success(service.continue_round(discussion_id, body))


@router.post("/{discussion_id}/finish")
async def finish_discussion(
    discussion_id: str,
    body: VersionedCommand,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """创建要求各方独立说明最终立场的收尾轮。"""

    return _success(service.finish(discussion_id, body))


@router.post("/{discussion_id}/stop")
async def stop_discussion(
    discussion_id: str,
    body: StopDiscussionRequest,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """提交停止事实并收敛 participant runtime 资源。"""

    return _success(await service.stop_discussion(discussion_id, body))


@router.post("/{discussion_id}/resume")
async def resume_discussion(
    discussion_id: str,
    body: VersionedCommand,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """手动重试待对账轮；正常应用重启会自动执行同一操作。"""

    return _success(service.resume(discussion_id, body))


@router.delete("/{discussion_id}")
async def delete_discussion(
    discussion_id: str,
    body: VersionedCommand,
    service: DiscussionService = Depends(get_discussion_service),
) -> dict[str, Any]:
    """软删除已收口研讨，并保留 Episode provenance tombstone。"""

    return _success(await service.delete(discussion_id, body))


@router.get("/{discussion_id}/events")
async def stream_discussion_events(
    discussion_id: str,
    service: DiscussionEventReader = Depends(get_discussion_event_reader),
    events: DiscussionEventBus = Depends(get_discussion_events),
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    """重放持久状态事件，并在 round publish 后才附带公开轮次正文。

    Args:
        discussion_id: 要订阅的研讨 ID。
        service: 用于读取持久事件和已发布 DTO 的服务。
        events: 只唤醒重新查询的进程内总线。
        after: 客户端已经处理的最大持久 sequence。

    Returns:
        带 heartbeat 的 SSE 流。
    """

    service.get(discussion_id)
    subscription = events.subscribe(discussion_id)

    async def generate() -> AsyncIterator[bytes]:
        """按 sequence 重查 SQLite，避免唤醒丢失造成事件缺口。"""

        cursor = after
        try:
            while True:
                batch = service.list_events(discussion_id, after_sequence=cursor)
                if batch:
                    for event in batch:
                        cursor = max(cursor, int(event["sequence"]))
                        payload = dict(event)
                        if event["type"] == "round_published":
                            snapshot = service.get(discussion_id)
                            payload["round"] = next(
                                (
                                    item
                                    for item in snapshot["rounds"]
                                    if item["number"] == event["round_number"]
                                ),
                                None,
                            )
                        yield _sse(payload)
                    continue
                delivery = await subscription.receive(timeout=_HEARTBEAT_SECONDS)
                if delivery is None:
                    yield b": heartbeat\n\n"
                elif delivery.kind != "state_changed" and delivery.payload is not None:
                    yield _sse(delivery.payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - StreamingResponse 已建立，必须在流内脱敏。
            correlation_id = uuid.uuid4().hex
            _logger.error(
                "discussion stream failed correlation_id=%s exception_type=%s",
                correlation_id,
                type(exc).__name__,
            )
            yield _sse(
                {
                    "type": "error",
                    "code": "DISCUSSION_INTERNAL_ERROR",
                    "message": "研讨事件流暂时无法继续",
                    "correlation_id": correlation_id,
                }
            )
        finally:
            subscription.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict[str, Any]) -> bytes:
    """把一条公开 discussion 事件编码成具名 SSE frame。

    Args:
        payload: 不含封闭正文的持久事件；publish 后可附公开 round。

    Returns:
        UTF-8 SSE 字节。
    """

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {payload['type']}\ndata: {body}\n\n".encode("utf-8")
