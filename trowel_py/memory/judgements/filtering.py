"""judgement 中未知 memory_id 的过滤边界。"""

from __future__ import annotations

import logging
from dataclasses import replace

from trowel_py.memory.judgements import JudgementReport

logger = logging.getLogger("trowel_py.memory.judgements")


def drop_unknown_memory_ids(
    report: JudgementReport,
    known_ids: frozenset[str],
) -> JudgementReport:
    """移除无法对应真实 note 的 judgement。"""
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
