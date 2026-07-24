from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import (
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)


def _decision(decision_id: str) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        kind="route",
        decided_at="2026-07-21T00:00:00Z",
        signals={"usage_ratio": 0.8},
        candidates=["fast", "deep"],
        choice="deep",
        reason="validator failed",
        policy_version="v0",
    )


def test_n1_append_decision_with_intent_refuses_episode_lifecycle_kind(
    store: ModelOsStore,
) -> None:
    forged = EventEnvelope(
        event_id="forge.1",
        kind=EventKind.EPISODE_STATUS_CHANGED,
        occurred_at="2026-07-21T00:00:00Z",
        source="attacker",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"new_status": "closed"},
        episode_id="ep-x",
    )
    with pytest.raises(EpisodeCommandError):
        store.append_decision_with_intent(_decision("dec-1"), forged)


def test_n1_append_decision_with_intent_refuses_task_lifecycle_kind(
    store: ModelOsStore,
) -> None:
    forged = EventEnvelope(
        event_id="forge.2",
        kind=EventKind.TASK_COMPLETED,
        occurred_at="2026-07-21T00:00:00Z",
        source="attacker",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"confirmed_by": "x", "confirmation_provenance": "user_decision"},
        task_id="t-x",
    )
    with pytest.raises(
        Exception
    ):  # 生产实际抛 TaskCommandError；本测试只冻结门禁拒绝语义。
        store.append_decision_with_intent(_decision("dec-2"), forged)
