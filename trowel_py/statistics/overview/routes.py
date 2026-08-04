"""提供可局部降级的 Statistics 总览 HTTP 入口。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from trowel_py.statistics.schemas import ApiEnvelope
from trowel_py.statistics.window import parse_statistics_window

from .query import OverviewSources, load_overview_statistics
from .schemas import OverviewStatisticsData

router = APIRouter()


@router.get(
    "/overview",
    response_model=ApiEnvelope[OverviewStatisticsData],
)
async def get_overview_statistics(
    request: Request,
    start_date: str = Query(),
    end_date: str = Query(),
    timezone_name: str = Query(alias="timezone"),
) -> ApiEnvelope[OverviewStatisticsData] | JSONResponse:
    """并行读取五个公开 read model，并返回可局部降级的总览。

    Args:
        request: 用于读取应用已装配的 Statistics reader。
        start_date: 首尾均包含的第一个 ISO 日期。
        end_date: 首尾均包含的最后一个 ISO 日期。
        timezone_name: 解释日期边界的 IANA 时区名称。

    Returns:
        成功总览；日期参数无效时返回统一错误 envelope。
    """

    try:
        window = parse_statistics_window(
            date.fromisoformat(start_date),
            date.fromisoformat(end_date),
            timezone_name,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={"success": False, "data": None, "error": str(exc)},
        )
    data = await load_overview_statistics(
        OverviewSources(
            agent=getattr(request.app.state, "agent_statistics_reader", None),
            memory=getattr(request.app.state, "memory_statistics_reader", None),
            runtime=getattr(request.app.state, "runtime_statistics_reader", None),
            collector=getattr(request.app.state, "telemetry_collector", None),
            calls=getattr(request.app.state, "call_statistics_reader", None),
            session_problems=getattr(
                request.app.state,
                "session_problem_statistics_reader",
                None,
            ),
        ),
        window,
    )
    return ApiEnvelope[OverviewStatisticsData](success=True, data=data, error=None)
