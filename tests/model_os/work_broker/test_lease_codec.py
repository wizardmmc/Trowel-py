from __future__ import annotations

import json
import sqlite3

import pytest

from trowel_py.model_os.work_broker import (
    BudgetDimensions,
    ModelTier,
    WorkKind,
    WorkLease,
)
from trowel_py.model_os.work_broker.lease_codec import cap_to_json, row_to_lease
from trowel_py.quota.types import Provider


def lease_row(
    *,
    granted_cap: object = None,
    provider: object = "glm",
    work_kind: object = "default",
    model_tier: object = "fast",
    fencing_token: object = 7,
) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT "
            "'lease-example' AS lease_id, "
            "'glm:account-a:0' AS slot, "
            "? AS provider, "
            "'account-a' AS account_id, "
            "? AS work_kind, "
            "? AS model_tier, "
            "? AS granted_cap, "
            "'2026-07-23T00:00:00+00:00' AS acquired_at, "
            "'2026-07-23T00:10:00+00:00' AS expires_at, "
            "? AS fencing_token, "
            "'task-example' AS task_id, "
            "'work-example' AS work_item_id",
            (provider, work_kind, model_tier, granted_cap, fencing_token),
        ).fetchone()
        assert row is not None
        return row
    finally:
        connection.close()


def decode(row: sqlite3.Row) -> WorkLease:
    return row_to_lease(
        row,
        loads=json.loads,
        budget_dimensions_type=BudgetDimensions,
        work_lease_type=WorkLease,
        provider_type=Provider,
        work_kind_type=WorkKind,
        model_tier_type=ModelTier,
    )


def test_cap_to_json_preserves_payload_order_and_default_spacing() -> None:
    cap = BudgetDimensions(calls=3, tokens=5, cost=0.25, wall_seconds=7)

    assert (
        cap_to_json(
            None,
            dumps=lambda payload: pytest.fail(f"不应序列化无限额度：{payload!r}"),
        )
        is None
    )
    assert cap_to_json(cap, dumps=json.dumps) == (
        '{"calls": 3, "tokens": 5, "cost": 0.25, "wall_seconds": 7}'
    )


def test_cap_to_json_passes_the_complete_payload_to_runtime_dumps() -> None:
    seen: list[dict[str, object]] = []

    def capture(payload: dict[str, object]) -> str:
        seen.append(payload)
        return "encoded"

    result = cap_to_json(
        BudgetDimensions(calls=0),
        dumps=capture,
    )

    assert result == "encoded"
    assert seen == [
        {
            "calls": 0,
            "tokens": None,
            "cost": None,
            "wall_seconds": None,
        }
    ]


def test_row_to_lease_rebuilds_enums_cap_and_scalar_fields() -> None:
    lease = decode(
        lease_row(
            granted_cap=('{"calls": 3, "tokens": 5, "cost": 0.25, "wall_seconds": 7}')
        )
    )

    assert lease == WorkLease(
        lease_id="lease-example",
        slot="glm:account-a:0",
        provider=Provider.GLM,
        account_id="account-a",
        work_kind=WorkKind.DEFAULT,
        model_tier=ModelTier.FAST,
        granted_cap=BudgetDimensions(
            calls=3,
            tokens=5,
            cost=0.25,
            wall_seconds=7,
        ),
        acquired_at="2026-07-23T00:00:00+00:00",
        expires_at="2026-07-23T00:10:00+00:00",
        fencing_token=7,
        task_id="task-example",
        work_item_id="work-example",
    )


@pytest.mark.parametrize("stored_cap", [None, "", 0])
def test_row_to_lease_treats_falsey_stored_caps_as_unlimited(
    stored_cap: object,
) -> None:
    assert decode(lease_row(granted_cap=stored_cap)).granted_cap is None


def test_row_to_lease_defaults_missing_cap_axes_to_none() -> None:
    assert decode(lease_row(granted_cap='{"calls": 2}')).granted_cap == (
        BudgetDimensions(calls=2)
    )


@pytest.mark.parametrize(
    ("overrides", "error_type"),
    [
        ({"granted_cap": "{"}, json.JSONDecodeError),
        ({"provider": "unknown"}, ValueError),
        ({"work_kind": "unknown"}, ValueError),
        ({"model_tier": "unknown"}, ValueError),
        ({"fencing_token": "not-an-int"}, ValueError),
    ],
)
def test_row_to_lease_preserves_malformed_value_errors(
    overrides: dict[str, object],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        decode(lease_row(**overrides))
