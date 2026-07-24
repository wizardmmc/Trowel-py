from __future__ import annotations

import inspect
import typing
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from trowel_py.model_os import work_broker
from trowel_py.model_os.work_broker import (
    BudgetDimensions,
    UsageRecord,
    WorkBroker,
)
from trowel_py.quota.types import Provider


def test_policy_facades_keep_descriptors_signatures_and_types() -> None:
    expected = {
        "_narrow_cap": (
            "(policy_cap: 'BudgetDimensions', req_cap: 'BudgetDimensions | None') "
            "-> 'BudgetDimensions'",
            {
                "policy_cap": BudgetDimensions,
                "req_cap": BudgetDimensions | None,
                "return": BudgetDimensions,
            },
        ),
        "_slot_id": (
            "(provider: 'Provider', account: 'str', idx: 'int') -> 'str'",
            {
                "provider": Provider,
                "account": str,
                "idx": int,
                "return": str,
            },
        ),
        "_parse_iso": (
            "(value: 'str') -> 'datetime'",
            {"value": str, "return": datetime},
        ),
        "_utc_day": (
            "(occurred_at: 'str') -> 'str'",
            {"occurred_at": str, "return": str},
        ),
        "_validate_cap": (
            "(cap: 'BudgetDimensions') -> 'None'",
            {"cap": BudgetDimensions, "return": type(None)},
        ),
        "_validate_usage": (
            "(usage: 'UsageRecord') -> 'None'",
            {"usage": UsageRecord, "return": type(None)},
        ),
    }

    for name, (signature, hints) in expected.items():
        descriptor = WorkBroker.__dict__[name]
        facade = getattr(WorkBroker, name)
        assert isinstance(descriptor, staticmethod)
        assert str(inspect.signature(facade)) == signature
        assert facade.__module__ == work_broker.__name__
        assert facade.__qualname__ == f"WorkBroker.{name}"
        assert typing.get_type_hints(facade) == hints


def test_policy_facades_delegate_current_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
    results = {
        name: object()
        for name in (
            "narrow_cap",
            "slot_id",
            "parse_iso",
            "utc_day",
            "validate_cap",
            "validate_usage",
        )
    }

    def recorder(name: str):
        def record(*args: Any, **kwargs: Any) -> object:
            observed[name] = (args, kwargs)
            return results[name]

        return record

    for name in results:
        monkeypatch.setattr(
            work_broker,
            f"_run_{name}",
            recorder(name),
            raising=False,
        )

    budget_type = object()
    datetime_type = object()
    utc = object()

    def min_fn(left: object, right: object) -> tuple[object, object]:
        return left, right

    def isinstance_fn(value: object, kind: object) -> tuple[object, object]:
        return value, kind

    bool_type = type("BoolType", (), {})
    int_type = type("IntType", (), {})
    float_type = type("FloatType", (), {})

    def isfinite_fn(value: object) -> object:
        return value

    parse_iso = object()
    monkeypatch.setattr(work_broker, "BudgetDimensions", budget_type)
    monkeypatch.setattr(work_broker, "datetime", datetime_type)
    monkeypatch.setattr(work_broker, "timezone", SimpleNamespace(utc=utc))
    monkeypatch.setattr(work_broker, "min", min_fn, raising=False)
    monkeypatch.setattr(work_broker, "isinstance", isinstance_fn, raising=False)
    monkeypatch.setattr(work_broker, "bool", bool_type, raising=False)
    monkeypatch.setattr(work_broker, "int", int_type, raising=False)
    monkeypatch.setattr(work_broker, "float", float_type, raising=False)
    monkeypatch.setattr(work_broker, "math", SimpleNamespace(isfinite=isfinite_fn))

    policy_cap = object()
    req_cap = object()
    provider = object()
    cap = object()
    usage = object()

    assert (
        WorkBroker._narrow_cap(policy_cap, req_cap) is results["narrow_cap"]  # type: ignore[arg-type]
    )
    assert WorkBroker._slot_id(provider, "account", 2) is results["slot_id"]  # type: ignore[arg-type]
    monkeypatch.setattr(WorkBroker, "_parse_iso", staticmethod(lambda value: parse_iso))
    assert WorkBroker._parse_iso("timestamp") is parse_iso
    assert WorkBroker._utc_day("timestamp") is results["utc_day"]
    assert WorkBroker._validate_cap(cap) is None  # type: ignore[arg-type,func-returns-value]
    assert WorkBroker._validate_usage(usage) is None  # type: ignore[arg-type,func-returns-value]

    narrow_args, narrow_dependencies = observed["narrow_cap"]
    assert narrow_args == (policy_cap, req_cap)
    assert narrow_dependencies["budget_type"] is budget_type
    assert narrow_dependencies["min_fn"]("left", "right") == ("left", "right")

    assert observed["slot_id"] == ((provider, "account", 2), {})
    utc_args, utc_dependencies = observed["utc_day"]
    assert utc_args == ("timestamp",)
    assert utc_dependencies["parse_iso"]("value") is parse_iso

    cap_args, cap_dependencies = observed["validate_cap"]
    assert cap_args == (cap,)
    assert cap_dependencies["isinstance_resolver"]() is isinstance_fn
    assert cap_dependencies["bool_type_resolver"]() is bool_type
    assert cap_dependencies["int_type_resolver"]() is int_type
    assert cap_dependencies["number_types_resolver"]() == int_type | float_type
    assert cap_dependencies["isfinite_resolver"]() is isfinite_fn

    usage_args, usage_dependencies = observed["validate_usage"]
    assert usage_args == (usage,)
    parser = usage_dependencies["parse_iso_resolver"]()
    assert parser("value") is parse_iso
    assert usage_dependencies["isfinite_resolver"]() is isfinite_fn


