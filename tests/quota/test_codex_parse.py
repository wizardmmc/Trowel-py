"""Codex rate-limit -> quota mapping (slice-093-pre, criterion 4).

Fixture is the real Codex 0.144.0 ``account/rateLimits/updated`` notification
(``tests/codex_host/fixtures/notifications.jsonl`` L10), unfolded to the
slice-077 translator's AgentEvent payload shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.quota.codex import parse_codex_rate_limit
from trowel_py.quota.types import Provider, QuotaStatus, QuotaWindowKind

FIXTURES = Path(__file__).parent / "fixtures"
_PAYLOAD_PATH = FIXTURES / "codex_ratelimit_real.json"
pytestmark = pytest.mark.skipif(
    not _PAYLOAD_PATH.exists(),
    reason="local Codex recording is gitignored; not present on a fresh clone",
)


def _payload() -> dict:
    return json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))


NOW = 1_700_000_000_000


def test_real_payload_maps_rate_limit_window_and_converts_seconds_to_ms() -> None:
    snap = parse_codex_rate_limit(_payload(), account_id="codex", fetched_at=NOW)

    assert snap.status is QuotaStatus.OK
    assert snap.provider is Provider.CODEX
    assert snap.account_id == "codex"
    assert snap.plan_level == "pro"

    assert len(snap.windows) == 1
    window = snap.windows[0]
    assert window.kind is QuotaWindowKind.RATE_LIMIT
    assert window.used_percent == 20
    # resetsAt was epoch SECONDS (1784949908) -> milliseconds.
    assert window.resets_at == 1784949908000


def test_missing_primary_is_no_data() -> None:
    snap = parse_codex_rate_limit(
        {"primary": None, "plan_type": "pro"}, account_id="c", fetched_at=NOW
    )
    assert snap.status is QuotaStatus.NO_DATA


def test_primary_without_used_percent_is_no_data() -> None:
    snap = parse_codex_rate_limit(
        {"primary": {"resetsAt": 1}, "plan_type": "pro"},
        account_id="c",
        fetched_at=NOW,
    )
    assert snap.status is QuotaStatus.NO_DATA
