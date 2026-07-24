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
from trowel_py.memory.prompt import (
    EPISODE_MAX_ITEM_CHARS,
    EPISODE_MAX_ITEMS_PER_DATE,
    EPISODE_MAX_ITEMS_PER_FIELD,
    EPISODE_MAX_TOTAL_CHARS,
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
    """新 draft 使用结构化列表；events 仅用于读取旧记录。"""

    date: str
    outcomes: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    corrections: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    events: str = ""

    def all_items(self) -> list[str]:
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


def validate_draft(draft: Draft) -> list[str]:
    """非空错误列表会拒绝整个 draft，不能部分落盘。"""
    return _run_validation(
        draft,
        note_kinds=NOTE_KINDS,
        verification_tiers=VERIFICATION_TIERS,
        max_items_per_date=EPISODE_MAX_ITEMS_PER_DATE,
        max_items_per_field=EPISODE_MAX_ITEMS_PER_FIELD,
        max_item_chars=EPISODE_MAX_ITEM_CHARS,
        max_total_chars=EPISODE_MAX_TOTAL_CHARS,
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
    )


def _str_list(value: Any) -> tuple[str, ...]:
    return _run_str_list(value)
