"""persistent success watermark for weekly/monthly tidy (slice-063).

The tidy scheduler used to fire only on "the next Monday / the next 1st" with
no memory of what already succeeded, so a missed week/month was silently
dropped — tidy filters by exact ISO week / month, so the next run handled only
the *then*-current interval. This module adds a per-scope ``last_successful``
watermark: on startup or scheduled wake the scheduler enumerates every
COMPLETED period after the watermark and catches up oldest-first, advancing
the watermark only after a period fully succeeds.

State lives at ``<root>/meta/tidy-state.json`` and is replaced atomically
(temp file + ``os.replace``) so a crash mid-write never leaves a half file
(C-6). A corrupt or missing file is NEVER read as "everything succeeded" — it
bootstraps to empty, and the next run catches up only the most recent
completed period, never the whole history (C-8). Deeper history is reachable
only via an explicit ``--from`` (the CLI), never by default.

Period enumeration is pure: ``enumerate_pending_weeks/months`` take the
watermark + ``now`` and return the completed periods to catch up,
oldest-first, never including the in-progress week/month (C-1/C-2/C-3).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: watermark file, next to ``meta/.tidy.lock`` and ``meta/snapshots/``.
_STATE_REL = "meta/tidy-state.json"

#: safety caps — a pathological ancient watermark can never loop forever.
MAX_PENDING_WEEKS = 520  # ~10 years
MAX_PENDING_MONTHS = 1200  # 100 years

_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True)
class TidyState:
    """Per-scope success watermark. ``None`` = never succeeded (bootstrap)."""

    weekly_last: str | None = None
    monthly_last: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "weekly": {"last_successful": self.weekly_last},
            "monthly": {"last_successful": self.monthly_last},
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: object) -> "TidyState":
        if not isinstance(d, dict):
            return cls()
        w = d.get("weekly")
        m = d.get("monthly")
        w = w if isinstance(w, dict) else {}
        m = m if isinstance(m, dict) else {}
        return cls(
            weekly_last=_valid_period(w.get("last_successful"), "weekly"),
            monthly_last=_valid_period(m.get("last_successful"), "monthly"),
            updated_at=d.get("updated_at"),
        )

    def with_weekly(self, period: str, updated_at: str) -> "TidyState":
        """Immutable copy with the weekly watermark advanced."""
        return replace(self, weekly_last=period, updated_at=updated_at)

    def with_monthly(self, period: str, updated_at: str) -> "TidyState":
        """Immutable copy with the monthly watermark advanced."""
        return replace(self, monthly_last=period, updated_at=updated_at)


def state_path(root: Path | str) -> Path:
    """Absolute path of the watermark file under ``root``."""
    return Path(root) / _STATE_REL


def load_state(root: Path | str) -> TidyState:
    """Load the watermark. Missing/corrupt → empty state (C-6 conservative).

    A corrupt file is never read as "all succeeded": we log and return an
    empty state so the next run re-catches-up the most recent period.
    """
    path = state_path(root)
    if not path.exists():
        return TidyState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[memory] tidy state corrupt (%s) — bootstrapping from empty", exc
        )
        return TidyState()
    return TidyState.from_dict(data)


def save_state(root: Path | str, state: TidyState) -> None:
    """Atomically replace the state file (temp + ``os.replace``, C-6).

    State is written LAST — business artifacts are already on disk by the time
    we advance the watermark, so a crash here at most causes a safe re-run of
    the just-finished period (never a false success).
    """
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def advance_watermark(
    root: Path | str, scope: str, period: str, now: datetime
) -> TidyState:
    """Advance one scope's watermark, preserving the other scope (atomic)."""
    prev = load_state(root)
    stamp = now.isoformat()
    new = (
        prev.with_weekly(period, stamp)
        if scope == "weekly"
        else prev.with_monthly(period, stamp)
    )
    save_state(root, new)
    return new


# ---------- period math (pure) ----------


def last_iso_week(now: datetime) -> str:
    """ISO week of the most-recent COMPLETED week at ``now`` (C-3) as YYYY-Www.

    ``now - 7d`` always lands in the previous ISO week regardless of weekday,
    so this is safe from a missed/catchup run too. Handles 跨年
    (e.g. 2027-01-04 → 2026-W53).
    """
    prev = (now - timedelta(days=7)).date()
    y, w, _ = prev.isocalendar()
    return f"{y:04d}-W{w:02d}"


