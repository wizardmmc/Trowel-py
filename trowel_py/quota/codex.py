"""Codex/GPT quota source (slice-093-pre).

``parse_codex_rate_limit`` maps the slice-077 ``rate_limit_updated`` AgentEvent
payload (the unfolded ``account/rateLimits/updated`` notification) to a
``QuotaSnapshot``. There is NO HTTP here: Codex pushes the snapshot during
turns; the read model subscribes via ``make_codex_observer`` (bound to the
SessionHub event observer) which calls this mapper.

Unit gotcha: Codex ``primary.resetsAt`` is epoch SECONDS (verified against the
real 0.144.0 fixture ``tests/codex_host/fixtures/notifications.jsonl`` L10),
while GLM ``nextResetTime`` is epoch ms. This mapper normalises to ms so the
read model serves one unit.

Field casing: the translator (slice-077) unfolds top-level fields to
snake_case (``plan_type``), but passes ``primary`` through verbatim, so its
inner fields stay camelCase (``usedPercent`` / ``resetsAt``).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)

#: the single GPT/Codex account id v0 tracks (one Codex login per machine).
DEFAULT_CODEX_ACCOUNT_ID = "codex"

#: turn-terminal event types — on these the Codex slot is no longer being
#: actively refreshed, so the observer marks it stale (criterion 4: "turn 间 stale").
_TERMINAL_TYPES = frozenset({"finished", "interrupted", "error"})


def _as_float(value: Any) -> float | None:
    """Coerce to a finite float; reject bool / NaN."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    return None


def _now_ms() -> int:
    """Epoch milliseconds."""

    return int(time.time() * 1000)


def parse_codex_rate_limit(
    payload: Mapping[str, Any], *, account_id: str, fetched_at: int
) -> QuotaSnapshot:
    """Map a Codex ``rate_limit_updated`` payload to a ``QuotaSnapshot``.

    Missing ``primary`` or a non-numeric ``usedPercent`` -> ``NO_DATA``.
    """

    primary = payload.get("primary")
    if not isinstance(primary, Mapping):
        return QuotaSnapshot(
            provider=Provider.CODEX,
            account_id=account_id,
            plan_level=None,
            windows=(),
            fetched_at=fetched_at,
            status=QuotaStatus.NO_DATA,
        )

    used = _as_float(primary.get("usedPercent"))
    if used is None:
        return QuotaSnapshot(
            provider=Provider.CODEX,
            account_id=account_id,
            plan_level=None,
            windows=(),
            fetched_at=fetched_at,
            status=QuotaStatus.NO_DATA,
        )

    resets = primary.get("resetsAt")
    resets_ms: int | None = None
    if isinstance(resets, (int, float)) and not isinstance(resets, bool):
        resets_ms = int(resets * 1000)  # seconds -> milliseconds

    plan_type = payload.get("plan_type")
    plan_level = plan_type if isinstance(plan_type, str) and plan_type else None

    window = QuotaWindow(
        kind=QuotaWindowKind.RATE_LIMIT,
        used_percent=used,
        resets_at=resets_ms,
        raw=dict(primary),
    )
    return QuotaSnapshot(
        provider=Provider.CODEX,
        account_id=account_id,
        plan_level=plan_level,
        windows=(window,),
        fetched_at=fetched_at,
        status=QuotaStatus.OK,
    )


def make_codex_observer(
    read_model: QuotaReadModel,
    *,
    account_id: str = DEFAULT_CODEX_ACCOUNT_ID,
    now_ms: Callable[[], int] | None = None,
) -> Callable[[Mapping[str, Any]], None]:
    """Build a sync SessionHub observer that folds ``rate_limit_updated`` in.

    The observer receives a dumped AgentEvent envelope (the shape the hub
    yields). It ignores everything except ``type == "rate_limit_updated"``,
    whose ``payload`` it maps via ``parse_codex_rate_limit`` and records.
    Non-matching events cost one dict lookup.
    """

    clock = now_ms or _now_ms

    def observe(envelope: Mapping[str, Any]) -> None:
        if not isinstance(envelope, Mapping):
            return
        etype = envelope.get("type")
        if etype == "rate_limit_updated":
            data = envelope.get("payload")
            if isinstance(data, Mapping):
                snapshot = parse_codex_rate_limit(
                    data, account_id=account_id, fetched_at=int(clock())
                )
                read_model.update(snapshot)
        elif etype in _TERMINAL_TYPES:
            # turn ended — the account rate limit is no longer being pushed, so
            # serve the last snapshot as stale until the next turn refreshes it.
            read_model.mark_stale(Provider.CODEX, account_id)

    return observe
