"""judge agent 草稿的宽松解析。"""

from __future__ import annotations

import json
from typing import cast

from trowel_py.memory.judgements import (
    VALID_ATTRIBUTIONS,
    VALID_OUTCOMES,
    Attribution,
    HitJudgement,
    JudgementReport,
    MissJudgement,
    Outcome,
)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    return bool(value)


def _parse_draft(
    text: str,
    *,
    cc_session_id: str,
    segment_id: str = "",
) -> JudgementReport:
    from trowel_py.memory.judge import JudgeError

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judgement-draft.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeError("judgement-draft.json is not a JSON object")

    hits: list[HitJudgement] = []
    for item in data.get("hits", []) or []:
        if not isinstance(item, dict):
            continue
        outcome = item.get("outcome")
        if outcome not in VALID_OUTCOMES:
            outcome = "unknown"
        hits.append(
            HitJudgement(
                memory_id=str(item.get("memory_id") or ""),
                used=_coerce_bool(item.get("used")),
                outcome=cast(Outcome, outcome),
                reason=str(item.get("reason") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    misses: list[MissJudgement] = []
    for item in data.get("recall_miss", []) or []:
        if not isinstance(item, dict):
            continue
        attribution = item.get("attribution")
        if attribution not in VALID_ATTRIBUTIONS:
            continue
        misses.append(
            MissJudgement(
                memory_id=str(item.get("memory_id") or ""),
                attribution=cast(Attribution, attribution),
                reason=str(item.get("reason") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    return JudgementReport(
        cc_session_id=cc_session_id,
        hits=tuple(hits),
        recall_miss=tuple(misses),
        summary=str(data.get("summary") or ""),
        segment_id=segment_id,
    )
