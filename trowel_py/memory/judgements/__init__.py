"""会话 judgement 的数据契约与稳定入口。"""

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
    memory_id: str
    used: bool
    outcome: Outcome
    reason: str
    evidence: str


@dataclass(frozen=True)
class MissJudgement:
    memory_id: str
    attribution: Attribution
    reason: str
    evidence: str


@dataclass(frozen=True)
class JudgementReport:
    cc_session_id: str
    hits: tuple[HitJudgement, ...]
    recall_miss: tuple[MissJudgement, ...]
    summary: str
    segment_id: str = ""


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
