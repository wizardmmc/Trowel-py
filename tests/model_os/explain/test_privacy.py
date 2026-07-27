from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest

from trowel_py.model_os.journal import InvalidJournalCursor, JournalIdentityConflict
from trowel_py.model_os.observation import ReplayStatus, replay_policy_decision
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)


SECRETS = (
    "sk-EXAMPLE-1234567890",
    "Bearer abc.def.secret",
    "eyJhbGciOiJIUzI1NiJ9.payload.signature",
    "http://127.0.0.1:7897/private",
)


def _event(event_id: str, payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=EventKind.NOTE,
        occurred_at="2026-07-25T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v1",
        payload=payload,
    )


def test_embedded_secrets_and_private_text_never_leave_default_reads(
    store: ModelOsStore,
) -> None:
    payload = {
        "note": " | ".join(f"prefix {value} suffix" for value in SECRETS),
        "prompt": "private prompt",
        "thinking": "private thinking",
        "private_chat": "private chat",
        "content": {"nested": ["private nested body"]},
    }
    store.append_event(_event("event.private", payload))
    decision = DecisionRecord(
        decision_id="decision.private",
        kind="route",
        disposition=DecisionDisposition.NO_ACTION,
        decided_at="2026-07-25T00:00:01Z",
        signals={"refs": ["event.private"]},
        candidates=["fast", "deep"],
        choice="fast",
        reason="default_fast",
        policy_version="v1",
    )
    store.append_decision(decision)

    raw_event = store.list_events()[0][1]
    page_text = json.dumps(asdict(store.read_journal_page(limit=10)))
    explain_text = json.dumps(asdict(store.explain_decision(decision.decision_id)))
    raw_text = json.dumps(raw_event.payload)
    for secret in (
        *SECRETS,
        "private prompt",
        "private thinking",
        "private chat",
        "private nested body",
    ):
        assert secret not in raw_text
        assert secret not in page_text
        assert secret not in explain_text


def test_errors_and_cursor_do_not_echo_private_content(store: ModelOsStore) -> None:
    first = _event("event.conflict", {"note": f"prefix {SECRETS[0]} suffix"})
    store.append_event(first)
    with pytest.raises(JournalIdentityConflict) as conflict:
        store.append_event(replace(first, payload={"note": "private body changed"}))
    assert SECRETS[0] not in str(conflict.value)
    assert "private body changed" not in str(conflict.value)

    for bad_cursor in (SECRETS[0], "private cursor body", "x" * 4097):
        with pytest.raises(InvalidJournalCursor) as invalid:
            store.read_journal_page(cursor=bad_cursor)
        assert bad_cursor not in str(invalid.value)

    unsafe_time = replace(
        first,
        event_id="event.unsafe-time",
        occurred_at=f"prefix {SECRETS[0]} suffix",
    )
    with pytest.raises(ValueError) as invalid_time:
        store.append_event(unsafe_time)
    assert SECRETS[0] not in str(invalid_time.value)


@pytest.mark.parametrize(
    "field_value",
    [
        "private free text",
        "prefix sk-EXAMPLE-1234567890 suffix",
        "prefix Bearer abc.secret suffix",
    ],
)
def test_decision_validation_error_does_not_echo_rejected_value(
    store: ModelOsStore, field_value: str
) -> None:
    decision = DecisionRecord(
        decision_id="decision.invalid",
        kind="route",
        disposition=DecisionDisposition.NO_ACTION,
        decided_at="2026-07-25T00:00:00Z",
        signals={"refs": []},
        candidates=[field_value],
        choice="fast",
        reason="default_fast",
        policy_version="v1",
    )
    with pytest.raises(ValueError) as caught:
        store.append_decision(decision)
    assert field_value not in str(caught.value)


def test_public_read_models_fail_closed_for_unsafe_legacy_metadata(
    store: ModelOsStore,
) -> None:
    """旧库或内部写入绕过新门禁时，公开读模型仍不得回显原值。"""

    assert store._conn is not None
    secret = "sk-EXAMPLE-legacy-secret"
    private_ref = "/Users/alice/private/session.jsonl"
    store._conn.execute(
        "INSERT INTO events (event_id, kind, occurred_at, source, provenance, "
        "policy_version, work_item_id, task_id, outcome, payload, payload_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "private event id",
            f"prefix Bearer {secret} suffix",
            "2026-07-25T00:00:00Z",
            "internal",
            "machine_observation",
            secret,
            private_ref,
            "task.private",
            secret,
            json.dumps({"evidence_ref": private_ref}),
            "sha256:legacy",
        ),
    )
    store._conn.execute(
        "INSERT INTO decisions (decision_id, kind, disposition, decided_at, "
        "task_id, policy_version, signals, candidates, choice, reason, "
        "identity_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "decision.private.metadata",
            f"prefix Bearer {secret} suffix",
            "no_action",
            "2026-07-25T00:00:01Z",
            "task.private",
            secret,
            json.dumps({"refs": [private_ref]}),
            json.dumps([{"prompt": "private legacy prompt"}]),
            "private legacy choice",
            "private legacy reason",
            "sha256:legacy-metadata",
        ),
    )
    store._conn.execute(
        "INSERT INTO decisions (decision_id, kind, disposition, decided_at, "
        "task_id, policy_version, signals, candidates, choice, reason, "
        "identity_hash) VALUES (?, 'work_broker.usage', 'no_action', ?, ?, ?, "
        "'{\"refs\":[]}', ?, 'recorded', 'usage_observed', ?)",
        (
            "decision.private.usage",
            "2026-07-25T00:00:02Z",
            "task.private",
            secret,
            json.dumps(
                [{"role": "usage", "cost": 1.0, "cost_source": secret}]
            ),
            "sha256:legacy-usage",
        ),
    )
    store._conn.commit()

    page = store.read_journal_page(limit=20)
    decision = store.explain_decision("decision.private.metadata")
    scope = store.explain_scope("task", "task.private")
    replay = replay_policy_decision(store, "decision.private.metadata")
    metrics = store.read_metrics(
        window_start="2026-07-24T00:00:00+00:00",
        window_end="2026-07-26T00:00:00+00:00",
    )
    public_text = json.dumps(
        {
            "page": asdict(page),
            "decision": asdict(decision),
            "scope": asdict(scope),
            "replay": asdict(replay),
            "metrics": asdict(metrics),
        },
        default=str,
    )

    assert secret not in public_text
    assert private_ref not in public_text
    assert "private legacy prompt" not in public_text
    assert "private legacy choice" not in public_text
    assert "private legacy reason" not in public_text
    assert decision.disposition == "legacy_unknown"
    assert replay.status is ReplayStatus.UNSUPPORTED
    reliability = next(
        item for item in metrics.dimensions if item.name == "reliability"
    )
    assert reliability.cost.sources == ("unknown",)
