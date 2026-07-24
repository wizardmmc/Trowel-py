from __future__ import annotations

import inspect
from typing import Any, Callable, cast

import pytest

from trowel_py.model_os import store


def test_store_facade_keeps_codec_signatures_and_modules() -> None:
    expected: dict[Callable[..., Any], str] = {
        store._pending_to_payload: ("(p: 'PendingDescriptor') -> 'dict[str, Any]'"),
        store._pending_from_payload: ("(p: 'dict[str, Any]') -> 'PendingDescriptor'"),
        store._snapshot_to_payload: ("(s: 'EpisodeSnapshot') -> 'dict[str, Any]'"),
        store._validate_episode_snapshot: (
            "(snapshot: 'EpisodeSnapshot', payload_text: 'str') -> 'None'"
        ),
        store._snapshot_from_payload: ("(p: 'dict[str, Any]') -> 'EpisodeSnapshot'"),
    }
    assert {
        function: str(inspect.signature(function)) for function in expected
    } == expected
    assert {function.__module__ for function in expected} == {
        "trowel_py.model_os.store"
    }


def test_pending_facade_uses_current_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_type = object()
    waiting_subtype = object()
    captured: dict[str, Any] = {}
    expected = object()

    def decode(payload: dict, **values: Any) -> Any:
        captured.update(payload=payload, **values)
        return expected

    monkeypatch.setattr(store, "PendingDescriptor", pending_type)
    monkeypatch.setattr(store, "WaitingSubtype", waiting_subtype)
    monkeypatch.setattr(store, "_run_pending_from_payload", decode)

    payload = {"kind": "input"}
    assert store._pending_from_payload(payload) is expected
    assert captured == {
        "payload": payload,
        "pending_type": pending_type,
        "waiting_subtype": waiting_subtype,
    }


def test_snapshot_encoder_and_validator_use_current_facade_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encode_pending = object()
    snapshot = cast(Any, object())
    encoded = object()
    captured: list[tuple[Any, ...]] = []

    monkeypatch.setattr(store, "_pending_to_payload", encode_pending)

    def encode(value: Any, **options: Any) -> Any:
        captured.append(("encode", value, options))
        return encoded

    monkeypatch.setattr(
        store,
        "_run_snapshot_to_payload",
        encode,
    )
    monkeypatch.setattr(store, "_MAX_SNAPSHOT_PAYLOAD_BYTES", 17)

    class CurrentError(Exception):
        pass

    monkeypatch.setattr(store, "EpisodeCommandError", CurrentError)

    def validate(value: Any, text: str, **options: Any) -> None:
        captured.append(("validate", value, text, options))

    monkeypatch.setattr(
        store,
        "_run_validate_snapshot",
        validate,
    )

    assert store._snapshot_to_payload(snapshot) is encoded
    store._validate_episode_snapshot(snapshot, "payload")
    assert captured == [
        (
            "encode",
            snapshot,
            {"encode_pending": encode_pending},
        ),
        (
            "validate",
            snapshot,
            "payload",
            {
                "max_payload_bytes": 17,
                "error_type": CurrentError,
            },
        ),
    ]


def test_snapshot_decoder_uses_current_facade_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = (
        "_pending_from_payload",
        "EpisodeSnapshot",
        "SideEffectRecord",
        "ArtifactRef",
        "SnapshotRef",
        "SnapshotSource",
    )
    sentinels = {name: object() for name in names}
    captured: dict[str, Any] = {}
    expected = object()

    for name, sentinel in sentinels.items():
        monkeypatch.setattr(store, name, sentinel)

    def decode(payload: dict, **values: Any) -> Any:
        captured.update(payload=payload, **values)
        return expected

    monkeypatch.setattr(store, "_run_snapshot_from_payload", decode)
    payload = {"work_item_goal": "x"}

    assert store._snapshot_from_payload(payload) is expected
    assert captured == {
        "payload": payload,
        "decode_pending": sentinels["_pending_from_payload"],
        "snapshot_type": sentinels["EpisodeSnapshot"],
        "side_effect_type": sentinels["SideEffectRecord"],
        "artifact_type": sentinels["ArtifactRef"],
        "snapshot_ref_type": sentinels["SnapshotRef"],
        "snapshot_source": sentinels["SnapshotSource"],
    }
