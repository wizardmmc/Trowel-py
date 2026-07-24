"""提炼 draft 的稳定模型、解析与 gate 入口。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from trowel_py.memory.draft.parser import parse_diary as _run_parse_diary
from trowel_py.memory.draft.parser import parse_draft as _run_parse_draft
from trowel_py.memory.draft.parser import parse_note as _run_parse_note
from trowel_py.memory.draft.parser import str_list as _run_str_list
from trowel_py.memory.draft.validation import (
    procedure_warnings as _run_procedure_warnings,
)
from trowel_py.memory.draft.validation import validate_draft as _run_validation
from trowel_py.memory.draft.episode import (
    DraftCorrection as DraftCorrection,
    DraftDecision as DraftDecision,
    DraftEpisodeItem,
    DraftEvidence as DraftEvidence,
    DraftOpenLoop as DraftOpenLoop,
    DraftOutcome as DraftOutcome,
    episode_item_text,
    episode_item_to_dict as episode_item_to_dict,
    parse_episode_item,
    project_episode_items,
)
from trowel_py.memory.prompt import (
    NOTE_KINDS,
    VERIFICATION_TIERS,
)


@dataclass(frozen=True)
class DraftNote:
    title: str
    summary: str = ""
    body: str = ""
    tags: tuple[str, ...] = ()
    kind: str = "fact"
    verification: str = "inferred-untested"
    verification_reason: str = ""
    pain: int = 0
    pain_reason: str = ""
    conflicts_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftDiary:
    """items 是 v2 写入契约；四列表与 events 仅用于旧记录。"""

    date: str
    outcomes: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    corrections: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    events: str = ""
    items: tuple[DraftEpisodeItem, ...] = ()

    def __post_init__(self) -> None:
        if not self.items:
            return
        outcomes, decisions, corrections, open_loops = project_episode_items(self.items)
        if not self.outcomes:
            object.__setattr__(self, "outcomes", outcomes)
        if not self.decisions:
            object.__setattr__(self, "decisions", decisions)
        if not self.corrections:
            object.__setattr__(self, "corrections", corrections)
        if not self.open_loops:
            object.__setattr__(self, "open_loops", open_loops)

    def all_items(self) -> list[str]:
        if self.items:
            return [episode_item_text(item) for item in self.items]
        return [
            *self.outcomes,
            *self.decisions,
            *self.corrections,
            *self.open_loops,
        ]


@dataclass(frozen=True)
class Draft:
    notes: tuple[DraftNote, ...] = ()
    diary: tuple[DraftDiary, ...] = ()
    reflection: str = ""
    escalate_to_human: tuple[str, ...] = ()


def parse_draft(text: str) -> Draft:
    return _run_parse_draft(
        text,
        loads=json.loads,
        draft_type=Draft,
        parse_note=_parse_note,
        parse_diary=_parse_diary,
    )


def validate_draft(
    draft: Draft,
    *,
    legal_source_refs: set[str] | None = None,
) -> list[str]:
    """非空错误列表会拒绝整个 draft，不能部分落盘。"""
    return _run_validation(
        draft,
        note_kinds=NOTE_KINDS,
        verification_tiers=VERIFICATION_TIERS,
        legal_source_refs=legal_source_refs,
    )


_PROCEDURE_ELEMENTS: dict[str, tuple[str, ...]] = {
    "trigger": ("trigger", "触发", "场景是", "什么场景"),
    "procedure": ("procedure", "做法", "步骤", "怎么做"),
    "stop": ("stop", "何时停", "停止条件", "终止"),
    "anti-pattern": ("anti-pattern", "anti pattern", "别做", "不要", "反面"),
}


def procedure_warnings(draft: Draft) -> list[str]:
    """缺少过程要素只告警，不阻止模型继续产出。"""
    return _run_procedure_warnings(draft, elements=_PROCEDURE_ELEMENTS)


def _parse_note(n: dict[str, Any]) -> DraftNote:
    return _run_parse_note(n, note_type=DraftNote)


def _parse_diary(d: dict[str, Any]) -> DraftDiary:
    return _run_parse_diary(
        d,
        diary_type=DraftDiary,
        str_list=_str_list,
        parse_episode_item=parse_episode_item,
    )


def _str_list(value: Any) -> tuple[str, ...]:
    return _run_str_list(value)
