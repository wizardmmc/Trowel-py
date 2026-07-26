from __future__ import annotations

import pytest

from trowel_py.model_os.types import WaitingCondition
from trowel_py.model_os.waking import (
    WakeConditionKind,
    WakeObservation,
    condition_from_waiting,
    matches_condition,
)


def _waiting(**overrides) -> WaitingCondition:
    values = {
        "kind": "waiting_event",
        "cause": "等待条件",
        "condition_id": "wake.condition.1",
        "condition_kind": "file",
        "target_ref": "file:/tmp/result",
        "match_params": {"state": "exists"},
        "registered_at": "2026-07-26T08:00:00Z",
        "catchup_policy": "no_catchup",
    }
    values.update(overrides)
    return WaitingCondition(**values)


@pytest.mark.parametrize(
    ("waiting", "observation"),
    [
        (
            _waiting(condition_kind="time", target_ref="timer:morning", deadline="2026-07-26T09:00:00Z", catchup_policy="merge_once"),
            WakeObservation("time-1", WakeConditionKind.TIME, "clock", "2026-07-26T09:01:00Z", "timer", {}),
        ),
        (
            _waiting(kind="waiting_user", condition_kind=None, target_ref=None, match_params=None, correlation_id="question-1"),
            WakeObservation("input-1", WakeConditionKind.USER_INPUT, "question-1", "2026-07-26T09:00:00Z", "user", {}),
        ),
        (
            _waiting(condition_kind="host_event", target_ref="host:local", match_params={"event": "wake"}),
            WakeObservation("host-1", WakeConditionKind.HOST_EVENT, "host:local", "2026-07-26T09:00:00Z", "host", {"event": "wake"}),
        ),
        (
            _waiting(condition_kind="process", target_ref="process:123", match_params={"state": "exited", "start_identity": "a"}),
            WakeObservation("process-1", WakeConditionKind.OBSERVED_STATE, "process:123", "2026-07-26T09:00:00Z", "process", {"state_kind": "process", "state": "exited", "start_identity": "a"}),
        ),
        (
            _waiting(condition_kind="manual", target_ref="task:1", match_params=None),
            WakeObservation("manual-1", WakeConditionKind.MANUAL, "task:1", "2026-07-26T09:00:00Z", "user", {}),
        ),
    ],
)
def test_supported_conditions_match(
    waiting: WaitingCondition,
    observation: WakeObservation,
) -> None:
    condition = condition_from_waiting("task-1", waiting)
    assert condition is not None
    assert matches_condition(condition, observation)


def test_time_condition_does_not_match_before_deadline() -> None:
    condition = condition_from_waiting(
        "task-1",
        _waiting(
            condition_kind="time",
            target_ref="timer:morning",
            deadline="2026-07-26T09:00:00Z",
            catchup_policy="merge_once",
        ),
    )
    assert condition is not None

    assert not matches_condition(
        condition,
        WakeObservation(
            "time-early",
            WakeConditionKind.TIME,
            "clock",
            "2026-07-26T08:59:59Z",
            "timer",
            {},
        ),
    )


def test_observation_before_registration_is_stale() -> None:
    condition = condition_from_waiting("task-1", _waiting())
    assert condition is not None

    assert not matches_condition(
        condition,
        WakeObservation(
            "file-before-registration",
            WakeConditionKind.OBSERVED_STATE,
            "file:/tmp/result",
            "2026-07-26T07:59:59Z",
            "file-observer",
            {"state_kind": "file", "state": "exists"},
        ),
    )


def test_unknown_condition_kind_is_not_automatically_observed() -> None:
    assert condition_from_waiting(
        "task-1",
        _waiting(condition_kind="third_party_magic"),
    ) is None
