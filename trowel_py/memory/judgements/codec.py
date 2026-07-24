"""judgement 严格 JSON 编解码。"""

from __future__ import annotations

from typing import cast

from trowel_py.memory.judgements import (
    Attribution,
    HitJudgement,
    JudgementReport,
    MissJudgement,
    Outcome,
    VALID_ATTRIBUTIONS,
    VALID_OUTCOMES,
)


def _hit_to_dict(h: HitJudgement) -> dict[str, object]:
    return {
        "memory_id": h.memory_id,
        "used": h.used,
        "outcome": h.outcome,
        "reason": h.reason,
        "evidence": h.evidence,
    }


def _miss_to_dict(m: MissJudgement) -> dict[str, object]:
    return {
        "memory_id": m.memory_id,
        "attribution": m.attribution,
        "reason": m.reason,
        "evidence": m.evidence,
    }


def _hit_from_dict(d: dict[str, object]) -> HitJudgement:
    outcome = d.get("outcome")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r} in judgement hit")
    return HitJudgement(
        memory_id=str(d.get("memory_id") or ""),
        used=bool(d.get("used")),
        outcome=cast(Outcome, outcome),
        reason=str(d.get("reason") or ""),
        evidence=str(d.get("evidence") or ""),
    )


def _miss_from_dict(d: dict[str, object]) -> MissJudgement:
    attribution = d.get("attribution")
    if attribution not in VALID_ATTRIBUTIONS:
        raise ValueError(f"unknown attribution {attribution!r} in judgement miss")
    return MissJudgement(
        memory_id=str(d.get("memory_id") or ""),
        attribution=cast(Attribution, attribution),
        reason=str(d.get("reason") or ""),
        evidence=str(d.get("evidence") or ""),
    )


def _report_to_dict(r: JudgementReport) -> dict[str, object]:
    return {
        "cc_session_id": r.cc_session_id,
        "hits": [_hit_to_dict(hit) for hit in r.hits],
        "recall_miss": [_miss_to_dict(miss) for miss in r.recall_miss],
        "summary": r.summary,
        "segment_id": r.segment_id,
    }


def _report_from_dict(d: dict[str, object]) -> JudgementReport:
    raw_hits_value = d.get("hits", [])
    raw_hits = raw_hits_value if isinstance(raw_hits_value, list) else []
    raw_miss_value = d.get("recall_miss", [])
    raw_miss = raw_miss_value if isinstance(raw_miss_value, list) else []
    return JudgementReport(
        cc_session_id=str(d.get("cc_session_id") or ""),
        hits=tuple(_hit_from_dict(hit) for hit in raw_hits if isinstance(hit, dict)),
        recall_miss=tuple(
            _miss_from_dict(miss) for miss in raw_miss if isinstance(miss, dict)
        ),
        summary=str(d.get("summary") or ""),
        segment_id=str(d.get("segment_id") or ""),
    )
