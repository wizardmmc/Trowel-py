"""接收桌面 Host、renderer 和 Python 组件提交的遥测批次。"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from trowel_py.statistics.schemas import ApiEnvelope, TelemetrySubmitData
from trowel_py.telemetry.contracts import TelemetryBatchRequest

router = APIRouter(tags=["telemetry"])


@router.post(
    "/batches",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiEnvelope[TelemetrySubmitData],
)
def submit_telemetry_batch(
    body: TelemetryBatchRequest,
    request: Request,
) -> ApiEnvelope[TelemetrySubmitData] | JSONResponse:
    """校验并把遥测批次放入不等待 SQLite 的有界队列。

    Args:
        body: 来源组件提交的版本化 span/metric 批次。
        request: 用于读取当前应用持有的 collector。

    Returns:
        202 接受结果；collector 不可用时返回统一 503 envelope。
    """

    collector = getattr(request.app.state, "telemetry_collector", None)
    if collector is None:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "data": None,
                "error": "telemetry collector unavailable",
            },
        )
    result = collector.submit(body)
    return ApiEnvelope[TelemetrySubmitData](
        success=True,
        data=TelemetrySubmitData(
            accepted=result.accepted,
            rejected=result.rejected,
            dropped=result.dropped,
            duplicate=result.duplicate,
            error_categories=result.error_categories,
        ),
        error=None,
    )
