"""向后续统计页提供统一时间窗、质量和新鲜度查询入口。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from trowel_py.statistics.schemas import ApiEnvelope, TelemetryStatisticsData
from trowel_py.statistics.service import build_telemetry_statistics
from trowel_py.statistics.window import parse_statistics_window

router = APIRouter(tags=["statistics"])


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
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        window = parse_statistics_window(start, end, timezone_name)
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
