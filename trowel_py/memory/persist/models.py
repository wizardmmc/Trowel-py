"""Persist 的结果契约。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PersistReport:
    """一次草稿落盘的产物与完成状态。"""

    notes_written: int
    diary_written: int
    verification_counts: dict[str, int]
    notes_created: tuple[str, ...] = ()
    notes_updated: tuple[str, ...] = ()
    notes_skipped: tuple[str, ...] = ()
    episode_written: bool = False
    reflection_written: bool = False
    escalation_written: bool = False
    manifest_path: str | None = None
    ok: bool = False
