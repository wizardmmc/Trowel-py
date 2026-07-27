"""自动调度总开关的持久状态。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def _identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def automation_is_paused(store) -> bool:
    with store._read_tx():
        row = store._conn.execute(
            "SELECT payload FROM events WHERE kind=? ORDER BY seq DESC LIMIT 1",
            (EventKind.AUTOMATION_MODE_CHANGED,),
        ).fetchone()
    if row is None:
        return False
    return bool(json.loads(row["payload"])["paused"])


def set_automation_paused(
    store,
    *,
    paused: bool,
    idempotency_key: str,
) -> bool:
    if not isinstance(paused, bool):
        raise ValueError("paused must be a boolean")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key must be non-empty")
    identity = _identity(idempotency_key)
    store.append_event(
        EventEnvelope(
            event_id=f"automation.mode_changed.{identity}",
            kind=EventKind.AUTOMATION_MODE_CHANGED,
            occurred_at=datetime.now(timezone.utc).isoformat(),
            source="user",
            provenance=Provenance.USER_DECISION,
            policy_version=store._policy_version,
            payload={
                "paused": paused,
                "idempotency_key_hash": f"sha256:{hashlib.sha256(idempotency_key.encode()).hexdigest()}",
            },
        )
    )
    return automation_is_paused(store)
