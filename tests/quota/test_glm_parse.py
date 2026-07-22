"""GLM Coding Plan quota mapping (slice-093-pre, pass criteria 1 + 3).

The fixture is the REAL response recorded from the active Max account on
2026-07-22 (HTTP 200; see memory ``glm-coding-plan-quota-endpoint``). Not
synthetic — the cc/codex "real fixture only" rule applies to the provider
quota contract too: a hand-written response only proves the parser matches
the author's imagination.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.quota.glm import parse_glm_quota
from trowel_py.quota.types import Provider, QuotaStatus, QuotaWindowKind

FIXTURES = Path(__file__).parent / "fixtures"
_REAL_FIXTURE = FIXTURES / "glm_quota_real.json"


def _load(name: str) -> dict:
    """Read a real-recorded JSON fixture from tests/quota/fixtures."""

    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not _REAL_FIXTURE.exists(),
    reason="local GLM recording is gitignored; not present on a fresh clone",
)
def test_real_max_account_maps_three_windows_with_used_percent() -> None:
    """Criterion 1: real response -> ok; session/weekly/monthly mapped;
    ``percentage`` read as USED (remaining = 100 - p); ``resets_at`` kept as
    epoch ms verbatim."""

    raw = _load("glm_quota_real.json")
    snap = parse_glm_quota(raw, account_id="glm-a", fetched_at=1_700_000_000_000)

    assert snap.status is QuotaStatus.OK
    assert snap.provider is Provider.GLM
    assert snap.account_id == "glm-a"
    assert snap.plan_level == "max"
    assert snap.fetched_at == 1_700_000_000_000

    by_kind = {w.kind: w for w in snap.windows}
    assert set(by_kind) == {
        QuotaWindowKind.SESSION_5H,
        QuotaWindowKind.WEEKLY,
        QuotaWindowKind.WEB_SEARCHES_MONTHLY,
    }
    # ``percentage`` is USED, verbatim from the real response.
    assert by_kind[QuotaWindowKind.SESSION_5H].used_percent == 1
    assert by_kind[QuotaWindowKind.WEEKLY].used_percent == 90
    assert by_kind[QuotaWindowKind.WEB_SEARCHES_MONTHLY].used_percent == 68
    # ``resets_at`` preserved as epoch ms, not reinterpreted.
    assert by_kind[QuotaWindowKind.SESSION_5H].resets_at == 1784748691984
    assert by_kind[QuotaWindowKind.WEEKLY].resets_at == 1784858417972


def test_empty_or_missing_limits_is_no_data_not_crash() -> None:
    """Criterion 3: the undocumented endpoint changed shape (limits missing
    or empty) -> ``no-data``, never an exception."""

    assert parse_glm_quota(
        {"data": {"limits": []}}, account_id="x", fetched_at=1
    ).status is QuotaStatus.NO_DATA
    assert parse_glm_quota(
        {"data": {}}, account_id="x", fetched_at=1
    ).status is QuotaStatus.NO_DATA
    assert parse_glm_quota({}, account_id="x", fetched_at=1).status is QuotaStatus.NO_DATA


def test_non_object_limit_entries_are_dropped_not_crash() -> None:
    """Criterion 3: a malformed ``limits`` entry (bare int / string) must not
    raise — it is skipped, and any valid entry still maps."""

    raw = {
        "code": 200,
        "data": {
            "limits": [
                7,
                "junk",
                {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 5},
            ]
        },
    }
    snap = parse_glm_quota(raw, account_id="x", fetched_at=1)
    assert snap.status is QuotaStatus.OK
    assert [w.kind for w in snap.windows] == [QuotaWindowKind.SESSION_5H]


def test_all_non_object_limits_is_no_data() -> None:
    assert (
        parse_glm_quota(
            {"data": {"limits": [7, "junk"]}}, account_id="x", fetched_at=1
        ).status
        is QuotaStatus.NO_DATA
    )
