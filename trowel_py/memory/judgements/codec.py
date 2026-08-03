"""在判效数据契约与 JSON 字段字典之间转换。"""

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
    """按固定字段集展开 Hit，不额外校验或转换字段值。"""
    return {
        "memory_id": h.memory_id,
        "used": h.used,
        "outcome": h.outcome,
        "reason": h.reason,
        "evidence": h.evidence,
    }


def _miss_to_dict(m: MissJudgement) -> dict[str, object]:
    """按固定字段集展开 Recall miss，不额外校验或转换字段值。"""
    return {
        "memory_id": m.memory_id,
        "attribution": m.attribution,
        "reason": m.reason,
        "evidence": m.evidence,
    }


def _hit_from_dict(d: dict[str, object]) -> HitJudgement:
    """读取 Hit，并严格校验 outcome 词表。

    ``memory_id``、``reason`` 和 ``evidence`` 的假值变为空字符串，其余值
    使用 ``str()``；``used`` 使用 Python 真值规则，因此非空字符串
    ``"false"`` 也会得到 ``True``。

    Raises:
        ValueError: outcome 不在允许词表中。
        TypeError: outcome 不可哈希。
    """
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
    """读取 Recall miss，并严格校验 attribution 词表。

    ``memory_id``、``reason`` 和 ``evidence`` 的假值变为空字符串，其余值
    使用 ``str()``。

    Raises:
        ValueError: attribution 不在允许词表中。
        TypeError: attribution 不可哈希。
    """
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
    """按固定字段集展开报告，将两个元组转换为字典列表且不校验字段值。"""
    return {
        "cc_session_id": r.cc_session_id,
        "hits": [_hit_to_dict(hit) for hit in r.hits],
        "recall_miss": [_miss_to_dict(miss) for miss in r.recall_miss],
        "summary": r.summary,
        "segment_id": r.segment_id,
        "activity_dates": list(r.activity_dates),
    }


def _report_from_dict(d: dict[str, object]) -> JudgementReport:
    """从字段字典读取报告，并兼容集合形状错误。

    ``hits`` 和 ``recall_miss`` 只有在值为列表时才解析，否则按空列表处理；
    列表中的非对象项会跳过，对象项的非法 outcome 或 attribution 则中止整份
    报告。顶层文本字段的假值变为空字符串，其余值使用 ``str()``。两个列表中
    保留项的原顺序不变。

    Raises:
        ValueError: 任一对象项的 outcome 或 attribution 不在允许词表中。
        TypeError: 任一对象项的 outcome 或 attribution 不可哈希。
    """
    raw_hits_value = d.get("hits", [])
    raw_hits = raw_hits_value if isinstance(raw_hits_value, list) else []
    raw_miss_value = d.get("recall_miss", [])
    raw_miss = raw_miss_value if isinstance(raw_miss_value, list) else []
    raw_activity_dates_value = d.get("activity_dates", [])
    raw_activity_dates = (
        raw_activity_dates_value
        if isinstance(raw_activity_dates_value, list)
        else []
    )
    return JudgementReport(
        cc_session_id=str(d.get("cc_session_id") or ""),
        hits=tuple(_hit_from_dict(hit) for hit in raw_hits if isinstance(hit, dict)),
        recall_miss=tuple(
            _miss_from_dict(miss) for miss in raw_miss if isinstance(miss, dict)
        ),
        summary=str(d.get("summary") or ""),
        segment_id=str(d.get("segment_id") or ""),
        activity_dates=tuple(
            str(value) for value in raw_activity_dates if str(value).strip()
        ),
    )
