"""提供各模型服务商额度的只读 HTTP 接口，响应不包含各窗口的 ``raw`` 字段。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import QuotaSnapshot

router = APIRouter()


def snapshot_to_wire(snapshot: QuotaSnapshot) -> dict[str, Any]:
    """把统一额度快照转换为不含各窗口 ``raw`` 字段的 HTTP 响应数据。"""

    return {
        "provider": snapshot.provider.value,
        "account_id": snapshot.account_id,
        "plan_level": snapshot.plan_level,
        "status": snapshot.status.value,
        "fetched_at": snapshot.fetched_at,
        "windows": [
            {
                "kind": window.kind.value,
                "used_percent": window.used_percent,
                "resets_at": window.resets_at,
            }
            for window in snapshot.windows
        ],
    }


def _read_model(request: Request) -> QuotaReadModel | None:
    """返回 FastAPI 应用持有的额度读模型；没有可用读模型时返回 ``None``。"""

    return getattr(request.app.state, "quota_read_model", None)


@router.get("/api/quota")
async def list_quota(request: Request) -> dict[str, Any]:
    """返回所有已知账户的额度快照。"""

    # 保持异步端点，避免 FastAPI 把读取放入线程池；调度任务和 SessionHub
    # 回调都在应用事件循环中写入。
    model = _read_model(request)
    snapshots = model.all() if model is not None else ()
    return {
        "success": True,
        "data": [snapshot_to_wire(snapshot) for snapshot in snapshots],
        "error": None,
    }
