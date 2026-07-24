"""Memory 健康与使用质量指标的稳定入口。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import HARMFUL_RETIRE_THRESHOLD

from .health import compute_north_star as _compute_north_star
from .usage import memory_usage_metrics as _memory_usage_metrics

if TYPE_CHECKING:
    from trowel_py.memory.promotion_policy import PromotionPolicy


def compute_north_star(root: Path | str, *, today: str | None = None) -> dict[str, Any]:
    """计算 note 语料健康指标。"""
    return _compute_north_star(
        root,
        today=today,
        store_cls=MemoryStore,
        harmful_retire_threshold=HARMFUL_RETIRE_THRESHOLD,
    )


def memory_usage_metrics(
    root: Path | str,
    *,
    policy: "PromotionPolicy | None" = None,
    local_tz: Any | None = None,
) -> dict[str, Any]:
    """计算带覆盖率标签的 memory 使用质量指标。"""
    return _memory_usage_metrics(root, policy=policy, local_tz=local_tz)


__all__ = ["compute_north_star", "memory_usage_metrics"]
