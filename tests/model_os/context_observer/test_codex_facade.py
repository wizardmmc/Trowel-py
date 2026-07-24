from __future__ import annotations

import inspect
from typing import Any

from trowel_py.model_os import context_observer


def test_codex_calculator_facades_keep_signatures_and_identity() -> None:
    expected = {
        "_codex_sample": (
            "(usage: 'NormalizedCodexUsage', *, generation: 'int', "
            "native_session_id: 'str', source_version: 'str | None') "
            "-> 'ContextSample'"
        ),
        "extract_codex_samples": (
            "(events: 'Sequence[NormalizedCodexUsage | "
            "NormalizedCodexCompaction]', *, native_session_id: 'str', "
            "source_version: 'str | None' = None) -> 'list[ContextSample]'"
        ),
    }
    for name, signature in expected.items():
        value = getattr(context_observer, name)
        assert str(inspect.signature(value)) == signature
        assert value.__module__ == context_observer.__name__
        assert value.__qualname__ == name


def test_codex_calculator_facades_inject_runtime_dependencies(monkeypatch) -> None:
    usage: Any = object()
    events: list[Any] = [usage]
    sample = object()
    extracted = [sample]
    seen: dict[str, object] = {}
    dependencies = {
        "sample_type": object(),
        "confidence_type": object(),
        "unavailable_reason_type": object(),
        "dict_fn": object(),
        "round_fn": object(),
        "compaction_type": object(),
        "isinstance_fn": object(),
        "sample_from_usage_fn": object(),
    }

    def fake_sample(value, **kwargs):
        seen["sample"] = (value, kwargs)
        return sample

    def fake_extract(value, **kwargs):
        seen["extract"] = (value, kwargs)
        return extracted

    monkeypatch.setattr(context_observer, "_run_codex_sample", fake_sample)
    monkeypatch.setattr(
        context_observer,
        "_run_extract_codex_samples",
        fake_extract,
    )
    for name, value in (
        ("ContextSample", dependencies["sample_type"]),
        ("ContextConfidence", dependencies["confidence_type"]),
        ("UnavailableReason", dependencies["unavailable_reason_type"]),
        ("NormalizedCodexCompaction", dependencies["compaction_type"]),
    ):
        monkeypatch.setattr(context_observer, name, value)
    for name, value in (
        ("dict", dependencies["dict_fn"]),
        ("round", dependencies["round_fn"]),
        ("isinstance", dependencies["isinstance_fn"]),
    ):
        monkeypatch.setattr(context_observer, name, value, raising=False)

    assert (
        context_observer._codex_sample(
            usage,
            generation=3,
            native_session_id="session-example",
            source_version="runtime-example",
        )
        is sample
    )
    monkeypatch.setattr(
        context_observer,
        "_codex_sample",
        dependencies["sample_from_usage_fn"],
    )
    assert (
        context_observer.extract_codex_samples(
            events,
            native_session_id="session-example",
            source_version="runtime-example",
        )
        is extracted
    )
    assert seen == {
        "sample": (
            usage,
            {
                "generation": 3,
                "native_session_id": "session-example",
                "source_version": "runtime-example",
                "sample_type": dependencies["sample_type"],
                "confidence_type": dependencies["confidence_type"],
                "unavailable_reason_type": dependencies["unavailable_reason_type"],
                "dict_fn": dependencies["dict_fn"],
                "round_fn": dependencies["round_fn"],
            },
        ),
        "extract": (
            events,
            {
                "native_session_id": "session-example",
                "source_version": "runtime-example",
                "compaction_type": dependencies["compaction_type"],
                "isinstance_fn": dependencies["isinstance_fn"],
                "sample_from_usage_fn": dependencies["sample_from_usage_fn"],
            },
        ),
    }
