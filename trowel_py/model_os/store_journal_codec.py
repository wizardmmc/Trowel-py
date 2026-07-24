"""Model OS Store 的 journal 行编解码。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def payload_json(
    payload: dict[str, Any],
    *,
    redact_fn: Callable[[Any], Any],
    json_dumps: Callable[..., str],
    sha256_fn: Callable[[bytes], Any],
    str_type: Callable[[Any], str],
) -> tuple[str, str]:
    redacted = redact_fn(payload)
    text = json_dumps(
        redacted,
        ensure_ascii=False,
        sort_keys=True,
        default=str_type,
    )
    digest = sha256_fn(text.encode("utf-8")).hexdigest()[:12]
    return text, f"sha256:{digest}"


def dumps(
    value: Any,
    *,
    redact_fn: Callable[[Any], Any],
    json_dumps: Callable[..., str],
    str_type: Callable[[Any], str],
) -> str:
    return json_dumps(
        redact_fn(value),
        ensure_ascii=False,
        sort_keys=True,
        default=str_type,
    )


def event_params(
    event: Any,
    payload_text: str,
    payload_hash: str,
) -> tuple[Any, ...]:
    return (
        event.event_id,
        event.kind,
        event.occurred_at,
        event.source,
        event.provenance.value,
        event.policy_version,
        event.work_item_id,
        event.task_id,
        event.episode_id,
        event.native_session_id,
        event.cause_id,
        event.correlation_id,
        event.outcome,
        payload_text,
        payload_hash,
        event.lease_id,
        event.owner,
        event.fencing_token,
    )


def event_identity(event: Any, payload_hash: str) -> tuple[Any, ...]:
    # occurred_at 不参与逻辑身份；重试可发生在不同墙钟时间。
    return (
        event.kind,
        event.source,
        event.provenance.value,
        event.policy_version,
        event.work_item_id,
        event.task_id,
        event.episode_id,
        event.native_session_id,
        event.cause_id,
        event.correlation_id,
        event.outcome,
        payload_hash,
        event.lease_id,
        event.owner,
        event.fencing_token,
    )


def event_row_identity(
    row: Any,
    payload_hash: str,
    *,
    int_fn: Callable[[Any], int],
) -> tuple[Any, ...]:
    fencing_token = row["fencing_token"]
    return (
        row["kind"],
        row["source"],
        row["provenance"],
        row["policy_version"],
        row["work_item_id"],
        row["task_id"],
        row["episode_id"],
        row["native_session_id"],
        row["cause_id"],
        row["correlation_id"],
        row["outcome"],
        row["payload_hash"],
        row["lease_id"],
        row["owner"],
        int_fn(fencing_token) if fencing_token is not None else None,
    )


def decision_params(
    decision: Any,
    *,
    dumps_fn: Callable[[Any], str],
    redact_fn: Callable[[Any], Any],
) -> tuple[Any, ...]:
    return (
        decision.decision_id,
        decision.kind,
        decision.decided_at,
        decision.work_item_id,
        decision.task_id,
        decision.episode_id,
        decision.cause_id,
        decision.correlation_id,
        decision.policy_version,
        dumps_fn(decision.signals),
        dumps_fn(decision.candidates),
        decision.choice,
        redact_fn(decision.reason),
        (
            dumps_fn(decision.budget_before)
            if decision.budget_before is not None
            else None
        ),
        (
            dumps_fn(decision.budget_after)
            if decision.budget_after is not None
            else None
        ),
    )


def lease_from_row(
    row: Any,
    *,
    lease_type: Callable[..., Any],
    int_fn: Callable[[Any], int],
) -> Any:
    return lease_type(
        lease_id=row["lease_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        owner=row["owner"],
        acquired_at=row["acquired_at"],
        expires_at=row["expires_at"],
        idempotency_key=row["idempotency_key"],
        fencing_token=int_fn(row["fencing_token"]),
    )


def event_from_row(
    row: Any,
    *,
    event_type: Callable[..., Any],
    provenance_type: Callable[[Any], Any],
    json_loads: Callable[[str], Any],
    int_fn: Callable[[Any], int],
) -> Any:
    return event_type(
        event_id=row["event_id"],
        kind=row["kind"],
        occurred_at=row["occurred_at"],
        source=row["source"],
        provenance=provenance_type(row["provenance"]),
        policy_version=row["policy_version"],
        payload=json_loads(row["payload"]),
        work_item_id=row["work_item_id"],
        task_id=row["task_id"],
        episode_id=row["episode_id"],
        native_session_id=row["native_session_id"],
        cause_id=row["cause_id"],
        correlation_id=row["correlation_id"],
        outcome=row["outcome"],
        lease_id=row["lease_id"],
        owner=row["owner"],
        fencing_token=(
            int_fn(row["fencing_token"]) if row["fencing_token"] is not None else None
        ),
    )


def decision_from_row(
    row: Any,
    *,
    decision_type: Callable[..., Any],
    json_loads: Callable[[str], Any],
) -> Any:
    return decision_type(
        decision_id=row["decision_id"],
        kind=row["kind"],
        decided_at=row["decided_at"],
        signals=json_loads(row["signals"]),
        candidates=json_loads(row["candidates"]),
        choice=row["choice"],
        reason=row["reason"],
        policy_version=row["policy_version"],
        budget_before=(
            json_loads(row["budget_before"]) if row["budget_before"] else None
        ),
        budget_after=(json_loads(row["budget_after"]) if row["budget_after"] else None),
        work_item_id=row["work_item_id"],
        task_id=row["task_id"],
        episode_id=row["episode_id"],
        cause_id=row["cause_id"],
        correlation_id=row["correlation_id"],
    )
