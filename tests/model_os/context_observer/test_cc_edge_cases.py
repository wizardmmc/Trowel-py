from __future__ import annotations

from trowel_py.model_os.context_observer import (
    NormalizedCcEvent,
    extract_cc_samples,
)
from trowel_py.model_os import context_observer


def assistant(message_id: str | None, input_tokens: int) -> NormalizedCcEvent:
    return NormalizedCcEvent(
        type="assistant",
        subtype=None,
        timestamp=None,
        message_id=message_id,
        model="glm-5.2",
        usage={"input_tokens": input_tokens},
        turn_id=None,
    )


def boundary() -> NormalizedCcEvent:
    return NormalizedCcEvent(
        type="system",
        subtype="compact_boundary",
        timestamp=None,
        message_id=None,
        model=None,
        usage=None,
        turn_id=None,
    )


def test_no_id_events_stay_distinct_and_duplicate_uses_latest_generation() -> None:
    samples = extract_cc_samples(
        [
            assistant(None, 1),
            assistant("", 2),
            assistant("duplicate", 3),
            boundary(),
            assistant("duplicate", 4),
        ],
        native_session_id="session-example",
        main_or_subagent="main",
    )

    assert [
        (sample.request_identity, sample.generation, sample.used_tokens)
        for sample in samples
    ] == [
        ("(no-id)", 0, 1),
        ("(no-id)", 0, 2),
        ("duplicate", 1, 4),
    ]


def test_as_int_uses_facade_exception_types(monkeypatch) -> None:
    class RuntimeConversionError(Exception):
        pass

    def fail_conversion(_value: object) -> str:
        raise RuntimeConversionError

    monkeypatch.setattr(
        context_observer,
        "TypeError",
        RuntimeConversionError,
        raising=False,
    )
    monkeypatch.setattr(
        context_observer,
        "ValueError",
        RuntimeConversionError,
        raising=False,
    )
    monkeypatch.setattr(context_observer, "str", fail_conversion, raising=False)

    assert context_observer._as_int(object()) == 0
