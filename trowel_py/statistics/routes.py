"""向后续统计页提供统一时间窗、质量和新鲜度查询入口。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from trowel_py.statistics.agent.schemas import AgentStatisticsData
from trowel_py.statistics.agent.service import build_agent_statistics
from trowel_py.statistics.calls.schemas import CallDetailData, CallListData
from trowel_py.statistics.calls.service import build_call_detail, build_call_list
from trowel_py.statistics.memory.schemas import MemoryStatisticsData
from trowel_py.statistics.memory.service import build_memory_statistics
from trowel_py.statistics.runtime.schemas import RuntimeStatisticsData
from trowel_py.statistics.runtime.service import build_runtime_statistics
from trowel_py.statistics.schemas import ApiEnvelope, TelemetryStatisticsData
from trowel_py.statistics.session_problems.schemas import SessionProblemListData
from trowel_py.statistics.session_problems.service import (
    build_session_problem_list,
)
from trowel_py.statistics.service import build_telemetry_statistics
from trowel_py.statistics.window import StatisticsWindow, parse_statistics_window

router = APIRouter(tags=["statistics"])


@router.get(
    "/session-problems",
    response_model=ApiEnvelope[SessionProblemListData],
)
def get_session_problem_list(
    request: Request,
    start_date: str = Query(),
    end_date: str = Query(),
    timezone_name: str = Query(alias="timezone"),
    limit_value: str = Query(default="50", alias="limit"),
    cursor: str | None = Query(default=None),
) -> ApiEnvelope[SessionProblemListData] | JSONResponse:
    """按会话关闭时间倒序返回一页非空复盘问题。

    完整分析但没有问题的会话只进入时间窗计数，不进入列表。响应不会公开生成
    Agent 的模型、运行 ID、原生会话 ID 或本机路径。
    """

    reader = getattr(request.app.state, "session_problem_statistics_reader", None)
    if reader is None:
        return _error_response(
            503,
            "statistics session problems source unavailable",
        )
    try:
        window = _parse_window(start_date, end_date, timezone_name)
        limit = int(limit_value)
        data = build_session_problem_list(
            reader,
            window,
            limit=limit,
            cursor=cursor,
        )
    except (ValueError, OverflowError) as exc:
        return _error_response(422, str(exc))
    return ApiEnvelope[SessionProblemListData](
        success=True,
        data=data,
        error=None,
    )


@router.get(
    "/calls",
    response_model=ApiEnvelope[CallListData],
)
def get_call_list(
    request: Request,
    start_date: str = Query(),
    end_date: str = Query(),
    timezone_name: str = Query(alias="timezone"),
    component: str | None = Query(default=None),
    operation: str | None = Query(default=None),
    runtime: str | None = Query(default=None),
    status: str | None = Query(default=None),
    minimum_duration_value: str = Query(default="0", alias="minimum_duration_ms"),
    limit_value: str = Query(default="50", alias="limit"),
    cursor: str | None = Query(default=None),
) -> ApiEnvelope[CallListData] | JSONResponse:
    """筛选原始受控 span，并按稳定游标返回最近调用。

    Args:
        request: 用于读取应用持有的调用详情 reader。
        start_date: 首尾均包含的第一个 ISO 日期。
        end_date: 首尾均包含的最后一个 ISO 日期。
        timezone_name: 解释日期边界的 IANA 时区名称。
        component: 可选受控组件筛选。
        operation: 可选受控操作名筛选。
        runtime: 可选 Claude Code 或 Codex 筛选。
        status: 可选 ok、error 或 unset 筛选。
        minimum_duration_value: 字符串形式的非负毫秒耗时下限。
        limit_value: 字符串形式的 1 至 200 页面大小。
        cursor: 上一页返回的稳定游标。

    Returns:
        成功调用页；参数或来源不可用时返回统一错误 envelope。
    """

    reader = getattr(request.app.state, "call_statistics_reader", None)
    if reader is None:
        return _error_response(503, "statistics calls source unavailable")
    try:
        window = _parse_window(start_date, end_date, timezone_name)
        minimum_duration_ms = float(minimum_duration_value)
        limit = int(limit_value)
        data = build_call_list(
            reader,
            window,
            component=component,
            operation=operation,
            runtime=runtime,
            status=status,
            minimum_duration_ms=minimum_duration_ms,
            limit=limit,
            cursor=cursor,
        )
    except (ValueError, OverflowError) as exc:
        return _error_response(422, str(exc))
    return ApiEnvelope[CallListData](success=True, data=data, error=None)


@router.get(
    "/calls/{trace_id}",
    response_model=ApiEnvelope[CallDetailData],
)
def get_call_detail(
    trace_id: str,
    request: Request,
) -> ApiEnvelope[CallDetailData] | JSONResponse:
    """按去身份化 trace ID 返回有限跨层图和明确缺口。

    Args:
        trace_id: 调用列表返回的 16 字节十六进制随机身份。
        request: 用于读取应用持有的调用详情 reader。

    Returns:
        成功详情；参数无效、找不到或来源不可用时返回统一错误 envelope。
    """

    reader = getattr(request.app.state, "call_statistics_reader", None)
    if reader is None:
        return _error_response(503, "statistics calls source unavailable")
    try:
        data = build_call_detail(reader, trace_id)
    except ValueError as exc:
        return _error_response(422, str(exc))
    if data is None:
        return _error_response(404, "call trace not found")
    return ApiEnvelope[CallDetailData](success=True, data=data, error=None)


@router.get(
    "/agent",
    response_model=ApiEnvelope[AgentStatisticsData],
)
def get_agent_statistics(
    request: Request,
    start_date: str = Query(),
    end_date: str = Query(),
    timezone_name: str = Query(alias="timezone"),
) -> ApiEnvelope[AgentStatisticsData] | JSONResponse:
    """读取双 runtime 用户 session，并返回 Agent 页 read model。

    Args:
        request: 用于读取应用持有的 Agent statistics reader。
        start_date: 首尾均包含的第一个 ISO 日期。
        end_date: 首尾均包含的最后一个 ISO 日期。
        timezone_name: 解释日期边界的 IANA 时区名称。

    Returns:
        成功 read model；参数或来源不可用时返回统一错误 envelope。
    """

    reader = getattr(request.app.state, "agent_statistics_reader", None)
    if reader is None:
        return _error_response(503, "statistics agent source unavailable")
    try:
        window = _parse_window(start_date, end_date, timezone_name)
    except ValueError as exc:
        return _error_response(422, str(exc))
    return ApiEnvelope[AgentStatisticsData](
        success=True,
        data=build_agent_statistics(reader, window),
        error=None,
    )


@router.get(
    "/memory",
    response_model=ApiEnvelope[MemoryStatisticsData],
)
def get_memory_statistics(
    request: Request,
    start_date: str = Query(),
    end_date: str = Query(),
    timezone_name: str = Query(alias="timezone"),
) -> ApiEnvelope[MemoryStatisticsData] | JSONResponse:
    """读取 Memory 使用事实和资产，并返回不含正文的 read model。

    Args:
        request: 用于读取应用持有的 Memory statistics reader。
        start_date: 首尾均包含的第一个 ISO 日期。
        end_date: 首尾均包含的最后一个 ISO 日期。
        timezone_name: 解释日期边界的 IANA 时区名称。

    Returns:
        成功 read model；参数或来源不可用时返回统一错误 envelope。
    """
    reader = getattr(request.app.state, "memory_statistics_reader", None)
    if reader is None:
        return _error_response(503, "statistics memory source unavailable")
    try:
        window = _parse_window(start_date, end_date, timezone_name)
    except ValueError as exc:
        return _error_response(422, str(exc))
    return ApiEnvelope[MemoryStatisticsData](
        success=True,
        data=build_memory_statistics(reader, window),
        error=None,
    )


@router.get(
    "/runtime",
    response_model=ApiEnvelope[RuntimeStatisticsData],
)
def get_runtime_statistics(
    request: Request,
    start_date: str = Query(),
    end_date: str = Query(),
    timezone_name: str = Query(alias="timezone"),
) -> ApiEnvelope[RuntimeStatisticsData] | JSONResponse:
    """读取桌面生命周期、连接、SQLite 和资源 owner 统计。

    Args:
        request: 用于读取应用持有的 runtime reader 和 collector。
        start_date: 首尾均包含的第一个 ISO 日期。
        end_date: 首尾均包含的最后一个 ISO 日期。
        timezone_name: 解释日期边界的 IANA 时区名称。

    Returns:
        成功 read model；参数或来源不可用时返回统一错误 envelope。
    """

    reader = getattr(request.app.state, "runtime_statistics_reader", None)
    collector = getattr(request.app.state, "telemetry_collector", None)
    if reader is None or collector is None:
        return _error_response(503, "statistics runtime source unavailable")
    try:
        window = _parse_window(start_date, end_date, timezone_name)
    except ValueError as exc:
        return _error_response(422, str(exc))
    return ApiEnvelope[RuntimeStatisticsData](
        success=True,
        data=build_runtime_statistics(reader, collector, window),
        error=None,
    )


@router.get(
    "/telemetry",
    response_model=ApiEnvelope[TelemetryStatisticsData],
)
def get_telemetry_statistics(
    request: Request,
    start_date: str = Query(),
    end_date: str = Query(),
    timezone_name: str = Query(alias="timezone"),
    resolution_value: str = Query(default="hour", alias="resolution"),
) -> ApiEnvelope[TelemetryStatisticsData] | JSONResponse:
    """读取聚合遥测，并统一返回时间窗、样本质量和来源新鲜度。

    Args:
        request: 用于读取应用持有的 telemetry reader 和 collector。
        start_date: 首尾均包含的第一个 ISO 日期。
        end_date: 首尾均包含的最后一个 ISO 日期。
        timezone_name: 解释日期边界的 IANA 时区名称。
        resolution_value: hour 或 day。

    Returns:
        成功 read model；查询参数或来源不可用时返回统一错误 envelope。
    """

    reader = getattr(request.app.state, "telemetry_reader", None)
    collector = getattr(request.app.state, "telemetry_collector", None)
    if reader is None or collector is None:
        return _error_response(503, "statistics telemetry source unavailable")
    try:
        window = _parse_window(start_date, end_date, timezone_name)
        if resolution_value not in {"hour", "day"}:
            raise ValueError("resolution must be hour or day")
        resolution: Literal["hour", "day"] = (
            "hour" if resolution_value == "hour" else "day"
        )
    except ValueError as exc:
        return _error_response(422, str(exc))
    data = build_telemetry_statistics(
        reader,
        collector,
        window,
        resolution=resolution,
    )
    return ApiEnvelope[TelemetryStatisticsData](
        success=True,
        data=data,
        error=None,
    )


def _parse_window(
    start_date: str,
    end_date: str,
    timezone_name: str,
) -> StatisticsWindow:
    """解析 Statistics 路由共用的日期和时区参数。

    Args:
        start_date: 首尾均包含的第一个 ISO 日期。
        end_date: 首尾均包含的最后一个 ISO 日期。
        timezone_name: 解释日期边界的 IANA 时区名称。

    Returns:
        已按当地零点解析的半开时间窗。
    """

    return parse_statistics_window(
        date.fromisoformat(start_date),
        date.fromisoformat(end_date),
        timezone_name,
    )


def _error_response(status_code: int, error: str) -> JSONResponse:
    """构造 Statistics API 共用的错误 envelope。

    Args:
        status_code: HTTP 错误状态码。
        error: 不含私密正文的稳定错误文本。
    """

    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "error": error},
    )
