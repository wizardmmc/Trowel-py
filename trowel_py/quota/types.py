"""Frozen value objects for the cross-provider quota read model (slice-093-pre).

Plain enums + frozen dataclasses: no behaviour, no I/O. The GLM poller
(``glm.py``), the Codex adapter (``codex.py``) and the read model
(``read_model.py``) build on these. Mirrors the model_os ``types.py``
convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Provider(str, Enum):
    """Which provider an account draws quota from."""

    GLM = "glm"
    CODEX = "codex"


class QuotaStatus(str, Enum):
    """State of a quota snapshot, mirroring the KiwiGaze usage-client pattern.

    Any failure (the endpoint changed, auth expired, network dropped) maps to
    a non-ok status and the read model degrades — it never blocks an episode.
    ``STALE`` is set by the read model when a cached snapshot ages out.
    """

    OK = "ok"
    NO_DATA = "no-data"
    AUTH_ERROR = "auth-error"
    SERVER_ERROR = "server-error"
    NETWORK_ERROR = "network-error"
    STALE = "stale"


class QuotaWindowKind(str, Enum):
    """What quota window a ``QuotaWindow`` describes.

    GLM distinguishes session/weekly/monthly-search; Codex exposes a single
    rolling ``rate_limit`` window. The two providers' kinds are NOT one-to-one
    — the WorkBroker compares ``used_percent`` (how full), not kind equality.
    """

    SESSION_5H = "session_5h"
    WEEKLY = "weekly"
    RATE_LIMIT = "rate_limit"
    WEB_SEARCHES_MONTHLY = "web_searches_monthly"


@dataclass(frozen=True)
class QuotaWindow:
    """One quota window.

    Attributes:
        kind: which window this is.
        used_percent: percent USED (remaining = 100 - used_percent). Unified
            across providers: GLM ``percentage`` and Codex ``usedPercent`` are
            both "used", so the semantics line up.
        resets_at: epoch milliseconds when the window refills, or ``None`` if
            the provider did not report one. Normalised to ms regardless of
            the provider's native unit.
        raw: the provider's fields preserved verbatim, so a later UI pass needs
            no schema change (same principle as slice-077 decision 5).
    """

    kind: QuotaWindowKind
    used_percent: float
    resets_at: int | None
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class QuotaSnapshot:
    """A provider's quota state for one account at a moment.

    Attributes:
        provider: glm | codex.
        account_id: which account slot (e.g. ``glm-a``); provider-scoped.
        plan_level: provider-reported plan tier (``max``/``pro``/``lite``) or
            ``None`` when unreported.
        windows: the quota windows, ordered. Empty when status is not ``OK``.
        fetched_at: epoch ms the snapshot was produced.
        status: see ``QuotaStatus``.
    """

    provider: Provider
    account_id: str
    plan_level: str | None
    windows: tuple[QuotaWindow, ...]
    fetched_at: int
    status: QuotaStatus
