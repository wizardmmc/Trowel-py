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
    """返回 Memory 根目录下固定的 Tidy 水位文件路径。

    Args:
        root: Memory 根目录。

    Returns:
        ``<root>/meta/tidy-state.json``。
    """
    return Path(root) / _STATE_REL


def load_state(root: Path | str) -> TidyState:
    """读取 Tidy 水位，无法读取或解析时返回空状态。

    文件不存在、读取失败或 JSON 损坏都会从空水位重新开始，并仅对后两种情况
    记录警告。成功解码的 JSON 交给 :meth:`TidyState.from_dict` 恢复；非字典
    值返回空状态，类型或格式无效的周月周期分别置空。

    Args:
        root: Memory 根目录。

    Returns:
        文件中的水位；无法恢复时为 ``TidyState()``。
    """
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
    """将 Tidy 水位写入同目录临时文件，再原子替换正式文件。

    临时文件固定为 ``tidy-state.json.tmp``；并发调用不会获得额外锁保护，写入
    或替换失败也可能遗留该文件。

    Args:
        root: Memory 根目录；缺失的 ``meta`` 目录会自动创建。
        state: 待持久化的完整水位。

    Raises:
        OSError: 创建目录、写临时文件或替换正式文件失败。
    """
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
    """覆盖一个周期水位，保留另一周期水位并持久化结果。

    ``scope == "weekly"`` 时覆盖周水位，其他值均按月水位处理。``period`` 不
    校验格式或先后顺序，因此调用方也可以覆盖为更早水位。

    Args:
        root: Memory 根目录。
        scope: 要覆盖的周期范围。
        period: 新水位文本。
        now: 用于生成 ``updated_at`` 的时间。

    Returns:
        已写入文件的新状态。

    Raises:
        OSError: 创建目录、写临时文件或替换正式状态文件失败。
    """
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
    """返回周月水位及各自尚待处理的已完成周期。

    文件缺失或损坏时按空水位计算，因此每个范围只列出最近一个已完成周期。
    待处理列表使用周期枚举器的默认上限，不包含当前进行中的周期。

    Args:
        root: Memory 根目录。
        now: 计算已完成周期的基准时间；省略时使用本地当前时间。

    Returns:
        含 ``weekly``、``monthly`` 和 ``updated_at`` 的状态字典；前两个字段
        各含 ``last_successful`` 与 ``pending``。

    Raises:
        ValueError: 已保存的月水位推进后得到无法解析的五位年份。
        OverflowError: 基准时间的上一周期或已保存周水位的下一周期超出
            ``datetime`` 支持范围。
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
