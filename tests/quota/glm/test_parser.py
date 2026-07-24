from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pytest

from tests.quota.glm.support import RECORDED_PATH, load_recorded
from trowel_py.quota.glm import (
    _as_float,
    _as_int,
    _find_limit,
    parse_glm_quota,
)
from trowel_py.quota.types import Provider, QuotaStatus, QuotaWindowKind


@pytest.mark.skipif(
    not RECORDED_PATH.exists(),
    reason="本机录制不存在",
)
def test_recorded_response_maps_three_windows() -> None:
    snapshot = parse_glm_quota(
        load_recorded(),
        account_id="account",
        fetched_at=1_700_000_000_000,
    )

    assert snapshot.status is QuotaStatus.OK
    assert snapshot.provider is Provider.GLM
    assert snapshot.account_id == "account"
    assert snapshot.plan_level is None or isinstance(
        snapshot.plan_level,
        str,
    )
    assert snapshot.fetched_at == 1_700_000_000_000
    by_kind = {window.kind: window for window in snapshot.windows}
    assert set(by_kind) == {
        QuotaWindowKind.SESSION_5H,
        QuotaWindowKind.WEEKLY,
        QuotaWindowKind.WEB_SEARCHES_MONTHLY,
    }
    assert by_kind[QuotaWindowKind.SESSION_5H].raw.get("unit") == 3
    assert by_kind[QuotaWindowKind.WEEKLY].raw.get("unit") == 6
    assert {kind: window.raw.get("type") for kind, window in by_kind.items()} == {
        QuotaWindowKind.SESSION_5H: "TOKENS_LIMIT",
        QuotaWindowKind.WEEKLY: "TOKENS_LIMIT",
        QuotaWindowKind.WEB_SEARCHES_MONTHLY: "TIME_LIMIT",
    }
    assert all(math.isfinite(window.used_percent) for window in snapshot.windows)


@pytest.mark.parametrize(
    "raw",
    [
        {"data": {"limits": []}},
        {"data": {}},
        {},
        {"data": {"limits": [7, "junk"]}},
    ],
)
def test_missing_usable_limits_returns_no_data(raw: dict) -> None:
    assert (
        parse_glm_quota(raw, account_id="x", fetched_at=1).status is QuotaStatus.NO_DATA
    )


def test_non_object_limits_are_ignored() -> None:
    snapshot = parse_glm_quota(
        {
            "code": 200,
            "data": {
                "limits": [
                    7,
                    "junk",
                    {
                        "type": "TOKENS_LIMIT",
                        "unit": 3,
                        "percentage": 5,
                    },
                ]
            },
        },
        account_id="x",
        fetched_at=1,
    )
    assert [window.kind for window in snapshot.windows] == [QuotaWindowKind.SESSION_5H]


def test_window_order_unit_fallback_and_monthly_derivation() -> None:
    fallback = {
        "type": "TOKENS_LIMIT",
        "percentage": 8,
        "number": 999,
    }
    exact = {
        "type": "TOKENS_LIMIT",
        "unit": 3,
        "percentage": 7,
        "nextResetTime": 1234,
        "number": 1,
    }
    snapshot = parse_glm_quota(
        {
            "data": {
                "level": "custom",
                "limits": [
                    fallback,
                    {
                        "type": "TIME_LIMIT",
                        "currentValue": 2,
                        "usage": 8,
                    },
                    exact,
                ],
            }
        },
        account_id="x",
        fetched_at=1,
    )

    assert snapshot.plan_level == "custom"
    assert [window.kind for window in snapshot.windows] == [
        QuotaWindowKind.SESSION_5H,
        QuotaWindowKind.WEEKLY,
        QuotaWindowKind.WEB_SEARCHES_MONTHLY,
    ]
    assert [window.used_percent for window in snapshot.windows] == [
        7,
        8,
        25,
    ]
    assert snapshot.windows[0].raw == exact
    assert snapshot.windows[0].raw is not exact
    assert snapshot.windows[0].resets_at == 1234


def test_find_limit_uses_type_and_unit_not_number() -> None:
    fallback = {"type": "TOKENS_LIMIT", "number": 3}
    exact = {"type": "TOKENS_LIMIT", "unit": 6, "number": 1}
    limits: list[Mapping[str, Any]] = [
        {"type": "OTHER", "unit": 6},
        fallback,
        exact,
    ]
    assert _find_limit(limits, "TOKENS_LIMIT", 6) is exact
    assert _find_limit(limits, "TOKENS_LIMIT", 3) is fallback
    assert _find_limit(limits, "TOKENS_LIMIT", None) is fallback


def test_numeric_coercion_keeps_existing_edges() -> None:
    assert _as_float(True) is None
    assert _as_float(2) == 2.0
    assert _as_float(float("nan")) is None
    assert math.isnan(_as_float("nan") or 0.0)
    assert _as_float("inf") == float("inf")
    assert _as_float(" ") is None
    assert _as_float("invalid") is None

    assert _as_int(True) is None
    assert _as_int(2) == 2
    assert _as_int(2.0) == 2
    assert _as_int(2.5) is None
    assert _as_int("2") is None
