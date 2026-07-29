"""按当前 Note ID 集合过滤判效报告中的未知引用。"""

from __future__ import annotations

import logging
from dataclasses import replace

from trowel_py.memory.judgements import JudgementReport

logger = logging.getLogger("trowel_py.memory.judgements")


def drop_unknown_memory_ids(
    report: JudgementReport,
    known_ids: frozenset[str],
) -> JudgementReport:
    """移除当前 Note 集合中不存在的 Hit 和 Recall miss。

    两组条目分别按原顺序过滤，不去重；会话 ID、片段 ID 和摘要保持不变。
    函数始终通过 ``dataclasses.replace`` 返回新报告，仅在实际丢弃条目时记录
    两组合计数量。未知 ID 可能来自伪造、删除或过期快照，本函数不区分原因。

    Args:
        report: 要过滤的判效报告。
        known_ids: 当前允许引用的持久化 ``Note.memory_id`` 集合。

    Returns:
        只保留已知 Note ID 的新报告。
    """
    kept_hits = tuple(hit for hit in report.hits if hit.memory_id in known_ids)
    kept_miss = tuple(
        miss for miss in report.recall_miss if miss.memory_id in known_ids
    )
    dropped = (len(report.hits) - len(kept_hits)) + (
        len(report.recall_miss) - len(kept_miss)
    )
    if dropped:
        logger.info(
            "dropped %d fabricated memory_id judgement(s) for %s",
            dropped,
            report.cc_session_id,
        )
    return replace(report, hits=kept_hits, recall_miss=kept_miss)
