"""提供 Memory 健康与使用质量指标的公开入口。

本模块通过显式包装器保持既有公开导入路径、函数 ``__module__`` 属性与签名，
并提供接收同请求 Note 快照的组合入口。健康指标包装器会把本模块当前绑定的
``MemoryStore`` 与 harmful 阈值注入底层实现，使调用方替换这些依赖后仍然生效。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import HARMFUL_RETIRE_THRESHOLD

from .health import compute_north_star as _compute_north_star
from .usage import memory_usage_metrics as _memory_usage_metrics

if TYPE_CHECKING:
    from trowel_py.memory.promotion_policy import PromotionPolicy
    from trowel_py.memory.types import Note


def compute_north_star(
    root: Path | str,
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """计算 Note 语料和原始访问与反馈日志的健康指标。

    每次调用都把本模块当前绑定的 ``MemoryStore`` 和
    ``HARMFUL_RETIRE_THRESHOLD`` 传给底层实现。函数不写入 Memory 数据；
    无效 Note 与日志行的跳过规则由底层存储和日志读取器决定。

    Args:
        root: Note、访问日志和反馈日志所在的 Memory 根目录。
        today: 报告的 ``as_of`` 日期；为 None 或空字符串时底层使用系统本地
            当前日期。函数不校验非空日期文本。

    Returns:
        包含 ``as_of``、以非退休 Note 为分母的 harmful 比率、活动 Note 数、
        矛盾或已取代 Note 合计、harmful 高值 Note 数、阈值、原始读取与
        harmful 反馈计数，以及固定为 ``None`` 的已知问题重复率占位值的字典。
    """
    return _compute_north_star(
        root,
        today=today,
        store_cls=MemoryStore,
        harmful_retire_threshold=HARMFUL_RETIRE_THRESHOLD,
    )


def compute_north_star_from_notes(
    root: Path | str,
    notes_with_id: list[tuple[str, "Note"]],
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """使用同请求已加载的 Note 快照计算语料健康和原始日志计数。

    Args:
        root: access 与 outcome 日志所在的 Memory 根目录。
        notes_with_id: 文件 stem 与 Note 组成的同请求只读快照。
        today: 报告的 ``as_of`` 日期；为空时使用系统本地日期。

    Returns:
        与 ``compute_north_star`` 相同、但不再次扫描 Note 文件的健康指标。
    """
    return _compute_north_star(
        root,
        today=today,
        store_cls=MemoryStore,
        harmful_retire_threshold=HARMFUL_RETIRE_THRESHOLD,
        notes_with_id=notes_with_id,
    )


def memory_usage_metrics(
    root: Path | str,
    *,
    policy: "PromotionPolicy | None" = None,
    local_tz: Any | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    """计算 Memory 使用质量指标，并按证据覆盖率和样本量标注可信度。

    Args:
        root: 访问日志、会话归因、判断报告和 Note 所在的 Memory 根目录。
        policy: 计算可信度标签的晋升策略；为 None 时使用默认策略。
        local_tz: 解释会话活动日期的本地时区；为 None 时使用系统本地时区。
        window_start: 可选半开查询窗起点；与终点同时提供时只统计窗内证据。
        window_end: 可选半开查询窗终点；不能单独提供。

    Returns:
        包含策略、identity、retrieval、effect、recall 及已知问题重复率占位值的
        指标字典。
    """
    return _memory_usage_metrics(
        root,
        policy=policy,
        local_tz=local_tz,
        window_start=window_start,
        window_end=window_end,
    )


def memory_usage_metrics_from_notes(
    root: Path | str,
    notes_with_id: list[tuple[str, "Note"]],
    *,
    policy: "PromotionPolicy | None" = None,
    local_tz: Any | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    """使用同请求已加载的 Note 快照计算 Memory 使用质量。

    Args:
        root: 访问日志、会话归因和判效报告所在的 Memory 根目录。
        notes_with_id: 文件 stem 与 Note 组成的同请求只读快照。
        policy: 计算可信度标签的晋升策略；为 None 时使用默认策略。
        local_tz: 解释会话活动日期的本地时区；为空时使用系统本地时区。
        window_start: 可选半开查询窗起点；必须与终点同时提供。
        window_end: 可选半开查询窗终点；必须与起点同时提供。

    Returns:
        与 ``memory_usage_metrics`` 相同、但不再次扫描 Note 文件的指标字典。
    """
    return _memory_usage_metrics(
        root,
        policy=policy,
        local_tz=local_tz,
        window_start=window_start,
        window_end=window_end,
        notes_with_id=notes_with_id,
    )


__all__ = [
    "compute_north_star",
    "compute_north_star_from_notes",
    "memory_usage_metrics",
    "memory_usage_metrics_from_notes",
]