def last_month(now: datetime) -> str:
    """Most-recent COMPLETED month at ``now`` (C-3) as YYYY-MM.

    The day before this month's 1st is last month's last day — robust on any
    trigger day, not just the 1st.
    """
    first = now.date().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _parse_iso_week(s: str) -> tuple[int, int]:
    m = _ISO_WEEK_RE.match(s)
    if not m:
        raise ValueError(f"bad ISO week string: {s!r}")
    y, w = int(m.group(1)), int(m.group(2))
    # raises ValueError if the week doesn't exist (e.g. 2026-W53)
    datetime.fromisocalendar(y, w, 1)
    return y, w


def _parse_month(s: str) -> tuple[int, int]:
    m = _MONTH_RE.match(s)
    if not m:
        raise ValueError(f"bad month string: {s!r}")
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12:
        raise ValueError(f"bad month string: {s!r}")
    return y, mo


def _valid_period(value: object, scope: str) -> str | None:
    """Return ``value`` if it's a well-formed period string for ``scope``,
    else None — so a valid-JSON-but-malformed watermark degrades to the
    conservative bootstrap instead of crashing ``--status`` / catchup (C-6)."""
    if not isinstance(value, str):
        return None
    try:
        if scope == "weekly":
            _parse_iso_week(value)
        else:
            _parse_month(value)
    except ValueError:
        return None
    return value


def next_iso_week(s: str) -> str:
    """The ISO week after ``s`` (YYYY-Www), handling 跨年 and W53 years."""
    y, w = _parse_iso_week(s)
    d = datetime.fromisocalendar(y, w, 1) + timedelta(days=7)
    ny, nw, _ = d.isocalendar()
    return f"{ny:04d}-W{nw:02d}"


def next_month(s: str) -> str:
    """The month after ``s`` (YYYY-MM), handling 跨年."""
    y, mo = _parse_month(s)
    mo += 1
    if mo > 12:
        mo, y = 1, y + 1
    return f"{y:04d}-{mo:02d}"


def enumerate_pending_weeks(
    last_successful: str | None, now: datetime, *, cap: int = MAX_PENDING_WEEKS
) -> list[str]:
    """Completed ISO weeks after ``last_successful`` up to the most-recent
    completed week, oldest-first (C-1/C-4).

    - ``None`` → only the most-recent completed week (C-8 bootstrap).
    - Never includes the in-progress week (C-2): the upper bound is
      :func:`last_iso_week`, which is always strictly before ``now``'s week.
    - Zero-padded ``YYYY-Www`` compares as a string correctly, including 跨年.
    """
    end = last_iso_week(now)
    if last_successful is None:
        return [end]
    pending: list[str] = []
    cur = last_successful
    while len(pending) < cap:
        nxt = next_iso_week(cur)
        if nxt > end:
            break
        pending.append(nxt)
        cur = nxt
    return pending


def enumerate_pending_months(
    last_successful: str | None, now: datetime, *, cap: int = MAX_PENDING_MONTHS
) -> list[str]:
    """Completed months after ``last_successful`` up to the most-recent
    completed month, oldest-first (C-1/C-4).

    - ``None`` → only the most-recent completed month (C-8 bootstrap).
    - Never includes the in-progress month (C-2).
    """
    end = last_month(now)
    if last_successful is None:
        return [end]
    pending: list[str] = []
    cur = last_successful
    while len(pending) < cap:
        nxt = next_month(cur)
        if nxt > end:
            break
        pending.append(nxt)
        cur = nxt
    return pending


def tidy_status(root: Path | str, now: datetime | None = None) -> dict[str, object]:
    """Read-only snapshot of the watermark + pending periods (no writes).

    Used by ``trowel memory tidy --status`` for diagnostics: shows how far each
    scope's watermark has advanced and which completed periods still need a run.
    """
    now = now or datetime.now()
    state = load_state(root)
    return {
        "weekly": {
            "last_successful": state.weekly_last,
            "pending": enumerate_pending_weeks(state.weekly_last, now),
        },
        "monthly": {
            "last_successful": state.monthly_last,
            "pending": enumerate_pending_months(state.monthly_last, now),
        },
        "updated_at": state.updated_at,
    }
