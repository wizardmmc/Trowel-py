from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.model_os.cognitive_signals import InMemorySignalAuthorityRegistry
from trowel_py.model_os.routing import CognitiveSignalBridge
from trowel_py.model_os.routing import (
    CognitiveRouter,
    RouteCandidate,
    RouteMode,
    RouteRequest,
    RouteReason,
    RouteReviewClass,
    RoutingConfig,
    record_route_actual,
    record_route_review,
)
from trowel_py.model_os.work_broker import ModelTier
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def test_real_cc_terminal_fixture_reaches_production_signal_projection(
    tmp_path: Path,
) -> None:
    fixture = json.loads(
        Path("tests/fixtures/model-os-signals-083.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    authority = InMemorySignalAuthorityRegistry()
    store = ModelOsStore(tmp_path / "model-os.db", signal_authority=authority)
    store.open()
    try:
        task = store.create_task_from_user_request(
            original_goal="匿名真实信号任务",
            idempotency_key="route-signal-task",
        )
        store.promote_to_warm(task.task_id)
        episode, lease = store.start_episode(
            work_item_id=task.primary_work_item_id,
            owner="signal-test",
            ttl_seconds=600,
            idempotency_key="route-signal-episode",
            task_id=task.task_id,
        )
        store.bind_episode_runtime(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            agent_session_id="agent-real-fixture",
            runtime="claude_code",
            native_session_id="native-real-fixture",
            runtime_generation="generation-real-fixture",
            runtime_pid=None,
            runtime_pgid=None,
            correlation_id="route-signal-test",
            activate=True,
        )
        bridge = CognitiveSignalBridge(store, authority)
        bridge.observe(
            {
                "schema": "agent-event-v1",
                "session_id": "agent-real-fixture",
                "runtime": "claude_code",
                "seq": 1,
                "type": "turn_start",
                "turn_id": "turn-real-fixture",
                "item_id": None,
                "payload": {},
            },
            runtime_generation="generation-real-fixture",
        )
        atom = fixture["atoms"][0]
        assert atom == {"type": "result", "subtype": "success", "is_error": False}
        bridge.observe(
            {
                "schema": "agent-event-v1",
                "session_id": "agent-real-fixture",
                "runtime": "claude_code",
                "seq": 2,
                "type": "finished",
                "turn_id": None,
                "item_id": None,
                "payload": {},
            },
            runtime_generation="generation-real-fixture",
        )

        page = store.signals_for_task(
            task.task_id,
            as_of="2099-01-01T00:00:00+00:00",
            limit=10,
        )
        assert len(page.items) == 1
        assert page.items[0].kind.subtype == "success"
    finally:
        store.close()


def _bound_task(store):
    task = store.create_task_from_user_request(
        original_goal="匿名路由信号任务",
        idempotency_key="route-signal-task",
    )
    store.promote_to_warm(task.task_id)
    episode, lease = store.start_episode(
        work_item_id=task.primary_work_item_id,
        owner="signal-test",
        ttl_seconds=600,
        idempotency_key="route-signal-episode",
        task_id=task.task_id,
    )
    store.bind_episode_runtime(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        agent_session_id="agent-route-signal",
        runtime="claude_code",
        native_session_id="native-route-signal",
        runtime_generation="generation-route-signal",
        runtime_pid=None,
        runtime_pgid=None,
        correlation_id="route-signal-test",
        activate=True,
    )
    return task, episode


def _router(store) -> CognitiveRouter:
    return CognitiveRouter(
        store,
        RoutingConfig(
            RouteMode.SHADOW,
            {
                "claude_code": (
                    RouteCandidate(ModelTier.FAST, "haiku", "low"),
                    RouteCandidate(ModelTier.DEEP, "opus", "high"),
                )
            },
        ),
    )


def _observe(bridge, *, seq: int, event_type: str, item_id=None, payload=None):
    bridge.observe(
        {
            "schema": "agent-event-v1",
            "session_id": "agent-route-signal",
            "runtime": "claude_code",
            "seq": seq,
            "type": event_type,
            "turn_id": "turn-route-signal",
            "item_id": item_id,
            "payload": payload or {},
        },
        runtime_generation="generation-route-signal",
    )


def test_real_validator_failure_upgrades_but_permission_signal_does_not(
    tmp_path: Path,
) -> None:
    for scenario in ("validator", "permission"):
        authority = InMemorySignalAuthorityRegistry()
        store = ModelOsStore(tmp_path / f"{scenario}.db", signal_authority=authority)
        store.open()
        try:
            task, episode = _bound_task(store)
            bridge = CognitiveSignalBridge(store, authority)
            _observe(bridge, seq=1, event_type="turn_start")
            if scenario == "validator":
                _observe(
                    bridge,
                    seq=2,
                    event_type="tool_call",
                    item_id="tool-validator",
                    payload={
                        "input": {
                            "command": ".venv/bin/python -m pytest -q tests/model_os"
                        }
                    },
                )
                _observe(
                    bridge,
                    seq=3,
                    event_type="tool_result",
                    item_id="tool-validator",
                    payload={"exit_code": 1, "is_error": True},
                )
                terminal_seq = 4
            else:
                _observe(bridge, seq=2, event_type="approval_request")
                terminal_seq = 3
            _observe(bridge, seq=terminal_seq, event_type="finished")

            recorded = _router(store).route(
                idempotency_key=f"next-{scenario}",
                work_item_id=task.primary_work_item_id,
                task_id=task.task_id,
                previous_episode_id=episode.episode_id,
                runtime="claude_code",
                fixed_model="sonnet",
                fixed_effort="medium",
                request=RouteRequest(),
            )

            expected = (
                RouteReason.VALIDATOR_FAILURE
                if scenario == "validator"
                else RouteReason.DEFAULT_FAST
            )
            assert recorded.decision.reason is expected
        finally:
            store.close()


def test_real_cc_successful_validator_without_exit_code_is_trusted(
    tmp_path: Path,
) -> None:
    fixture = json.loads(
        Path("tests/fixtures/model-os-routing-094.jsonl").read_text(encoding="utf-8")
    )
    assert fixture["provenance"] == "real_recording"
    authority = InMemorySignalAuthorityRegistry()
    store = ModelOsStore(tmp_path / "validator-success.db", signal_authority=authority)
    store.open()
    try:
        task, _episode = _bound_task(store)
        bridge = CognitiveSignalBridge(store, authority)
        _observe(bridge, seq=1, event_type="turn_start")
        _observe(
            bridge,
            seq=2,
            event_type="tool_call",
            item_id="tool-validator-success",
            payload={
                "input": {
                    "command": ".venv/bin/python -m ruff check "
                    "trowel_py/model_os/routing/models.py"
                }
            },
        )
        _observe(
            bridge,
            seq=3,
            event_type="tool_result",
            item_id="tool-validator-success",
            payload=fixture["atoms"][0],
        )

        page = store.signals_for_task(
            task.task_id,
            as_of="2099-01-01T00:00:00+00:00",
            limit=10,
        )

        validator = next(
            item for item in page.items if item.kind.family.value == "validator_outcome"
        )
        assert validator.kind.subtype == "pass"
        assert validator.payload.exit_code == 0

        unrelated = _router(store).route(
            idempotency_key="unrelated-route",
            work_item_id=task.primary_work_item_id,
            task_id="another-task",
            previous_episode_id=None,
            runtime="claude_code",
            fixed_model="haiku",
            fixed_effort="low",
            request=RouteRequest(),
        )
        record_route_actual(
            store,
            unrelated.decision_id,
            episode_id=_episode.episode_id,
            model="haiku",
            effort="low",
            tier=ModelTier.FAST,
            evidence_ref="binding-unrelated",
        )
        store.append_event(
            EventEnvelope(
                event_id="event.episode.start.result.unrelated",
                kind=EventKind.COMMAND_RESULT,
                occurred_at="2026-07-26T00:00:01Z",
                source="episode_runner",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version="episode-start-v1",
                payload={"result_code": "terminal_observed", "evidence_refs": []},
                work_item_id=task.primary_work_item_id,
                task_id=task.task_id,
                episode_id=_episode.episode_id,
                cause_id="decision.episode.start.unrelated",
                correlation_id="command.episode.start.unrelated",
            )
        )
        with pytest.raises(ValueError, match="does not belong"):
            record_route_review(
                store,
                unrelated.decision_id,
                classification=RouteReviewClass.CORRECT,
                trusted_verifier=True,
                evidence_refs=(validator.signal_id,),
                reviewer_ref="reviewer",
            )
    finally:
        store.close()
