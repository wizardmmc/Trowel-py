"""提供 note 效果重算的公开入口与结果模型。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Any

from trowel_py.memory.access_log import read_access_log, read_outcome_log
from trowel_py.memory.activity_dates import _parse_iso_to_date, _system_local_tz
from trowel_py.memory.attribution import AttributionIndex
from trowel_py.memory.judgements import load_all_judgement_reports
from trowel_py.memory.store import MemoryStore


@dataclass(frozen=True)
class NoteEffect:
    """一条 note 从读取日志和 judgement 重建出的效果证据。

    Attributes:
        stem: note 文件当前的 stem，也是读取日志和结果映射使用的键。
        memory_id: note frontmatter 中与标题和文件名解耦的稳定标识；旧数据可能为空。
        refs: 用户会话产生的有效读取事件数。
        read_sessions: 实际读取过 note 的用户会话标识集合。
        helpful_sessions: 对 note 提供有帮助证据的用户会话标识集合。
        harmful_sessions: 对 note 提供有害证据的用户会话标识集合。
        unused_sessions: 明确表示未使用 note 的用户会话标识集合。
        read_dates: 有效读取事件按本地时区换算出的日期集合。
        helpful_read_dates: 提供有帮助证据的会话实际读取 note 的日期集合。
    """

    stem: str
    memory_id: str
    refs: int
    read_sessions: frozenset[str]
    helpful_sessions: frozenset[str]
    harmful_sessions: frozenset[str]
    unused_sessions: frozenset[str]
    read_dates: frozenset[str]
    helpful_read_dates: frozenset[str]

    @property
    def read_session_count(self) -> int:
        """返回实际读取过该笔记的独立会话数。"""
        return len(self.read_sessions)

    @property
    def helpful_refs(self) -> int:
        """返回被判为有帮助的独立会话数。"""
        return len(self.helpful_sessions)

    @property
    def harmful_refs(self) -> int:
        """返回被判为有害的独立会话数。"""
        return len(self.harmful_sessions)

    @property
    def unused_refs(self) -> int:
        """返回被明确判为未使用的独立会话数。"""
        return len(self.unused_sessions)

    @property
    def distinct_days(self) -> int:
        """返回有帮助会话实际读取该笔记的不同日期数。"""
        return len(self.helpful_read_dates)

    @property
    def last_ref(self) -> str:
        """返回最近一次有效读取日期；没有读取时返回空字符串。"""
        return max(self.read_dates) if self.read_dates else ""


# effects 会从本模块回引 NoteEffect，必须等该类型定义完成后再导入。
from trowel_py.memory.recompute.effects import (  # noqa: E402
    compute_note_effects as _compute_note_effects,
)
from trowel_py.memory.recompute.counters import (  # noqa: E402
    recompute_counters as _recompute_counters,
)


def compute_note_effects(
    root: Path | str,
    *,
    local_tz: tzinfo | None = None,
) -> dict[str, NoteEffect]:
    """汇总每条笔记的用户会话级效果证据。

    只接纳归因为用户会话的证据；outcome 必须关联同一会话的真实 read。同一
    note 与会话出现冲突时，按 ``harmful > helpful > unused`` 取唯一结果。
    该门面在调用时将本模块当前绑定的依赖传给底层实现，因此对这些符号的
    monkeypatch 能作用于聚合过程。

    Args:
        root: memory 根目录。
        local_tz: 读取时间采用的时区；省略时使用系统本地时区。

    Returns:
        以 note stem 为键的效果；没有有效证据的 note 不出现在结果中。
    """
    return _compute_note_effects(
        root,
        local_tz=local_tz,
        store_cls=MemoryStore,
        attribution_index_cls=AttributionIndex,
        system_local_tz_fn=_system_local_tz,
        parse_iso_to_date_fn=_parse_iso_to_date,
        read_access_log_fn=read_access_log,
        read_outcome_log_fn=read_outcome_log,
        load_reports_fn=load_all_judgement_reports,
        effect_cls=NoteEffect,
    )


def recompute_counters(
    root: Path | str,
    *,
    local_tz: tzinfo | None = None,
) -> dict[str, Any]:
    """从效果证据重算并覆盖 note 的效果缓存字段。

    覆盖字段为 ``refs``、``read_sessions``、``helpful_refs``、
    ``harmful_refs`` 和 ``last_ref``。仅更新有重算效果或需要清除旧非零缓存
    的 note；没有证据且缓存已为零的 note 不计入更新数量。

    Args:
        root: memory 根目录。
        local_tz: 读取时间采用的时区；省略时使用系统本地时区。

    Returns:
        更新数量以及读取、会话、有帮助和有害计数的重算合计值。清除旧缓存
        会增加更新数量，但不会增加这些合计值。
    """
    return _recompute_counters(
        root,
        local_tz=local_tz,
        store_cls=MemoryStore,
        compute_effects_fn=compute_note_effects,
    )


__all__ = [
    "Any",
    "AttributionIndex",
    "MemoryStore",
    "NoteEffect",
    "Path",
    "_parse_iso_to_date",
    "_system_local_tz",
    "compute_note_effects",
    "dataclass",
    "defaultdict",
    "load_all_judgement_reports",
    "read_access_log",
    "read_outcome_log",
    "recompute_counters",
    "tzinfo",
]
