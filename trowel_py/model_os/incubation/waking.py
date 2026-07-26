"""把公共 WakeObservation 消费为 IncubationPlan 的单轮 ready 事实。"""

from __future__ import annotations

import json

from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    Provenance,
    TaskStatus,
)
from trowel_py.model_os.waking import (
    WakeCatchupPolicy,
    WakeCondition,
    WakeConditionKind,
    WakeDisposition,
    WakeEvent,
    WakeObservation,
)
from trowel_py.model_os.waking.matcher import matches_condition

from .codec import decode_wake_event, digest, encode_wake_event
from .models import POLICY_VERSION, parse_instant


def wake_conditions(repository) -> tuple[WakeCondition, ...]:
    rows = repository._conn.execute(
        "SELECT * FROM incubation_plans WHERE status='pending_wake' "
        "ORDER BY created_at, plan_id"
    ).fetchall()
    conditions: list[WakeCondition] = []
    for row in rows:
        conditions.append(_condition(row))
        if row["deadline"] is not None:
            conditions.append(_deadline_condition(row))
    return tuple(conditions)


def consume_wake(
    repository, observation: WakeObservation
) -> tuple[WakeEvent, ...]:
    store = repository._store
    conn = repository._conn
    with store._tx():
        prior = conn.execute(
            "SELECT wake_json, observation_fingerprint FROM incubation_wakes "
            "WHERE observation_id=? ORDER BY consumed_at, plan_id",
            (observation.observation_id,),
        ).fetchall()
        if prior:
            if any(
                row["observation_fingerprint"] != observation.fingerprint
                for row in prior
            ):
                raise ValueError("observation_id reused with different content")
            return tuple(decode_wake_event(row["wake_json"]) for row in prior)

        rows = conn.execute(
            "SELECT * FROM incubation_plans WHERE status='pending_wake' "
            "ORDER BY created_at, plan_id"
        ).fetchall()
        consumed: list[WakeEvent] = []
        for row in rows:
            if not _matches(row, observation):
                continue
            snapshot = store.replay()
            task = next(
                (item for item in snapshot.tasks if item.task_id == row["task_id"]),
                None,
            )
            if (
                task is None
                or task.status is not TaskStatus.INCUBATING
                or task.waiting_condition is None
                or task.waiting_condition.condition_id
                != f"incubation:{row['plan_id']}"
            ):
                repository._fail_before_run(
                    row,
                    "task_changed",
                    observation.observed_at,
                    restore_task=False,
                )
                continue
            if row["deadline"] is not None and parse_instant(
                row["deadline"], "deadline"
            ) <= parse_instant(observation.observed_at, "observed_at"):
                repository._fail_before_run(
                    row, "deadline_expired", observation.observed_at
                )
                continue

            wake = _wake(row, observation)
            encoded = encode_wake_event(wake)
            conn.execute(
                "UPDATE incubation_plans SET status='ready', cycle=1, updated_at=? "
                "WHERE plan_id=? AND status='pending_wake'",
                (observation.observed_at, row["plan_id"]),
            )
            conn.execute(
                "INSERT INTO incubation_wakes VALUES (?,?,?,?,?,?,?)",
                (
                    wake.dedupe_key,
                    observation.observation_id,
                    observation.fingerprint,
                    row["plan_id"],
                    1,
                    encoded,
                    observation.observed_at,
                ),
            )
            store._insert_event_in_tx(
                EventEnvelope(
                    event_id=wake.wake_id,
                    kind=EventKind.WAKE_CONSUMED,
                    occurred_at=observation.observed_at,
                    source=observation.source,
                    provenance=Provenance.MACHINE_OBSERVATION,
                    policy_version=POLICY_VERSION,
                    payload=json.loads(encoded),
                    work_item_id=row["work_item_id"],
                    task_id=row["task_id"],
                    correlation_id=f"incubation:{row['plan_id']}:1",
                )
            )
            consumed.append(wake)
        return tuple(consumed)


def _condition(row) -> WakeCondition:
    raw = json.loads(row["wake_condition_json"])
    return WakeCondition(
        condition_id=f"incubation:{row['plan_id']}",
        task_id=row["task_id"],
        kind=WakeConditionKind(raw["kind"]),
        target_ref=raw["target_ref"],
        match_params=raw["match_params"],
        due_at=raw["due_at"],
        catchup_policy=(
            WakeCatchupPolicy.MERGE_ONCE
            if raw["kind"] == WakeConditionKind.TIME.value
            else WakeCatchupPolicy.NO_CATCHUP
        ),
        registered_at=row["created_at"],
    )


def _deadline_condition(row) -> WakeCondition:
    return WakeCondition(
        condition_id=f"incubation-deadline:{row['plan_id']}",
        task_id=row["task_id"],
        kind=WakeConditionKind.TIME,
        target_ref="clock",
        match_params={},
        due_at=row["deadline"],
        catchup_policy=WakeCatchupPolicy.MERGE_ONCE,
        registered_at=row["created_at"],
    )


def _matches(row, observation: WakeObservation) -> bool:
    if (
        row["deadline"] is not None
        and observation.kind is WakeConditionKind.TIME
        and observation.observation_id
        == f"time:incubation-deadline:{row['plan_id']}:{row['deadline']}"
    ):
        return True
    if observation.kind in {WakeConditionKind.USER_INPUT, WakeConditionKind.MANUAL}:
        if observation.target_ref in {
            row["plan_id"],
            row["task_id"],
            f"incubation:{row['plan_id']}",
        }:
            return True
    return matches_condition(_condition(row), observation)


def _wake(row, observation: WakeObservation) -> WakeEvent:
    dedupe = digest(f"{row['plan_id']}\0cycle-1")
    return WakeEvent(
        wake_id=f"wake.incubation.{dedupe}",
        condition_id=f"incubation:{row['plan_id']}",
        observation_id=observation.observation_id,
        task_id=row["task_id"],
        episode_id=None,
        dedupe_key=dedupe,
        observed_at=observation.observed_at,
        source=observation.source,
        catchup_policy=WakeCatchupPolicy.MERGE_ONCE,
        disposition=WakeDisposition.READY,
        observation_fingerprint=observation.fingerprint,
    )
