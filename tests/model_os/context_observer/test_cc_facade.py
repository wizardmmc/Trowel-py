from __future__ import annotations

import inspect
from typing import Any

from trowel_py.model_os import context_observer


def test_cc_calculator_facades_keep_signatures_and_identity() -> None:
    expected = {
        "_is_compact_boundary": "(ev: 'NormalizedCcEvent') -> 'bool'",
        "_is_synthetic_assistant": "(ev: 'NormalizedCcEvent') -> 'bool'",
        "_as_int": "(value: 'object') -> 'int'",
        "_cc_sample": (
            "(ev: 'NormalizedCcEvent', *, generation: 'int', "
            "native_session_id: 'str', main_or_subagent: 'MainOrSubagent', "
            "source_version: 'str | None', window: 'int | None', "
            "usage: 'Mapping[str, int] | None') -> 'ContextSample'"
        ),
        "latest_main_ratio": (
            "(samples: 'Iterable[ContextSample]') -> 'ContextSample | None'"
        ),
    }
    for name, expected_signature in expected.items():
        value = getattr(context_observer, name)
        assert str(inspect.signature(value)) == expected_signature
        assert value.__module__ == context_observer.__name__
        assert value.__qualname__ == name

    extract = context_observer.extract_cc_samples
    signature = inspect.signature(extract)
    assert signature.parameters["window_resolver"].default is (
        context_observer.resolve_window
    )
    without_resolver_default = signature.replace(
        parameters=[
            parameter.replace(default=inspect.Parameter.empty)
            if parameter.name == "window_resolver"
            else parameter
            for parameter in signature.parameters.values()
        ]
    )
    assert str(without_resolver_default) == (
        "(events: 'Sequence[NormalizedCcEvent]', *, native_session_id: 'str', "
        "main_or_subagent: 'MainOrSubagent', source_version: 'str | None' = None, "
        "window_resolver) -> 'list[ContextSample]'"
    )
    assert extract.__module__ == context_observer.__name__
    assert extract.__qualname__ == "extract_cc_samples"


def test_cc_calculator_facades_inject_runtime_dependencies(monkeypatch) -> None:
    events: list[Any] = [object()]
    samples: list[Any] = [object()]
    extracted = [object()]
    latest = object()
    seen: dict[str, object] = {}
    window_resolver = object()
    dependencies = {
        "is_compact_boundary_fn": object(),
        "is_synthetic_assistant_fn": object(),
        "sample_from_event_fn": object(),
        "enumerate_fn": object(),
        "list_fn": object(),
    }
    reversed_fn = object()

    def fake_extract(value, **kwargs):
        seen["extract"] = (value, kwargs)
        return extracted

    def fake_latest(value, **kwargs):
        seen["latest"] = (value, kwargs)
        return latest

    for name, value in (
        ("_run_extract_cc_samples", fake_extract),
        ("_run_latest_main_ratio", fake_latest),
        ("_is_compact_boundary", dependencies["is_compact_boundary_fn"]),
        ("_is_synthetic_assistant", dependencies["is_synthetic_assistant_fn"]),
        ("_cc_sample", dependencies["sample_from_event_fn"]),
    ):
        monkeypatch.setattr(context_observer, name, value)
    monkeypatch.setattr(
        context_observer,
        "enumerate",
        dependencies["enumerate_fn"],
        raising=False,
    )
    monkeypatch.setattr(
        context_observer,
        "list",
        dependencies["list_fn"],
        raising=False,
    )
    monkeypatch.setattr(
        context_observer,
        "reversed",
        reversed_fn,
        raising=False,
    )

    assert (
        context_observer.extract_cc_samples(
            events,
            native_session_id="session-example",
            main_or_subagent="main",
            source_version="runtime-example",
            window_resolver=window_resolver,
        )
        is extracted
    )
    assert context_observer.latest_main_ratio(samples) is latest
    assert seen == {
        "extract": (
            events,
            {
                "native_session_id": "session-example",
                "main_or_subagent": "main",
                "source_version": "runtime-example",
                "window_resolver": window_resolver,
                **dependencies,
            },
        ),
        "latest": (
            samples,
            {
                "list_fn": dependencies["list_fn"],
                "reversed_fn": reversed_fn,
            },
        ),
    }


