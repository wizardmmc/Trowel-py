from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest

from trowel_py.model_os import store, store_journal_codec


def _event_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "event_id": "event-example",
        "kind": "note_appended",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "source": "test",
        "provenance": "unknown",
        "policy_version": "v0",
        "payload": "{}",
        "payload_hash": "stored-hash",
        "work_item_id": None,
        "task_id": None,
        "episode_id": None,
        "native_session_id": None,
        "cause_id": None,
        "correlation_id": None,
        "outcome": None,
        "lease_id": None,
        "owner": None,
        "fencing_token": None,
    }
    row.update(overrides)
    return row


def _decision_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "decision_id": "decision-example",
        "kind": "choose",
        "decided_at": "2026-01-01T00:00:00+00:00",
        "signals": "{}",
        "candidates": "[]",
        "choice": "continue",
        "reason": "test",
        "policy_version": "v0",
        "budget_before": None,
        "budget_after": None,
        "work_item_id": None,
        "task_id": None,
        "episode_id": None,
        "cause_id": None,
        "correlation_id": None,
    }
    row.update(overrides)
    return row


def test_journal_codec_facades_keep_complete_contracts() -> None:
    expected = {
        "_payload_json": "(payload: 'dict[str, Any]') -> 'tuple[str, str]'",
        "_dumps": "(value: 'Any') -> 'str'",
        "_event_params": (
            "(event: 'EventEnvelope', payload_text: 'str', "
            "payload_hash: 'str') -> 'tuple'"
        ),
        "_event_identity": ("(event: 'EventEnvelope', payload_hash: 'str') -> 'tuple'"),
        "_event_row_identity": ("(row: 'sqlite3.Row', payload_hash: 'str') -> 'tuple'"),
        "_decision_params": "(decision: 'DecisionRecord') -> 'tuple'",
        "_lease_from_row": "(row: 'sqlite3.Row') -> 'Lease'",
        "_event_from_row": "(row: 'sqlite3.Row') -> 'EventEnvelope'",
        "_decision_from_row": "(row: 'sqlite3.Row') -> 'DecisionRecord'",
    }

    for name, signature in expected.items():
        facade = getattr(store, name)
        implementation = getattr(store_journal_codec, name.removeprefix("_"))
        assert str(inspect.signature(facade)) == signature
        assert facade.__module__ == store.__name__
        assert facade.__qualname__ == name
        assert facade.__defaults__ is None
        assert facade.__kwdefaults__ is None
        assert facade is not implementation


