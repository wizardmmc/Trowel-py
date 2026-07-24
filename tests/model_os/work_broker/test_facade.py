from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path
import textwrap
from types import SimpleNamespace

import trowel_py.model_os as model_os
from trowel_py.model_os import work_broker
from trowel_py.model_os.work_broker import (
    BudgetDimensions,
    WorkBroker,
    WorkLease,
)
from trowel_py.model_os.work_broker.schema import SCHEMA_SQL
from tests.model_os.work_broker._support import _default

PUBLIC_NAMES = (
    "WorkKind",
    "ModelTier",
    "CatchupPolicy",
    "DenialReason",
    "StaleWorkLease",
    "IdempotencyConflict",
    "BudgetDimensions",
    "BrokerPolicy",
    "WorkRequest",
    "WorkLease",
    "WorkDenial",
    "UsageRecord",
    "UsageTotals",
    "WorkBroker",
)


def signature_digest(names: tuple[str, ...]) -> str:
    manifest = "\n".join(
        f"{name}{inspect.signature(getattr(work_broker, name))}" for name in names
    )
    return hashlib.sha256(manifest.encode()).hexdigest()


def method_signature_digest() -> str:
    manifest = "\n".join(
        f"{name}{inspect.signature(getattr(WorkBroker, name))}"
        for name in WorkBroker.__dict__
        if callable(getattr(WorkBroker, name, None))
    )
    return hashlib.sha256(manifest.encode()).hexdigest()


def test_public_types_keep_identity_and_module() -> None:
    for name in PUBLIC_NAMES:
        broker_value = getattr(work_broker, name)
        assert getattr(model_os, name) is broker_value
        assert broker_value.__module__ == "trowel_py.model_os.work_broker"


def test_public_and_method_signatures_are_stable() -> None:
    assert signature_digest(PUBLIC_NAMES) == (
        "04f9d6c56f46d97a45edb87444ea610f3e5758b86aef0e8ac21fa997b31e7d2e"
    )
    assert method_signature_digest() == (
        "a519c9d9632ea055cc300bea970d8ff868f276d3cf82f8f15b78163589152090"
    )


def test_module_is_a_stable_package_facade() -> None:
    assert work_broker.__name__ == "trowel_py.model_os.work_broker"
    assert hasattr(work_broker, "__path__")
    assert work_broker._SCHEMA_SQL is SCHEMA_SQL


def test_open_reads_schema_from_facade_at_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        work_broker,
        "_SCHEMA_SQL",
        "CREATE TABLE runtime_schema_probe (value TEXT);",
    )
    broker = WorkBroker(tmp_path / "broker.db")
    broker.open()
    try:
        assert broker._conn is not None
        row = broker._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='runtime_schema_probe'"
        ).fetchone()
        assert row is not None
    finally:
        broker.close()


def test_lease_codec_facades_keep_descriptors_signatures_and_module() -> None:
    assert isinstance(WorkBroker.__dict__["_cap_to_json"], staticmethod)
    assert not isinstance(WorkBroker.__dict__["_row_to_lease"], staticmethod)
    assert str(inspect.signature(WorkBroker._cap_to_json)) == (
        "(cap: 'BudgetDimensions | None') -> 'str | None'"
    )
    assert str(inspect.signature(WorkBroker._row_to_lease)) == (
        "(self, row: 'sqlite3.Row') -> 'WorkLease'"
    )
    assert WorkBroker._cap_to_json.__module__ == work_broker.__name__
    assert WorkBroker._row_to_lease.__module__ == work_broker.__name__
    assert WorkBroker._cap_to_json.__qualname__ == "WorkBroker._cap_to_json"
    assert WorkBroker._row_to_lease.__qualname__ == "WorkBroker._row_to_lease"


def test_lease_codec_facades_inject_runtime_dependencies(monkeypatch) -> None:
    cap = BudgetDimensions(calls=1)
    row = object()
    seen: dict[str, object] = {}
    lease = object()
    dumps = object()
    loads = object()
    budget_dimensions_type = object()
    work_lease_type = object()
    provider_type = object()
    work_kind_type = object()
    model_tier_type = object()

    def fake_cap(candidate, *, dumps):
        seen["cap"] = (candidate, dumps)
        return "encoded"

    def fake_row(candidate, **dependencies):
        seen["row"] = (candidate, dependencies)
        return lease

    monkeypatch.setattr(work_broker, "_run_cap_to_json", fake_cap)
    monkeypatch.setattr(work_broker, "_run_row_to_lease", fake_row)
    monkeypatch.setattr(work_broker, "json", SimpleNamespace(dumps=dumps, loads=loads))
    monkeypatch.setattr(work_broker, "BudgetDimensions", budget_dimensions_type)
    monkeypatch.setattr(work_broker, "WorkLease", work_lease_type)
    monkeypatch.setattr(work_broker, "Provider", provider_type)
    monkeypatch.setattr(work_broker, "WorkKind", work_kind_type)
    monkeypatch.setattr(work_broker, "ModelTier", model_tier_type)

    assert WorkBroker._cap_to_json(cap) == "encoded"
    assert WorkBroker.__new__(WorkBroker)._row_to_lease(row) is lease
    assert seen["cap"] == (cap, dumps)
    assert seen["row"] == (
        row,
        {
            "loads": loads,
            "budget_dimensions_type": budget_dimensions_type,
            "work_lease_type": work_lease_type,
            "provider_type": provider_type,
            "work_kind_type": work_kind_type,
            "model_tier_type": model_tier_type,
        },
    )


def test_production_methods_continue_calling_lease_codec_facades(
    broker: WorkBroker,
    monkeypatch,
) -> None:
    encoded: list[BudgetDimensions | None] = []
    decoded: list[object] = []
    original_cap = WorkBroker._cap_to_json
    original_row = WorkBroker._row_to_lease

    def record_cap(cap):
        encoded.append(cap)
        return original_cap(cap)

    def record_row(self, row):
        decoded.append(row)
        return original_row(self, row)

    monkeypatch.setattr(WorkBroker, "_cap_to_json", staticmethod(record_cap))
    lease = broker.request(_default(account_id="glm-a"))
    assert isinstance(lease, WorkLease)
    monkeypatch.setattr(WorkBroker, "_row_to_lease", record_row)

    assert broker.active_leases() == (lease,)
    assert encoded == [lease.granted_cap]
    assert len(decoded) == 1


def test_all_existing_lease_codec_call_sites_stay_on_facades() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(WorkBroker)))
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"_cap_to_json", "_row_to_lease"}
    ]

    assert calls.count("_cap_to_json") == 1
    assert calls.count("_row_to_lease") == 4
