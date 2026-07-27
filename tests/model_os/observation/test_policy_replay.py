from __future__ import annotations

from trowel_py.model_os.journal import JournalBoundary
from trowel_py.model_os.observation import (
    DEFAULT_REPLAY_REGISTRY,
    PolicyReplayRegistry,
    ReplayedDecision,
    ReplayEvaluation,
    ReplayStatus,
    replay_policy_decision,
)
from trowel_py.model_os.routing import (
    RouteCandidate,
    RouteInput,
    RouteMode,
    UserRoutePreference,
    decide_route,
    record_route_decision,
)
from trowel_py.model_os.scheduling import (
    ScheduleCandidate,
    ScheduleInput,
    decide_schedule,
    record_schedule_decision,
)
from trowel_py.model_os.types import DecisionDisposition, DecisionRecord
from trowel_py.model_os.work_broker import ModelTier


def _candidate(
    task_id: str,
    *,
    created_at: str,
    priority: int = 0,
) -> ScheduleCandidate:
    return ScheduleCandidate(
        work_item_id=f"work.{task_id}",
        task_id=task_id,
        priority=priority,
        warm_rank=0,
        created_at=created_at,
        ready_epoch_ref=f"ready.{task_id}",
        virtual_service_segments=0,
    )


def test_attention_replay_uses_frozen_input_without_scan_or_write(
    store,
    monkeypatch,
) -> None:
    schedule_input = ScheduleInput(
        trigger_event_ref="event.wake.1",
        journal_boundary=JournalBoundary(event_seq=17, decision_seq=4),
        candidates=(
            _candidate("task.a", created_at="2026-07-26T00:00:01Z"),
            _candidate("task.b", created_at="2026-07-26T00:00:00Z"),
        ),
    )
    recorded = record_schedule_decision(store, decide_schedule(schedule_input))
    before = store.journal_boundary()

    def reject_full_scan(*args, **kwargs):
        raise AssertionError("policy replay must not materialize the decision ledger")

    monkeypatch.setattr(store, "list_decisions", reject_full_scan)
    report = replay_policy_decision(store, recorded.decision_id)

    assert report.status is ReplayStatus.MATCHED
    assert report.recorded.choice == "dispatch"
    assert report.recorded.target_task_id == "task.b"
    assert report.replayed == report.recorded
    assert report.input_refs == ("event.wake.1",)
    assert report.missing_fields == ()
    assert store.journal_boundary() == before


def test_attention_replay_preserves_user_override_and_original_boundary(store) -> None:
    schedule_input = ScheduleInput(
        trigger_event_ref="event.user.override.1",
        journal_boundary=JournalBoundary(event_seq=21, decision_seq=8),
        candidates=(
            _candidate("task.a", created_at="2026-07-26T00:00:00Z"),
            _candidate("task.b", created_at="2026-07-26T00:00:01Z"),
        ),
        current_foreground_task_id="task.a",
        previous_foreground_task_id="task.b",
        user_override_task_id="task.b",
    )
    recorded = record_schedule_decision(store, decide_schedule(schedule_input))

    report = replay_policy_decision(store, recorded.decision_id)

    assert report.status is ReplayStatus.MATCHED
    assert report.replayed.reason_code == "user_override_switch"
    assert report.replayed.target_task_id == "task.b"
    assert report.input_boundary == JournalBoundary(event_seq=21, decision_seq=8)


def test_attention_replay_preserves_original_offset_string_sorting(store) -> None:
    schedule_input = ScheduleInput(
        trigger_event_ref="event.wake.offset",
        journal_boundary=JournalBoundary(event_seq=22, decision_seq=9),
        candidates=(
            _candidate("task.a", created_at="2026-07-26T01:00:00+01:00"),
            _candidate("task.b", created_at="2026-07-26T00:30:00+00:00"),
        ),
    )
    original = decide_schedule(schedule_input)
    recorded = record_schedule_decision(store, original)

    report = replay_policy_decision(store, recorded.decision_id)

    assert original.target_task_id == "task.b"
    assert report.status is ReplayStatus.MATCHED
    assert report.replayed == report.recorded


def test_legacy_attention_record_with_hashed_sort_input_is_unavailable(store) -> None:
    store.append_decision(
        DecisionRecord(
            decision_id="decision.attention.legacy",
            kind="attention.schedule",
            disposition=DecisionDisposition.NO_ACTION,
            decided_at="2026-07-26T00:00:00Z",
            signals={"refs": ["event.wake.legacy"]},
            candidates=[
                {
                    "work_item_id": "work.a",
                    "task_id": "task.a",
                    "suspended_episode_id": None,
                    "priority": 0,
                    "warm_rank": 0,
                    "created_at_ref": "sha256:abcdef123456",
                    "ready_epoch_ref": "ready.a",
                    "virtual_service_segments": 0,
                    "journal_event_seq": 1,
                }
            ],
            choice="continue",
            reason="active_focus",
            policy_version="attention-v0",
            task_id="task.a",
        )
    )

    report = replay_policy_decision(store, "decision.attention.legacy")

    assert report.status is ReplayStatus.UNAVAILABLE
    assert set(report.missing_fields) == {
        "candidates.created_at",
        "current_foreground_task_id",
        "journal_boundary.decision_seq",
    }
    assert report.replayed is None