def test_parse_iso_facade_delegates_current_datetime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}
    result = object()

    def parse(value: str, **dependencies: Any) -> object:
        observed.update(value=value, **dependencies)
        return result

    class DatetimeType:
        @staticmethod
        def fromisoformat(value: str) -> tuple[str, str]:
            return "parsed", value

    utc = object()
    monkeypatch.setattr(work_broker, "_run_parse_iso", parse, raising=False)
    monkeypatch.setattr(work_broker, "datetime", DatetimeType)
    monkeypatch.setattr(work_broker, "timezone", SimpleNamespace(utc=utc))

    assert WorkBroker._parse_iso("timestamp") is result
    assert observed["value"] == "timestamp"
    assert observed["fromisoformat"]("value") == ("parsed", "value")
    assert observed["utc_resolver"]() is utc


def test_policy_facades_resolve_dependencies_after_input_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MutatingPolicyCap:
        tokens = None
        cost = None
        wall_seconds = None

        @property
        def calls(self) -> int:
            monkeypatch.setattr(
                work_broker,
                "min",
                lambda left, right: "runtime-min",
                raising=False,
            )
            return 4

    narrowed = WorkBroker._narrow_cap(
        MutatingPolicyCap(),  # type: ignore[arg-type]
        BudgetDimensions(calls=3),
    )
    assert narrowed.calls == "runtime-min"

    class MutatingValidationCap:
        tokens = None
        cost = None
        wall_seconds = None

        @property
        def calls(self) -> int:
            monkeypatch.setattr(
                work_broker,
                "isinstance",
                lambda value, kind: True,
                raising=False,
            )
            return 1

    with pytest.raises(ValueError, match="BudgetDimensions.calls must be an int"):
        WorkBroker._validate_cap(MutatingValidationCap())  # type: ignore[arg-type]

    class MutatingUsage:
        calls = 0
        input_tokens = 0
        output_tokens = 0
        wall_seconds = None
        cost = None

        @property
        def occurred_at(self) -> str:
            def reject(value: str) -> datetime:
                raise RuntimeError(f"runtime parser: {value}")

            monkeypatch.setattr(WorkBroker, "_parse_iso", staticmethod(reject))
            return "2026-07-24T00:00:00+00:00"

    with pytest.raises(RuntimeError, match="runtime parser"):
        WorkBroker._validate_usage(MutatingUsage())  # type: ignore[arg-type]

    parser_calls: list[str] = []

    def parser_a(value: str) -> datetime:
        parser_calls.append("a")
        return datetime.fromisoformat(value)

    def parser_b(value: str) -> datetime:
        parser_calls.append("b")
        return datetime.fromisoformat(value)

    class TwoGenerationUsage:
        calls = 0
        input_tokens = 0
        output_tokens = 0
        wall_seconds = None
        cost = None

        def __init__(self) -> None:
            self.reads = 0

        @property
        def occurred_at(self) -> str:
            self.reads += 1
            parser = parser_a if self.reads == 1 else parser_b
            monkeypatch.setattr(WorkBroker, "_parse_iso", staticmethod(parser))
            return "2026-07-24T00:00:00+00:00"

    WorkBroker._validate_usage(TwoGenerationUsage())  # type: ignore[arg-type]
    assert parser_calls == ["a"]

    class TwoGenerationCostCap:
        calls = None
        tokens = None
        wall_seconds = None

        def __init__(self) -> None:
            self.reads = 0

        @property
        def cost(self) -> float:
            self.reads += 1
            result = False if self.reads == 1 else True
            monkeypatch.setattr(
                work_broker,
                "isinstance",
                lambda value, kind: result,
                raising=False,
            )
            return 1.0

    WorkBroker._validate_cap(TwoGenerationCostCap())  # type: ignore[arg-type]

    class TwoGenerationUsageCost:
        occurred_at = "2026-07-24T00:00:00+00:00"
        calls = 0
        input_tokens = 0
        output_tokens = 0
        wall_seconds = None

        def __init__(self) -> None:
            self.reads = 0

        @property
        def cost(self) -> float:
            self.reads += 1
            result = self.reads != 1
            monkeypatch.setattr(
                work_broker,
                "math",
                SimpleNamespace(isfinite=lambda value: result),
            )
            return 1.0

    with pytest.raises(ValueError, match="UsageRecord.cost must be finite"):
        WorkBroker._validate_usage(TwoGenerationUsageCost())  # type: ignore[arg-type]


def test_parse_iso_resolves_utc_after_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_utc = object()
    runtime_utc = object()
    observed: list[object] = []
    result = object()

    class Parsed:
        @property
        def tzinfo(self) -> object:
            monkeypatch.setattr(
                work_broker,
                "timezone",
                SimpleNamespace(utc=runtime_utc),
            )
            return object()

        def astimezone(self, utc: object) -> object:
            observed.append(utc)
            return result

    class DatetimeType:
        @staticmethod
        def fromisoformat(value: str) -> Parsed:
            return Parsed()

    monkeypatch.setattr(work_broker, "datetime", DatetimeType)
    monkeypatch.setattr(
        work_broker,
        "timezone",
        SimpleNamespace(utc=initial_utc),
    )

    assert WorkBroker._parse_iso("timestamp") is result
    assert observed == [runtime_utc]
