from __future__ import annotations

import inspect

from trowel_py.model_os import context_adapters, context_codec, context_observer


def test_context_codec_and_adapter_facades_keep_signatures_and_identity() -> None:
    expected = {
        "context_sample_to_dict": "(sample: 'ContextSample') -> 'dict'",
        "context_sample_from_dict": (
            "(d: 'Mapping[str, object]', native_session_id: 'str') -> 'ContextSample'"
        ),
        "_opt_str": "(v: 'object') -> 'str | None'",
        "_opt_int": "(v: 'object') -> 'int | None'",
        "_opt_float": "(v: 'object') -> 'float | None'",
        "codex_context_events_from_agent": (
            "(events: 'Sequence[Mapping[str, object]]') -> "
            "'list[NormalizedCodexUsage | NormalizedCodexCompaction]'"
        ),
        "cc_context_events_from_agent": (
            "(events: 'Sequence[Mapping[str, object]]') -> 'list[NormalizedCcEvent]'"
        ),
    }

    for name, signature in expected.items():
        value = getattr(context_observer, name)
        assert str(inspect.signature(value)) == signature
        assert value.__module__ == context_observer.__name__
        assert value.__qualname__ == name

    assert context_codec.sample_to_dict is not context_observer.context_sample_to_dict
    assert context_adapters.codex_events_from_agent is not (
        context_observer.codex_context_events_from_agent
    )


def test_sample_codec_facades_inject_runtime_dependencies(monkeypatch) -> None:
    sample = object()
    payload: dict[str, object] = {}
    seen: dict[str, object] = {}
    sample_type = object()
    opt_str_fn = object()
    opt_int_fn = object()
    opt_float_fn = object()
    confidence_type = object()
    unavailable_reason_type = object()
    str_fn = object()
    int_fn = object()
    restored = object()

    def fake_to_dict(value):
        seen["to"] = value
        return {"encoded": True}

    def fake_from_dict(data, native_session_id, **dependencies):
        seen["from"] = (data, native_session_id, dependencies)
        return restored

    monkeypatch.setattr(context_observer, "_run_sample_to_dict", fake_to_dict)
    monkeypatch.setattr(context_observer, "_run_sample_from_dict", fake_from_dict)
    monkeypatch.setattr(context_observer, "ContextSample", sample_type)
    monkeypatch.setattr(context_observer, "_opt_str", opt_str_fn)
    monkeypatch.setattr(context_observer, "_opt_int", opt_int_fn)
    monkeypatch.setattr(context_observer, "_opt_float", opt_float_fn)
    monkeypatch.setattr(context_observer, "ContextConfidence", confidence_type)
    monkeypatch.setattr(
        context_observer,
        "UnavailableReason",
        unavailable_reason_type,
    )
    monkeypatch.setattr(context_observer, "str", str_fn, raising=False)
    monkeypatch.setattr(context_observer, "int", int_fn, raising=False)

    assert context_observer.context_sample_to_dict(sample) == {"encoded": True}
    assert context_observer.context_sample_from_dict(payload, "session-example") is (
        restored
    )
    assert seen == {
        "to": sample,
        "from": (
            payload,
            "session-example",
            {
                "sample_type": sample_type,
                "opt_str_fn": opt_str_fn,
                "opt_int_fn": opt_int_fn,
                "opt_float_fn": opt_float_fn,
                "confidence_type": confidence_type,
                "unavailable_reason_type": unavailable_reason_type,
                "str_fn": str_fn,
                "int_fn": int_fn,
            },
        ),
    }


def test_adapter_facades_inject_runtime_dependencies(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    seen: dict[str, object] = {}
    mapping_type = object()
    usage_type = object()
    compaction_type = object()
    cc_event_type = object()
    opt_str_fn = object()
    opt_int_fn = object()
    str_fn = object()
    isinstance_fn = object()

    def fake_codex(value, **dependencies):
        seen["codex"] = (value, dependencies)
        return ["codex"]

    def fake_cc(value, **dependencies):
        seen["cc"] = (value, dependencies)
        return ["cc"]

    monkeypatch.setattr(
        context_observer,
        "_run_codex_events_from_agent",
        fake_codex,
    )
    monkeypatch.setattr(context_observer, "_run_cc_events_from_agent", fake_cc)
    monkeypatch.setattr(context_observer, "Mapping", mapping_type)
    monkeypatch.setattr(context_observer, "NormalizedCodexUsage", usage_type)
    monkeypatch.setattr(
        context_observer,
        "NormalizedCodexCompaction",
        compaction_type,
    )
    monkeypatch.setattr(context_observer, "NormalizedCcEvent", cc_event_type)
    monkeypatch.setattr(context_observer, "_opt_str", opt_str_fn)
    monkeypatch.setattr(context_observer, "_opt_int", opt_int_fn)
    monkeypatch.setattr(context_observer, "str", str_fn, raising=False)
    monkeypatch.setattr(
        context_observer,
        "isinstance",
        isinstance_fn,
        raising=False,
    )

    assert context_observer.codex_context_events_from_agent(events) == ["codex"]
    assert context_observer.cc_context_events_from_agent(events) == ["cc"]
    assert seen == {
        "codex": (
            events,
            {
                "mapping_type": mapping_type,
                "usage_type": usage_type,
                "compaction_type": compaction_type,
                "opt_str_fn": opt_str_fn,
                "opt_int_fn": opt_int_fn,
                "str_fn": str_fn,
                "isinstance_fn": isinstance_fn,
            },
        ),
        "cc": (
            events,
            {
                "mapping_type": mapping_type,
                "event_type": cc_event_type,
                "opt_str_fn": opt_str_fn,
                "isinstance_fn": isinstance_fn,
            },
        ),
    }
