"""额度只读模型的 HTTP 接口；wire 层不会暴露 provider 原始字段。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import QuotaSnapshot

router = APIRouter()


def snapshot_to_wire(snapshot: QuotaSnapshot) -> dict[str, Any]:
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
    return getattr(request.app.state, "quota_read_model", None)


@router.get("/api/quota")
async def list_quota(request: Request) -> dict[str, Any]:
    """返回所有已知账户的额度快照。"""
    # 保持 async route，使读取与 scheduler/observer 写入都留在事件循环线程。
    model = _read_model(request)
    snapshots = model.all() if model is not None else ()
    return {
        "success": True,
        "data": [snapshot_to_wire(snapshot) for snapshot in snapshots],
        "error": None,
    }
