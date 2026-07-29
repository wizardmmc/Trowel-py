"""定义会话判效数据契约，并集中导出编解码、过滤和仓储入口。

``Literal`` 和 ``VALID_*`` 只定义允许词表，三个 frozen dataclass 的构造器均
不校验字段值。JSON 解码器校验 outcome 和 attribution 词表，judge 草稿解析器
执行兼容归一化，judge facade 在保存前过滤不存在的 ``Note.memory_id``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_META_DIR = "meta"
_JUDGEMENTS_DIR = "judgements"

VALID_OUTCOMES: frozenset[str] = frozenset({"helpful", "harmful", "unused", "unknown"})
VALID_ATTRIBUTIONS: frozenset[str] = frozenset({"retrieval_miss", "awareness_miss"})

Outcome = Literal["helpful", "harmful", "unused", "unknown"]
Attribution = Literal["retrieval_miss", "awareness_miss"]


@dataclass(frozen=True)
class HitJudgement:
    """记录一条已检索、读取或使用的 Note 的会话判效。

    Attributes:
        memory_id: 持久化的 ``Note.memory_id``；judge 流程保存前会按当前 Note
            集合过滤不存在的 ID，直接构造或调用仓储不会校验。
        used: 会话是否把该 Note 内容用于实际决策或动作。
        outcome: helpful、harmful 表示使用后的效果，unused 表示未用于决策或
            动作，unknown 表示无法判断。构造器不强制该值与 ``used`` 一致。
        reason: 得出使用与效果判断的理由。
        evidence: 支撑判断的会话步骤或原文证据。
    """

    memory_id: str
    used: bool
    outcome: Outcome
    reason: str
    evidence: str


@dataclass(frozen=True)
class MissJudgement:
    """记录一条当时已有、应使用却没有用上的 Note。

    当时不存在相关 Note 的 novelty 不属于 Recall miss。

    Attributes:
        memory_id: 被漏用的持久化 ``Note.memory_id``；judge 流程保存前会按
            当前 Note 集合过滤不存在的 ID，直接构造或调用仓储不会校验。
        attribution: 漏用归因；retrieval_miss 表示检索和 Dictionary 均未召回，
            awareness_miss 表示已检索到或注入上下文，却未意识到可以使用。
        reason: 判断该 Note 本应使用的理由。
        evidence: 会话中本可借助该 Note 避免绕弯的证据。
    """

    memory_id: str
    attribution: Attribution
    reason: str
    evidence: str


@dataclass(frozen=True)
class JudgementReport:
    """汇总一个 CC 会话或来源片段的可追溯判效结果。

    Attributes:
        cc_session_id: 被判效的 Claude Code 会话 ID。
        hits: 对已检索、读取或使用 Note 的逐条判断。
        recall_miss: 对当时存在但没有用上的 Note 的逐条判断。
        summary: 该会话 Memory 使用情况的一句话总结。
        segment_id: 来源片段 ID；空值写入 ``meta/judgements/<session>.json``，
            非空值写入 ``meta/judgements/<session>/<segment>.json``，其中冒号
            替换为下划线。同一会话存在分段报告时，批量加载会忽略平铺报告。
    """

    cc_session_id: str
    hits: tuple[HitJudgement, ...]
    recall_miss: tuple[MissJudgement, ...]
    summary: str
    segment_id: str = ""


# 以下子模块会反向导入本模块的数据契约，须在定义后延迟导入以免循环初始化失败。
from trowel_py.memory.judgements.codec import (  # noqa: E402
    _hit_from_dict,
    _hit_to_dict,
    _miss_from_dict,
    _miss_to_dict,
    _report_from_dict,
    _report_to_dict,
)
from trowel_py.memory.judgements.filtering import (  # noqa: E402
    drop_unknown_memory_ids,
)
from trowel_py.memory.judgements.repository import (  # noqa: E402
    _judgement_path,
    load_all_judgement_reports,
    load_judgement_report,
    save_judgement_report,
)

__all__ = [
    "Attribution",
    "HitJudgement",
    "JudgementReport",
    "MissJudgement",
    "Outcome",
    "VALID_ATTRIBUTIONS",
    "VALID_OUTCOMES",
    "_JUDGEMENTS_DIR",
    "_META_DIR",
    "_hit_from_dict",
    "_hit_to_dict",
    "_judgement_path",
    "_miss_from_dict",
    "_miss_to_dict",
    "_report_from_dict",
    "_report_to_dict",
    "drop_unknown_memory_ids",
    "load_all_judgement_reports",
    "load_judgement_report",
    "save_judgement_report",
]
