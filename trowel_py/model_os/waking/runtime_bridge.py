"""把统一 AgentEvent 的 host terminal 接到 pending channel loss。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _is_host_loss(event: Mapping[str, Any]) -> bool:
    event_type = event.get("type")
    payload = event.get("payload")
    details = payload if isinstance(payload, Mapping) else {}
    return event_type == "session_exited" or (
        event_type == "error" and details.get("subclass") == "host_error"
    )


async def observe_runtime_event(
    coordinator,
    session_id: str,
    event: Mapping[str, Any],
    *,
    generation: str,
):
    receipt = await coordinator.observe(
        session_id,
        event,
        generation=generation,
    )
    if _is_host_loss(event):
        return await coordinator.connection_lost(
            session_id,
            generation=generation,
        )
    return receipt