def test_malformed_frozen_timestamp_is_unavailable_instead_of_raising(store) -> None:
    recorded = record_schedule_decision(
        store,
        decide_schedule(
            ScheduleInput(
                trigger_event_ref="event.wake.malformed",
                journal_boundary=JournalBoundary(),
                candidates=(
                    _candidate("task.a", created_at="2026-07-26T00:00:00Z"),
                ),
            )
        ),
    )
    assert store._conn is not None
    store._conn.execute(
        "UPDATE decisions SET candidates=replace(candidates, ?, ?) "
        "WHERE decision_id=?",
        (
            "MjAyNi0wNy0yNlQwMDowMDowMFo",
            "not-valid-base64!",
            recorded.decision_id,
        ),
    )
    store._conn.commit()

    report = replay_policy_decision(store, recorded.decision_id)

    assert report.status is ReplayStatus.UNAVAILABLE
    assert report.missing_fields == ("schedule_input.invalid",)


def test_route_replay_reconstructs_structured_input_without_side_effects(store) -> None:
    route_input = RouteInput(
        work_item_id="work.route",
        task_id="task.route",
        runtime="codex",
        mode=RouteMode.SHADOW,
        user_preference=UserRoutePreference.AUTO,
        mandatory_markers=(),
        trusted_pre_route_markers=(),
        trusted_outcomes=(),
        previous_tier=None,
        fixed_model="baseline-model",
        fixed_effort="medium",
        candidates=(
            RouteCandidate(ModelTier.FAST, "fast-model", "low"),
            RouteCandidate(ModelTier.DEEP, "deep-model", "high"),
        ),
        input_fact_refs=("event.route.input.1",),
        evaluation_domain="coding",
        canary_approved=False,
    )
    recorded = record_route_decision(
        store,
        "route-replay-1",
        route_input,
        decide_route(route_input),
    )
    before = store.journal_boundary()

    report = replay_policy_decision(store, recorded.decision_id)

    assert report.status is ReplayStatus.MATCHED
    assert report.recorded.choice == "fast"
    assert report.replayed == report.recorded
    assert report.input_refs == ("event.route.input.1",)
    assert store.journal_boundary() == before


def test_replay_rejects_unregistered_policy_version_without_guessing(store) -> None:
    recorded = record_schedule_decision(
        store,
        decide_schedule(
            ScheduleInput(
                trigger_event_ref="event.wake.2",
                journal_boundary=JournalBoundary(),
                candidates=(),
            )
        ),
    )

    report = replay_policy_decision(
        store,
        recorded.decision_id,
        policy_version="attention-future-unregistered",
    )

    assert report.status is ReplayStatus.UNSUPPORTED
    assert report.replayed is None


def test_registered_new_policy_returns_structured_difference_without_writes(
    store,
) -> None:
    recorded = record_schedule_decision(
        store,
        decide_schedule(
            ScheduleInput(
                trigger_event_ref="event.policy.compare",
                journal_boundary=JournalBoundary(event_seq=8, decision_seq=3),
                candidates=(
                    _candidate(
                        "task.oldest",
                        created_at="2026-07-26T00:00:00Z",
                        priority=0,
                    ),
                    _candidate(
                        "task.priority",
                        created_at="2026-07-26T00:00:01Z",
                        priority=10,
                    ),
                ),
            )
        ),
    )

    def oldest_first(record):
        rows = [
            row
            for row in record.candidates
            if isinstance(row, dict) and row.get("role", "candidate") == "candidate"
        ]
        chosen = min(rows, key=lambda row: int(row["priority"]))
        return ReplayEvaluation(
            ReplayedDecision(
                choice="dispatch",
                reason_code="oldest_first",
                target_work_item_id=chosen["work_item_id"],
                target_task_id=chosen["task_id"],
                target_episode_id=chosen["suspended_episode_id"],
            ),
            input_boundary=JournalBoundary(event_seq=8, decision_seq=3),
        )

    registry: PolicyReplayRegistry = DEFAULT_REPLAY_REGISTRY.with_policy(
        "attention.schedule",
        "attention-oldest-test-v1",
        oldest_first,
    )
    before = store.journal_boundary()
    report = replay_policy_decision(
        store,
        recorded.decision_id,
        policy_version="attention-oldest-test-v1",
        registry=registry,
    )

    assert report.status is ReplayStatus.DIFFERENT
    assert report.recorded.target_task_id == "task.priority"
    assert report.replayed.target_task_id == "task.oldest"
    assert report.recorded.reason_code == "fair_service"
    assert report.replayed.reason_code == "oldest_first"
    assert store.journal_boundary() == before
