"""note 效果重算的稳定入口与数据模型。"""

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
    """由日志与 judgement 重建的 note 会话级效果。"""

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
        return len(self.read_sessions)

    @property
    def helpful_refs(self) -> int:
        return len(self.helpful_sessions)

    @property
    def harmful_refs(self) -> int:
        return len(self.harmful_sessions)

    @property
    def unused_refs(self) -> int:
        return len(self.unused_sessions)

    @property
    def distinct_days(self) -> int:
        return len(self.helpful_read_dates)

    @property
    def last_ref(self) -> str:
        return max(self.read_dates) if self.read_dates else ""


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
