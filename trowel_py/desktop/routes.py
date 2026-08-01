"""向 Electron Host 暴露不进入公开 OpenAPI 的 sidecar readiness。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from trowel_py.desktop.contract import (
    DESKTOP_CAPABILITIES,
    DESKTOP_PROTOCOL_VERSION,
    app_version,
)

router = APIRouter()


@router.get("/readiness", include_in_schema=False, response_model=None)
def readiness(request: Request) -> dict[str, object] | JSONResponse:
    """返回 Host 用于排除旧进程和版本串线的当前实例事实。"""
    instance_id = str(getattr(request.app.state, "desktop_instance_id", "")).strip()
    if not instance_id:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "data": None,
                "error": "desktop instance is not configured",
            },
        )
    return {
        "success": True,
        "data": {
            "status": "ready",
            "app_version": app_version(),
            "protocol_version": DESKTOP_PROTOCOL_VERSION,
            "instance_id": instance_id,
            "capabilities": list(DESKTOP_CAPABILITIES),
        },
        "error": None,
    }
