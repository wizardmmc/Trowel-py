"""从 Note 状态、harmful 引用和原始日志计算语料健康指标。

核心 harmful 比率只评估未退休 Note，并把矛盾/已取代状态与达到 harmful 阈值
视为同一风险集合。访问与反馈日志另作为原始事件计数返回，不参与该比率。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trowel_py.memory.access_log import read_access_log, read_outcome_log

if TYPE_CHECKING:
    from trowel_py.memory.types import Note


def compute_north_star(
    root: Path | str,
    *,
    today: str | None,
    store_cls: type,
    harmful_retire_threshold: int,
    notes_with_id: list[tuple[str, "Note"]] | None = None,
) -> dict[str, Any]:
    """计算 Note 语料风险比例和原始使用事件计数。

    所有 Note 指标只基于 ``store_cls(root).load_notes_with_id()`` 返回的记录；
    默认 ``MemoryStore`` 会跳过无有效 frontmatter 或类型不是 Note 的文件。
    ``active_notes`` 只统计 ``status == "active"``。单独返回的
    ``contradicted_or_superseded`` 和 ``harmful_high_notes`` 都在未退休记录内
    按 Note 条数统计，不按 ``memory_id`` 去重，ID 为空的 Note 也会计入。

    harmful 分母是 ``status != "retired"`` 的 Note 条数。分子是其中状态为
    ``contradicted`` 或 ``superseded``，或者 ``harmful_refs`` 大于等于阈值的
    Note 所对应的非空 ``memory_id`` 并集。因此同一 Note 同时满足两类条件只计
    一次，重复 ``memory_id`` 也只计一次，缺少 ID 的 Note 不进入分子但仍进入
    分母。没有未退休 Note 时分母按 1 处理，结果为 0.0；最终比率保留四位小数。

    ``raw_reads`` 统计 action 为 ``read`` 的访问日志事件，
    ``raw_harmful_outcomes`` 统计 outcome 为 ``harmful`` 的反馈日志事件。两者
    都不按 ID 或会话去重，也不校验读与反馈是否对应。

    Args:
        root: Note、访问日志和反馈日志所在的 Memory 根目录。
        today: 报告的 ``as_of`` 文本；为 None 或空字符串时使用系统本地日期，
            其他非空文本原样返回。
        store_cls: 接收根目录并提供 ``load_notes_with_id()`` 的存储类。
        harmful_retire_threshold: ``harmful_refs`` 达到或超过该值时判为高风险；
            函数不校验范围。
        notes_with_id: 可选的同请求 Note 快照；提供时不再次扫描 Note 文件。

    Returns:
        包含 ``as_of``、``harmful_memory_rate``、活动 Note 数、矛盾或已取代
        Note 合计、达到 harmful 阈值的 Note 数、阈值本身、原始读取与 harmful
        反馈事件数，以及固定为 ``None`` 的已知问题重复率占位值的字典。
    """
    all_notes = list(
        notes_with_id
        if notes_with_id is not None
        else store_cls(root).load_notes_with_id()
    )
    active = [note for _stem, note in all_notes if note.status == "active"]
    # 分子只从未退休总体取值，与分母保持同一范围，结果不会超过 1。
    non_retired = [note for _stem, note in all_notes if note.status != "retired"]
    contradicted_or_superseded = [
        note for note in non_retired if note.status in ("contradicted", "superseded")
    ]
    harmful_high = [
        note for note in non_retired if note.harmful_refs >= harmful_retire_threshold
    ]
    # 两类风险按非空 memory_id 取并集；重叠条件或重复 ID 都只计一次。
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
