"""Note 语料健康指标。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from trowel_py.memory.access_log import read_access_log, read_outcome_log


def compute_north_star(
    root: Path | str,
    *,
    today: str | None,
    store_cls: type,
    harmful_retire_threshold: int,
) -> dict[str, Any]:
    """从 note 与日志计算健康指标。"""
    all_notes = list(store_cls(root).load_notes_with_id())
    active = [note for _stem, note in all_notes if note.status == "active"]
    # harmful 分子与分母必须来自同一未退休总体，结果才不会超过 1。
    non_retired = [note for _stem, note in all_notes if note.status != "retired"]
    contradicted_or_superseded = [
        note for note in non_retired if note.status in ("contradicted", "superseded")
    ]
    harmful_high = [
        note for note in non_retired if note.harmful_refs >= harmful_retire_threshold
    ]
    # 同一 note 可能同时被标记矛盾且积累 harmful，不得重复计数。
    harmful_ids = {
        note.memory_id for note in contradicted_or_superseded if note.memory_id
    } | {note.memory_id for note in harmful_high if note.memory_id}
    harmful_rate = len(harmful_ids) / max(len(non_retired), 1)

    reads = sum(1 for record in read_access_log(root) if record.action == "read")
    harmful_outcomes = sum(
        1 for record in read_outcome_log(root) if record.outcome == "harmful"
    )

    return {
        "as_of": today or date.today().isoformat(),
        "harmful_memory_rate": round(harmful_rate, 4),
        "active_notes": len(active),
        "contradicted_or_superseded": len(contradicted_or_superseded),
        "harmful_high_notes": len(harmful_high),
        "harmful_threshold": harmful_retire_threshold,
        "known_issue_repeat_rate": None,
        "raw_reads": reads,
        "raw_harmful_outcomes": harmful_outcomes,
    }
