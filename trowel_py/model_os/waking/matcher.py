"""把 Task 等待投影成受支持的条件，并匹配可信 observation。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from trowel_py.model_os.types import WaitingCondition
from trowel_py.model_os.waking.models import (
    WakeCatchupPolicy,
    WakeCondition,
    WakeConditionKind,
    WakeObservation,
)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def condition_from_waiting(
    task_id: str, waiting: WaitingCondition
) -> WakeCondition | None:
    if not waiting.condition_id or not waiting.registered_at:
        return None
    condition_kind = waiting.condition_kind
    if waiting.kind == "waiting_user":
        kind = WakeConditionKind.USER_INPUT
        target_ref = waiting.correlation_id
    elif condition_kind == "time":
        kind = WakeConditionKind.TIME
        target_ref = waiting.target_ref or "clock"
    elif condition_kind == "host_event":
        kind = WakeConditionKind.HOST_EVENT
        target_ref = waiting.target_ref
    elif condition_kind in {"process", "file"}:
        kind = WakeConditionKind.OBSERVED_STATE
        target_ref = waiting.target_ref
    elif condition_kind == "manual":
        kind = WakeConditionKind.MANUAL
        target_ref = waiting.target_ref
    else:
        return None
    if not target_ref:
        return None
    try:
        catchup = WakeCatchupPolicy(
            waiting.catchup_policy or WakeCatchupPolicy.NO_CATCHUP.value
        )
    except ValueError:
        return None
    params = dict(waiting.match_params or {})
    if condition_kind in {"process", "file"}:
        params["state_kind"] = condition_kind
    return WakeCondition(
        condition_id=waiting.condition_id,
        task_id=task_id,
        kind=kind,
        target_ref=target_ref,
        match_params=params,
        due_at=waiting.deadline,
        catchup_policy=catchup,
        registered_at=waiting.registered_at,
        episode_id=waiting.episode_id,
    )


def _details_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def matches_condition(
    condition: WakeCondition, observation: WakeObservation
) -> bool:
    if condition.kind is not observation.kind:
        return False
    try:
        observed_at = _instant(observation.observed_at)
        if observed_at < _instant(condition.registered_at):
            return False
    except (TypeError, ValueError):
        return False
    fresh_until = observation.details.get("fresh_until")
    if isinstance(fresh_until, str):
        try:
            if observed_at > _instant(fresh_until):
                return False
        except (TypeError, ValueError):
            return False
    if condition.kind is WakeConditionKind.TIME:
        if condition.due_at is None:
            return False
        try:
            return observed_at >= _instant(condition.due_at)
        except (TypeError, ValueError):
            return False
    if condition.target_ref != observation.target_ref:
        return False
    return _details_match(condition.match_params, observation.details)
