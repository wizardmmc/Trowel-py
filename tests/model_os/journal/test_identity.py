from __future__ import annotations

from dataclasses import replace
import json

import pytest

from trowel_py.model_os.journal import JournalIdentityConflict
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import DecisionRecord, DecisionDisposition
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance
from trowel_py.model_os import store as store_module


def _decision(decision_id: str = "decision.route") -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        kind="route",
        disposition=DecisionDisposition.NO_ACTION,
        decided_at="2026-07-25T00:00:00Z",
        signals={"refs": ["event.context.high"]},
        candidates=["fast", "deep"],
        choice="deep",
        reason="context_high",
        policy_version="router-v1",
        budget_before={"calls": 8},
        budget_after={"calls": 7},
        work_item_id="work.1",
        task_id="task.1",
        episode_id="episode.1",
        cause_id="event.context.high",
        correlation_id=None,
    )


def test_decision_retry_ignores_only_decided_at(store: ModelOsStore) -> None:
    first = _decision()
    seq = store.append_decision(first)

    assert store.append_decision(
        replace(first, decided_at="2026-07-25T00:10:00Z")
    ) == seq


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "wake"),
        ("disposition", DecisionDisposition.SHADOW),
        ("work_item_id", "work.2"),
        ("task_id", "task.2"),
        ("episode_id", "episode.2"),
        ("cause_id", "event.other"),
        ("policy_version", "router-v2"),
        ("signals", {"refs": ["event.other"]}),
        ("candidates", ["fast"]),
        ("choice", "fast"),
        ("reason", "default_fast"),
        ("budget_before", {"calls": 9}),
        ("budget_after", {"calls": 6}),
    ],
)
def test_decision_semantic_field_change_conflicts(
    store: ModelOsStore, field: str, value: object
) -> None:
    first = _decision()
    store.append_decision(first)

    with pytest.raises(JournalIdentityConflict) as caught:
        store.append_decision(replace(first, **{field: value}))

    assert caught.value.entry_type == "decision"
    assert caught.value.entry_id == first.decision_id
    assert "context_high" not in str(caught.value)


@pytest.mark.parametrize(
    "change",
    [
        {"reason": "contains spaces"},
        {"reason": "UPPERCASE"},
        {"signals": {"usage": 0.9}},
        {"signals": {"refs": ["event.1"], "body": "private"}},
        {"candidates": ["free text is forbidden"]},
        {"choice": "Bearer secret"},
        {"budget_before": {"note": "private body"}},
        {"disposition": DecisionDisposition.LEGACY_UNKNOWN},
    ],
)
def test_new_decision_rejects_unstructured_or_legacy_content(
    store: ModelOsStore, change: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        store.append_decision(replace(_decision(), **change))


def test_event_identity_compares_payload_even_if_hash_collides(
    store: ModelOsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def colliding_payload(payload: dict[str, object]) -> tuple[str, str]:
        return json.dumps(payload, sort_keys=True), "sha256:forced-collision"

    monkeypatch.setattr(store_module, "_payload_json", colliding_payload)
    first = EventEnvelope(
        event_id="event.collision",
        kind=EventKind.NOTE,
        occurred_at="2026-07-25T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v1",
        payload={"value": 1},
    )
    store.append_event(first)

    with pytest.raises(JournalIdentityConflict):
        store.append_event(replace(first, payload={"value": 2}))
