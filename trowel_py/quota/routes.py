"""HTTP surface for the quota read model (slice-093-pre, criterion 6).

``GET /api/quota`` returns the unified snapshots so the frontend can render
account quota without bouncing to bigmodel.cn. Only the normalized fields are
exposed; the verbatim provider ``raw`` is dropped at the wire boundary (leaner
payload, and nothing provider-specific leaks to the client).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import QuotaSnapshot

router = APIRouter()


def snapshot_to_wire(snapshot: QuotaSnapshot) -> dict[str, Any]:
    """Normalized, JSON-friendly view of a snapshot (no raw provider fields)."""

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
    """The app-level quota read model, or None when startup did not wire one."""

    return getattr(request.app.state, "quota_read_model", None)


@router.get("/api/quota")
async def list_quota(request: Request) -> dict[str, Any]:
    """Every known account's quota snapshot (staleness applied by the model).

    ``async def`` (not sync ``def``) so the read stays in the event-loop thread
    alongside the scheduler/observer writers; a sync route would run in a
    threadpool and race the writers (slice-093-pre review HIGH-2).
    """

    model = _read_model(request)
    snapshots = model.all() if model is not None else ()
    return {
        "success": True,
        "data": [snapshot_to_wire(snapshot) for snapshot in snapshots],
        "error": None,
    }