def test_cc_helper_facades_inject_runtime_dependencies(monkeypatch) -> None:
    event: Any = object()
    sample = object()
    seen: dict[str, object] = {}
    caught_error = type("CaughtError", (Exception,), {})
    dependencies = {
        "synthetic_model": object(),
        "bool_type": object(),
        "int_type": object(),
        "float_type": object(),
        "isinstance_fn": object(),
        "str_fn": object(),
        "mapping_type": object(),
        "sample_type": object(),
        "confidence_type": object(),
        "unavailable_reason_type": object(),
        "as_int_fn": object(),
        "round_fn": object(),
        "dict_fn": object(),
    }

    def fake_boundary(value):
        seen["boundary"] = value
        return True

    def fake_synthetic(value, **kwargs):
        seen["synthetic"] = (value, kwargs)
        return False

    def fake_as_int(value, **kwargs):
        seen["as_int"] = (value, kwargs)
        return 7

    def fake_sample(value, **kwargs):
        seen["sample"] = (value, kwargs)
        return sample

    monkeypatch.setattr(context_observer, "_run_is_compact_boundary", fake_boundary)
    monkeypatch.setattr(
        context_observer,
        "_run_is_synthetic_assistant",
        fake_synthetic,
    )
    monkeypatch.setattr(context_observer, "_run_as_int", fake_as_int)
    monkeypatch.setattr(context_observer, "_run_cc_sample", fake_sample)
    monkeypatch.setattr(
        context_observer,
        "SYNTHETIC_MODEL",
        dependencies["synthetic_model"],
    )
    for name, value in (
        ("bool", dependencies["bool_type"]),
        ("int", dependencies["int_type"]),
        ("float", dependencies["float_type"]),
        ("isinstance", dependencies["isinstance_fn"]),
        ("str", dependencies["str_fn"]),
        ("TypeError", caught_error),
        ("ValueError", caught_error),
        ("round", dependencies["round_fn"]),
        ("dict", dependencies["dict_fn"]),
    ):
        monkeypatch.setattr(context_observer, name, value, raising=False)
    for name, value in (
        ("Mapping", dependencies["mapping_type"]),
        ("ContextSample", dependencies["sample_type"]),
        ("ContextConfidence", dependencies["confidence_type"]),
        ("UnavailableReason", dependencies["unavailable_reason_type"]),
    ):
        monkeypatch.setattr(context_observer, name, value)

    assert context_observer._is_compact_boundary(event) is True
    assert context_observer._is_synthetic_assistant(event) is False
    assert context_observer._as_int("7") == 7
    monkeypatch.setattr(
        context_observer,
        "_as_int",
        dependencies["as_int_fn"],
    )
    assert (
        context_observer._cc_sample(
            event,
            generation=2,
            native_session_id="session-example",
            main_or_subagent="main",
            source_version="runtime-example",
            window=200_000,
            usage={"input_tokens": 1},
        )
        is sample
    )
    assert seen == {
        "boundary": event,
        "synthetic": (
            event,
            {"synthetic_model": dependencies["synthetic_model"]},
        ),
        "as_int": (
            "7",
            {
                "bool_type": dependencies["bool_type"],
                "int_type": dependencies["int_type"],
                "float_type": dependencies["float_type"],
                "isinstance_fn": dependencies["isinstance_fn"],
                "int_fn": dependencies["int_type"],
                "str_fn": dependencies["str_fn"],
                "exception_types": (caught_error, caught_error),
            },
        ),
        "sample": (
            event,
            {
                "generation": 2,
                "native_session_id": "session-example",
                "main_or_subagent": "main",
                "source_version": "runtime-example",
                "window": 200_000,
                "usage": {"input_tokens": 1},
                "mapping_type": dependencies["mapping_type"],
                "sample_type": dependencies["sample_type"],
                "confidence_type": dependencies["confidence_type"],
                "unavailable_reason_type": dependencies["unavailable_reason_type"],
                "isinstance_fn": dependencies["isinstance_fn"],
                "as_int_fn": dependencies["as_int_fn"],
                "round_fn": dependencies["round_fn"],
                "dict_fn": dependencies["dict_fn"],
            },
        ),
    }