def test_journal_codec_facades_inject_current_store_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
    results = {
        name: object()
        for name in (
            "payload_json",
            "dumps",
            "event_params",
            "event_identity",
            "event_row_identity",
            "decision_params",
            "lease_from_row",
            "event_from_row",
            "decision_from_row",
        )
    }

    def recorder(name: str):
        def record(*args: Any, **kwargs: Any) -> Any:
            seen[name] = (args, kwargs)
            return results[name]

        return record

    for name in results:
        monkeypatch.setattr(store, f"_run_{name}", recorder(name))

    redact_fn = object()
    dumps_fn = object()
    loads_fn = object()
    sha256_fn = object()
    str_type = object()
    int_fn = object()
    lease_type = object()
    event_type = object()
    decision_type = object()
    provenance_type = object()
    decision_dumps = object()
    monkeypatch.setattr(store, "redact_payload", redact_fn)
    monkeypatch.setattr(
        store,
        "json",
        SimpleNamespace(dumps=dumps_fn, loads=loads_fn),
    )
    monkeypatch.setattr(store, "hashlib", SimpleNamespace(sha256=sha256_fn))
    monkeypatch.setattr(store, "str", str_type, raising=False)
    monkeypatch.setattr(store, "int", int_fn, raising=False)
    monkeypatch.setattr(store, "Lease", lease_type)
    monkeypatch.setattr(store, "EventEnvelope", event_type)
    monkeypatch.setattr(store, "DecisionRecord", decision_type)
    monkeypatch.setattr(store, "Provenance", provenance_type)

    payload: dict[str, Any] = {}
    value = object()
    event = object()
    row = object()
    decision = object()

    assert store._payload_json(payload) is results["payload_json"]
    assert store._dumps(value) is results["dumps"]
    monkeypatch.setattr(store, "_dumps", decision_dumps)
    assert store._event_params(event, "payload", "hash") is results["event_params"]
    assert store._event_identity(event, "hash") is results["event_identity"]
    assert store._event_row_identity(row, "hash") is results["event_row_identity"]
    assert store._decision_params(decision) is results["decision_params"]
    assert store._lease_from_row(row) is results["lease_from_row"]
    assert store._event_from_row(row) is results["event_from_row"]
    assert store._decision_from_row(row) is results["decision_from_row"]

    assert seen == {
        "payload_json": (
            (payload,),
            {
                "redact_fn": redact_fn,
                "json_dumps": dumps_fn,
                "sha256_fn": sha256_fn,
                "str_type": str_type,
            },
        ),
        "dumps": (
            (value,),
            {
                "redact_fn": redact_fn,
                "json_dumps": dumps_fn,
                "str_type": str_type,
            },
        ),
        "event_params": ((event, "payload", "hash"), {}),
        "event_identity": ((event, "hash"), {}),
        "event_row_identity": ((row, "hash"), {"int_fn": int_fn}),
        "decision_params": (
            (decision,),
            {"dumps_fn": decision_dumps, "redact_fn": redact_fn},
        ),
        "lease_from_row": (
            (row,),
            {"lease_type": lease_type, "int_fn": int_fn},
        ),
        "event_from_row": (
            (row,),
            {
                "event_type": event_type,
                "provenance_type": provenance_type,
                "json_loads": loads_fn,
                "int_fn": int_fn,
            },
        ),
        "decision_from_row": (
            (row,),
            {"decision_type": decision_type, "json_loads": loads_fn},
        ),
    }


def test_event_decoder_preserves_field_error_priority() -> None:
    invalid_provenance = {
        "event_id": "event-example",
        "kind": "note_appended",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "source": "test",
        "provenance": "not-a-provenance",
    }
    with pytest.raises(ValueError, match="not-a-provenance"):
        store._event_from_row(invalid_provenance)  # type: ignore[arg-type]

    malformed_payload = {
        **invalid_provenance,
        "provenance": "unknown",
        "policy_version": "v0",
        "payload": "{",
    }
    with pytest.raises(json.JSONDecodeError):
        store._event_from_row(malformed_payload)  # type: ignore[arg-type]


def test_event_row_identity_uses_stored_hash() -> None:
    identity = store._event_row_identity(  # type: ignore[arg-type]
        _event_row(),
        "argument-hash",
    )

    assert identity[11] == "stored-hash"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, None),
        ("{}", {}),
        ("", None),
        ("0", 0),
        ("false", False),
    ],
)
def test_decision_decoder_preserves_budget_truthiness(
    stored: str | None,
    expected: object,
) -> None:
    decision = store._decision_from_row(  # type: ignore[arg-type]
        _decision_row(budget_before=stored, budget_after=stored)
    )

    assert decision.budget_before == expected
    assert decision.budget_after == expected


def test_event_and_lease_fencing_conversion_matches_legacy_reads() -> None:
    class CountingRow(dict[str, Any]):
        fencing_reads = 0

        def __getitem__(self, key: str) -> Any:
            if key == "fencing_token":
                self.fencing_reads += 1
            return super().__getitem__(key)

    fenced_row = CountingRow(_event_row(fencing_token="7"))
    fenced = store._event_from_row(fenced_row)  # type: ignore[arg-type]
    assert fenced.fencing_token == 7
    assert fenced_row.fencing_reads == 2

    unfenced_row = CountingRow(_event_row(fencing_token=None))
    unfenced = store._event_from_row(unfenced_row)  # type: ignore[arg-type]
    assert unfenced.fencing_token is None
    assert unfenced_row.fencing_reads == 1

    lease_row = {
        "lease_id": "lease-example",
        "resource_type": "episode",
        "resource_id": "episode-example",
        "owner": "owner-example",
        "acquired_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-01-01T01:00:00+00:00",
        "idempotency_key": None,
        "fencing_token": None,
    }
    with pytest.raises(TypeError):
        store._lease_from_row(lease_row)  # type: ignore[arg-type]
