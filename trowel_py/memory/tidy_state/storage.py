"""Tidy 水位的原子持久化与状态查询。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from .models import TidyState
from .periods import enumerate_pending_months, enumerate_pending_weeks

logger = logging.getLogger("trowel_py.memory.tidy_state")

_STATE_REL = "meta/tidy-state.json"


def state_path(root: Path | str) -> Path:
    return Path(root) / _STATE_REL


def load_state(root: Path | str) -> TidyState:
    """缺失或损坏的文件保守降级为空水位。"""
    path = state_path(root)
    if not path.exists():
        return TidyState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[memory] tidy state corrupt (%s) — bootstrapping from empty",
            exc,
        )
        return TidyState()
    return TidyState.from_dict(data)


def save_state(root: Path | str, state: TidyState) -> None:
    """同目录写临时文件后原子替换正式水位。"""
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        state.to_dict(),
        ensure_ascii=False,
        indent=2,
    )
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def advance_watermark(
    root: Path | str,
    scope: str,
    period: str,
    now: datetime,
) -> TidyState:
    """推进一个 scope，同时保留另一个 scope 的当前水位。"""
    previous = load_state(root)
    stamp = now.isoformat()
    updated = (
        previous.with_weekly(period, stamp)
        if scope == "weekly"
        else previous.with_monthly(period, stamp)
    )
    save_state(root, updated)
    return updated


def tidy_status(
    root: Path | str,
    now: datetime | None = None,
) -> dict[str, object]:
    """只读返回当前水位和待补的已完成周期。"""
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
