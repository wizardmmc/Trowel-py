"""向 Electron Host 暴露不进入公开 OpenAPI 的 sidecar readiness。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trowel_py.desktop.contract import (
    DESKTOP_CAPABILITIES,
    DESKTOP_PROTOCOL_VERSION,
    app_version,
)

router = APIRouter()


class ReportProcessRequest(BaseModel):
    """携带 Trowel 自有子进程的登记令牌和当前 PID。

    Attributes:
        token: sidecar 为固定 owner 和可信启动树签发的随机令牌。
        pid: 发起请求的 Trowel 子进程自报 PID；账本还会用实时 PPID 链和启动
            身份交叉核验，不能只凭该数值登记。
    """

    token: str = Field(min_length=20, max_length=200)
    pid: int = Field(gt=1)


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


@router.get("/resources", include_in_schema=False, response_model=None)
def resource_summary(request: Request) -> dict[str, object] | JSONResponse:
    """返回 Host 可读取的去身份化资源数量和 drain 状态。"""

    registry = getattr(request.app.state, "resource_registry", None)
    if registry is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "data": None, "error": "resource registry unavailable"},
        )
    data = registry.private_summary()
    coordinator = getattr(request.app.state, "drain_coordinator", None)
    data["draining"] = bool(coordinator is not None and coordinator.draining)
    return {"success": True, "data": data, "error": None}


@router.post("/resources/register", include_in_schema=False, response_model=None)
def register_process(
    body: ReportProcessRequest,
    request: Request,
) -> dict[str, object] | JSONResponse:
    """按预签发 owner 登记间接启动的 Trowel 自有子进程。"""

    registry = getattr(request.app.state, "resource_registry", None)
    if registry is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "data": None, "error": "resource registry unavailable"},
        )
    try:
        registry.register_reported_process(body.token, pid=body.pid)
    except (ValueError, RuntimeError) as exc:
        return JSONResponse(
            status_code=409,
            content={"success": False, "data": None, "error": str(exc)},
        )
    return {
        "success": True,
        "data": {"registered": True},
        "error": None,
    }


@router.post("/drain", include_in_schema=False, response_model=None)
async def drain(request: Request) -> dict[str, object] | JSONResponse:
    """触发并等待 sidecar 内部资源收敛；重复请求复用同一次退出序列。"""

    coordinator = getattr(request.app.state, "drain_coordinator", None)
    if coordinator is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "data": None, "error": "drain coordinator unavailable"},
        )
    report = await coordinator.drain()
    return {"success": True, "data": report.to_private_dict(), "error": None}
